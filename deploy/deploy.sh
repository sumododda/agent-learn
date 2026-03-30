#!/usr/bin/env bash
set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
PROJECT_ID="agent-learn-490805"
REGION="us-central1"
CLUSTER_NAME="agent-learn-cluster"
REPO_NAME="agent-learn"
NAMESPACE="agent-learn"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# ─── Helper Functions ────────────────────────────────────────────────────────
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ─── Step 1: Enable APIs ────────────────────────────────────────────────────
enable_apis() {
  info "Enabling GCP APIs..."
  gcloud services enable \
    container.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    --project="$PROJECT_ID" --quiet
}

# ─── Step 2: Create Artifact Registry ───────────────────────────────────────
create_registry() {
  if gcloud artifacts repositories describe "$REPO_NAME" \
       --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    info "Artifact Registry repo already exists"
  else
    info "Creating Artifact Registry repo..."
    gcloud artifacts repositories create "$REPO_NAME" \
      --repository-format=docker \
      --location="$REGION" \
      --project="$PROJECT_ID"
  fi
}

# ─── Step 3: Create GKE Autopilot Cluster ───────────────────────────────────
create_cluster() {
  if gcloud container clusters describe "$CLUSTER_NAME" \
       --region="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    info "GKE cluster already exists"
  else
    info "Creating GKE Autopilot cluster (this takes ~5 minutes)..."
    gcloud container clusters create-auto "$CLUSTER_NAME" \
      --region="$REGION" \
      --project="$PROJECT_ID"
  fi
  info "Getting cluster credentials..."
  gcloud container clusters get-credentials "$CLUSTER_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID"
}

# ─── Step 4: Create Secrets in Secret Manager ───────────────────────────────
create_secrets() {
  local secrets=("jwt-secret-key" "encryption-pepper" "turnstile-secret-key" "resend-api-key" "resend-from-email" "postgres-password")

  for secret_name in "${secrets[@]}"; do
    if gcloud secrets describe "$secret_name" --project="$PROJECT_ID" &>/dev/null; then
      info "Secret '$secret_name' already exists"
    else
      read -rsp "Enter value for $secret_name: " secret_value
      echo
      printf '%s' "$secret_value" | gcloud secrets create "$secret_name" \
        --data-file=- \
        --project="$PROJECT_ID"
      info "Created secret '$secret_name'"
    fi
  done
}

# ─── Step 5: Sync Secrets to Kubernetes ──────────────────────────────────────
# Creates a k8s Secret named app-secrets from Secret Manager values
sync_secrets() {
  info "Syncing secrets to Kubernetes..."
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

  local args=()
  local secret_keys=("jwt-secret-key" "encryption-pepper" "turnstile-secret-key" "resend-api-key" "resend-from-email" "postgres-password")

  for key in "${secret_keys[@]}"; do
    local value
    value=$(gcloud secrets versions access latest --secret="$key" --project="$PROJECT_ID")
    args+=(--from-literal="${key}=${value}")
  done

  kubectl create secret generic app-secrets \
    -n "$NAMESPACE" \
    "${args[@]}" \
    --dry-run=client -o yaml | kubectl apply -f -

  info "Secrets synced to namespace $NAMESPACE"
}

# ─── Step 6: Build and Push Images ──────────────────────────────────────────
build_and_push() {
  info "Configuring Docker for Artifact Registry..."
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

  info "Building backend image..."
  docker build -f "$SCRIPT_DIR/Dockerfile.backend" \
    -t "${REGISTRY}/backend:latest" \
    "$ROOT_DIR/backend"

  info "Building frontend image..."
  docker build -f "$SCRIPT_DIR/Dockerfile.frontend" \
    --build-arg NEXT_PUBLIC_TURNSTILE_SITE_KEY="${NEXT_PUBLIC_TURNSTILE_SITE_KEY:-}" \
    -t "${REGISTRY}/frontend:latest" \
    "$ROOT_DIR/frontend"

  info "Pushing images..."
  docker push "${REGISTRY}/backend:latest"
  docker push "${REGISTRY}/frontend:latest"
}

# ─── Step 7: Deploy to GKE ──────────────────────────────────────────────────
deploy() {
  info "Applying Kubernetes manifests..."

  # Namespace (idempotent)
  kubectl apply -f "$SCRIPT_DIR/k8s/namespace.yaml"

  # Network policies
  kubectl apply -f "$SCRIPT_DIR/k8s/network-policies.yaml"

  # PostgreSQL
  kubectl apply -f "$SCRIPT_DIR/k8s/postgres.yaml"
  info "Waiting for PostgreSQL to be ready..."
  kubectl rollout status statefulset/postgres -n "$NAMESPACE" --timeout=120s

  # Run migrations
  info "Running database migrations..."
  kubectl delete job migration -n "$NAMESPACE" --ignore-not-found
  kubectl apply -f "$SCRIPT_DIR/k8s/migration-job.yaml"
  kubectl wait --for=condition=complete job/migration -n "$NAMESPACE" --timeout=120s

  # Backend API
  kubectl apply -f "$SCRIPT_DIR/k8s/backend-api.yaml"
  kubectl rollout status deployment/backend-api -n "$NAMESPACE" --timeout=120s

  # Backend Worker
  kubectl apply -f "$SCRIPT_DIR/k8s/backend-worker.yaml"
  kubectl rollout status deployment/backend-worker -n "$NAMESPACE" --timeout=120s

  # Frontend
  kubectl apply -f "$SCRIPT_DIR/k8s/frontend.yaml"
  kubectl rollout status deployment/frontend -n "$NAMESPACE" --timeout=120s

  # Ingress
  kubectl apply -f "$SCRIPT_DIR/k8s/ingress.yaml"
}

# ─── Step 8: Print Status ───────────────────────────────────────────────────
print_status() {
  info "Deployment complete!"
  echo
  kubectl get pods -n "$NAMESPACE"
  echo
  info "Waiting for Ingress external IP (may take 2-5 minutes)..."
  for i in $(seq 1 30); do
    local ip
    ip=$(kubectl get ingress agent-learn-ingress -n "$NAMESPACE" \
         -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
    if [[ -n "$ip" ]]; then
      echo
      info "Ingress IP: $ip"
      info "Set Cloudflare DNS: CNAME learn.blekcipher.com -> $ip (or A record)"
      info "Then visit: https://learn.blekcipher.com"
      return
    fi
    sleep 10
  done
  warn "Ingress IP not yet assigned. Check with: kubectl get ingress -n $NAMESPACE"
}

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
  info "Deploying agent-learn to GKE..."
  info "Project: $PROJECT_ID | Region: $REGION | Cluster: $CLUSTER_NAME"
  echo

  gcloud config set project "$PROJECT_ID" --quiet

  enable_apis
  create_registry
  create_cluster
  create_secrets
  sync_secrets
  build_and_push
  deploy
  print_status
}

main "$@"

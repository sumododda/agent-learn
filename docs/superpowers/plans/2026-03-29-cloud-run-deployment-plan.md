# Cloud Run Deployment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy agent-learn (FastAPI + Next.js + PostgreSQL) to GCP Cloud Run with GitHub Actions CI/CD, zero cold starts, and Cloudflare DNS.

**Architecture:** Three Cloud Run services (frontend, backend-api, backend-worker) backed by Cloud SQL PostgreSQL. Frontend proxies `/api/*` to backend via Next.js rewrites. Cloudflare DNS points to frontend's `*.run.app` URL. GitHub Actions builds images to ghcr.io and deploys via Workload Identity Federation (keyless).

**Tech Stack:** GCP Cloud Run, Cloud SQL, Secret Manager, Workload Identity Federation, ghcr.io, GitHub Actions, Cloudflare DNS

**Estimated monthly cost:** ~$35-45 (Cloud SQL micro ~$8, three Cloud Run min-instances=1 ~$25-30, no load balancer)

---

## File Structure

```
.github/
  workflows/
    deploy.yml                    # CI/CD: build ghcr.io images + deploy Cloud Run
deploy/
  cloudrun/
    deploy.sh                     # One-shot infra bootstrap script
backend/
  app/
    worker.py                     # MODIFY: add health check server for Cloud Run
```

---

### Task 1: GCP Project Setup & Enable APIs

**Files:** `deploy/cloudrun/deploy.sh` (will be created in Task 6)

- [ ] **Step 1: Set active project**

```bash
gcloud config set project agent-learn-491717
```

Expected: `Updated property [core/project].`

- [ ] **Step 2: Enable required APIs**

```bash
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project=agent-learn-491717
```

Expected: `Operation ... finished successfully.` (may take 30-60s)

- [ ] **Step 3: Set default region**

```bash
gcloud config set run/region us-central1
```

---

### Task 2: Create Cloud SQL Instance & Database

- [ ] **Step 1: Create Cloud SQL PostgreSQL instance**

```bash
gcloud sql instances create agentlearn-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=us-central1 \
  --storage-size=10GB \
  --storage-auto-increase \
  --availability-type=zonal \
  --backup-start-time=04:00 \
  --project=agent-learn-491717
```

Expected: Takes 3-5 minutes. Output shows `STATUS: RUNNABLE` when done.

- [ ] **Step 2: Set the postgres password**

```bash
gcloud sql users set-password postgres \
  --instance=agentlearn-db \
  --password=GENERATE_A_SECURE_PASSWORD \
  --project=agent-learn-491717
```

- [ ] **Step 3: Create the application database and user**

```bash
gcloud sql databases create agentlearn \
  --instance=agentlearn-db \
  --project=agent-learn-491717
```

```bash
gcloud sql users create agentlearn \
  --instance=agentlearn-db \
  --password=GENERATE_A_SECURE_PASSWORD \
  --project=agent-learn-491717
```

- [ ] **Step 4: Note the instance connection name**

```bash
gcloud sql instances describe agentlearn-db \
  --format='value(connectionName)' \
  --project=agent-learn-491717
```

Expected output: `agent-learn-491717:us-central1:agentlearn-db`

This value is used in all Cloud Run `--add-cloudsql-instances` flags.

---

### Task 3: Create Secrets in Secret Manager

Store each application secret. These will be mounted as env vars in Cloud Run.

- [ ] **Step 1: Generate and store secrets**

```bash
# Generate secure random values for JWT and encryption
JWT_SECRET=$(openssl rand -base64 48)
ENCRYPTION_PEPPER=$(openssl rand -base64 48)

# Store each secret (the user must provide real values for turnstile, resend, etc.)
echo -n "$JWT_SECRET" | gcloud secrets create jwt-secret-key --data-file=- --project=agent-learn-491717
echo -n "$ENCRYPTION_PEPPER" | gcloud secrets create encryption-pepper --data-file=- --project=agent-learn-491717
echo -n "YOUR_TURNSTILE_SECRET" | gcloud secrets create turnstile-secret-key --data-file=- --project=agent-learn-491717
echo -n "YOUR_RESEND_API_KEY" | gcloud secrets create resend-api-key --data-file=- --project=agent-learn-491717
echo -n "YOUR_RESEND_FROM_EMAIL" | gcloud secrets create resend-from-email --data-file=- --project=agent-learn-491717
echo -n "THE_DB_PASSWORD_FROM_TASK_2" | gcloud secrets create db-password --data-file=- --project=agent-learn-491717
```

- [ ] **Step 2: Verify secrets were created**

```bash
gcloud secrets list --project=agent-learn-491717
```

Expected: 6 secrets listed.

---

### Task 4: Workload Identity Federation for GitHub Actions

This allows GitHub Actions to deploy to GCP without storing service account keys.

- [ ] **Step 1: Create a service account for GitHub Actions**

```bash
gcloud iam service-accounts create github-actions-deploy \
  --display-name="GitHub Actions Deploy" \
  --project=agent-learn-491717
```

- [ ] **Step 2: Grant required roles to the service account**

```bash
PROJECT_ID=agent-learn-491717
SA_EMAIL=github-actions-deploy@${PROJECT_ID}.iam.gserviceaccount.com

# Cloud Run Admin (deploy services)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/run.admin"

# Act as the Cloud Run runtime service account
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/iam.serviceAccountUser"

# Cloud SQL Client (for migrations job)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/cloudsql.client"

# Secret Manager accessor (read secrets during deploy)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor"
```

- [ ] **Step 3: Create Workload Identity Pool**

```bash
gcloud iam workload-identity-pools create github-pool \
  --location="global" \
  --display-name="GitHub Actions Pool" \
  --project=agent-learn-491717
```

- [ ] **Step 4: Create OIDC Provider for GitHub**

```bash
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --project=agent-learn-491717
```

- [ ] **Step 5: Bind the service account to the GitHub repo**

```bash
PROJECT_NUM=$(gcloud projects describe agent-learn-491717 --format='value(projectNumber)')

gcloud iam service-accounts add-iam-policy-binding \
  github-actions-deploy@agent-learn-491717.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/github-pool/attribute.repository/sumododda/agent-learn" \
  --project=agent-learn-491717
```

- [ ] **Step 6: Note the Workload Identity Provider resource name**

```bash
echo "projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
```

Save this value for the GitHub Actions workflow.

---

### Task 5: Grant Cloud Run Default Service Account Secret Access

The Cloud Run services run as the default Compute service account. It needs access to secrets and Cloud SQL.

- [ ] **Step 1: Grant roles to default compute SA**

```bash
PROJECT_NUM=$(gcloud projects describe agent-learn-491717 --format='value(projectNumber)')
COMPUTE_SA="${PROJECT_NUM}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding agent-learn-491717 \
  --member="serviceAccount:$COMPUTE_SA" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding agent-learn-491717 \
  --member="serviceAccount:$COMPUTE_SA" \
  --role="roles/cloudsql.client"
```

---

### Task 6: Add Worker Health Check Endpoint

Cloud Run requires containers to respond to HTTP health checks. The worker currently has no HTTP server.

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_worker_health.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_worker_health.py`:

```python
"""Test the worker health check HTTP server."""

import asyncio
import aiohttp
import pytest
from app.worker import start_health_server


@pytest.mark.asyncio
async def test_health_server_responds_200():
    """Health server should return 200 OK on GET /."""
    server, runner = await start_health_server(port=9999)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:9999/") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"
    finally:
        await runner.cleanup()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_worker_health.py -v
```

Expected: `FAILED` — `ImportError: cannot import name 'start_health_server'`

- [ ] **Step 3: Install aiohttp (needed for lightweight health server)**

Add `aiohttp` to `requirements.txt` if not already present:

```
aiohttp>=3.9.0
```

- [ ] **Step 4: Implement the health server in worker.py**

Add to `backend/app/worker.py`, before the `run_worker` function:

```python
import os
from aiohttp import web

async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})

async def start_health_server(port: int | None = None) -> tuple[web.AppRunner, web.TCPSite]:
    """Start a minimal HTTP health server for Cloud Run probes."""
    app = web.Application()
    app.router.add_get("/", _health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = port or int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health server listening on port %d", port)
    return runner, site
```

Update the `run_worker` function to start the health server:

```python
async def run_worker() -> None:
    """Main worker loop: claim jobs, process them, handle shutdown."""
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    shutdown_event = asyncio.Event()

    # Start health check server for Cloud Run
    health_runner, _health_site = await start_health_server()

    loop = asyncio.get_running_loop()
    # ... rest of function unchanged ...

    # Cleanup health server on shutdown
    logger.info("Worker %s shutting down", worker_id)
    await health_runner.cleanup()
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_worker_health.py -v
```

Expected: `PASSED`

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
cd backend && python -m pytest --tb=short -q
```

Expected: All existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/worker.py backend/tests/test_worker_health.py backend/requirements.txt
git commit -m "feat: add health check HTTP server to worker for Cloud Run"
```

---

### Task 7: Create GitHub Actions Deploy Workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

env:
  PROJECT_ID: agent-learn-491717
  REGION: us-central1
  # Cloud SQL instance connection name
  CLOUD_SQL_INSTANCE: agent-learn-491717:us-central1:agentlearn-db

permissions:
  contents: read
  packages: write
  id-token: write  # Required for Workload Identity Federation

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      # ── Build & push images to ghcr.io ──────────────────────────
      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build & push backend image
        uses: docker/build-push-action@v6
        with:
          context: ./backend
          file: ./deploy/Dockerfile.backend
          push: true
          tags: ghcr.io/${{ github.repository }}/backend:${{ github.sha }}

      - name: Build & push frontend image
        uses: docker/build-push-action@v6
        with:
          context: ./frontend
          file: ./deploy/Dockerfile.frontend
          push: true
          tags: ghcr.io/${{ github.repository }}/frontend:${{ github.sha }}
          build-args: |
            NEXT_PUBLIC_TURNSTILE_SITE_KEY=${{ vars.NEXT_PUBLIC_TURNSTILE_SITE_KEY }}

      # ── Authenticate to GCP via Workload Identity ───────────────
      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: github-actions-deploy@${{ env.PROJECT_ID }}.iam.gserviceaccount.com

      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v2

      # ── Deploy Cloud Run services ───────────────────────────────
      - name: Deploy backend-api
        uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: agentlearn-backend
          image: ghcr.io/${{ github.repository }}/backend:${{ github.sha }}
          region: ${{ env.REGION }}
          flags: >-
            --add-cloudsql-instances=${{ env.CLOUD_SQL_INSTANCE }}
            --min-instances=1
            --max-instances=3
            --memory=512Mi
            --cpu=1
            --port=8000
            --allow-unauthenticated
            --set-secrets=JWT_SECRET_KEY=jwt-secret-key:latest,ENCRYPTION_PEPPER=encryption-pepper:latest,TURNSTILE_SECRET_KEY=turnstile-secret-key:latest,RESEND_API_KEY=resend-api-key:latest,RESEND_FROM_EMAIL=resend-from-email:latest,POSTGRES_PASSWORD=db-password:latest
          env_vars: |
            DATABASE_URL=postgresql+asyncpg://agentlearn:PLACEHOLDER@/agentlearn?host=/cloudsql/agent-learn-491717:us-central1:agentlearn-db
            CORS_ORIGINS=https://learn.blekcipher.com
            DOCS_ENABLED=false

      - name: Deploy backend-worker
        uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: agentlearn-worker
          image: ghcr.io/${{ github.repository }}/backend:${{ github.sha }}
          region: ${{ env.REGION }}
          flags: >-
            --add-cloudsql-instances=${{ env.CLOUD_SQL_INSTANCE }}
            --min-instances=1
            --max-instances=1
            --memory=512Mi
            --cpu=1
            --no-cpu-throttling
            --port=8080
            --command=python,-m,app.worker
            --allow-unauthenticated
            --set-secrets=JWT_SECRET_KEY=jwt-secret-key:latest,ENCRYPTION_PEPPER=encryption-pepper:latest,TURNSTILE_SECRET_KEY=turnstile-secret-key:latest,RESEND_API_KEY=resend-api-key:latest,RESEND_FROM_EMAIL=resend-from-email:latest,POSTGRES_PASSWORD=db-password:latest
          env_vars: |
            DATABASE_URL=postgresql+asyncpg://agentlearn:PLACEHOLDER@/agentlearn?host=/cloudsql/agent-learn-491717:us-central1:agentlearn-db
            CORS_ORIGINS=https://learn.blekcipher.com

      - name: Deploy frontend
        uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: agentlearn-frontend
          image: ghcr.io/${{ github.repository }}/frontend:${{ github.sha }}
          region: ${{ env.REGION }}
          flags: >-
            --min-instances=1
            --max-instances=3
            --memory=256Mi
            --cpu=1
            --port=3000
            --allow-unauthenticated
          env_vars: |
            INTERNAL_API_URL=https://agentlearn-backend-866219667830.us-central1.run.app

      # ── Run database migrations ─────────────────────────────────
      - name: Run Alembic migrations
        run: |
          gcloud run jobs create agentlearn-migrate \
            --image=ghcr.io/${{ github.repository }}/backend:${{ github.sha }} \
            --region=${{ env.REGION }} \
            --add-cloudsql-instances=${{ env.CLOUD_SQL_INSTANCE }} \
            --set-secrets=POSTGRES_PASSWORD=db-password:latest \
            --set-env-vars="DATABASE_URL=postgresql+asyncpg://agentlearn:PLACEHOLDER@/agentlearn?host=/cloudsql/agent-learn-491717:us-central1:agentlearn-db" \
            --command=alembic,upgrade,head \
            --max-retries=2 \
            --project=${{ env.PROJECT_ID }} \
            2>/dev/null || true
          gcloud run jobs update agentlearn-migrate \
            --image=ghcr.io/${{ github.repository }}/backend:${{ github.sha }} \
            --region=${{ env.REGION }} \
            --add-cloudsql-instances=${{ env.CLOUD_SQL_INSTANCE }} \
            --set-secrets=POSTGRES_PASSWORD=db-password:latest \
            --set-env-vars="DATABASE_URL=postgresql+asyncpg://agentlearn:PLACEHOLDER@/agentlearn?host=/cloudsql/agent-learn-491717:us-central1:agentlearn-db" \
            --command=alembic,upgrade,head \
            --max-retries=2 \
            --project=${{ env.PROJECT_ID }}
          gcloud run jobs execute agentlearn-migrate \
            --region=${{ env.REGION }} \
            --wait \
            --project=${{ env.PROJECT_ID }}
```

**Note on DATABASE_URL:** The `PLACEHOLDER` for the password in the DATABASE_URL env var is a known Cloud Run limitation with `--set-secrets` for inline env vars. The actual approach is to construct the URL at runtime. See Task 8 for the entrypoint wrapper that resolves this.

- [ ] **Step 2: Commit**

```bash
mkdir -p .github/workflows
git add .github/workflows/deploy.yml
git commit -m "ci: add GitHub Actions Cloud Run deploy workflow"
```

---

### Task 8: Add Cloud Run Entrypoint Wrapper for DATABASE_URL

Cloud Run mounts secrets as env vars, but DATABASE_URL needs the password interpolated. We solve this with a small entrypoint script.

**Files:**
- Create: `deploy/cloudrun/entrypoint.sh`
- Modify: `deploy/Dockerfile.backend`

- [ ] **Step 1: Create the entrypoint script**

Create `deploy/cloudrun/entrypoint.sh`:

```bash
#!/bin/sh
# Construct DATABASE_URL from components at runtime
# POSTGRES_PASSWORD is injected by Cloud Run from Secret Manager
export DATABASE_URL="postgresql+asyncpg://agentlearn:${POSTGRES_PASSWORD}@/agentlearn?host=/cloudsql/${CLOUD_SQL_INSTANCE}"
exec "$@"
```

- [ ] **Step 2: Update Dockerfile.backend to include the entrypoint**

Update `deploy/Dockerfile.backend`:

```dockerfile
# agent-learn backend
FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/

# Cloud Run entrypoint — constructs DATABASE_URL from secrets at runtime
COPY ../deploy/cloudrun/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Wait — the Dockerfile context is `./backend`, so we can't COPY from `../deploy/`. We need to either:
(a) Move entrypoint.sh into `backend/`
(b) Change the Docker build context

Simpler: put the entrypoint in `backend/entrypoint.sh`.

Updated plan — create `backend/entrypoint.sh`:

```bash
#!/bin/sh
# Construct DATABASE_URL from secrets injected by Cloud Run
if [ -n "$POSTGRES_PASSWORD" ] && [ -n "$CLOUD_SQL_INSTANCE" ]; then
  export DATABASE_URL="postgresql+asyncpg://agentlearn:${POSTGRES_PASSWORD}@/agentlearn?host=/cloudsql/${CLOUD_SQL_INSTANCE}"
fi
exec "$@"
```

Updated `deploy/Dockerfile.backend`:

```dockerfile
# agent-learn backend
FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Update GitHub Actions workflow**

Remove `DATABASE_URL` from the `env_vars` in the deploy steps. Instead, add `CLOUD_SQL_INSTANCE` as an env var:

For backend-api and backend-worker deploy steps, change `env_vars` to:

```yaml
env_vars: |
  CLOUD_SQL_INSTANCE=agent-learn-491717:us-central1:agentlearn-db
  CORS_ORIGINS=https://learn.blekcipher.com
  DOCS_ENABLED=false
```

For the migrations job, same approach — the entrypoint constructs DATABASE_URL.

- [ ] **Step 4: Commit**

```bash
git add backend/entrypoint.sh deploy/Dockerfile.backend .github/workflows/deploy.yml
git commit -m "feat: add entrypoint script to construct DATABASE_URL from Cloud Run secrets"
```

---

### Task 9: Configure GitHub Repository Variables

These are needed by the GitHub Actions workflow. Set them via GitHub CLI.

- [ ] **Step 1: Set repository variables**

```bash
# The Workload Identity Provider full resource name (from Task 4, Step 6)
gh variable set GCP_WIF_PROVIDER \
  --body "projects/PROJECT_NUM/locations/global/workloadIdentityPools/github-pool/providers/github-provider"

# Turnstile site key (public, not a secret)
gh variable set NEXT_PUBLIC_TURNSTILE_SITE_KEY \
  --body "YOUR_TURNSTILE_SITE_KEY"
```

- [ ] **Step 2: Verify variables**

```bash
gh variable list
```

Expected: `GCP_WIF_PROVIDER` and `NEXT_PUBLIC_TURNSTILE_SITE_KEY` listed.

---

### Task 10: First Deploy & Verify

- [ ] **Step 1: Push to main to trigger the workflow**

```bash
git push origin main
```

- [ ] **Step 2: Watch the deploy**

```bash
gh run watch
```

Expected: All steps green. Backend, worker, and frontend deployed.

- [ ] **Step 3: Get the frontend Cloud Run URL**

```bash
gcloud run services describe agentlearn-frontend \
  --region=us-central1 \
  --format='value(status.url)' \
  --project=agent-learn-491717
```

Expected: `https://agentlearn-frontend-HASH-uc.a.run.app`

- [ ] **Step 4: Smoke test the backend through the frontend proxy**

```bash
FRONTEND_URL=$(gcloud run services describe agentlearn-frontend --region=us-central1 --format='value(status.url)' --project=agent-learn-491717)
curl -s "$FRONTEND_URL/api/health"
```

Expected: `{"status":"ok"}`

- [ ] **Step 5: Smoke test the frontend**

Open the frontend URL in a browser. The login/signup page should render.

---

### Task 11: Configure Cloudflare DNS

- [ ] **Step 1: Get the frontend Cloud Run URL**

From Task 10 Step 3, you have the URL like `https://agentlearn-frontend-HASH-uc.a.run.app`.

- [ ] **Step 2: Add CNAME record in Cloudflare**

In the Cloudflare dashboard for `blekcipher.com`:

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | `learn` | `agentlearn-frontend-HASH-uc.a.run.app` | Orange (Proxied) |

- [ ] **Step 3: Set Cloudflare SSL to Full**

In Cloudflare dashboard → SSL/TLS → Overview → set mode to **Full**.

(Not "Full Strict" — Cloud Run's cert is for `*.run.app`, not `learn.blekcipher.com`. Cloudflare "Full" encrypts the connection but doesn't verify the hostname match.)

- [ ] **Step 4: Verify the domain**

```bash
curl -s https://learn.blekcipher.com/api/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 5: Update CORS_ORIGINS if needed**

The backend already has `CORS_ORIGINS=https://learn.blekcipher.com` set in the workflow. Verify this works by testing the full app flow (signup/login/create course).

---

### Task 12: Update Frontend INTERNAL_API_URL After First Deploy

After the first deploy, we know the backend's actual Cloud Run URL. Update the frontend's env var and the GitHub Actions workflow.

- [ ] **Step 1: Get the backend Cloud Run URL**

```bash
gcloud run services describe agentlearn-backend \
  --region=us-central1 \
  --format='value(status.url)' \
  --project=agent-learn-491717
```

Expected: `https://agentlearn-backend-HASH-uc.a.run.app`

- [ ] **Step 2: Update the frontend service env var**

```bash
BACKEND_URL=$(gcloud run services describe agentlearn-backend --region=us-central1 --format='value(status.url)' --project=agent-learn-491717)

gcloud run services update agentlearn-frontend \
  --region=us-central1 \
  --set-env-vars="INTERNAL_API_URL=$BACKEND_URL" \
  --project=agent-learn-491717
```

- [ ] **Step 3: Update GitHub Actions workflow with the actual backend URL**

In `.github/workflows/deploy.yml`, update the frontend deploy step's `env_vars`:

```yaml
env_vars: |
  INTERNAL_API_URL=https://agentlearn-backend-HASH-uc.a.run.app
```

Replace `HASH` with the actual value from Step 1.

- [ ] **Step 4: Commit and push**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: set frontend INTERNAL_API_URL to actual backend Cloud Run URL"
git push origin main
```

---

### Task 13: End-to-End Verification

- [ ] **Step 1: Verify health endpoint**

```bash
curl -s https://learn.blekcipher.com/api/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 2: Verify frontend renders**

Open `https://learn.blekcipher.com` in a browser. Login page should render with Turnstile widget.

- [ ] **Step 3: Test signup/login flow**

Create an account, verify email flow works (Resend), login.

- [ ] **Step 4: Test course creation**

Create a test course to verify the full pipeline:
- Backend API receives the request
- Worker picks up the job from the database
- Course generates successfully

- [ ] **Step 5: Verify Cloud Run services are healthy**

```bash
gcloud run services list --region=us-central1 --project=agent-learn-491717
```

Expected: 3 services, all with status "Ready" and min-instances=1.

# GKE Deployment Design — agent-learn

**Date:** 2026-03-22
**Domain:** learn.blekcipher.com (Cloudflare DNS)
**GCP Project:** agent-learn-490805

## Architecture

```
Cloudflare DNS (learn.blekcipher.com)
  │  HTTPS termination (orange cloud proxy)
  ▼
GKE Ingress (HTTP)
  │
  ├─► frontend Service → frontend Deployment (Next.js standalone, 1 replica)
  │
  └─► backend-api Service → backend-api Deployment (FastAPI + uvicorn, 1 replica, HPA to 3)
                                │
                                ├── backend-worker Deployment (same image, runs app.worker, 1 replica)
                                │
                                └── postgres StatefulSet (PostgreSQL 16, 1 replica, 10Gi PVC)
```

## Infrastructure

| Component | Choice |
|-----------|--------|
| Cluster | GKE Autopilot, us-central1 |
| Registry | Artifact Registry (`us-central1-docker.pkg.dev/agent-learn-490805/agent-learn`) |
| Secrets | GCP Secret Manager → GKE Secret Store CSI driver |
| TLS | Cloudflare proxy terminates HTTPS; GKE serves HTTP |
| DNS | CNAME `learn.blekcipher.com` → GKE Ingress external IP |
| Migrations | Kubernetes Job (runs `alembic upgrade head` before deploy) |

## Docker Images

### backend (single image, two entrypoints)

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# API entrypoint (default)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- backend-api runs with default CMD
- backend-worker overrides: `["python", "-m", "app.worker"]`
- migration job overrides: `["alembic", "upgrade", "head"]`

### frontend

```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_TURNSTILE_SITE_KEY
RUN npm run build

FROM node:22-alpine
WORKDIR /app
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
CMD ["node", "server.js"]
EXPOSE 3000
```

## Kubernetes Resources

### Namespace
- `agent-learn`

### Secrets (from GCP Secret Manager)
Synced via SecretProviderClass:
- `jwt-secret-key` (min 32 chars)
- `encryption-pepper` (min 32 chars)
- `turnstile-secret-key`
- `resend-api-key`
- `postgres-password`

### PostgreSQL (StatefulSet)
- Image: `postgres:16-alpine`
- 1 replica
- PVC: 10Gi SSD (`standard-rwo`)
- Environment: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
- Service: `postgres` (ClusterIP, port 5432)
- No backups (accepted trade-off)

### backend-api (Deployment)
- 1 replica, HPA min=1 max=3 (CPU target 70%)
- Port 8000
- Readiness probe: `GET /api/health`
- Liveness probe: `GET /api/health`
- Resources: 256Mi request, 512Mi limit, 250m CPU request
- Environment:
  - `DATABASE_URL=postgresql+asyncpg://agentlearn:$(POSTGRES_PASSWORD)@postgres:5432/agentlearn`
  - `CORS_ORIGINS=https://learn.blekcipher.com`
  - `DOCS_ENABLED=false`
  - Secrets mounted from Secret Manager

### backend-worker (Deployment)
- 1 replica (no HPA — single worker)
- Same image as backend-api
- Command override: `["python", "-m", "app.worker"]`
- No service (not externally accessible)
- Same env vars and secrets as backend-api
- Liveness: exec `python -c "print('ok')"` (process-level)
- Resources: 256Mi request, 512Mi limit, 250m CPU

### frontend (Deployment)
- 1 replica
- Port 3000
- Readiness probe: `GET /`
- Build args baked in: `NEXT_PUBLIC_API_URL=https://learn.blekcipher.com`, `NEXT_PUBLIC_TURNSTILE_SITE_KEY`
- Resources: 128Mi request, 256Mi limit, 100m CPU

### Migration Job
- Runs before deploy (manual or CI trigger)
- Same backend image
- Command: `["alembic", "upgrade", "head"]`
- `restartPolicy: Never`, `backoffLimit: 3`
- Same DB env vars

### Ingress
- GKE managed Ingress (GCE class)
- HTTP only (Cloudflare handles TLS)
- Path routing:
  - `/api/*` → backend-api:8000
  - `/*` → frontend:3000

### NetworkPolicy
- postgres: only accepts from backend-api and backend-worker
- backend-worker: no ingress, egress to postgres + internet
- backend-api: ingress from Ingress controller, egress to postgres + internet
- frontend: ingress from Ingress controller, egress to backend-api

## Deployment Order

1. Enable GCP APIs (container, artifactregistry, secretmanager)
2. Create Artifact Registry repository
3. Create GKE Autopilot cluster
4. Install Secret Store CSI driver + GCP provider
5. Create secrets in Secret Manager
6. Build and push Docker images
7. Apply k8s namespace + secrets
8. Apply postgres StatefulSet + Service
9. Run migration Job
10. Apply backend-api Deployment + Service + HPA
11. Apply backend-worker Deployment
12. Apply frontend Deployment + Service
13. Apply Ingress
14. Get Ingress external IP → set Cloudflare CNAME

## Files to Create

```
deploy/
├── Dockerfile.backend
├── Dockerfile.frontend
├── k8s/
│   ├── namespace.yaml
│   ├── secret-provider.yaml
│   ├── postgres.yaml          (StatefulSet + Service + PVC)
│   ├── migration-job.yaml
│   ├── backend-api.yaml       (Deployment + Service + HPA)
│   ├── backend-worker.yaml    (Deployment)
│   ├── frontend.yaml          (Deployment + Service)
│   ├── ingress.yaml
│   └── network-policies.yaml
└── deploy.sh                  (end-to-end deploy script)
```

## Estimated Cost
- GKE Autopilot pods: ~$15-25/month
- Secret Manager: ~$0.50/month
- Artifact Registry: ~$0.50/month
- Ingress static IP: free (ephemeral)
- **Total: ~$16-26/month**

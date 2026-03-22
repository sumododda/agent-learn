# GKE Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy agent-learn to GKE Autopilot with PostgreSQL, Secret Manager, and Cloudflare DNS at learn.blekcipher.com

**Architecture:** GKE Autopilot cluster running 4 workloads (frontend, backend-api, backend-worker, postgres) with GCP Secret Manager for secrets, Artifact Registry for images, and Cloudflare for DNS/TLS termination.

**Tech Stack:** GKE Autopilot, Artifact Registry, Secret Manager, PostgreSQL 16, Docker, Kubernetes

---

## File Structure

```
deploy/
├── Dockerfile.backend
├── Dockerfile.frontend
├── k8s/
│   ├── namespace.yaml
│   ├── secret-provider.yaml
│   ├── postgres.yaml
│   ├── migration-job.yaml
│   ├── backend-api.yaml
│   ├── backend-worker.yaml
│   ├── frontend.yaml
│   ├── ingress.yaml
│   └── network-policies.yaml
└── deploy.sh
```

Also modified:
- `.gitignore` — add deploy safety entries
- `README.md` — rewrite to reflect actual codebase
- `frontend/next.config.ts` — production CSP for learn.blekcipher.com

---

### Task 1: Harden .gitignore and update README

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `frontend/next.config.ts`

- [ ] **Step 1: Update .gitignore**
  Add: `frontend/.env.local`, `frontend/.env.production.local`, `*.env.local`, `deploy/*.log`

- [ ] **Step 2: Rewrite README.md**
  Replace the existing design-doc README with a concise project README that reflects the actual implementation: tech stack (FastAPI + Next.js + PostgreSQL), features, setup instructions, and deployment notes. Remove references to Supabase, Clerk, Trigger.dev, LiteLLM, Deep Agents.

- [ ] **Step 3: Commit**
  `git commit -m "docs: update README and gitignore for deployment"`

---

### Task 2: Create Dockerfiles

**Files:**
- Create: `deploy/Dockerfile.backend`
- Create: `deploy/Dockerfile.frontend`
- Create: `backend/.dockerignore`
- Create: `frontend/.dockerignore`

- [ ] **Step 1: Create backend Dockerfile**
  Python 3.13-slim, copy requirements.txt, pip install, copy app code. CMD uvicorn. Expose 8000.

- [ ] **Step 2: Create frontend Dockerfile**
  Multi-stage: node:22-alpine builder (npm ci, npm run build with Next.js standalone output), then slim runner copying .next/standalone + .next/static + public. CMD node server.js. Expose 3000. Accept NEXT_PUBLIC_* as build args.

- [ ] **Step 3: Create .dockerignore files**
  Exclude .venv, __pycache__, node_modules, .env, tests, .git, etc.

- [ ] **Step 4: Verify frontend needs standalone output**
  Check next.config.ts for `output: 'standalone'` — add if missing.

- [ ] **Step 5: Test Docker builds locally**
  ```bash
  docker build -f deploy/Dockerfile.backend -t agent-learn-backend ./backend
  docker build -f deploy/Dockerfile.frontend -t agent-learn-frontend ./frontend
  ```

- [ ] **Step 6: Commit**
  `git commit -m "deploy: add Dockerfiles for backend and frontend"`

---

### Task 3: Create Kubernetes manifests

**Files:**
- Create: `deploy/k8s/namespace.yaml`
- Create: `deploy/k8s/postgres.yaml`
- Create: `deploy/k8s/backend-api.yaml`
- Create: `deploy/k8s/backend-worker.yaml`
- Create: `deploy/k8s/frontend.yaml`
- Create: `deploy/k8s/ingress.yaml`
- Create: `deploy/k8s/network-policies.yaml`

- [ ] **Step 1: namespace.yaml**
  Create `agent-learn` namespace.

- [ ] **Step 2: postgres.yaml**
  StatefulSet + Service + PVC. PostgreSQL 16-alpine, 10Gi SSD, ClusterIP service on 5432. Password from k8s Secret (populated from Secret Manager later).

- [ ] **Step 3: backend-api.yaml**
  Deployment + Service + HPA. 1 replica, HPA min=1 max=3 at 70% CPU. Port 8000. Readiness/liveness on /api/health. Environment from ConfigMap + Secrets. Resources: 256Mi/512Mi, 250m CPU.

- [ ] **Step 4: backend-worker.yaml**
  Deployment only (no Service). 1 replica. Same image, command override for `python -m app.worker`. Same env vars.

- [ ] **Step 5: frontend.yaml**
  Deployment + Service. 1 replica. Port 3000. Readiness on `/`. Resources: 128Mi/256Mi, 100m CPU.

- [ ] **Step 6: ingress.yaml**
  GCE Ingress class. Path rules: `/api/*` → backend-api:8000, `/*` → frontend:3000.

- [ ] **Step 7: network-policies.yaml**
  - postgres: ingress only from backend-api and backend-worker
  - backend-worker: no ingress, egress to postgres + internet
  - backend-api: ingress from ingress controller, egress to postgres + internet
  - frontend: ingress from ingress controller only

- [ ] **Step 8: Commit**
  `git commit -m "deploy: add Kubernetes manifests"`

---

### Task 4: Create deploy script

**Files:**
- Create: `deploy/deploy.sh`

- [ ] **Step 1: Write deploy.sh**
  End-to-end script that:
  1. Sets GCP project and region variables
  2. Enables required APIs
  3. Creates Artifact Registry repo (if not exists)
  4. Creates GKE Autopilot cluster (if not exists)
  5. Gets cluster credentials
  6. Installs Secret Store CSI driver (via gcloud addon)
  7. Creates secrets in GCP Secret Manager (interactive prompts)
  8. Creates k8s namespace and syncs secrets to k8s
  9. Builds and pushes Docker images
  10. Applies k8s manifests in order (postgres → wait → migration job → backend → frontend → ingress)
  11. Prints the Ingress external IP for Cloudflare DNS setup

- [ ] **Step 2: Make executable**
  `chmod +x deploy/deploy.sh`

- [ ] **Step 3: Commit**
  `git commit -m "deploy: add end-to-end deployment script"`

---

### Task 5: Commit everything, update remote, push

- [ ] **Step 1: Squash all uncommitted work into a single commit**
  Stage all new and modified files. Commit with descriptive message.

- [ ] **Step 2: Verify nothing sensitive is staged**
  Check `git diff --cached` for API keys, passwords, secrets.

- [ ] **Step 3: Push to remote and make public**
  ```bash
  git push origin main
  ```
  Then set repo visibility to public via `gh repo edit --visibility public`.

---

### Task 6: Run deployment to GKE

- [ ] **Step 1: Execute deploy.sh** (or run steps manually)
- [ ] **Step 2: Verify all pods are running** (`kubectl get pods -n agent-learn`)
- [ ] **Step 3: Run migration job** (`kubectl apply -f deploy/k8s/migration-job.yaml`)
- [ ] **Step 4: Get Ingress IP** (`kubectl get ingress -n agent-learn`)
- [ ] **Step 5: Set Cloudflare CNAME** `learn.blekcipher.com` → Ingress IP
- [ ] **Step 6: Verify end-to-end** — visit https://learn.blekcipher.com

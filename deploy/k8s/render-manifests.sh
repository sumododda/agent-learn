#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${1:-${SCRIPT_DIR}/.rendered}"
BACKEND_IMAGE="${BACKEND_IMAGE:-ghcr.io/sumododda/agent-learn/backend:latest}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-ghcr.io/sumododda/agent-learn/frontend:latest}"

rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"

copy_manifest() {
  cp "${SCRIPT_DIR}/$1" "${OUTPUT_DIR}/$1"
}

render_manifest() {
  local input_file="$1"
  local output_file="$2"
  local search="$3"
  local replacement="$4"

  sed "s|${search}|${replacement}|g" "${SCRIPT_DIR}/${input_file}" > "${OUTPUT_DIR}/${output_file}"
}

copy_manifest "namespace.yaml"
copy_manifest "postgres.yaml"
copy_manifest "backend-config.yaml"
copy_manifest "frontend-config.yaml"
copy_manifest "managed-cert.yaml"
copy_manifest "network-policies.yaml"
copy_manifest "ingress.yaml"

render_manifest "backend-api.yaml" "backend-api.yaml" "ghcr.io/sumododda/agent-learn/backend:latest" "${BACKEND_IMAGE}"
render_manifest "backend-worker.yaml" "backend-worker.yaml" "ghcr.io/sumododda/agent-learn/backend:latest" "${BACKEND_IMAGE}"
render_manifest "migration-job.yaml" "migration-job.yaml" "ghcr.io/sumododda/agent-learn/backend:latest" "${BACKEND_IMAGE}"
render_manifest "frontend.yaml" "frontend.yaml" "ghcr.io/sumododda/agent-learn/frontend:latest" "${FRONTEND_IMAGE}"

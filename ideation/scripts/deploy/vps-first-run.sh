#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/ideation-rag}"

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required but was not found." >&2
  exit 1
fi

cd "$PROJECT_DIR/infrastructure"

if [ ! -f .env ]; then
  cp .env.prod.example .env
  echo "Created $PROJECT_DIR/infrastructure/.env"
  echo "Edit secrets and Dify keys before running docker compose." >&2
  exit 1
fi

docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps

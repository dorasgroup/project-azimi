#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/ideation-rag}"
PACKAGE_PATH="${PACKAGE_PATH:-/tmp/ideation-rag-vps.tar}"
IMAGES_PATH="${IMAGES_PATH:-/tmp/ideation-rag-docker-images.tar}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker first, then run this script again." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not available. Install docker compose plugin first." >&2
  exit 1
fi

if [ ! -f "$PACKAGE_PATH" ]; then
  echo "Project package not found: $PACKAGE_PATH" >&2
  exit 1
fi

if [ ! -f "$IMAGES_PATH" ]; then
  echo "Docker images tar not found: $IMAGES_PATH" >&2
  exit 1
fi

mkdir -p "$PROJECT_DIR"
rm -rf "$PROJECT_DIR"/*

case "$PACKAGE_PATH" in
  *.zip)
    if ! command -v unzip >/dev/null 2>&1; then
      echo "unzip is not installed. Use ideation-rag-vps.tar instead of zip." >&2
      exit 1
    fi
    unzip -o "$PACKAGE_PATH" -d "$PROJECT_DIR"
    ;;
  *.tar)
    tar -xf "$PACKAGE_PATH" -C "$PROJECT_DIR"
    ;;
  *)
    echo "Unsupported package format: $PACKAGE_PATH" >&2
    exit 1
    ;;
esac

cd "$PROJECT_DIR/infrastructure"

if [ ! -f .env ]; then
  cp .env.prod.example .env
  echo "Created $PROJECT_DIR/infrastructure/.env"
  echo "Edit .env before running containers." >&2
fi

docker load -i "$IMAGES_PATH"

docker compose -f docker-compose.offline.yml up -d
docker compose -f docker-compose.offline.yml ps

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$DifyDockerRoot = "D:\Dify\docker"
$DistRoot = Join-Path $ProjectRoot "dist\dify-full-migration"
$DifyPackageTar = Join-Path $DistRoot "dify-full-docker-with-data.tar"
$DifyImagesTar = Join-Path $DistRoot "dify-docker-images.tar"
$VpsScript = Join-Path $DistRoot "vps-load-dify-full-migration.sh"

if (-not (Test-Path -LiteralPath $DifyDockerRoot)) {
    throw "Dify docker directory not found: $DifyDockerRoot"
}

New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null

if (Test-Path -LiteralPath $DifyPackageTar) {
    Remove-Item -LiteralPath $DifyPackageTar -Force
}

Write-Host "Creating direct full Dify tar package from $DifyDockerRoot"
Write-Host "This includes volumes: Workspace, Knowledge, Studio, Chatflows, API keys, uploads, vectors."
tar -cf $DifyPackageTar -C $DifyDockerRoot .

$images = @(
    "busybox:latest",
    "langgenius/dify-api:1.14.1",
    "langgenius/dify-web:1.14.1",
    "postgres:15-alpine",
    "redis:6-alpine",
    "langgenius/dify-sandbox:0.2.15",
    "langgenius/dify-plugin-daemon:0.6.0-local",
    "ubuntu/squid:latest",
    "nginx:latest",
    "semitechnologies/weaviate:1.27.0"
)

Write-Host "Checking required Dify images..."
foreach ($image in $images) {
    $exists = docker image inspect $image 2>$null
    if (-not $exists) {
        Write-Host "Pulling missing image: $image"
        docker pull $image
    }
}

if (Test-Path -LiteralPath $DifyImagesTar) {
    Remove-Item -LiteralPath $DifyImagesTar -Force
}

Write-Host "Saving Dify Docker images. This can take a while..."
docker save -o $DifyImagesTar $images

@'
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/dify}"
PACKAGE_PATH="${PACKAGE_PATH:-/tmp/dify-full-docker-with-data.tar}"
IMAGES_PATH="${IMAGES_PATH:-/tmp/dify-docker-images.tar}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker first." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not available." >&2
  exit 1
fi

if [ ! -f "$PACKAGE_PATH" ]; then
  echo "Dify full package not found: $PACKAGE_PATH" >&2
  exit 1
fi

if [ ! -f "$IMAGES_PATH" ]; then
  echo "Dify images tar not found: $IMAGES_PATH" >&2
  exit 1
fi

mkdir -p "$PROJECT_DIR"

if [ -d "$PROJECT_DIR" ] && [ "$(find "$PROJECT_DIR" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)" -gt 0 ]; then
  BACKUP_DIR="${PROJECT_DIR}.backup.$(date +%Y%m%d-%H%M%S)"
  mv "$PROJECT_DIR" "$BACKUP_DIR"
  mkdir -p "$PROJECT_DIR"
  echo "Existing Dify backup created: $BACKUP_DIR"
fi

tar -xf "$PACKAGE_PATH" -C "$PROJECT_DIR"

cd "$PROJECT_DIR"

# Change exposed ports on VPS to avoid conflict with existing projects.
sed -i 's/^EXPOSE_NGINX_PORT=.*/EXPOSE_NGINX_PORT=19080/' .env
sed -i 's/^EXPOSE_NGINX_SSL_PORT=.*/EXPOSE_NGINX_SSL_PORT=19443/' .env
sed -i 's/^EXPOSE_PLUGIN_DEBUGGING_PORT=.*/EXPOSE_PLUGIN_DEBUGGING_PORT=15003/' .env
sed -i 's/^NGINX_CLIENT_MAX_BODY_SIZE=.*/NGINX_CLIENT_MAX_BODY_SIZE=500M/' .env

docker load -i "$IMAGES_PATH"

# Fix common bind-mount ownership issues after extracting Windows-created tar files on Linux.
mkdir -p volumes/db/data volumes/app/storage volumes/plugin_daemon volumes/redis/data volumes/weaviate
chown -R 70:70 volumes/db/data || true
chown -R 1001:1001 volumes/app/storage || true
chown -R 1001:1001 volumes/plugin_daemon || true
chown -R 999:999 volumes/redis/data || true
chmod -R u+rwX,g+rwX volumes || true

docker compose up -d
docker compose ps

echo ""
echo "Migrated local Dify should be available at:"
echo "http://5.10.249.198:19080"
'@ | Set-Content -LiteralPath $VpsScript -Encoding UTF8

Write-Host ""
Write-Host "Direct full Dify migration bundle is ready:"
Get-ChildItem -LiteralPath $DistRoot | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize

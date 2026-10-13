$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$DifyDockerRoot = "D:\Dify\docker"
$DistRoot = Join-Path $ProjectRoot "dist\dify-full-migration"
$StagingRoot = Join-Path $DistRoot "dify-docker"
$DifyPackageTar = Join-Path $DistRoot "dify-full-docker-with-data.tar"
$DifyImagesTar = Join-Path $DistRoot "dify-docker-images.tar"
$VpsScript = Join-Path $DistRoot "vps-load-dify-full-migration.sh"

if (-not (Test-Path -LiteralPath $DifyDockerRoot)) {
    throw "Dify docker directory not found: $DifyDockerRoot"
}

Write-Host "IMPORTANT:"
Write-Host "For a consistent migration, stop local Dify before running this script:"
Write-Host "  cd D:\Dify\docker"
Write-Host "  docker compose down"
Write-Host ""

New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null

if (Test-Path -LiteralPath $StagingRoot) {
    Remove-Item -LiteralPath $StagingRoot -Recurse -Force
}

Write-Host "Copying full Dify docker directory including volumes..."
robocopy $DifyDockerRoot $StagingRoot /E /XF *.log | Out-Null
if ($LASTEXITCODE -le 7) {
    $global:LASTEXITCODE = 0
} else {
    throw "Robocopy failed with exit code $LASTEXITCODE"
}

$envPath = Join-Path $StagingRoot ".env"
$envContent = Get-Content -LiteralPath $envPath
$envContent = $envContent -replace "^EXPOSE_NGINX_PORT=.*", "EXPOSE_NGINX_PORT=19080"
$envContent = $envContent -replace "^EXPOSE_NGINX_SSL_PORT=.*", "EXPOSE_NGINX_SSL_PORT=19443"
$envContent = $envContent -replace "^EXPOSE_PLUGIN_DEBUGGING_PORT=.*", "EXPOSE_PLUGIN_DEBUGGING_PORT=15003"
$envContent = $envContent -replace "^NGINX_CLIENT_MAX_BODY_SIZE=.*", "NGINX_CLIENT_MAX_BODY_SIZE=500M"
$envContent | Set-Content -LiteralPath $envPath -Encoding UTF8

if (Test-Path -LiteralPath $DifyPackageTar) {
    Remove-Item -LiteralPath $DifyPackageTar -Force
}

Write-Host "Creating full Dify tar package with volumes..."
tar -cf $DifyPackageTar -C $StagingRoot .

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
  echo "Existing $PROJECT_DIR will be backed up."
  BACKUP_DIR="${PROJECT_DIR}.backup.$(date +%Y%m%d-%H%M%S)"
  mv "$PROJECT_DIR" "$BACKUP_DIR"
  mkdir -p "$PROJECT_DIR"
  echo "Backup created: $BACKUP_DIR"
fi

tar -xf "$PACKAGE_PATH" -C "$PROJECT_DIR"

docker load -i "$IMAGES_PATH"

cd "$PROJECT_DIR"

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
Write-Host "Full Dify migration bundle is ready:"
Get-ChildItem -LiteralPath $DistRoot | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize

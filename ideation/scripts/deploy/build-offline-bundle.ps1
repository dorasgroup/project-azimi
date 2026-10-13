$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$DistRoot = Join-Path $ProjectRoot "dist"
$OfflineRoot = Join-Path $DistRoot "ideation-rag-offline"
$ProjectZip = Join-Path $OfflineRoot "ideation-rag-vps.zip"
$ProjectTar = Join-Path $OfflineRoot "ideation-rag-vps.tar"
$ImagesTar = Join-Path $OfflineRoot "ideation-rag-docker-images.tar"

New-Item -ItemType Directory -Path $OfflineRoot -Force | Out-Null

Write-Host "Building deployment project package..."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "package-vps.ps1")
Copy-Item -LiteralPath (Join-Path $DistRoot "ideation-rag-vps.zip") -Destination $ProjectZip -Force

if (Test-Path -LiteralPath $ProjectTar) {
    Remove-Item -LiteralPath $ProjectTar -Force
}

Write-Host "Creating tar package for Linux servers without unzip..."
tar -cf $ProjectTar -C (Join-Path $DistRoot "ideation-rag-vps") .

Write-Host "Building backend image..."
docker build `
  -t ideation-rag-backend:offline `
  -f (Join-Path $ProjectRoot "backend\Dockerfile") `
  (Join-Path $ProjectRoot "backend")

Write-Host "Building frontend image..."
docker build `
  -t ideation-rag-frontend:offline `
  --build-arg NEXT_PUBLIC_API_BASE_URL=/api/v1 `
  -f (Join-Path $ProjectRoot "frontend\Dockerfile") `
  (Join-Path $ProjectRoot "frontend")

Write-Host "Pulling runtime images..."
docker pull postgres:15-alpine
docker pull nginx:1.27-alpine

if (Test-Path -LiteralPath $ImagesTar) {
    Remove-Item -LiteralPath $ImagesTar -Force
}

Write-Host "Saving Docker images to tar..."
docker save `
  -o $ImagesTar `
  ideation-rag-backend:offline `
  ideation-rag-frontend:offline `
  postgres:15-alpine `
  nginx:1.27-alpine

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "vps-load-offline.sh") -Destination (Join-Path $OfflineRoot "vps-load-offline.sh") -Force

Write-Host ""
Write-Host "Offline bundle is ready:"
Write-Host $OfflineRoot
Write-Host ""
Write-Host "Files:"
Get-ChildItem -LiteralPath $OfflineRoot | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize

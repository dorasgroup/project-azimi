$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$DistRoot = Join-Path $ProjectRoot "dist"
$PackageRoot = Join-Path $DistRoot "ideation-rag-vps"
$ArchivePath = Join-Path $DistRoot "ideation-rag-vps.zip"

if (Test-Path -LiteralPath $PackageRoot) {
    Remove-Item -LiteralPath $PackageRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $PackageRoot | Out-Null

$items = @(
    "backend",
    "frontend",
    "infrastructure",
    "nginx",
    "scripts",
    "docs",
    "Font",
    ".gitignore"
)

foreach ($item in $items) {
    $source = Join-Path $ProjectRoot $item
    if (-not (Test-Path -LiteralPath $source)) {
        continue
    }

    $destination = Join-Path $PackageRoot $item
    if ((Get-Item -LiteralPath $source).PSIsContainer) {
        robocopy $source $destination /E /XD node_modules .next __pycache__ .pytest_cache .ruff_cache .mypy_cache .venv dist tmp /XF .env .env.local *.log *.pyc *.pyo | Out-Null
        if ($LASTEXITCODE -le 7) {
            $global:LASTEXITCODE = 0
        } else {
            throw "Robocopy failed for $item with exit code $LASTEXITCODE"
        }
    } else {
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

New-Item -ItemType Directory -Path (Join-Path $PackageRoot "storage\uploads") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PackageRoot "storage\logs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PackageRoot "storage\tmp") -Force | Out-Null

Copy-Item `
    -LiteralPath (Join-Path $PackageRoot "infrastructure\.env.prod.example") `
    -Destination (Join-Path $PackageRoot "infrastructure\.env") `
    -Force

if (Test-Path -LiteralPath $ArchivePath) {
    Remove-Item -LiteralPath $ArchivePath -Force
}

Compress-Archive -Path (Join-Path $PackageRoot "*") -DestinationPath $ArchivePath -Force

Write-Host "Package created:"
Write-Host $ArchivePath

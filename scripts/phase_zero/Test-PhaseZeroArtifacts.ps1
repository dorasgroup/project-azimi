param(
    [Parameter(Mandatory = $true)]
    [string]$BackupDirectory,
    [string]$BenchmarkPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $BenchmarkPath) {
    $BenchmarkPath = Join-Path $Root "docs\crawling-improvement\artifacts\phase-0-baseline.json"
}

foreach ($script in Get-ChildItem -File $PSScriptRoot -Filter "*.ps1") {
    [void][scriptblock]::Create((Get-Content -Raw -LiteralPath $script.FullName))
}

$benchmark = Get-Content -Raw -Encoding UTF8 -LiteralPath $BenchmarkPath | ConvertFrom-Json
if (-not $benchmark.job.id -or -not $benchmark.job.topic) {
    throw "Benchmark job metadata is incomplete."
}
if (@($benchmark.sources).Count -ne [int]$benchmark.coverage.selected_sources) {
    throw "Benchmark source count does not match coverage.selected_sources."
}
$covered = @($benchmark.sources | Where-Object { $_.accepted_documents -gt 0 }).Count
$missing = @($benchmark.sources | Where-Object { $_.accepted_documents -eq 0 }).Count
if ($covered -ne [int]$benchmark.coverage.covered_sources -or
    $missing -ne [int]$benchmark.coverage.missing_sources) {
    throw "Benchmark coverage totals are inconsistent."
}
if (@($benchmark.sources | Group-Object source_id | Where-Object Count -ne 1).Count -gt 0) {
    throw "Benchmark contains duplicate source IDs."
}
if (@($benchmark.sources | Where-Object {
    $_.accepted_documents -eq 0 -and -not $_.failure_category
}).Count -gt 0) {
    throw "Every missing source must have a failure category."
}

$manifestPath = Join-Path $BackupDirectory "manifest.json"
$restorePath = Join-Path $BackupDirectory "restore-verification.json"
$manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
$restore = Get-Content -Raw -Encoding UTF8 -LiteralPath $restorePath | ConvertFrom-Json
if (-not $manifest.restore_verified -or -not $restore.success) {
    throw "Backup restore has not been verified."
}
foreach ($artifact in $manifest.artifacts) {
    $path = Join-Path $BackupDirectory $artifact.name
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing backup artifact: $($artifact.name)"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    if ($actual -ne $artifact.sha256) {
        throw "Checksum mismatch: $($artifact.name)"
    }
}

$trackedDocumentation = @(
    Get-ChildItem -Recurse -File (Join-Path $Root "docs\crawling-improvement")
)
$secretPatterns = @(
    '(?i)api[_-]?key\s*[=:]\s*["''][^"'']{8,}',
    '(?i)password\s*[=:]\s*["''][^"'']{8,}',
    '(?i)bearer\s+[a-z0-9._-]{16,}'
)
foreach ($file in $trackedDocumentation) {
    $text = Get-Content -Raw -Encoding UTF8 -LiteralPath $file.FullName
    foreach ($pattern in $secretPatterns) {
        if ($text -match $pattern) {
            throw "Potential secret in tracked phase-zero artifact: $($file.FullName)"
        }
    }
}

Write-Host "Phase-zero artifact verification passed."

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupDirectory
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ComposeFile = Join-Path $Root "docker\compose.yml"
$dumpPath = Join-Path $BackupDirectory "rasad-postgres.dump"
if (-not (Test-Path -LiteralPath $dumpPath)) {
    throw "Rasad database dump was not found: $dumpPath"
}

$container = "rasad-analysis-postgres-1"
$temporaryDatabase = "phase_zero_restore_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
if ($temporaryDatabase -notmatch '^phase_zero_restore_[0-9]{8}_[0-9]{6}$') {
    throw "Unsafe temporary database name."
}
$temporaryDump = "/tmp/$temporaryDatabase.dump"

function Invoke-PsqlScalar {
    param([string]$Database, [string]$Sql)
    $value = docker exec $container psql -U rasad -d $Database -At -v ON_ERROR_STOP=1 -c $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "Database query failed against $Database."
    }
    return ($value -join "`n").Trim()
}

$created = $false
Push-Location $Root
try {
    docker cp $dumpPath "$container`:$temporaryDump"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not copy dump into the PostgreSQL container."
    }

    docker exec $container createdb -U rasad $temporaryDatabase
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create temporary restore database."
    }
    $created = $true

    docker exec $container pg_restore -U rasad -d $temporaryDatabase `
        --no-owner --no-privileges --exit-on-error $temporaryDump
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore failed."
    }

    $tables = @(
        "users",
        "sources",
        "monitoring_jobs",
        "monitoring_job_sources",
        "crawled_documents",
        "analysis_reports",
        "bot_identities",
        "system_logs"
    )
    $comparisons = @()
    foreach ($table in $tables) {
        if ($table -notmatch '^[a-z_]+$') { throw "Unsafe table name: $table" }
        $originalCount = [int64](Invoke-PsqlScalar -Database "rasad" -Sql "SELECT count(*) FROM $table")
        $restoredCount = [int64](Invoke-PsqlScalar -Database $temporaryDatabase -Sql "SELECT count(*) FROM $table")
        $comparisons += [ordered]@{
            table = $table
            original = $originalCount
            restored = $restoredCount
            matches = $originalCount -eq $restoredCount
        }
    }

    $originalMigration = Invoke-PsqlScalar -Database "rasad" -Sql "SELECT version_num FROM alembic_version"
    $restoredMigration = Invoke-PsqlScalar -Database $temporaryDatabase -Sql "SELECT version_num FROM alembic_version"
    $originalLatestJob = Invoke-PsqlScalar -Database "rasad" -Sql "SELECT id FROM monitoring_jobs ORDER BY created_at DESC LIMIT 1"
    $restoredLatestJob = Invoke-PsqlScalar -Database $temporaryDatabase -Sql "SELECT id FROM monitoring_jobs ORDER BY created_at DESC LIMIT 1"
    $allCountsMatch = @($comparisons | Where-Object { -not $_.matches }).Count -eq 0
    $verified = $allCountsMatch -and `
        ($originalMigration -eq $restoredMigration) -and `
        ($originalLatestJob -eq $restoredLatestJob)

    $verification = [ordered]@{
        verified_at = (Get-Date).ToUniversalTime().ToString("o")
        success = $verified
        temporary_database = $temporaryDatabase
        counts = $comparisons
        migration = [ordered]@{
            original = $originalMigration
            restored = $restoredMigration
            matches = $originalMigration -eq $restoredMigration
        }
        latest_job = [ordered]@{
            original = $originalLatestJob
            restored = $restoredLatestJob
            matches = $originalLatestJob -eq $restoredLatestJob
        }
    }
    $verification | ConvertTo-Json -Depth 20 | Set-Content `
        -Path (Join-Path $BackupDirectory "restore-verification.json") -Encoding UTF8
    if (-not $verified) {
        throw "Restored database does not match the source database."
    }
}
finally {
    if ($created) {
        if ($temporaryDatabase -match '^phase_zero_restore_[0-9]{8}_[0-9]{6}$') {
            docker exec $container psql -U rasad -d postgres -v ON_ERROR_STOP=1 `
                -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$temporaryDatabase' AND pid <> pg_backend_pid()" | Out-Null
            docker exec $container dropdb -U rasad $temporaryDatabase | Out-Null
        }
    }
    docker exec $container rm -f $temporaryDump | Out-Null
    Pop-Location
}

$manifestPath = Join-Path $BackupDirectory "manifest.json"
if (Test-Path $manifestPath) {
    $manifest = Get-Content -Raw $manifestPath | ConvertFrom-Json
    $manifest.restore_verified = $true
    $manifest | Add-Member -NotePropertyName "restore_verified_at" `
        -NotePropertyValue (Get-Date).ToUniversalTime().ToString("o") -Force
    $manifest | ConvertTo-Json -Depth 20 | Set-Content -Path $manifestPath -Encoding UTF8
}
Write-Host "Restore verification passed for $BackupDirectory"

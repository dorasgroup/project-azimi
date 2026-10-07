param(
    [switch]$SkipSlow
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Compose = @("compose", "--env-file", ".env", "-f", "docker/compose.yml")

function Run-Step {
    param(
        [string]$Name,
        [scriptblock]$Command
    )

    Write-Host ""
    Write-Host "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Name (exit code $LASTEXITCODE)"
    }
}

Push-Location $Root
try {
    Run-Step "Docker services" {
        docker @Compose --profile worker ps
    }

    Run-Step "Backend unit tests" {
        docker @Compose exec -T api pytest -q
    }

    Run-Step "Backend compile" {
        docker @Compose exec -T api python -m compileall app scripts
    }

    Run-Step "Alembic check" {
        docker @Compose exec -T api alembic check
    }

    Run-Step "Database verification" {
        docker @Compose exec -T api python -m scripts.verify_database
    }

    Run-Step "OpenAPI verification" {
        docker @Compose exec -T api python -m scripts.verify_openapi
    }

    Run-Step "Report exports" {
        docker @Compose exec -T api python -m scripts.verify_report_exports
    }

    Run-Step "Security" {
        docker @Compose exec -T api python -m scripts.verify_security
    }

    Run-Step "Observability" {
        docker @Compose exec -T api python -m scripts.verify_observability
    }

    Run-Step "MVP scenario" {
        docker @Compose exec -T api python -m scripts.verify_mvp
    }

    Run-Step "Professional dashboard" {
        docker @Compose exec -T api python -m scripts.verify_professional_features
    }

    Run-Step "Worker registered tasks" {
        docker @Compose exec -T worker celery -A app.workers.celery_app inspect registered
    }

    Run-Step "Frontend lint" {
        Push-Location "$Root/frontend"
        try {
            npm run lint
        }
        finally {
            Pop-Location
        }
    }

    Run-Step "Frontend build" {
        Push-Location "$Root/frontend"
        try {
            npm run build
        }
        finally {
            Pop-Location
        }
    }

    if (-not $SkipSlow) {
        Run-Step "Crawler verification" {
            docker @Compose exec -T worker python -m scripts.verify_crawler
        }

        Run-Step "JavaScript crawler verification" {
            docker @Compose exec -T worker python -m scripts.verify_javascript_crawler
        }

        Run-Step "Content processing" {
            docker @Compose exec -T worker python -m scripts.verify_content_processing
        }

        Run-Step "Performance" {
            docker @Compose exec -T worker python -m scripts.verify_performance
        }

        Run-Step "Monitoring flow" {
            docker @Compose exec -T worker python -m scripts.verify_monitoring_flow
        }
    }

    Run-Step "Git diff whitespace check" {
        git diff --check
    }

    Write-Host ""
    Write-Host "Project verification passed."
}
finally {
    Pop-Location
}

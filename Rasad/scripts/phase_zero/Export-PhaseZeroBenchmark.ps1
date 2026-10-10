param(
    [string]$JobId,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ComposeFile = Join-Path $Root "docker\compose.yml"

if ($JobId) {
    $parsedJobId = [Guid]::Empty
    if (-not [Guid]::TryParse($JobId, [ref]$parsedJobId)) {
        throw "JobId must be a valid UUID."
    }
    $jobPredicate = "WHERE id = '$($parsedJobId.ToString())'::uuid"
}
else {
    $jobPredicate = ""
}

if (-not $OutputPath) {
    $OutputPath = Join-Path $Root "docs\crawling-improvement\artifacts\phase-0-baseline.json"
}

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$sql = @"
WITH target_job AS (
    SELECT *
    FROM monitoring_jobs
    $jobPredicate
    ORDER BY created_at DESC
    LIMIT 1
),
source_logs AS (
    SELECT DISTINCT ON (context_json->>'source_id')
        context_json->>'source_id' AS source_id,
        context_json
    FROM system_logs
    WHERE job_id = (SELECT id FROM target_job)
      AND message = 'Source crawled and prepared'
    ORDER BY context_json->>'source_id', created_at DESC
),
document_counts AS (
    SELECT source_id, count(*)::int AS accepted_documents
    FROM crawled_documents
    WHERE job_id = (SELECT id FROM target_job)
    GROUP BY source_id
),
source_rows AS (
    SELECT
        s.id,
        s.title,
        s.base_url,
        COALESCE(l.context_json#>>'{crawl_diagnostics,status}', 'unknown') AS crawl_status,
        COALESCE(l.context_json#>>'{crawl_diagnostics,reason_code}', 'unknown') AS reason_code,
        COALESCE((l.context_json#>>'{crawl_diagnostics,discovered_total}')::int, 0) AS discovered_urls,
        COALESCE((l.context_json#>>'{crawl_diagnostics,selected_total}')::int, 0) AS selected_urls,
        COALESCE((l.context_json#>>'{crawl_diagnostics,skipped_by_limit}')::int, 0) AS skipped_by_limit,
        COALESCE((l.context_json->>'crawled_pages')::int, 0) AS fetched_pages,
        COALESCE((l.context_json#>>'{crawl_diagnostics,successful_pages}')::int, 0) AS successful_pages,
        COALESCE((l.context_json#>>'{crawl_diagnostics,failed_pages}')::int, 0) AS failed_pages,
        COALESCE(d.accepted_documents, 0) AS accepted_documents,
        COALESCE((l.context_json->>'rejected_documents')::int, 0) AS rejected_documents,
        COALESCE(l.context_json->'rejection_reasons', '{}'::jsonb) AS rejection_reasons,
        COALESCE((l.context_json#>>'{crawl_diagnostics,duration_ms}')::int, 0) AS duration_ms,
        COALESCE(l.context_json#>>'{crawl_diagnostics,summary}', 'No diagnostic was recorded') AS diagnostic_summary,
        CASE
            WHEN lower(s.base_url) LIKE '%python.org%' THEN 'test_or_irrelevant_source'
            WHEN l.context_json#>>'{crawl_diagnostics,summary}' ILIKE '%non-public address%' THEN 'dns_sinkhole'
            WHEN l.context_json#>>'{crawl_diagnostics,summary}' ILIKE '%connection attempts failed%' THEN 'network_unreachable'
            WHEN l.context_json#>>'{crawl_diagnostics,reason_code}' = 'robots_blocked' THEN 'robots_blocked'
            WHEN l.context_json#>>'{crawl_diagnostics,reason_code}' = 'no_urls_discovered' THEN 'no_urls_discovered'
            WHEN COALESCE((l.context_json#>>'{crawl_diagnostics,http_status_counts,403}')::int, 0) > 0 THEN 'http_restricted'
            WHEN COALESCE(d.accepted_documents, 0) = 0
                 AND COALESCE((l.context_json->'rejection_reasons'->>'Content relevance score is below the configured threshold')::int, 0) > 0
                THEN 'no_topic_match'
            WHEN l.context_json#>>'{crawl_diagnostics,reason_code}' = 'probe_failed' THEN 'connection_failed'
            WHEN l.context_json IS NULL THEN 'unknown'
            ELSE 'unknown'
        END AS failure_category
    FROM target_job j
    JOIN monitoring_job_sources m ON m.job_id = j.id
    JOIN sources s ON s.id = m.source_id
    LEFT JOIN source_logs l ON l.source_id = s.id::text
    LEFT JOIN document_counts d ON d.source_id = s.id
),
coverage AS (
    SELECT
        count(*)::int AS selected_sources,
        count(*) FILTER (WHERE accepted_documents > 0)::int AS covered_sources,
        count(*) FILTER (WHERE accepted_documents = 0)::int AS missing_sources,
        COALESCE(sum(accepted_documents), 0)::int AS accepted_documents
    FROM source_rows
)
SELECT jsonb_pretty(
    jsonb_build_object(
        'job', jsonb_build_object(
            'id', j.id,
            'topic', j.topic,
            'date_from_jalali', j.date_from_jalali,
            'date_to_jalali', j.date_to_jalali,
            'status', j.status,
            'started_at', j.started_at,
            'finished_at', j.finished_at,
            'duration_seconds', CASE
                WHEN j.started_at IS NOT NULL AND j.finished_at IS NOT NULL
                    THEN extract(epoch FROM (j.finished_at - j.started_at))::int
                ELSE NULL
            END
        ),
        'coverage', jsonb_build_object(
            'selected_sources', c.selected_sources,
            'covered_sources', c.covered_sources,
            'missing_sources', c.missing_sources,
            'coverage_percent', CASE
                WHEN c.selected_sources = 0 THEN 0
                ELSE round(c.covered_sources::numeric * 100 / c.selected_sources, 2)
            END,
            'accepted_documents', c.accepted_documents
        ),
        'sources', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_object(
                    'source_id', r.id,
                    'title', r.title,
                    'base_url', r.base_url,
                    'crawl_status', r.crawl_status,
                    'reason_code', r.reason_code,
                    'failure_category', CASE WHEN r.accepted_documents > 0 THEN NULL ELSE r.failure_category END,
                    'diagnostic_summary', r.diagnostic_summary,
                    'discovered_urls', r.discovered_urls,
                    'selected_urls', r.selected_urls,
                    'skipped_by_limit', r.skipped_by_limit,
                    'fetched_pages', r.fetched_pages,
                    'successful_pages', r.successful_pages,
                    'failed_pages', r.failed_pages,
                    'accepted_documents', r.accepted_documents,
                    'rejected_documents', r.rejected_documents,
                    'rejection_reasons', r.rejection_reasons,
                    'duration_ms', r.duration_ms,
                    'retry_recommended', r.failure_category IN ('dns_sinkhole', 'network_unreachable', 'connection_failed'),
                    'configuration_review_required', r.failure_category IN ('no_urls_discovered', 'redirected_outside_allowed_host', 'test_or_irrelevant_source'),
                    'remote_egress_required', r.failure_category IN ('dns_sinkhole', 'network_unreachable'),
                    'content_restriction', r.failure_category IN ('robots_blocked', 'http_restricted')
                )
                ORDER BY r.title, r.base_url
            )
            FROM source_rows r
        ), '[]'::jsonb)
    )
)
FROM target_job j
CROSS JOIN coverage c;
"@

Push-Location $Root
try {
    $json = docker compose -f $ComposeFile exec -T postgres `
        psql -U rasad -d rasad -At -v ON_ERROR_STOP=1 -c $sql
    if ($LASTEXITCODE -ne 0) {
        throw "Benchmark query failed with exit code $LASTEXITCODE."
    }
    if (-not $json) {
        throw "No monitoring job was found for the requested benchmark."
    }
    $parsed = ($json -join "`n") | ConvertFrom-Json
    $normalized = $parsed | ConvertTo-Json -Depth 30
    [IO.File]::WriteAllText(
        [IO.Path]::GetFullPath($OutputPath),
        $normalized + "`n",
        (New-Object Text.UTF8Encoding($false))
    )
    Write-Host "Benchmark written to $OutputPath"
}
finally {
    Pop-Location
}

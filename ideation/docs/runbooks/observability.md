# Observability operations runbook

Use both Production Compose files for every command. Never run the Production file alone
and never use `down -v`, `volume rm`, prune, reset, or a database migration command.

## Check Prometheus targets and rules

```bash
docker exec infrastructure-prometheus-1 \
  wget -qO- 'http://127.0.0.1:9090/api/v1/targets?state=active'
docker exec infrastructure-prometheus-1 \
  wget -qO- 'http://127.0.0.1:9090/api/v1/rules'
```

Do not print full responses into tickets without reviewing labels. No public Prometheus
UI or Alertmanager exists.

## Increased HTTP 5xx

1. Confirm `SustainedHttp5xxRate` and traffic volume in Prometheus.
2. Group `ideation_http_requests_total{status_class="5xx"}` by route/method.
3. Select a request ID from the matching Backend JSON event and search that exact ID.
4. Check readiness and PostgreSQL continuity before checking optional AI dependencies.
5. Roll back Backend if the increase began exactly with a deployment and is unexplained.

## Slow Chat

1. Compare p50/p95 `ideation_chat_duration_seconds` with retrieval/model histograms.
2. Check Dify duration/error/timeout metrics and `ideation_ai_fallbacks_total`.
3. Confirm current request traffic before treating an empty histogram as healthy.
4. Never log or copy prompt/answer content during diagnosis.

## Dify unavailable

Check bounded Dify outcomes and timeouts, Dify container health, then a sanitized health
probe. Upload may report `indexing_failed`; Chat may return its controlled error/fallback.
Do not retry by manually duplicating an Upload or changing providers.

## Weaviate unavailable / fallback increased

Confirm Weaviate readiness independently, then compare Weaviate errors with
`ideation_ai_fallbacks_total{from_dependency="weaviate",to_dependency="dify"}`. A
successful Dify fallback is a warning, not an HTTP failure. Persistent fallback can still
increase latency and should be investigated.

## Upload failures

Compare `ideation_upload_received_total`, result outcomes, validation/storage duration,
indexing outcomes, Backend disk capacity, and Dify dependency metrics. Duplicate is not
failure. Do not inspect file content or filename and do not re-index a duplicate manually.

## Prometheus disk filling

1. Check `/prometheus` usage inside the Prometheus container and host filesystem free space.
2. Confirm the configured 10-day and 2GB retention flags.
3. Stop Prometheus if host safety is threatened; Backend remains independent.
4. Do not remove either the Prometheus or PostgreSQL volume during incident response.

## Target down

For Backend, check container health, internal `/metrics`, and Compose DNS. For Prometheus
self-target failure, inspect the Prometheus container/config. Never make Prometheus a
Backend readiness dependency.

## Phase 2 rollback

1. Recreate Backend and Nginx from the validated Phase-1 rollback override using
   `--no-build --no-deps`.
2. Stop only `infrastructure-prometheus-1`; do not delete its volume.
3. Verify Login, Upload, duplicate, Chat, CORS, liveness/readiness, and PostgreSQL mount.
4. Record the trigger and observed metrics without copying secrets or user data.

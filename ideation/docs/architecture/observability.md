# Production observability architecture

Status: Phase 2 implementation for the active FastAPI production path.

## Request correlation and log flow

`RequestObservabilityMiddleware` is the outer application middleware. It accepts
`X-Request-ID` only when the value is 1–128 characters and matches the safe character
set `[A-Za-z0-9._:-]`; otherwise it generates a UUID. A `ContextVar` makes the value
available to logs during that request, the response always receives the header, and the
context is reset in `finally`.

Production application logs are single-line JSON on stdout and in the existing bounded
files. The schema is:

- `timestamp`: UTC ISO-8601 timestamp;
- `level`, `service`, `environment`, `message`/`event`;
- `request_id` when a request context exists;
- optional `method`, full public `route` template, `status_code`, and `duration_ms`;
- optional bounded `dependency`, `operation`, `outcome`, and `error_type`;
- internal exception frame locations only, without exception values or source lines.

Raw paths, query strings, request/response bodies, chat content, filenames, identifiers,
credentials, and external document identifiers are not logged. Common credential-shaped
text is redacted by the formatter as defense in depth. Uvicorn access logging and httpx
INFO request logging are disabled to prevent duplicate/raw-URL records. Health/metrics
requests are omitted from application request logs.

Search a request in Docker logs with an exact, validated request ID:

```bash
docker logs infrastructure-backend-1 2>&1 | jq -c \
  'select(.request_id == "SAFE_REQUEST_ID")'
```

## Metrics and label policy

The official Prometheus Python client exports `/metrics` directly from Backend. Nginx
returns 404 for public `/metrics`; Prometheus scrapes `backend:8000/metrics` only on the
internal Compose network. A scrape performs no PostgreSQL, Dify, or Weaviate call.

Permitted labels are deliberately bounded:

- HTTP: `method`, full route template, `status_class`;
- dependencies: `dependency` (`dify`/`weaviate`), fixed `operation`, fixed `outcome`;
- fallback: fixed source, destination, and reason;
- Upload/Chat result: fixed outcome only.

Request/user/upload/document/category/session IDs, filenames, raw paths, query strings,
exception messages, prompts, answers, and model content are forbidden labels.

### HTTP metrics

- `ideation_http_requests_total`
- `ideation_http_request_duration_seconds`
- `ideation_http_requests_in_progress`
- `ideation_http_unhandled_exceptions_total`

### Upload metrics

- `ideation_upload_received_total`
- `ideation_upload_results_total{outcome}`
- `ideation_upload_validation_duration_seconds`
- `ideation_upload_storage_duration_seconds`
- `ideation_upload_indexing_duration_seconds`
- `ideation_upload_size_bytes`
- `ideation_upload_indexing_results_total{outcome}`

### Chat and AI dependency metrics

- `ideation_chat_requests_total`
- `ideation_chat_results_total{outcome}`
- `ideation_chat_duration_seconds`
- `ideation_chat_retrieval_duration_seconds`
- `ideation_chat_model_duration_seconds`
- `ideation_chat_responses_with_sources_total`
- `ideation_dependency_requests_total{dependency,operation,outcome}`
- `ideation_dependency_request_duration_seconds{dependency,operation}`
- `ideation_dependency_timeouts_total{dependency,operation}`
- `ideation_ai_fallbacks_total{from_dependency,to_dependency,reason}`

Duplicate Uploads are counted as `duplicate`, not failure, and do not create a second
index call. Instrumentation only observes existing calls.

## Worker model

Production currently runs one Uvicorn worker and Phase 2 does not change that count. The
launcher supports `BACKEND_WORKERS` without lowering it and activates official client
multiprocess aggregation when `PROMETHEUS_MULTIPROC_DIR` is set. The dedicated directory
is prepared before workers spawn; `livesum` is used for the in-progress gauge; graceful
worker shutdown marks its gauge files dead. Container restart clears old metric database
files in that dedicated directory. A two-worker integration test verifies aggregation.

## Prometheus

Production uses `ghcr.io/prometheus/prometheus:v3.5.5` (LTS), with no host port,
10-day/2GB retention, 0.50 CPU/256MiB limit and 0.05 CPU/64MiB reservation. Its named
`prometheus_data` volume is separate from PostgreSQL. Backend health/readiness never
depends on Prometheus.

Configuration: `infrastructure/prometheus/prometheus.yml`  
Rules: `infrastructure/prometheus/rules/ideation-alerts.yml`

Validate both with the exact pinned image:

```bash
docker run --rm --entrypoint /bin/promtool \
  -v /opt/ideation-project/infrastructure/prometheus:/etc/prometheus:ro \
  ghcr.io/prometheus/prometheus:v3.5.5 \
  check config /etc/prometheus/prometheus.yml
```

Grafana, Alertmanager, external notifications, OpenTelemetry, distributed tracing, and
all later-phase messaging/ingestion components are intentionally absent.

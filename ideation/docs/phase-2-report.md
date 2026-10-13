# Phase 2 — observability, metrics, and operational monitoring

Date: 2026-08-16  
Scope: Phase 2 only; Phase 3 and all explicitly prohibited architecture components are absent.

## Pre-change production state

- Active application: Nginx, Next.js Frontend, one-process Uvicorn Backend, PostgreSQL.
- Backend command: `alembic upgrade head` followed by Uvicorn; one worker, no Gunicorn.
- Phase-1 Backend image: `sha256:dad55159359296c907610746335883b301c6145a2ca711cd39db3ed93a78c3f0`.
- PostgreSQL: healthy, zero restarts, original external
  `infrastructure_pg_data` mounted at `/var/lib/postgresql/data`.
- Existing request middleware was not registered; logs were plain text and Uvicorn/httpx
  emitted raw request paths. Existing application log files occupied about 444KiB.
- An old experimental `prom/prometheus:latest` container existed only in exited state;
  its config targeted inactive future components and was not part of Production.

Host capacity before change: 6 CPUs, about 4.7GiB available RAM, 57GiB free disk, 40%
filesystem use, 17% inode use, and no swap pressure. This is sufficient for a Prometheus
instance capped at 0.50 CPU/256MiB and 2GB storage.

### Repeatable pre-instrumentation workload (three samples)

| Operation | p50 |
|---|---:|
| Login | 354.610 ms |
| Liveness | 6.770 ms |
| Readiness | 89.203 ms |
| Small Upload | 340.925 ms |
| Duplicate | 151.589 ms |
| Chat | 647.866 ms |

Chat samples were 2487.227, 647.866, and 599.492ms, illustrating provider/cache
variability and why the initial alert uses a conservative 15-second p95 threshold with
traffic and a 15-minute `for`. Backend idle snapshot was 0.23% CPU and 75.39MiB RAM.

## Implemented design

The request-ID design, JSON log schema, complete metric catalog, bounded label policy,
worker lifecycle, internal endpoint, and Prometheus topology are authoritative in
`docs/architecture/observability.md`. Operational response procedures are in
`docs/runbooks/observability.md`.

Prometheus is pinned to official LTS `v3.5.5` from GHCR, has no public port, retains 10
days up to 2GB, and uses its own named volume. Eight rules cover Backend down, sustained
5xx, Chat latency, Upload failure, Dify errors/timeouts, Weaviate errors, fallback rate,
and internal target down. No Alertmanager, notification integration, or Grafana exists.

## Pre-rollout validation

| Check | Status | Evidence |
|---|---|---|
| Phase-1 Backend suite | PASS | Included in full suite; no regression |
| Phase-2 observability tests | PASS | 13 focused tests |
| Full Backend suite | PASS | 48 passed before rollout; final rerun 44 passed plus four environment-gated skips |
| Ruff changed Python | PASS | No findings |
| Request ID/error/concurrency | PASS | Generated, preserved, replaced, error header, no context leak |
| Logging/redaction/template | PASS | JSON schema and secret tests; complete public templates |
| HTTP/Upload/Chat/dependency metrics | PASS | Success/failure/duplicate/fallback/timeout scenarios |
| Cardinality policy | PASS | Raw/identity labels absent from exposition |
| Two-worker aggregation | PASS | Two Uvicorn workers; 40 concurrent requests aggregate to one sample/40 |
| Multiprocess restart cleanup | PASS | Pre-restart request series absent after container restart |
| Metrics exposition | PASS | Prometheus parser accepts output; repeated scrape stable |
| Prometheus config | PASS | `promtool check config` |
| Alert rules | PASS | `promtool check rules`; eight rules |
| New migration/schema change | PASS | None |

## Rollout and post-instrumentation results

Rollback artifacts were written outside Git at
`/var/backups/ideation-phase2/rollout-20260816T085749Z` with restrictive permissions.
The immutable Phase-1 Backend tag resolves to the recorded Phase-1 image, the rollback
Compose override resolves the original external PostgreSQL volume, and the Phase-1 Nginx
snapshot passes `nginx -t`. The Phase-1 rollback drill had already completed in 24.1
seconds. The Phase-2 rollback path was validated but not invoked because every rollout
gate passed.

The exact controlled rollout used both Production Compose files:

```bash
docker compose -f infrastructure/docker-compose.prod.yml \
  -f infrastructure/docker-compose.restore-volume.yml \
  up -d --no-deps backend
docker compose -f infrastructure/docker-compose.prod.yml \
  -f infrastructure/docker-compose.restore-volume.yml restart nginx
docker compose -f infrastructure/docker-compose.prod.yml \
  -f infrastructure/docker-compose.restore-volume.yml \
  up -d --no-deps prometheus
```

Backend replacement took 10.721 seconds and Prometheus start took 0.517 seconds. Only
Backend, Nginx, and the new independent Prometheus service were touched. PostgreSQL was
not restarted or recreated. No migration was created or applied; Alembic remained
`20260721_0002 (head)`.

### Live smoke and security results

| Check | Status | Result |
|---|---|---|
| Liveness/readiness | PASS | 200/200 and stable throughout observation |
| Request ID | PASS | Valid caller value preserved on a 401 response |
| Protected route without JWT | PASS | `/api/v1/auth/me` returned 401; invalid JWT returned 401 |
| Role enforcement | PASS | Temporary normal user received 403 from `/api/v1/admin/users` |
| Prototype routes | PASS | `/api/v1/users` and `/api/v1/documents/*` returned 404 |
| CORS allowed | PASS | Production origin returned explicit ACAO and successful preflight |
| CORS denied | PASS | Unapproved origin returned 400 with no ACAO |
| Public `/metrics` | PASS | Local public edge and real HTTPS domain both returned 404 |
| Internal `/metrics` | PASS | 200, valid Prometheus text, no dependency call |
| Login | PASS | Three authenticated rounds plus role smoke |
| Small Upload | PASS | Three uploads indexed successfully |
| Duplicate | PASS | Three duplicates reused the record and caused no second indexing |
| Chat | PASS | Three successful responses |
| AI failure/fallback | PASS | Three Weaviate transport errors fell back successfully to Dify |
| Test cleanup | PASS | Created content removed through APIs; both temporary accounts inactive |
| Logs | PASS | No secret pattern, chat text, traceback, MissingGreenlet, or repeated error |

The Backend emitted 34 parseable application JSON events during the main workload. Six
non-JSON lines were process/migration startup output rather than application request
records. There were no ERROR/CRITICAL application events; three sanitized WARNING events
recorded the expected Weaviate-to-Dify fallback. The Docker log grew to about 18KiB.

### Metrics and Prometheus

- Internal exposition after workload: about 44,945 bytes and 427 series.
- Warm scrape p50: 8.847ms over five samples; the first cold scrape was 37.264ms.
- Forbidden identity/content label keys were absent. Observed application label values
  remained in the documented fixed sets; Python runtime metadata labels were standard.
- Upload metrics reported 3 success and 3 duplicate; Chat reported 3 success.
- Dependency metrics reported 3 controlled Weaviate transport errors and 3 fallbacks.
- Both `ideation-backend` and Prometheus self-target remained `up`.
- All eight rules loaded with health `ok`; none fired during observation.
- Prometheus used about 38.2MiB of its 256MiB limit and 412KiB on disk.

### Before/after repeatable baseline

Both runs used the same three-round workload and report p50.

| Operation | Before | After | Change |
|---|---:|---:|---:|
| Login | 354.610ms | 302.535ms | -14.7% |
| Liveness | 6.770ms | 2.908ms | -57.0% |
| Readiness | 89.203ms | 51.910ms | -41.8% |
| Small Upload | 340.925ms | 151.192ms | -55.7% |
| Duplicate | 151.589ms | 88.655ms | -41.5% |
| Chat | 647.866ms | 833.284ms | +28.6% |

No fast internal path regressed. Chat samples were 7515.566, 278.881, and 833.284ms;
the broad spread is consistent with external-provider/cache variability, remains below
the Phase-1 8.19-second baseline, and does not indicate instrumentation overhead.
Backend idle memory rose from 75.39MiB to 79.53MiB (+4.14MiB); it reached 88.32MiB just
after the workload. During nine one-minute production samples, Backend CPU median was
0.32% with two brief sampling spikes and no sustained load; memory was flat at 79.53MiB.
Prometheus memory stayed between 38.04 and 38.16MiB.

The observation ran from 09:02:12Z through 09:17:50Z (15 minutes 38 seconds for
Prometheus; more than 16 minutes for Backend). Backend, Prometheus, and PostgreSQL stayed
healthy with zero restarts; both targets stayed up and no rule fired.

### PostgreSQL continuity

The merged Compose maps logical `postgres_data` to external
`infrastructure_pg_data -> /var/lib/postgresql/data`, exactly matching the active
container. PostgreSQL retained its pre-rollout start time, image, healthy state, and zero
restart count. Read-only aggregate queries confirmed existing users, uploads, and
categories. No database value or secret is included in this report.

### Rollback command

```bash
docker compose -f infrastructure/docker-compose.prod.yml \
  -f infrastructure/docker-compose.restore-volume.yml stop prometheus
docker compose -f infrastructure/docker-compose.prod.yml \
  -f infrastructure/docker-compose.restore-volume.yml \
  -f /var/backups/ideation-phase2/rollout-20260816T085749Z/docker-compose.rollback.yml \
  up -d --no-build --no-deps backend nginx
```

This leaves both PostgreSQL and Prometheus volumes intact. Backend does not depend on
Prometheus for startup, liveness, readiness, or request processing.

## Files changed

- Backend observability: `app/observability/*`, request middleware, JSON logging,
  exception handling, launcher, and metrics endpoint.
- Bounded instrumentation: Upload, Chat, Dify/Weaviate dependency paths.
- Deployment: Backend Dockerfile/dependency, Nginx public denial, Compose Prometheus
  service, pinned Prometheus config and rules.
- Tests: Phase-2 observability/multiprocess coverage and fallback assertion.
- Documentation: this report, current-production architecture, observability design,
  and operations runbook.

No migration/schema, public API contract, provider, worker count, Phase-3 component,
Grafana, Alertmanager, or external notification was added.

## Remaining risks

- Prometheus is local-only and has no Alertmanager/Grafana by explicit scope; operators
  must inspect rules/metrics directly.
- Chat latency remains provider-dependent, so the initial 15-second p95 alert is
  deliberately conservative and should be tuned only after more production traffic.
- The repository has no Git baseline. Rollback therefore relies on immutable local
  images and the restricted external snapshot directory.
- Whole-tree Ruff still reports 90 pre-existing findings in untouched prototype/legacy
  code; Ruff on all Phase-2-changed Python files passes.

## Current acceptance matrix

| Criterion | Status |
|---|---|
| Request ID | PASS |
| Structured logging | PASS |
| Secret redaction | PASS |
| HTTP metrics | PASS |
| Upload metrics | PASS |
| Chat metrics | PASS |
| Dify/Weaviate/fallback metrics | PASS |
| Multi-worker compatibility | PASS |
| `/metrics` internal-only | PASS |
| Prometheus target/rules | PASS |
| Phase-1 regression | PASS |
| Post-instrumentation baseline/overhead | PASS |
| PostgreSQL continuity | PASS |
| Rollback | PASS (validated and ready; not invoked) |
| Phase 2 ready for owner approval | PENDING |

All technical Phase-2 criteria pass. Phase 2 is ready for explicit owner approval.
Phase 3 has not started.

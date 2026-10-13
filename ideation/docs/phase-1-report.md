# Phase 1 architecture stabilization report

Date: 2026-08-16  
Scope: phase 1 only; no phase 2 components were activated

> **Latest rollout status (2026-08-16T07:57Z): PHASE-1 ROLLOUT ACCEPTED TECHNICALLY.**
> The duplicate regression was fixed and covered by real-PostgreSQL AsyncSession tests.
> The corrected Backend, Phase-1 Frontend, and Phase-1 Nginx configuration are active.
> Full security, Upload/duplicate, Chat, cleanup, and baseline smoke checks passed.
> PostgreSQL was never restarted and remains on the original external volume. Final
> owner sign-off is still pending; Phase 2 has not started.

## Summary

The current FastAPI production path is now explicitly documented and protected by
environment-driven, fail-closed CORS validation. Separate liveness and PostgreSQL-only
readiness probes were added, production/offline Compose healthchecks were aligned, and
the frontend's final absolute API fallback was changed to same-origin `/api/v1`.
No database schema or volume was changed. The owner-approved Phase-1 application images
were rebuilt/recreated under the controlled rollout described at the end of this report;
PostgreSQL was not rebuilt, recreated, or restarted.

## Git state before changes

`/opt/ideation-project` did not contain `.git`, and no parent under `/opt` contained a
Git worktree. Therefore the pre-change branch/status and a Git-generated diff cannot be
recovered in this workspace. Existing files were preserved and no unrelated cleanup was
performed. This is an acceptance limitation and must be corrected before deployment so
the release has a reviewable, reversible commit.

## Architecture findings

Docker labels identify the active application project as `infrastructure`, launched
from `docker-compose.prod.yml` plus `docker-compose.restore-volume.yml`. Active services
are Nginx, Next.js frontend, FastAPI `backend`, and PostgreSQL. Dify runs as a separate
stack and includes its own Weaviate. No active `core-api`, `ai-gateway`, or
`ingestion-worker` container was found.

The volume overlay is operationally important: it binds production PostgreSQL to the
existing external volume. Deployment and rollback commands must include both Compose
files until the owner explicitly retires that recovery arrangement. Running the
production file alone is not approved for this environment.

The authoritative architecture, endpoint paths, upload/chat flows, component status,
and capability ownership are in `docs/architecture/current-production.md`. The three
existing ADRs are marked as target architecture rather than current production.

## Changes applied

- CORS accepts only trimmed, valid `http(s)://host[:port]` origins; duplicates are
  removed, paths/credentials/query/fragment are rejected, wildcard plus credentials is
  rejected, and production cannot start with an empty list.
- Production Compose now requires `CORS_ALLOWED_ORIGINS` explicitly.
- `/api/v1/health/live` checks only the process.
- `/api/v1/health/ready` performs a bounded `SELECT 1` against PostgreSQL and returns
  sanitized `200`/`503` states.
- Dify health failure is reported as degraded without echoing exception details.
- Nginx aliases `/health/live` and `/health/ready` to the real backend probes.
- Backend Compose healthchecks use liveness; Nginx waits for backend health. Dify and
  Weaviate are intentionally excluded from restart-driving probes.
- Frontend logging now retains the same-origin `/api/v1` contract by default.
- Repeatable mock-backed tests cover CORS, startup, liveness/readiness, authentication
  guards, upload validation, SHA-256 duplicate detection, Dify upload failure, chat
  dependency failure, and Weaviate-to-Dify retrieval fallback.

## Changed files and reasons

| File | Reason |
|---|---|
| `README.md` | Official Compose entry points and production/development distinction |
| `backend/app/core/config.py` | Strict environment CORS parsing and production validation |
| `backend/app/main.py` | Use validated origins instead of wildcard |
| `backend/app/api/v1/health.py` | Liveness, PostgreSQL readiness, sanitized degraded state |
| `backend/tests/test_phase1_safety.py` | Phase 1 protective test suite |
| `backend/.env.example` | Explicit secret-free development CORS example |
| `frontend/lib/logging/client.ts` | Same-origin API fallback |
| `infrastructure/docker-compose.prod.yml` | Required CORS and backend healthcheck |
| `infrastructure/docker-compose.offline.yml` | Offline parity for CORS and healthcheck |
| `infrastructure/.env.prod.example` | Secret-free production CORS example |
| `nginx/conf.d/ideation-rag.prod.conf` | Probe routing aligned to FastAPI |
| `docs/architecture/current-production.md` | Current architecture and ownership baseline |
| `docs/architecture/adr/001-modular-monolith.md` | Mark future intent, not deployed state |
| `docs/architecture/adr/002-weaviate-and-dify-adapters.md` | Mark future intent, not deployed state |
| `docs/architecture/adr/003-event-driven-messaging.md` | Mark future intent, not deployed state |
| `docs/phase-1-report.md` | Results, deployment, rollback, and acceptance record |

No migration file was added or changed.

## Tests and exact results

### Before changes

- Python test runner: not runnable (`pytest` was absent from the host environment).
- Safe live HTTP checks against the unchanged running stack:
  - `/api/v1/health`: `200`, 15-byte body, 2.297 ms sample.
  - `/api/v1/admin/users` without a token: `401`, 3.617 ms sample.
  - `/health/live`: `404`, 3.869 ms sample, confirming the Nginx mismatch.
- Seven Compose definitions: syntax/config validation passed.
- Active Nginx: `nginx -t` passed.

The two legacy admin tests were not run because they use configured credentials and one
creates/deletes a user. Running them against an unidentified test database would violate
the no-destructive-production-testing rule.

### After changes

- Temporary isolated test environment: `/tmp/ideation-phase1-venv`.
- Phase 1 suite: **15 passed**, 4 deprecation warnings, 1.29 s.
- Safe legacy health test: **1 passed**, 4 deprecation warnings, 1.22 s.
- Collection: **18 tests collected**.
- Ruff on changed Python files: **passed**.
- Repository-wide Ruff: **failed with 128 pre-existing findings** in mostly untouched
  legacy/incomplete modules; those files were not reformatted in this phase.
- Backend production Docker image build: **passed**.
- Frontend production Docker image build (including Next.js compilation, lint, and type
  validation): **passed**. `npm audit` reported 1 moderate and 4 high dependency
  advisories; automatic dependency upgrades were not applied in this stabilization.
- Direct host-side frontend lint was unavailable because local Node dependencies are
  absent (`next: not found`); the clean multi-stage Docker build supplied the repeatable
  build/lint/typecheck result instead.
- Production startup import with a valid CORS origin: **passed**; empty production CORS:
  **failed closed** as designed.
- Current Alembic state: `20260721_0002 (head)`; read-only check passed.
- Final combined safe run: **16 passed**, 4 known warnings, 1.24 s; changed-file Ruff
  and Python bytecode compilation both passed.
- Final merged production+restore Compose, offline Compose, five supporting Compose
  definitions, and active Nginx syntax validation all passed.

Warnings are existing Pydantic v2 class-config deprecations plus a TestClient dependency
deprecation; neither was expanded into unrelated cleanup.

## Baseline before and after

| Metric | Before | After verification |
|---|---:|---:|
| Existing compatibility health | 2.297 ms live sample | Contract retained by passing legacy test |
| Liveness | Nginx returned 404 | In-process request below 10 ms; route/proxy config added |
| Chat sample | Not safely runnable without user credential | Mock failure/fallback test below 5 ms, excluding external latency |
| Small upload sample | Not safely runnable without user credential | Mock filesystem/Dify-failure test 20 ms |
| Dify chat timeout | 120 s | 120 s (unchanged) |
| Dify retrieval/list/delete timeout | 30 s | 30 s (unchanged) |
| Dify document-upload timeout | 180 s | 180 s (unchanged) |
| Backend workers | 1 Uvicorn process | 1 (unchanged) |
| Application upload limit | 500 MiB | 500 MiB (unchanged) |
| Nginx upload limit | 500 MiB | 500 MiB (unchanged) |

Idle container snapshot before changes:

| Service | CPU | Memory |
|---|---:|---:|
| Backend | 0.20% | 111.2 MiB |
| Frontend | 0.00% | 103.3 MiB |
| Nginx | 0.00% | 12.24 MiB |
| PostgreSQL | 0.00% | 25.08 MiB |

The after-deployment live baseline is intentionally pending because this task did not
authorize/restart production. Capture the same metrics immediately after rollout.

## Not run and remaining risks

- No authenticated live login, Chat, or Upload request was made; no test credential was
  supplied and production data must not be mutated. Equivalent safety behavior is
  covered with mocks, but end-to-end acceptance remains pending.
- Existing legacy admin mutation tests are not isolated from a dedicated test database.
- There is no Git metadata, so ownership of pre-existing modifications is unknowable.
- The active PostgreSQL volume depends on a recovery overlay absent from older deployment
  instructions. Omitting it can attach a different volume and make data appear missing.
- Upload and Chat remain synchronous and tightly coupled to external dependencies by
  design for this phase.
- Local filesystem storage remains a single-VPS durability/scaling risk.
- A Dify timeout is currently caught by the broader transport-error branch in the Chat
  route, so timeout response classification may be `502` rather than the intended `504`.
  Changing that public error behavior is deferred for owner review.
- Full-project lint debt and Pydantic deprecations remain.
- Experimental Compose definitions contain development-only defaults and must never be
  used for production.
- The production app currently exposes incomplete prototype routes at `/api/v1/users`
  and `/api/v1/documents/*`. The document routes contain mock storage/auth behavior and
  the user-create route is separate from the guarded admin API. Frontend source does not
  call them, but external consumers cannot be ruled out. Removing or securing them would
  change public API behavior, so this phase documents the issue and requires an owner
  decision before a dedicated security change.

## Safe deployment steps

Do not execute these steps until the owner has reviewed the diff in a real Git worktree.
Use both production files because the active environment uses the external restore
volume overlay.

1. Record current container image IDs and copy the changed source/config files to a
   timestamped rollback directory outside the release tree.
2. Add `CORS_ALLOWED_ORIGINS` to `infrastructure/.env` using exact public origins, comma
   separated and without trailing slashes. Do not put this value in frontend code.
3. Validate without exposing rendered environment values:

   ```bash
   cd /opt/ideation-project
   docker compose -f infrastructure/docker-compose.prod.yml \
     -f infrastructure/docker-compose.restore-volume.yml config -q
   docker exec infrastructure-nginx-1 nginx -t
   docker exec infrastructure-backend-1 alembic current
   ```

4. Build only application images. Do not use `down`, `-v`, `prune`, or any volume
   command:

   ```bash
   docker compose -f infrastructure/docker-compose.prod.yml \
     -f infrastructure/docker-compose.restore-volume.yml build backend frontend
   ```

5. Recreate application services while retaining PostgreSQL and its volume:

   ```bash
   docker compose -f infrastructure/docker-compose.prod.yml \
     -f infrastructure/docker-compose.restore-volume.yml up -d --no-deps backend frontend
   docker compose -f infrastructure/docker-compose.prod.yml \
     -f infrastructure/docker-compose.restore-volume.yml up -d --no-deps nginx
   ```

6. Verify `/health/live`, `/health/ready`, `/api/v1/health`, an allowed/disallowed CORS
   preflight, unauthenticated admin rejection, then owner-supplied authenticated login,
   small upload, and chat. Capture Docker stats and latency again.

## Rollback

1. If readiness fails, stop rollout; do not restart PostgreSQL and do not touch volumes.
2. Restore the timestamped copies of the changed files and retag the recorded pre-phase
   backend/frontend image IDs to their Compose image names.
3. Validate Nginx and Compose with both files.
4. Recreate only `backend`, `frontend`, and `nginx` using `--no-build --no-deps`.
5. Verify compatibility health, authentication guard, login, upload listing, and chat.

No database rollback is required because phase 1 adds no migration and changes no data.
If a deployment tool unexpectedly proposes a database or volume replacement, abort.

## Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| Current production service identified | PASS | Docker labels and running containers |
| FastAPI declared official Core API | PASS | Authoritative architecture document |
| Target components kept out of production | PASS | No activation or Compose production addition |
| No schema/public business API change | PASS | No migration; existing routes retained |
| Production CORS fail-closed | PASS | Parser, startup validation, and tests |
| Allowed/disallowed/multiple/trim/empty/wildcard tests | PASS | Phase 1 suite |
| Frontend same-origin `/api/v1` | PASS | Both API clients and Docker build arg |
| Separate liveness/readiness | PASS | Unit/integration tests and aligned config |
| Dify/Weaviate excluded from liveness | PASS | Probe implementation |
| Upload/hash/Dify/chat fallback guards | PASS | Mock-backed phase 1 suite |
| Admin access guard | PASS | Live `401` and automated test |
| Prototype-route access decision | FAIL | Public-contract/security owner decision required |
| Compose/Nginx validation | PASS | Config checks and `nginx -t` |
| Full authenticated live regression | FAIL | No safe credential/test database supplied |
| Git baseline and reviewable diff | FAIL | Workspace has no `.git` metadata |
| Live after-deployment baseline | FAIL | Production was intentionally not restarted |

## Phase 2 candidates (not implemented)

After explicit owner approval: isolate integration tests in a disposable database;
resolve Dify timeout classification; define durable object storage/migration strategy;
then separately review background ingestion, messaging/outbox, AI Gateway, streaming,
and tenant isolation. None of these changes belongs to phase 1.

## Rollout preflight and acceptance update — 2026-08-16

### Runtime inventory before restart

No restart occurred. The active Compose project contained exactly four application
containers:

| Service | Runtime image | State before stop | Restarts |
|---|---|---|---:|
| Backend | `infrastructure-backend` | running | 0 |
| Frontend | `infrastructure-frontend` | running | 0 |
| Nginx | `nginx:1.27-alpine` | running | 0 |
| PostgreSQL | `postgres:15-alpine` | running, healthy | 0 |

Docker labels confirm that every active container was created from the required pair:
`docker-compose.prod.yml` plus `docker-compose.restore-volume.yml`. The production file
was not run alone.

### PostgreSQL mount reconciliation

`docker inspect` reported one PostgreSQL data mount:

| Property | Active container | Merged Compose (resolved) | Result |
|---|---|---|---|
| Type | named volume | named external volume | match |
| Source | `infrastructure_pg_data` | `infrastructure_pg_data` | match |
| Target | `/var/lib/postgresql/data` | `/var/lib/postgresql/data` | match |
| Writable | yes | yes | match |

Compose internally refers to the logical source as `postgres_data`; the restore overlay
resolves its external physical name to `infrastructure_pg_data`. Comparing the logical
name directly would be incorrect. After resolving `volumes.postgres_data.name`, the
active and merged mounts match exactly. No new volume was created or attached.

The merged configuration was generated as JSON in a temporary file, reviewed without
printing its environment section, and passed `config -q`. Its safe summary contained
only `backend`, `frontend`, `nginx`, and `postgres`; PostgreSQL retained the external
volume above, Backend retained the storage bind, and Nginx retained its read-only config
and certificate binds.

### Backup and rollback evidence

- Two PostgreSQL custom-format dumps were structurally validated with PostgreSQL 15
  `pg_restore --list`: both passed (47 and 616 TOC entries).
- The latest PostgreSQL-volume archive from 2026-08-15 passed both `gzip -t` and a full
  `tar -tzf` listing (1,342 entries).
- A transaction declared `READ ONLY` successfully confirmed a non-null current database,
  8 public tables, and one Alembic version row. No row contents or identifiers were read.
- The exact current Backend, Frontend, and Nginx image IDs remain present locally.

This proves a database rollback artifact is readable, but it is not a full restore drill:
restoring it would require a separate volume/database and was intentionally not attempted.
Application rollback is also not fully drillable because this workspace has no Git
metadata or verified pre-phase-1 copies of the changed Compose/Nginx files. That alone
prevents an exact, reviewed application-config rollback and remains a rollout blocker.

### CORS preflight

Values and hostnames were intentionally not printed.

| Check | Result |
|---|---|
| Active origin list parses as production-safe | **FAIL** — development/loopback origins |
| Active origins match configured public application origin | **FAIL** |
| `infrastructure/.env` contains required `CORS_ALLOWED_ORIGINS` key | **FAIL** |
| Current runtime rejects a controlled unlisted origin | **FAIL** — preflight returned `200` with allow-origin and credentials headers |

Deploying the new Backend with the current environment would fail startup by design.
No origin was guessed and no environment file was modified.

### Unauthenticated route preflight

Only non-mutating or deliberately invalid requests were sent. No valid create/finalize
payload was submitted.

| Request without JWT | Response | Result |
|---|---:|---|
| `POST /api/v1/documents/upload-sessions` | `200` | **FAIL / immediate stop** |
| `POST /api/v1/documents/finalize` with empty JSON | `422` | Not authenticated; invalid payload stopped execution |
| `POST /api/v1/users` with empty JSON | `422` | Not authenticated; invalid payload stopped execution |
| `GET /api/v1/admin/users` | `401` | PASS |
| `GET /api/v1/auth/me` | `401` | PASS |

OpenAPI declares no security requirement for `POST /api/v1/users` or either
`/api/v1/documents/*` operation. Source inspection confirms no JWT dependency on these
routes; `documents/finalize` supplies a mock user identifier to its write-oriented use
case. A valid payload was not attempted because it could mutate production data.

### Commands reviewed before any restart

The only approved Compose prefix is:

```bash
docker compose \
  -f /opt/ideation-project/infrastructure/docker-compose.prod.yml \
  -f /opt/ideation-project/infrastructure/docker-compose.restore-volume.yml
```

Had every preflight passed, the intended minimal rollout would have rebuilt/recreated
only Backend and Frontend and reloaded/recreated Nginx; PostgreSQL would remain untouched:

```bash
docker compose \
  -f /opt/ideation-project/infrastructure/docker-compose.prod.yml \
  -f /opt/ideation-project/infrastructure/docker-compose.restore-volume.yml config -q

docker compose \
  -f /opt/ideation-project/infrastructure/docker-compose.prod.yml \
  -f /opt/ideation-project/infrastructure/docker-compose.restore-volume.yml build backend frontend

docker compose \
  -f /opt/ideation-project/infrastructure/docker-compose.prod.yml \
  -f /opt/ideation-project/infrastructure/docker-compose.restore-volume.yml up -d --no-deps backend frontend

docker compose \
  -f /opt/ideation-project/infrastructure/docker-compose.prod.yml \
  -f /opt/ideation-project/infrastructure/docker-compose.restore-volume.yml up -d --no-deps nginx
```

These commands were **displayed/reviewed but not executed**. An exact rollback command
set cannot honestly be approved until a Git commit or verified pre-rollout copies of all
changed source/config files exist. The safe rollback sequence would then be: restore the
verified files, retag the recorded old Backend/Frontend images, validate merged Compose
and Nginx, and recreate only those three application services with `--no-build --no-deps`.
PostgreSQL and `infrastructure_pg_data` must not be recreated, detached, or restored
unless a separate database recovery procedure is explicitly authorized.

### Preflight baseline (not post-rollout)

Because rollout stopped, no post-rollout baseline exists.

| Metric | Current unchanged runtime |
|---|---:|
| `/api/v1/health` | 5.912 ms, HTTP 200 |
| `/health/live` | 13.838 ms, HTTP 404 (new config not loaded) |
| `/health/ready` | 10.836 ms, HTTP 404 (new config not loaded) |
| Backend CPU / RAM | 0.25% / 112.3 MiB |
| Frontend CPU / RAM | 0.00% / 103.6 MiB |
| Nginx CPU / RAM | 0.00% / 12.66 MiB |
| PostgreSQL CPU / RAM | 0.00% / 26.31 MiB |
| Actual Uvicorn worker processes | 1 |

The previous 30 minutes of logs contained 24 Backend lines and 35 Nginx lines, with no
matched error/exception, stack-trace, or obvious secret-bearing header/key patterns.
Frontend and PostgreSQL produced no lines in that interval. This was a pattern scan;
log content was not copied into the report.

### Rollout acceptance matrix

| Criterion | Status | Evidence/action |
|---|---|---|
| Active services/images inventoried | PASS | Docker inspect before restart |
| Active PostgreSQL mount identified | PASS | External named volume and target documented |
| Merged Compose generated/validated | PASS | Sanitized review plus `config -q` |
| Merged mount equals active mount | PASS | Effective external name resolved; exact match |
| Production Compose not run alone | PASS | No deployment command executed |
| No volume replacement/destructive operation | PASS | No volume operation performed |
| Backup artifact readable | PASS | Dump TOCs and volume archive validated |
| Full rollback drill | PENDING | No Git baseline; no isolated restore target |
| Production CORS configured and valid | FAIL | Loopback-only active value; required env key absent |
| Sensitive prototype routes require JWT | FAIL | Unauthenticated upload-session returned 200 |
| Exact application rollback approved | FAIL | Verified pre-phase-1 source/config copies unavailable |
| Minimal application rollout | PENDING | Stopped before build/restart |
| Liveness after rollout | PENDING | No rollout |
| Readiness/PostgreSQL continuity after rollout | PENDING | No rollout |
| Allowed/disallowed CORS after rollout | PENDING | No rollout |
| Authenticated Login | PENDING | No safe test credential supplied; no guessing/creation |
| Small Upload and duplicate detection | PENDING | No rollout and no safe credential |
| Sample Chat and controlled fallback | PENDING | No rollout and no safe credential |
| Post-rollout logs and baseline | PENDING | No rollout |
| No migration/schema change | PASS | No migration command or file change during rollout attempt |

### Required owner actions before another rollout attempt

1. Decide and approve the public-contract/security treatment of `/api/v1/users` and
   `/api/v1/documents/*`; every state-changing route must reject missing JWT before a
   valid payload reaches its use case.
2. Provide/confirm the real production origin and persist it as
   `CORS_ALLOWED_ORIGINS` without exposing it in reports.
3. Restore this project to a real Git worktree (or provide verified pre-phase-1 file
   copies) so the application rollback can be exact and reviewable.
4. Provide a dedicated safe test credential if authenticated Login/Upload/Chat smoke
   tests are required. No production user will be created or guessed.

No phase 2 work was started.

## Owner-approved security continuation and controlled rollout

Window: 2026-08-16T07:28Z–07:39Z  
Final status: **ROLLED BACK / PHASE 1 NOT READY FOR OWNER APPROVAL**

### Route-by-route decision

Call-sites were derived from the real Frontend source. No Frontend reference to
`/api/v1/users` or `/api/v1/documents/*` exists. The Upload UI calls
`/api/v1/uploads`; user administration calls `/api/v1/admin/users`.

| Route | Owning file/router | Classification | Frontend | Methods | Approved auth/role | Data/ownership | Source/OpenAPI result |
|---|---|---|---|---|---|---|---|
| `/api/v1/users` | `modules/users/presentation/routers.py` | prototype | no | POST | development opt-in + super_admin | creates users | absent in Production; Bearer security when development-enabled |
| `/api/v1/documents/upload-sessions` | `modules/documents/presentation/routers.py` | mock prototype | no | POST | development opt-in + authenticated | returns mock upload session | absent in Production; Bearer security when development-enabled |
| `/api/v1/documents/finalize` | same documents router | prototype | no | POST | development opt-in + authenticated | write use case uses authenticated user ID | absent in Production; Bearer security when development-enabled |
| `/api/v1/uploads` | `api/v1/uploads.py` | Production | yes | GET, POST | authenticated; super_admin may see all | normal users list/create only their records | Bearer security retained |
| `/api/v1/uploads/{id}` | same | Production | yes | DELETE | owner or super_admin | repository query scopes by `uploaded_by` | Bearer security retained |
| `/api/v1/uploads/{id}/categories` | same | Production | yes | PUT | owner or super_admin | repository query scopes by `uploaded_by` | Bearer security retained |
| `/api/v1/admin/users*` | `api/v1/admin.py` | Production | yes | GET, POST, PATCH, DELETE | super_admin only | user administration | Bearer security retained |
| `/api/v1/chat/messages` | `api/v1/chat.py` / `ChatService` | Production | yes | POST | authenticated | normal-user document query scoped by uploader; super_admin may query all | Bearer security retained |

`ENABLE_PROTOTYPE_ROUTES` defaults to false and Production Compose forces it to the
literal string `false`. The settings property also refuses to enable prototype routers
outside local/development/dev/test, so a Production environment override cannot
accidentally activate them. Physical prototype code was not deleted.

### Security and CORS changes prepared

- Prototype routers are absent from the Production app and return 404.
- In explicit development mode, prototype user administration uses the existing
  `require_super_admin`; prototype document routes use the existing `get_current_user`.
- The mock document finalize user ID was replaced with the authenticated user ID.
- Upload hash lookup, list, delete, and category updates are scoped by uploader for a
  normal user. Super-admin list/mutation access remains explicit.
- Normal-user Chat document selection includes an uploader predicate.
- Production env contains exactly one approved origin:
  `https://idea.haminyar.ir`; no trailing slash, wildcard, localhost, alternate scheme,
  `www`, or lookalike is accepted by the new image.
- Frontend call-sites and public request/response shapes were not changed.

### Automated preflight results

| Check | Result |
|---|---|
| Phase-1 safety/security plus safe legacy health | PASS — 29 tests |
| Missing JWT | PASS — 401 |
| Invalid/expired JWT | PASS — 401 |
| Normal user on user administration | PASS — 403 |
| Super-admin user administration | PASS — automated mock returns 200 |
| Prototype routes in Production | PASS — 404/absent from OpenAPI |
| Prototype routes in development | PASS — explicit enable plus Bearer security |
| Upload mutation ownership | PASS — actor-scoped repository call |
| Chat document ownership | PASS — uploader predicate in generated query |
| Exact allowed CORS | PASS |
| Wrong scheme, `www`, localhost, lookalike, trailing slash | PASS — rejected |
| Ruff on all changed Python files | PASS |
| Backend image build/startup/liveness | PASS |
| Frontend build, lint and typecheck | PASS |
| Production+restore merged Compose | PASS |
| Nginx syntax | PASS |
| Alembic current equals local head | PASS — `20260721_0002` |
| New migration | PASS — none |

The two old smoke tests that mutate users were not pointed at Production. Their required
security behavior is covered by the new isolated/mock tests and the controlled temporary
account below.

### Rollback artifacts created before build

Secure snapshot directory (mode 700):
`/var/backups/ideation-phase1/rollout-20260816T072839Z`

Environment/merged configuration/inventory files inside it are mode 600 and are not in
the project tree. Their contents were not printed.

| Component | Pre-rollout immutable local tag | Image ID |
|---|---|---|
| Backend | `ideation-rag-backend:pre-phase1-rollout-20260816t072839z` | `sha256:16ee76aeb355d036db52958c190f437e66038472526d7d1d9ec8397483bc068d` |
| Frontend | `ideation-rag-frontend:pre-phase1-rollout-20260816t072839z` | `sha256:42f02e1c90222feedec9419d57666a3c1cb558674830737aedd26459a26e0e1a` |
| Nginx | `ideation-rag-nginx:pre-phase1-rollout-20260816t072839z` | `sha256:6769dc3a703c719c1d2756bda113659be28ae16cf0da58dd5fd823d6b9a050ea` |

The rollback override was merged and validated before rollout. It selects these images,
uses the compatibility Backend health endpoint, restores the pre-rollout Nginx runtime
configuration, and still resolves PostgreSQL to the external volume
`infrastructure_pg_data` at `/var/lib/postgresql/data`.

### Controlled rollout result

1. Backend was recreated first and became healthy. Liveness, readiness, and compatibility
   health returned 200. PostgreSQL container ID/start time/mount were unchanged.
2. Frontend was recreated and `/login` returned 200. PostgreSQL remained unchanged.
3. Nginx needed an explicit restart because changing bind-mounted file content does not
   change Compose metadata. After restart both health aliases returned 200.
4. Live security checks passed: prototype routes 404; Upload/Admin without JWT 401;
   invalid JWT 401; exact CORS accepted and four controlled alternatives rejected.

A single transient Nginx upstream connection error occurred while Frontend was being
recreated. The subsequent page check was stable and no secret signal was found.

### Smoke test and failure

A temporary least-privilege `user` was created through `UserService`, the same official
application service used by the Admin API. Its password and token were generated/stored
only under a mode-700 runtime directory with mode-600 files and were never printed.

| Smoke operation | Result | Latency |
|---|---|---:|
| Login | PASS — 200, returned identity/role matched | 337.599 ms |
| `/auth/me` | PASS — 200 | not separately timed |
| user administration as normal user | PASS — 403 | not separately timed |
| prototype route with valid JWT | PASS — 404 | not separately timed |
| Create temporary category | PASS — 200 | 123.749 ms |
| Upload small non-sensitive text file | PASS — 200, indexed | 661.159 ms |
| Duplicate same file | **FAIL — 500** | not retained after failure |
| Chat | PENDING — immediate stop occurred first | — |

The duplicate request reached the existing duplicate branch and attempted to assign the
`categories` relationship on an Upload returned without eager-loaded categories. That
triggered SQLAlchemy asynchronous lazy I/O and `MissingGreenlet`. The response itself was
a sanitized generic error, but the Backend correctly logged the server-side stack trace.
This is an explained defect; nevertheless Upload regression is an explicit stop/rollback
condition, so no live fix or rollout retry was attempted.

### Test data and credential cleanup

| Artifact | Identifier | Final state |
|---|---|---|
| Temporary user | `3daabf15-572a-4fcd-8251-407214bf9e84` | retained inactive; Login and existing JWT both return 401 |
| Temporary category | `b594d66c-5a77-4e7f-a006-6f1c61f2a786` | deleted through API, 204 |
| Temporary Upload | `67bbd129-1850-4bf8-a53c-bb589c24a63e` | deleted through API, 204; service uses soft-delete metadata behavior |
| Duplicate | same Upload ID intended | failed before creating a second record |
| Chat/log data | none | no Chat request executed |

The original file/Dify document deletion completed via the official Upload API. Runtime
password, Login response, and token files were securely removed after the account was
disabled. The remaining smoke runtime directory was removed. The active deployment env
file was found with overly broad mode `666` and tightened to `600` without reading or
changing any secret value. No permanent super-admin was created.

### Executed rollback drill

The prevalidated command was executed immediately after the duplicate failure:

```bash
docker compose \
  -f infrastructure/docker-compose.prod.yml \
  -f infrastructure/docker-compose.restore-volume.yml \
  -f /var/backups/ideation-phase1/rollout-20260816T072839Z/docker-compose.rollback.yml \
  up -d --no-build --no-deps backend frontend nginx
```

Rollback completed in **24.1 seconds**. The three application services returned to the
recorded image IDs, compatibility health and Login page returned 200, and PostgreSQL
kept the same container, start time, external volume, eight-table count, and Alembic
head row. No database restore was needed.

### Final rolled-back runtime

The runtime is intentionally back on pre-Phase-1 application images. Consequently the
source/env fixes are **not active**: the prototype upload-session route again returns
200 without JWT and the old Backend accepts an unlisted CORS origin. This is expected
rollback behavior but means Production is not security-accepted.

Current rolled-back idle snapshot:

| Service | CPU | RAM |
|---|---:|---:|
| Backend | 0.29% | 69.63 MiB |
| Frontend | 0.00% | 33.86 MiB |
| Nginx | 0.00% | 5.766 MiB |
| PostgreSQL | 0.00% | 26.5 MiB |

Backend has one Uvicorn worker. Dify reported `ok`; the Weaviate container was running
with zero restarts. Final rolled-back service logs showed no error or obvious secret
patterns in the sampled interval.

### Files changed in this continuation

- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/app/modules/users/presentation/routers.py`
- `backend/app/modules/documents/presentation/routers.py`
- `backend/app/repositories/upload_repository.py`
- `backend/app/services/upload_service.py`
- `backend/app/api/v1/uploads.py`
- `backend/app/services/chat_service.py`
- `backend/app/api/v1/chat.py`
- `backend/tests/test_phase1_safety.py`
- `backend/tests/test_phase1_route_security.py`
- `backend/.env.example`
- `infrastructure/docker-compose.prod.yml`
- `infrastructure/docker-compose.offline.yml`
- `infrastructure/.env.prod.example`
- `infrastructure/.env` (deployment-only, contains no reported values beyond approved origin)
- `docs/architecture/current-production.md`
- `docs/phase-1-report.md`

No schema or migration file changed.

### Final acceptance matrix after rollback

| Criterion | Status | Evidence |
|---|---|---|
| Route/call-site analysis | PASS | Code-derived matrix above |
| Prototype fail-closed source implementation | PASS | Production cannot enable; tests/OpenAPI pass |
| Users administration authorization source/tests | PASS | 401/403/mock super-admin success |
| Upload/Chat ownership source/tests | PASS | Actor/uploader scoping tests |
| Exact Production CORS source/env/tests | PASS | One owner-approved origin |
| Reliable application rollback | PASS | Executed in 24.1 s |
| PostgreSQL mount/data continuity | PASS | Same container/external volume/read-only counts |
| No migration/schema change | PASS | Current=head; no new migration |
| Controlled rollout mechanics | PASS | Backend → Frontend → Nginx, PostgreSQL untouched |
| Login smoke | PASS | Temporary normal user, 200 |
| Upload smoke | PASS | Small file indexed, 200 |
| Duplicate detection smoke | **FAIL** | `500 MissingGreenlet` |
| Chat smoke | PENDING | Stopped before execution |
| Post-rollout baseline | PENDING | Rollout rolled back |
| Security controls active in Production | **FAIL** | Pre-Phase-1 runtime restored |
| Temporary credential revoked | PASS | User inactive; credential/token removed |
| Env/snapshot file permissions | PASS | Active env and snapshot files 600; directories 700 |
| Phase 1 ready for owner approval | **FAIL** | Duplicate defect must be fixed and rollout repeated |

The next action recorded at that rollback point was completed by the corrected
continuation below. The historical FAIL/PENDING values above describe the first attempt,
not the final runtime. No Phase-2 work started.

## Corrected duplicate rollout — final Phase-1 continuation

This section supersedes the rolled-back final state above. The owner explicitly approved
only the `MissingGreenlet` correction and controlled Phase-1 retry; no Phase-2 component
or architecture capability was activated.

### Confirmed root cause and minimal correction

The retained complete stack trace identified the exact failure at
`UploadService.create_upload`, on `existing.categories = categories`. SQLAlchemy's
collection setter first read the unloaded old relationship value, invoked the lazy
loader, and attempted asyncpg I/O outside `greenlet_spawn`. The failure occurred during
relationship assignment—not response serialization, validation, logging, commit, or
refresh. The application session uses `expire_on_commit=False`, so expiration was not
the cause.

`UploadRepository.get_by_hash` now applies `selectinload(Upload.categories)` only to the
duplicate lookup. `get_by_id` and `list_all` already used the same eager-loading strategy,
so no broad repository refactor was needed. The duplicate branch also commits when its
requested category association changes; its HTTP status, response schema, hash scope,
and indexing contract are unchanged. No sync I/O workaround or exception suppression
was added.

### Regression and pre-rollout validation

Tests ran against a disposable PostgreSQL 15 container backed by `tmpfs`, migrated to
the existing head, with a real `AsyncSession`. It was isolated from Production and was
removed after validation.

| Validation | Status | Evidence |
|---|---|---|
| New Upload / one record / one Dify call | PASS | Real AsyncSession plus mocked external Dify boundary |
| Duplicate with 0, 1, and 3 categories | PASS | Parametrized integration scenarios |
| Duplicate creates no new record | PASS | Per-owner row count remains one |
| Duplicate does not re-index | PASS | Dify mock call count remains one |
| Commit/refresh and list serialization | PASS | Categories remain available after explicit refresh |
| Category behavior | PASS | Requested associations persist and serialize in Upload listing |
| Query behavior | PASS | Duplicate path bounded to at most eight statements; no loop/N+1 |
| Cross-user scope and metadata isolation | PASS | Same bytes for another owner produce a distinct scoped record with no category leak |
| Full Backend suite | PASS | 35 passed; five pre-existing dependency/deprecation warnings |
| Prior security/compatibility tests | PASS | Included in the 35-test suite |
| Ruff on changed Python/tests | PASS | All checks passed |
| Backend Docker build | PASS | `sha256:dad55159359296c907610746335883b301c6145a2ca711cd39db3ed93a78c3f0` |
| Merged Production+restore Compose | PASS | `config --quiet`; safe fields inspected |
| Alembic current/head | PASS | Both `20260721_0002 (head)` |
| New migration/schema change | PASS | Zero new migration; existing four revision files unchanged |

### Rollback, mount, CORS, and pre-restart gate

- Active PostgreSQL inspect: named volume `infrastructure_pg_data` mounted read/write at
  `/var/lib/postgresql/data`.
- Merged Compose logical `postgres_data` resolves to the same external physical volume
  and target. The active/merged comparison is an exact match.
- PostgreSQL image, container start time (`2026-08-16T06:06:57Z`), restart count zero,
  and mount remained unchanged before, during, and after rollout.
- Existing validated PostgreSQL dump/archive evidence remains available; no restore was
  needed because no schema/data migration was performed.
- Production CORS is exactly `https://idea.haminyar.ir`; no secret value was printed.
- Active env is root-owned mode 600; rollback directory is mode 700 and files mode 600.
- All three immutable pre-Phase-1 tags and exact image IDs remain present. The rollback
  override revalidated with the same external PostgreSQL mount.
- The previous rollback drill completed successfully in 24.1 seconds. Rollback remains
  ready with the already documented three-file Compose command and does not include
  PostgreSQL.

### Executed rollout and final runtime

Only Backend was rebuilt. Backend and the already-built Phase-1 Frontend were recreated
with Production+restore Compose and `--no-deps`; Nginx alone was then recreated to load
the Phase-1 bind-mounted configuration. Application recreate took 11.251 seconds; the
full controlled sequence through Nginx readiness took approximately 55 seconds.

| Service | Final image ID | State |
|---|---|---|
| Backend | `sha256:dad55159359296c907610746335883b301c6145a2ca711cd39db3ed93a78c3f0` | healthy; zero restarts |
| Frontend | `sha256:f081cd1369e343efd251891af592e6d03bedc9d51e092321e58c6288f8819210` | running |
| Nginx | `sha256:6769dc3a703c719c1d2756bda113659be28ae16cf0da58dd5fd823d6b9a050ea` | running; config syntax valid |
| PostgreSQL | `sha256:5cce759a2777634ff1edd0d56b9241a2961deb7f72e125d4af6ae2928163a6b6` | healthy; original container/mount |

### Production smoke and baseline

The temporary account was created by `UserService` with a generated credential that was
never printed or persisted in the report. Cleanup used only official API/service paths.

| Check | Status | Result / baseline |
|---|---|---|
| Startup | PASS | Alembic no-op at head; Uvicorn startup complete; health became stable |
| Liveness | PASS | 7/7 HTTP 200; min 9.364 ms, median 12.306 ms, max 14.247 ms |
| Readiness | PASS | 7/7 HTTP 200; min 48.003 ms, median 53.662 ms, max 78.003 ms |
| Previous PostgreSQL/data | PASS | Read-only transaction: database present, 8 public tables, existing aggregate rows |
| Allowed CORS | PASS | 200 and exact allow-origin header |
| Disallowed CORS | PASS | 400 and no allow-origin header |
| Protected routes without JWT | PASS | Upload/Admin 401 |
| Production prototype routes | PASS | `/users` and both `/documents/*` probes return 404 |
| Login | PASS | 200 in 314.133 ms |
| Normal-user role boundary | PASS | `/auth/me` 200; `/admin/users` 403 |
| Small Upload | PASS | 200/indexed in 363.082 ms |
| Duplicate | PASS | 200 in 100.557 ms; same ID; one row; one category |
| No duplicate re-index | PASS | Live row/document ID unchanged; integration Dify call count exactly one |
| Category response | PASS | Upload listing contained the requested category after duplicate/commit |
| Chat | PASS | 200, answer present, one source, 8.194 s time-to-response |
| AI failure/fallback | PASS | One direct Weaviate connection warning; Dify retrieval and Chat both 200 |
| Dify / Weaviate | PASS | Dify container healthy; Weaviate readiness endpoint 200 |
| Backend resources | PASS | CPU 0.26%; RAM 74.2 MiB (0.93%) |
| Frontend resources | PASS | CPU 0.00%; RAM 35.3 MiB (0.44%) |
| Nginx resources | PASS | CPU 0.00%; RAM 7.434 MiB (0.09%) |
| PostgreSQL resources | PASS | CPU 0.00%; RAM 22.34 MiB (0.28%) |
| Backend workers | PASS | One Uvicorn worker |
| Log audit | PASS | 0 errors, tracebacks, MissingGreenlet, DetachedInstanceError, or secret markers |
| Repeating log errors | PASS | None; one explained fallback warning only |
| Test cleanup | PASS | Chat/Upload/Category API cleanup 204; account inactive; subsequent Login 401 |
| Residual active smoke data | PASS | Read-only aggregate checks: 0 active smoke users/uploads/chats/categories |

Upload deletion follows the application's existing soft-delete metadata contract; the
file and Dify document were deleted through the official Upload API. No credential or
JWT was written into either report.

### Final Phase-1 acceptance matrix

| Criterion | Status | Evidence |
|---|---|---|
| Duplicate detection | PASS | Integration and live duplicate smoke |
| All security tests | PASS | Automated suite plus live 401/403/404 probes |
| Production CORS | PASS | Exact allowed origin; controlled origin rejected |
| JWT / role / ownership | PASS | Tests and live least-privilege account |
| Prototype routing | PASS | Fail-closed and absent in Production |
| Login / Upload / Chat | PASS | All live operations successful |
| Duplicate without re-index | PASS | Same row/ID; external mock called once |
| Baseline | PASS | Latency, CPU, RAM, workers, dependencies, and logs recorded |
| Rollback | PASS | Prior 24.1-second drill; immutable tags/override revalidated |
| PostgreSQL and external volume unchanged | PASS | Same start time, zero restarts, exact mount |
| Migration/schema change | PASS | Zero; current equals head |
| Temporary credentials/data | PASS | Revoked/inactive and official cleanup verified |
| Corrected Phase-1 runtime active | PASS | Final image IDs and health verified |
| Owner final sign-off | PENDING | Awaiting explicit project-owner approval |
| Phase 2 | PENDING | Intentionally not started; prohibited until owner approval |

### Files changed in the corrected retry

- `backend/app/repositories/upload_repository.py`
- `backend/app/services/upload_service.py`
- `backend/tests/test_upload_duplicate_integration.py`
- `docs/phase-1-report.md`
- `docs/architecture/current-production.md`

Technical rollout and acceptance for Phase 1 are complete. No Phase-2 work has started;
the task must remain at the owner-approval boundary.

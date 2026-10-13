# Current production architecture (phase 2)

Status: **authoritative for the current production path**  
Scope: Phase 1 stabilization plus Phase 2 observability; no Phase 3 rollout

Deployment note (2026-08-16): the corrected Phase-1 runtime is active. Prototype routes
are disabled in Production, Upload/Chat ownership controls are active, strict CORS is
active, and the duplicate regression has passed both integration and Production smoke
tests. Phase-2 request correlation, structured logs, bounded application metrics, and
internal Prometheus are active. PostgreSQL remains on its original external volume with
no schema migration.

## Runtime decision

- The existing Python FastAPI application in `backend/` is the official production
  Core API. The `core-api/` directory is not the production Core API.
- PostgreSQL is the system of record for users, metadata, categories, upload records,
  and chat history.
- Dify is the current AI orchestrator and knowledge-base API. Weaviate is the current
  retrieval dependency. FastAPI accesses both directly today.
- Upload processing is synchronous in the API request: validate, stream to local
  storage, calculate SHA-256, detect duplicates, write metadata, and call Dify.
- `core-api/`, `ai-gateway/`, and `ingestion-worker/` must not enter the production
  request path until their later phases are explicitly completed and accepted.

The target ADRs describe future intent, not deployed behavior. They do not override
this document during Phases 1–2.

## Component status

| Component | Status | Evidence/role |
|---|---|---|
| `backend/` | production | FastAPI served by Uvicorn as Compose service `backend` |
| `core-api/` | incomplete | Security/test fragments only; no runnable package; not active |
| `ai-gateway/` | experimental | Runnable NestJS prototype; not active in production Compose |
| `ingestion-worker/` | experimental | Celery prototype; not active in production Compose |
| `frontend/` | production | Next.js service behind Nginx |
| PostgreSQL | production | Primary application metadata store |
| Dify | production dependency | Chat and document-indexing API |
| Weaviate | production dependency | Direct retrieval with Dify retrieval fallback |

## Production request paths

Nginx routes `/api/*` to FastAPI and all other application paths to Next.js. It also
exposes `/health/live` and `/health/ready` as narrow aliases to the FastAPI probes.
The frontend API clients use the same-origin base path `/api/v1`.

Primary FastAPI routes are:

| Methods | Path |
|---|---|
| `POST` | `/api/v1/auth/login` |
| `GET`, `PATCH` | `/api/v1/auth/me` |
| `GET`, `POST` | `/api/v1/admin/users` |
| `PATCH`, `DELETE` | `/api/v1/admin/users/{user_id}` |
| `GET`, `DELETE` | `/api/v1/admin/system-logs` |
| `GET`, `DELETE` | `/api/v1/admin/log-files/{filename}` |
| `GET`, `POST` | `/api/v1/uploads` |
| `DELETE` | `/api/v1/uploads/{upload_id}` |
| `PUT` | `/api/v1/uploads/{upload_id}/categories` |
| `GET`, `POST` | `/api/v1/categories` |
| `PUT`, `DELETE` | `/api/v1/categories/{category_id}` |
| `POST` | `/api/v1/chat/messages` |
| `GET` | `/api/v1/chat/history` |
| `DELETE` | `/api/v1/chat/history/{session_id}` |
| `POST` | `/api/v1/logs/frontend` |
| `GET` | `/api/v1/health`, `/api/v1/health/live`, `/api/v1/health/ready`, `/api/v1/health/dify` |
| `GET` | `/health/live` (compatibility route) |

The frontend's real Upload UI calls `/api/v1/uploads`. The parallel prototype
`/api/v1/documents/*` and `/api/v1/users` routes are fail-closed in Production and return
404 because `ENABLE_PROTOTYPE_ROUTES=false`; they may only be explicitly enabled in an
approved non-Production environment.

### Upload

`POST /api/v1/uploads` authenticates the user, validates extension and configured size,
streams the file to a temporary local file while calculating SHA-256, checks PostgreSQL
for a non-deleted matching hash, then stores metadata and synchronously asks Dify to
index non-audio documents. A Dify indexing failure leaves the local file and metadata
with `indexing_failed`; it does not move work to a background queue.

### Chat

`POST /api/v1/chat/messages` authenticates the user and resolves selected-category
documents in PostgreSQL. Retrieval goes directly to Weaviate and falls back to Dify's
dataset retrieval API. The answer call goes to Dify. Defined recoverable Dify HTTP
errors fall back to a response assembled from retrieved knowledge chunks. Chat history
is stored in PostgreSQL.

## Capability ownership

| Capability | Current owner |
|---|---|
| Authentication | FastAPI `auth` routes/services and PostgreSQL |
| Users | FastAPI user/admin routes/services and PostgreSQL |
| Uploads | FastAPI upload service (synchronous) |
| Categories | FastAPI category routes/repository and PostgreSQL |
| Chat | FastAPI chat route/service; Dify for answer orchestration |
| Retrieval | FastAPI `DifyService`; Weaviate primary, Dify API fallback |
| File Storage | VPS filesystem mounted at `UPLOAD_STORAGE_PATH` |
| Observability | FastAPI request IDs/JSON logs/metrics and internal Prometheus; no public metrics UI |

The prototype `/api/v1/users` and `/api/v1/documents/*` routes are not assigned
production capability ownership and are not registered in the Production runtime.

## Probe semantics

- Liveness checks only the FastAPI process and never Dify, Weaviate, or PostgreSQL.
- Readiness checks PostgreSQL with a bounded query and returns only an aggregate state.
- Dify is reported separately as an optional/degraded dependency and cannot trigger a
  backend restart loop.

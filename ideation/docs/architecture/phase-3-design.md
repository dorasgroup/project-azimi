# Phase 3 pre-implementation design

Date: 2026-08-16  
Status: approved Phase-3 implementation design; Phase 4 is excluded.

## Current findings

- Production Upload is synchronous: it atomically stores the file, creates `uploads`,
  calls the real Dify file API in the HTTP request, then commits.
- `ingestion_jobs` already exists and currently has zero rows. Its existing fields are
  sufficient: `created_at` is the queue timestamp; `started_at`, `completed_at`, and
  `failed_at` cover lifecycle timestamps. No migration is required.
- The legacy `ingestion-worker/` is a target-architecture mock. It uses mock parsing,
  mock Redis locking, a mock Dify adapter, a MinIO-shaped payload, and no Production
  PostgreSQL model. It will remain in the repository but will not enter Production.
- The Production worker will use the Backend package/model, PostgreSQL, shared local
  storage, and the existing real `DifyService` adapter.

## API contract

With `ASYNC_INGESTION_ENABLED=false`, the existing 200 synchronous contract is retained.
With it enabled, a new non-audio upload returns 202 with `id`, `ingestion_job_id`,
`status`, `status_url`, `request_id`, filename, and a safe message. An indexed duplicate
returns the existing Upload with 200 and creates neither a Job nor a Dify request. An
active duplicate returns its existing Job with 202.

Authenticated endpoints:

- `GET /api/v1/uploads/{upload_id}/ingestion`: owner or super-admin status.
- `POST /api/v1/uploads/{upload_id}/ingestion/retry`: owner or super-admin; only
  `failed` or `enqueue_failed`; guarded atomically.

Non-owners receive the existing policy's 404 response. No response contains a storage
path, broker URL, exception text, credentials, task ID, or provider identifier.

## State machine

Job states: `queued`, `processing`, `indexed`, `retrying`, `failed`,
`enqueue_failed`. Upload mirrors the high-level state. Valid transitions are:

```text
queued -> processing -> indexed
                     -> retrying -> processing
                     -> failed
retrying -----------------------> failed
enqueue_failed -> queued
failed -> queued (explicit authorized retry only)
```

Claims use a conditional PostgreSQL `UPDATE ... RETURNING`, so only one worker can move
an eligible Job to `processing`. Completion/failure updates Job and Upload within one
transaction. Invalid transitions are rejected and logged without identifiers beyond the
correlation ID.

## Queue contract and topology

- RabbitMQ Docker Official Image 3.13.7 pinned by immutable RepoDigest; internal ports
  only; dedicated durable volume. Docker Hub blocked the newer 4.3.4 pull from this VPS
  with a registry-level regional 403, so upgrading the pinned broker image remains an
  explicit operational follow-up rather than substituting an untrusted mirror.
- Durable direct exchange `ideation.ingestion.v1`, durable queue
  `ideation.ingestion.jobs.v1`, routing key `ingestion.process.v1`.
- Celery task name `ideation.ingestion.process.v1`, JSON only, result backend disabled,
  persistent delivery, late acknowledgement, lost-worker rejection, prefetch 1.
- Task positional payload contains only `job_id`. Headers carry `schema_version=1`, a
  validated request ID, and an enqueue UTC timestamp. No file bytes/content/path,
  filename, user ID, Upload ID, category ID, or secret enters RabbitMQ.
- One worker process is the conservative initial concurrency because Dify indexing is a
  long external operation, Production traffic is currently low, and it prevents Dify
  pressure while preserving over 4GiB host RAM headroom. Metrics determine later tuning.

## Storage and concurrency

Backend and worker both mount `../storage` at `/app/storage`. Files are streamed to a
temporary file, flushed, and atomically renamed before database commit and publication.
The worker resolves the stored path below `/app/storage/uploads`, rejects traversal and
symlinks, and never removes a file after processing failure.

Concurrent identical uploads are serialized by a PostgreSQL transaction-scoped advisory
lock derived from the authenticated user plus SHA-256, followed by the existing
owner-scoped duplicate query. This avoids a schema migration and produces at most one
active Upload/Job per owner/hash.

## Publish gap and retry

The Upload/Job transaction commits before broker publication. Publisher confirm success
leaves the Job queued. A known publish failure is recorded as `enqueue_failed` in a new
transaction and returned as a recoverable response. The retry endpoint changes it back
to queued atomically and republishes.

There is intentionally no Transactional Outbox in Phase 3. A process crash between
database commit and publish can leave a queued row without a message. Likewise, Dify can
succeed just before a worker crash, allowing an at-least-once redelivery to repeat the
external call. These are explicit Phase-4 reconciliation/idempotency risks; this design
does not claim exactly-once delivery.

Transient Dify timeouts, transport failures, 429, and 502/503/504 use at most three total
attempts with exponential backoff and jitter. Missing/unsafe files, invalid Job data,
provider authentication/configuration errors, and permanent 4xx responses fail without
automatic retry. Stored error codes are bounded and messages are sanitized.

## Rollout and rollback

Rollout order: validate backup/mount/config; start RabbitMQ; start real worker with an
empty queue; deploy Backend with the flag false; regressions; deploy compatible
Frontend; explicitly enable the flag; canary; failure/load tests; 30-minute observation.
PostgreSQL is never recreated or restarted.

Rollback first disables the flag, records active Job/queue state, allows an active task
bounded time to finish, restores Phase-2 Backend/Frontend images, then gracefully stops
worker and RabbitMQ without deleting their volume. The additive runtime data remains;
there is no schema downgrade and no queued file or Job deletion.

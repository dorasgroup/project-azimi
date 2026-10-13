# Ideation RAG

## Repository layout and production secrets

This directory contains the source of the ideation application exported from
the production server on 2026-10-13. It lives under `ideation/` in the shared
`project-azimi` repository; sibling projects are independent.

The export includes application code, tests, migrations, deployment definitions,
fonts and documentation. Backups, runtime storage, uploaded articles/documents,
database contents, logs, dependency/build caches, TLS material, operational `.env`
files and one-off server maintenance scripts are intentionally excluded.
Database migrations remain under `backend/alembic/`.

The `dify/docker/` directory contains deployment definitions, not a database or
knowledge-base export and not the upstream Dify source tree. Dify applications,
model credentials and indexed content remain on the server.

Copy the relevant `.env.example` files to local environment files and configure
all credentials out of band. `REPLACE_WITH_LOCAL_SECRET` values are deliberate
placeholders, never usable production credentials. The experimental GapGPT
adapter requires `GAPGPT_API_KEY` from the environment; no embedded key is shipped.
Do not commit real credentials or copy production volumes into this repository.

Transferring this source does not redeploy, stop or remove the running application.

The production application is a VPS-oriented stack: Nginx, Next.js, FastAPI,
PostgreSQL, internal Prometheus, RabbitMQ, and a Celery ingestion worker. Dify and
Weaviate remain the AI/RAG dependencies. The authoritative description is
[`docs/architecture/current-production.md`](docs/architecture/current-production.md).

## Compose entry points

| File | Purpose | Production status |
|---|---|---|
| `infrastructure/docker-compose.prod.yml` | Online VPS stack | Official production entry point |
| `infrastructure/docker-compose.offline.yml` | Prebuilt-image VPS stack | Official offline packaging variant |
| `infrastructure/docker-compose.restore-volume.yml` | Existing PostgreSQL volume override | Recovery-only overlay; never run alone |
| `infrastructure/docker-compose.yml` | Future architecture development sandbox | Experimental; not production |
| `infrastructure/docker-compose.mvp.yml` | Broader target-stack sandbox | Incomplete; not production |
| `infrastructure/dify-compose.yml` | Partial Dify integration definition | Development/support only |
| `infrastructure/weaviate-compose.yml` | Standalone Weaviate definition | Development/support only |
| `infrastructure/observability-compose.yml` | Observability experiments | Not part of the current production path |

Production configuration must define `CORS_ALLOWED_ORIGINS` as a comma-separated list
of exact origins. `ASYNC_INGESTION_ENABLED` explicitly controls synchronous versus
queued indexing; it defaults to `false` for staged rollout. RabbitMQ uses the root-only
Docker secret file `/var/lib/ideation-secrets/rabbitmq_password`, not `.env`. The
frontend production build uses same-origin `/api/v1` requests.

Validate without changing running services:

```bash
docker compose -f infrastructure/docker-compose.prod.yml \
  -f infrastructure/docker-compose.restore-volume.yml config -q
```

Do not deploy the experimental Compose files as substitutes for the production file.

FROM python:3.12.14-slim-bookworm AS base

ARG GIT_SHA="unknown"
ARG SOURCE_DIGEST="unknown"
ARG BUILD_TIME="unknown"
ARG APP_VERSION="0.1.0"
ARG MIGRATION_REVISION=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    GIT_SHA=${GIT_SHA} \
    SOURCE_DIGEST=${SOURCE_DIGEST} \
    BUILD_TIME=${BUILD_TIME} \
    APP_VERSION=${APP_VERSION} \
    MIGRATION_REVISION=${MIGRATION_REVISION}

LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source-digest="${SOURCE_DIGEST}"

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
# Source directories can arrive with restrictive host permissions (for example,
# after rsync). The runtime user must be able to traverse the package tree.
RUN chmod -R a+rX ./app
RUN pip install --upgrade pip==26.2.1 && pip install "."
RUN pip install --upgrade setuptools==84.0.0 msgpack==1.2.2 && pip check

FROM base AS runtime

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test

COPY tests ./tests
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY Dockerfile ./Dockerfile
COPY Dockerfile.worker ./Dockerfile.worker
RUN pip install ".[dev,worker]" \
    && pip check \
    && pip install --no-deps --upgrade lxml==6.1.2 \
    && python -c "import lxml; from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig; assert lxml.__version__ == '6.1.2'"

CMD ["pytest", "-q"]

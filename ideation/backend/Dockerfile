FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/lib/ideation-worker \
    && chown nobody:nogroup /var/lib/ideation-worker \
    && usermod --home /var/lib/ideation-worker nobody

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml /app/
RUN pip install --no-cache-dir .

COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY app /app/app

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m app.scripts.start_backend"]

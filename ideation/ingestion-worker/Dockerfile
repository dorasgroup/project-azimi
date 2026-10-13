FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . /app/

# Environment variables
ENV PYTHONUNBUFFERED=1

# Run Celery worker
CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info"]

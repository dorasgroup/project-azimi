import structlog
from celery import shared_task
from celery.exceptions import Reject
from .celery_app import celery_app
from .application import ProcessDocumentUseCase
from .adapters import MockDocumentParser, MockChunkingStrategy, DifyDatasetAdapter
from .idempotency import RedisIdempotencyAdapter
from .observability import trace_stage

logger = structlog.get_logger()

# Dependency Injection
parser = MockDocumentParser()
chunker = MockChunkingStrategy()
dify_adapter = DifyDatasetAdapter()
use_case = ProcessDocumentUseCase(parser, chunker, dify_adapter)
idempotency = RedisIdempotencyAdapter()

class ProviderRateLimitException(Exception): pass
class MalwareDetectedException(Exception): pass

@shared_task(bind=True, max_retries=5, name="worker.tasks.process_document_job")
@trace_stage("process_document_job")
def process_document_job(self, event_payload: dict):
    job_data = event_payload.get('data', {})
    job_id = job_data.get('jobId')
    pipeline_version = job_data.get('processing', {}).get('pipelineVersion', 'v1')
    tenant_id = job_data.get('tenantId')
    
    # Idempotency Key
    idempotency_key = f"ingestion:{tenant_id}:{job_id}:{pipeline_version}"
    
    if idempotency.is_completed(idempotency_key):
        logger.info("Job already completed. Skipping.", job_id=job_id)
        return {"status": "skipped", "reason": "already_completed"}

    if not idempotency.acquire_lock(idempotency_key):
        # Already running in another worker
        logger.warning("Job is locked by another worker.", job_id=job_id)
        raise self.retry(countdown=10) # Quick retry
        
    try:
        use_case.execute(job_data)
        idempotency.mark_completed(idempotency_key)
        return {"status": "completed", "jobId": job_id}
        
    except ProviderRateLimitException as exc:
        logger.warning("Rate limit hit. Retrying...", exc_info=exc)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 30)
        
    except MalwareDetectedException as exc:
        logger.error("Malware detected! Poison message.", exc_info=exc)
        idempotency.mark_completed(idempotency_key) # Prevent forever retries
        # Reject requeue=False sends it to Dead Letter Queue in RabbitMQ
        raise Reject(reason="Malware detected", requeue=False)
        
    except Exception as exc:
        logger.error("Non-retryable system error", exc_info=exc)
        raise Reject(reason=str(exc), requeue=False)
    finally:
        idempotency.release_lock(idempotency_key)

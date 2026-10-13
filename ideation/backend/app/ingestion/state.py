from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DOCUMENT_CREATED = "document_created"
    INDEXING = "indexing"
    INDEXED = "indexed"
    RETRYING = "retrying"
    FAILED = "failed"
    ENQUEUE_FAILED = "enqueue_failed"


ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.PROCESSING,
    JobStatus.DOCUMENT_CREATED,
    JobStatus.INDEXING,
    JobStatus.RETRYING,
}
RETRYABLE_JOB_STATUSES = {JobStatus.FAILED, JobStatus.ENQUEUE_FAILED}
TERMINAL_JOB_STATUSES = {JobStatus.INDEXED, JobStatus.FAILED}

ALLOWED_TRANSITIONS = {
    JobStatus.QUEUED: {JobStatus.PROCESSING, JobStatus.ENQUEUE_FAILED},
    JobStatus.PROCESSING: {
        JobStatus.DOCUMENT_CREATED,
        JobStatus.RETRYING,
        JobStatus.FAILED,
    },
    JobStatus.DOCUMENT_CREATED: {
        JobStatus.INDEXING,
        JobStatus.INDEXED,
        JobStatus.FAILED,
    },
    JobStatus.INDEXING: {
        JobStatus.INDEXED,
        JobStatus.FAILED,
    },
    JobStatus.RETRYING: {JobStatus.PROCESSING, JobStatus.FAILED},
    JobStatus.ENQUEUE_FAILED: {JobStatus.QUEUED},
    JobStatus.FAILED: {JobStatus.QUEUED},
    JobStatus.INDEXED: set(),
}


def transition_allowed(current: str, target: JobStatus, *, explicit_retry: bool = False) -> bool:
    try:
        source = JobStatus(current)
    except ValueError:
        return False
    if source is JobStatus.FAILED and target is JobStatus.QUEUED:
        return explicit_retry
    return target in ALLOWED_TRANSITIONS[source]

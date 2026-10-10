from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from app.api.operations.monitoring import (
    MonitoringCommands,
    MonitoringOperations,
    MonitoringQueries,
)
from app.bootstrap.transaction import TransactionRunner
from app.models.enums import JobStatus
from app.modules.monitoring.application import ProcessMonitoringJob
from app.modules.monitoring.application.workflows import MonitoringProgress, MonitoringStart
from app.schemas.monitoring import MonitoringJobCreate


class FakeUnitOfWork:
    def __init__(self, events: list[str] | None = None, fail_commit: bool = False) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.events = events
        self.fail_commit = fail_commit

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None

    async def commit(self) -> None:
        self.commits += 1
        if self.events is not None:
            self.events.append("commit")
        if self.fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self.events is not None:
            self.events.append("rollback")


class FakeCreateMonitoringJob:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def execute(self, draft):
        self.events.append("create")
        return FakeMonitoringJob()


class FakeMonitoringJob:
    def __init__(self) -> None:
        self.id = uuid4()
        self.topic = "topic"
        self.description = None
        self.status = JobStatus.PENDING
        self.date_from_jalali = "1405/04/01"
        self.date_to_jalali = "1405/04/02"
        self.bot_identity_id = uuid4()
        self.created_at = datetime.now(UTC)
        self.started_at = None
        self.finished_at = None
        self.error_message = None
        self.sources = []
        self.report = None


class FakeMonitoringQueue:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def enqueue(self, job_id) -> None:
        self.events.append("enqueue")


def monitoring_payload() -> MonitoringJobCreate:
    return MonitoringJobCreate(
        topic="topic",
        date_from_jalali="1405/04/01",
        date_to_jalali="1405/04/02",
        source_ids=[uuid4()],
        bot_identity_id=uuid4(),
    )


def monitoring_operations(events: list[str], unit_of_work: FakeUnitOfWork) -> MonitoringOperations:
    unused = object()
    return MonitoringOperations(
        MonitoringQueries(list_jobs=unused, get_job=unused),  # type: ignore[arg-type]
        MonitoringCommands(
            create_job=FakeCreateMonitoringJob(events),  # type: ignore[arg-type]
            update_job=unused,  # type: ignore[arg-type]
            retry_job=unused,  # type: ignore[arg-type]
            cancel_job=unused,  # type: ignore[arg-type]
            delete_job=unused,  # type: ignore[arg-type]
        ),
        FakeMonitoringQueue(events),
        TransactionRunner(unit_of_work),
    )


class FakeProcessor:
    def __init__(self) -> None:
        self.received = None
        self.closed = False
        self.failure = None

    async def start(self, job_id):
        self.received = job_id
        return MonitoringStart(context={"job_id": job_id})

    async def process_sources(self, context):
        return MonitoringProgress(documents=3)

    async def analyze(self, context, documents):
        return {"status": "completed", "documents": documents}

    async def fail(self, job_id, error):
        self.failure = error

    async def close(self):
        self.closed = True


class FailingProcessor(FakeProcessor):
    async def analyze(self, context, documents):
        raise RuntimeError("analysis failed")


class ResumeAnalysisProcessor(FakeProcessor):
    def __init__(self) -> None:
        super().__init__()
        self.sources_processed = False

    async def start(self, job_id):
        self.received = job_id
        return MonitoringStart(context={"job_id": job_id}, resume_analysis=True)

    async def process_sources(self, context):
        self.sources_processed = True
        return MonitoringProgress(documents=99)


class PhaseSixCompositionTests(IsolatedAsyncioTestCase):
    async def test_transaction_runner_commits_successful_command(self) -> None:
        unit_of_work = FakeUnitOfWork()

        async def operation():
            return "saved"

        result = await TransactionRunner(unit_of_work).run(operation)
        self.assertEqual(result, "saved")
        self.assertEqual(unit_of_work.commits, 1)
        self.assertEqual(unit_of_work.rollbacks, 0)

    async def test_transaction_runner_rolls_back_failed_command(self) -> None:
        unit_of_work = FakeUnitOfWork()

        async def operation():
            raise RuntimeError("failed")

        with self.assertRaises(RuntimeError):
            await TransactionRunner(unit_of_work).run(operation)
        self.assertEqual(unit_of_work.commits, 0)
        self.assertEqual(unit_of_work.rollbacks, 1)

    async def test_monitoring_enqueue_happens_after_successful_commit(self) -> None:
        events: list[str] = []
        operations = monitoring_operations(events, FakeUnitOfWork(events))

        await operations.create(monitoring_payload(), uuid4())

        self.assertEqual(events, ["create", "commit", "enqueue"])

    async def test_monitoring_does_not_enqueue_when_commit_fails(self) -> None:
        events: list[str] = []
        unit_of_work = FakeUnitOfWork(events, fail_commit=True)
        operations = monitoring_operations(events, unit_of_work)

        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            await operations.create(monitoring_payload(), uuid4())

        self.assertEqual(events, ["create", "commit", "rollback"])
        self.assertEqual(unit_of_work.rollbacks, 1)

    async def test_monitoring_workflow_delegates_to_processor_port(self) -> None:
        processor = FakeProcessor()
        job_id = uuid4()
        result = await ProcessMonitoringJob(processor).execute(job_id)
        self.assertEqual(result, {"status": "completed", "documents": 3})
        self.assertEqual(processor.received, job_id)
        self.assertTrue(processor.closed)

    async def test_monitoring_workflow_records_failure_and_closes_processor(self) -> None:
        processor = FailingProcessor()
        with self.assertRaisesRegex(RuntimeError, "analysis failed"):
            await ProcessMonitoringJob(processor).execute(uuid4())
        self.assertIsInstance(processor.failure, RuntimeError)
        self.assertTrue(processor.closed)

    async def test_monitoring_workflow_resumes_analysis_without_recrawling(self) -> None:
        processor = ResumeAnalysisProcessor()

        result = await ProcessMonitoringJob(processor).execute(uuid4())

        self.assertEqual(result, {"status": "completed", "documents": 0})
        self.assertFalse(processor.sources_processed)
        self.assertTrue(processor.closed)

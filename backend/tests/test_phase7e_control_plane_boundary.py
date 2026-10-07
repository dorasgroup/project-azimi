"""Phase 7E control-plane boundary and permanent-failure closure tests."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from app.modules.monitoring.application.control_event import (
    ControlOutcomeKind,
    ProcessMonitoringControlEvent,
)
from app.modules.monitoring.application.create_completion_tracker import CreateRunCompletionTracker
from app.modules.monitoring.application.evaluate_completion import EvaluateRunCompletionUseCase
from app.modules.monitoring.ports.completion import (
    TransientControlInfrastructureError,
    UnsupportedDeadlineHorizonError,
)
from app.workers.control_tasks import process_control_event


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONTROL_TASK = BACKEND_ROOT / "app" / "workers" / "control_tasks.py"


class ControlTaskArchitectureCharacterizationTests(TestCase):
    """The Celery task is a transport adapter, not a persistence component."""

    def test_control_task_has_no_monitoring_infrastructure_or_database_access(self) -> None:
        source = CONTROL_TASK.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules: list[str] = []
        called_attributes: list[str] = []
        identifiers: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.append(node.module or "")
                identifiers.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Name):
                identifiers.add(node.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called_attributes.append(node.func.attr)

        forbidden_import_prefixes = (
            "app.modules.monitoring.infrastructure",
            "sqlalchemy",
        )
        self.assertFalse(
            [
                module
                for module in imported_modules
                if module.startswith(forbidden_import_prefixes)
            ],
            imported_modules,
        )
        self.assertTrue(
            {
                "ControlWorkerSessionFactory",
                "SqlAlchemyRunCompletionTrackerRepository",
                "ThreadPoolExecutor",
                "asyncio",
            }.isdisjoint(identifiers),
            identifiers,
        )
        self.assertTrue(
            {"get_by_run_id", "save", "execute"}.isdisjoint(called_attributes),
            called_attributes,
        )
        self.assertNotIn("session", source.casefold())
        self.assertNotIn("engine", source.casefold())

    def test_monitoring_application_does_not_import_celery(self) -> None:
        violations: list[str] = []
        application_dir = BACKEND_ROOT / "app" / "modules" / "monitoring" / "application"
        for path in application_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("celery"):
                            violations.append(f"{path.name}:{alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith("celery"):
                        violations.append(f"{path.name}:{module}")
        self.assertEqual(violations, [])

    def test_monitoring_domain_does_not_import_infrastructure(self) -> None:
        violations: list[str] = []
        domain_dir = BACKEND_ROOT / "app" / "modules" / "monitoring" / "domain"
        for path in domain_dir.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "app.modules.monitoring.infrastructure" in source:
                violations.append(path.name)
        self.assertEqual(violations, [])


class ControlFailureClosureTests(IsolatedAsyncioTestCase):
    async def test_unsupported_deadline_rejected_before_persistence(self) -> None:
        trackers = Mock()
        trackers.get_or_create = AsyncMock()
        policy = Mock()
        policy.validate.side_effect = UnsupportedDeadlineHorizonError("unsupported")
        creator = CreateRunCompletionTracker(trackers, policy)

        with self.assertRaises(UnsupportedDeadlineHorizonError):
            await creator.execute(uuid4(), datetime.now(UTC) + timedelta(days=2))

        trackers.get_or_create.assert_not_awaited()

    async def test_deadline_event_uses_pre_persistence_validation(self) -> None:
        run_id = uuid4()
        deadline = datetime.now(UTC) + timedelta(days=2)
        trackers = Mock()
        trackers.get_by_run_id = AsyncMock(return_value=None)
        trackers.get_or_create = AsyncMock()
        policy = Mock()
        policy.validate.side_effect = UnsupportedDeadlineHorizonError("unsupported")
        evaluator = EvaluateRunCompletionUseCase(
            tracker_repository=trackers,
            run_repository=Mock(),
            aggregation_handoff=Mock(),
            deadline_policy=policy,
        )

        with self.assertRaises(UnsupportedDeadlineHorizonError):
            await evaluator.execute_event({
                "event": "deadline_check", "run_id": str(run_id),
                "deadline_at": deadline.isoformat(),
            })

        trackers.get_or_create.assert_not_awaited()

    async def test_runtime_permanent_error_is_durably_closed_without_ready(self) -> None:
        run_id = uuid4()
        tracker = Mock()
        tracker.status.value = "control_failed"
        evaluator = Mock()
        evaluator.execute_event = AsyncMock(side_effect=UnsupportedDeadlineHorizonError("unsupported"))
        trackers = Mock()
        trackers.mark_control_failed = AsyncMock(return_value=tracker)
        runs = Mock()
        processor = ProcessMonitoringControlEvent(evaluator, trackers, runs)

        result = await processor.execute({
            "event": "source_terminal", "run_id": str(run_id),
            "source_task_id": str(uuid4()),
        })

        self.assertEqual(result.kind, ControlOutcomeKind.PERMANENT_FAILURE)
        self.assertEqual(result.payload["status"], "control_failed")
        trackers.mark_control_failed.assert_awaited_once_with(run_id)
        runs.get_by_id.assert_not_called()

    async def test_unknown_application_error_is_not_swallowed(self) -> None:
        run_id = uuid4()
        evaluator = Mock()
        evaluator.execute_event = AsyncMock(side_effect=RuntimeError("programming defect"))
        processor = ProcessMonitoringControlEvent(evaluator, Mock(), Mock())

        with self.assertRaisesRegex(RuntimeError, "programming defect"):
            await processor.execute({
                "event": "source_terminal", "run_id": str(run_id),
                "source_task_id": str(uuid4()),
            })

    async def test_invalid_event_for_existing_run_closes_tracker(self) -> None:
        run_id = uuid4()
        tracker = Mock()
        tracker.status.value = "control_failed"
        trackers = Mock()
        trackers.mark_control_failed = AsyncMock(return_value=tracker)
        evaluator = Mock()
        evaluator.execute_event = AsyncMock()
        processor = ProcessMonitoringControlEvent(evaluator, trackers, Mock())

        result = await processor.execute({"event": "unknown", "run_id": str(run_id)})

        self.assertEqual(result.kind, ControlOutcomeKind.PERMANENT_FAILURE)
        self.assertEqual(result.payload["status"], "control_failed")
        trackers.mark_control_failed.assert_awaited_once_with(run_id)
        evaluator.execute_event.assert_not_awaited()


class ControlTransportFailureTests(TestCase):
    def test_transient_database_error_retries_without_budget(self) -> None:
        payload = {"event": "source_terminal", "run_id": str(uuid4())}
        process_control_event.push_request(retries=8)
        try:
            with patch("app.workers.control_tasks.run_control_event_worker") as worker:
                worker.side_effect = TransientControlInfrastructureError("db unavailable")
                with patch.object(process_control_event, "retry", side_effect=RuntimeError("retried")) as retry:
                    with self.assertRaisesRegex(RuntimeError, "retried"):
                        process_control_event.run(payload)
                    self.assertIsNone(retry.call_args.kwargs["max_retries"])
        finally:
            process_control_event.pop_request()

    def test_unknown_error_fails_task_instead_of_acknowledging_success(self) -> None:
        payload = {"event": "source_terminal", "run_id": str(uuid4())}
        with patch("app.workers.control_tasks.run_control_event_worker", side_effect=RuntimeError("bug")):
            with self.assertRaisesRegex(RuntimeError, "bug"):
                process_control_event.run(payload)

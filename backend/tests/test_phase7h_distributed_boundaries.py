"""Mode-aware publisher guardrail: retain legacy queue, fence distributed handoffs."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import TestCase


APP = Path(__file__).resolve().parents[1] / "app"
LEGACY_DIRECT_PUBLISHERS = {
    "app.modules.monitoring.infrastructure.queue",
    "app.modules.monitoring.infrastructure.deadline_scheduler",
    "app.modules.monitoring.infrastructure.source_terminal_publisher",
    "app.modules.analysis.infrastructure.celery_dispatcher",
    "app.modules.analysis.infrastructure.source_analysis_terminal_publisher",
    "app.modules.analysis.infrastructure.analysis_ready_publisher",
}


class TestDistributedPublisherBoundary(TestCase):
    def test_distributed_composition_never_imports_direct_publishers(self) -> None:
        # The job queue in providers is the legacy entrypoint and is not one
        # of the seven distributed DB-to-broker handoffs.
        composition_files = [
            APP / "bootstrap" / "monitoring_worker.py",
            APP / "bootstrap" / "analysis_worker.py",
            APP / "bootstrap" / "aggregation_worker.py",
            APP / "bootstrap" / "control_worker.py",
            APP / "modules" / "monitoring" / "infrastructure" / "pipeline_steps.py",
        ]
        for path in composition_files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = {
                node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertFalse(imported & LEGACY_DIRECT_PUBLISHERS, path)

        pipeline = composition_files[-1].read_text(encoding="utf-8")
        self.assertIn('execution_mode in ("distributed", "distributed_fanout")', pipeline)
        self.assertIn("OutboxSourceTaskDispatcher(session)", pipeline)

    def test_application_has_no_direct_celery_publish(self) -> None:
        for application_dir in (APP / "modules" / "monitoring" / "application",
                                APP / "modules" / "analysis" / "application"):
            for path in application_dir.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        self.assertNotIn(node.func.attr, {"send_task", "apply_async", "delay"}, path)

    def test_direct_publish_is_confined_to_explicit_legacy_adapters_and_relay(self) -> None:
        allowed = {APP / Path(module.removeprefix("app.").replace(".", "/") + ".py")
                   for module in LEGACY_DIRECT_PUBLISHERS}
        allowed.add(APP / "shared" / "integration" / "relay.py")
        for path in (APP / "modules").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "send_task" in text:
                self.assertIn(path, allowed)

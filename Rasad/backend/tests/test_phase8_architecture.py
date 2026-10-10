"""Guard the report worker's immutable-input and transport boundaries."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"


def imports(path):
    return {
        node.module or "" for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Import) for alias in node.names
    }


def test_reporting_application_never_reanalyzes_or_dispatches_tasks():
    for path in (ROOT / "modules/reports/application").glob("*.py"):
        assert not any(name.startswith(("celery", "sqlalchemy", "app.models", "app.modules.analysis", "app.modules.crawling", "app.modules.monitoring")) for name in imports(path)), path


def test_domains_do_not_import_each_other():
    for context, forbidden in (("reports", "monitoring"), ("monitoring", "reports")):
        for path in (ROOT / f"modules/{context}/domain").glob("*.py"):
            assert not any(name.startswith(f"app.modules.{forbidden}") for name in imports(path)), path


def test_report_task_is_transport_only():
    names = imports(ROOT / "workers/report_tasks.py")
    assert not any(name.startswith(("sqlalchemy", "app.models", "app.modules.reports.infrastructure", "app.reports", "pathlib")) for name in names)


def test_report_dispatch_and_terminal_are_outbox_only():
    for path in (ROOT / "modules/reports").rglob("*.py"):
        assert "send_task" not in path.read_text(encoding="utf-8"), path

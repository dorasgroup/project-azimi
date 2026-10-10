"""Architecture and Responsibility Guardrails for Monitoring Components."""

import ast
from pathlib import Path
import unittest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MONITORING_APP_ROOT = BACKEND_ROOT / "app" / "modules" / "monitoring" / "application"

FORBIDDEN_FRAMEWORKS = (
    "fastapi",
    "celery",
    "sqlalchemy",
    "app.db",
    "app.models",
    "app.services",
    "app.api",
    "app.crawler",
)


def _imports_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def _public_methods_of_class(path: Path, class_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not item.name.startswith("_"):
                        methods.append(item.name)
            return methods
    return []


class MonitoringArchitectureGuardrailTests(unittest.TestCase):
    def test_monitoring_application_has_no_infrastructure_imports(self) -> None:
        """Application layer must not import from infrastructure."""
        violations: list[str] = []
        for py_path in MONITORING_APP_ROOT.rglob("*.py"):
            rel_path = str(py_path.relative_to(BACKEND_ROOT)).replace("\\", "/")
            for imported in _imports_in(py_path):
                if "infrastructure" in imported.split("."):
                    violations.append(f"{rel_path} illegally imports {imported}")
        self.assertFalse(violations, "Application layer imports infrastructure:\n" + "\n".join(violations))

    def test_monitoring_application_has_no_framework_or_orm_imports(self) -> None:
        """Application layer must not import SQLAlchemy, Celery, FastAPI, or raw ORM models."""
        violations: list[str] = []
        for py_path in MONITORING_APP_ROOT.rglob("*.py"):
            rel_path = str(py_path.relative_to(BACKEND_ROOT)).replace("\\", "/")
            for imported in _imports_in(py_path):
                for forbidden in FORBIDDEN_FRAMEWORKS:
                    if imported == forbidden or imported.startswith(forbidden + "."):
                        violations.append(f"{rel_path} imports {imported}")
        self.assertFalse(violations, "Application layer imports forbidden framework:\n" + "\n".join(violations))

    def test_extracted_components_have_focused_interfaces(self) -> None:
        """Extracted components must have at most 1 or 2 focused public methods."""
        expected_components = {
            "startup.py": ("JobStartupStep", 1),
            "cancellation.py": ("CancellationGuard", 1),
            "progress.py": ("SourceProgressRecorder", 2),
            "source_failure.py": ("SourceFailureRecorder", 1),
            "source_batch.py": ("SourceBatchProcessor", 1),
            "coverage.py": ("CoverageRecorder", 1),
            "analysis_step.py": ("AnalysisStep", 1),
            "job_failure.py": ("JobFailureHandler", 1),
        }

        for filename, (class_name, max_public_methods) in expected_components.items():
            py_path = MONITORING_APP_ROOT / filename
            self.assertTrue(py_path.is_file(), f"Expected component file {filename} does not exist")
            public_methods = _public_methods_of_class(py_path, class_name)
            self.assertLessEqual(
                len(public_methods),
                max_public_methods,
                f"{class_name} in {filename} has {len(public_methods)} public methods ({public_methods}), exceeding limit of {max_public_methods}",
            )

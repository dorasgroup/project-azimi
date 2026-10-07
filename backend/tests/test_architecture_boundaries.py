"""Fast checks for the incremental architecture's dependency rules."""

import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_ROOT = BACKEND_ROOT / "app" / "shared" / "domain"
MODULE_DOMAIN_ROOTS = tuple(
    path for path in (BACKEND_ROOT / "app" / "modules").glob("*/domain") if path.is_dir()
)
APPLICATION_ROOTS = (
    BACKEND_ROOT / "app" / "modules" / "monitoring" / "application",
    BACKEND_ROOT / "app" / "modules" / "crawling" / "application",
    BACKEND_ROOT / "app" / "modules" / "documents" / "application",
    BACKEND_ROOT / "app" / "modules" / "analysis" / "application",
    BACKEND_ROOT / "app" / "modules" / "reports" / "application",
    BACKEND_ROOT / "app" / "modules" / "identity" / "application",
    BACKEND_ROOT / "app" / "modules" / "platform_settings" / "application",
)
API_ROUTES_ROOT = BACKEND_ROOT / "app" / "api" / "routes"
WORKERS_ROOT = BACKEND_ROOT / "app" / "workers"
WORKER_COMPOSITION_ROOTS = (
    BACKEND_ROOT / "app" / "bootstrap" / "crawler_worker.py",
    BACKEND_ROOT / "app" / "bootstrap" / "processing_worker.py",
)
LEGACY_SERVICE_ROOT = BACKEND_ROOT / "app" / "services"
FORBIDDEN_SESSION_METHODS = {
    "add",
    "commit",
    "delete",
    "execute",
    "flush",
    "get",
    "rollback",
    "scalar",
    "scalars",
}
FORBIDDEN_PREFIXES = (
    "fastapi",
    "celery",
    "sqlalchemy",
    "httpx",
    "asyncpg",
    "reportlab",
    "docx",
    "app.db",
    "app.api",
    "app.workers",
    "app.services",
    "app.models",
    "app.schemas",
)
LEGACY_FACADE_NAMES = {
    "AdminUserService",
    "AuthService",
    "BotIdentityService",
    "DashboardService",
    "MonitoringFlowService",
    "ReportService",
    "SourceService",
    "SystemLogService",
}
PHASE_EIGHT_GOD_ADAPTER_NAMES = {
    "AnalysisWorkflowAdapter",
    "BotIdentityOperationsAdapter",
    "MonitoringOperationsAdapter",
    "ReportOperationsAdapter",
    "SourceOperationsAdapter",
    "SqlAlchemyMonitoringJobProcessor",
}


def _imports_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def test_shared_domain_has_no_framework_or_infrastructure_imports() -> None:
    violations: list[str] = []
    for path in DOMAIN_ROOT.rglob("*.py"):
        for imported in _imports_in(path):
            if imported.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")
    assert not violations, "\n".join(violations)


def test_module_domains_have_no_framework_or_infrastructure_imports() -> None:
    violations: list[str] = []
    for root in MODULE_DOMAIN_ROOTS:
        for path in root.rglob("*.py"):
            for imported in _imports_in(path):
                if imported.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")
    assert not violations, "\n".join(violations)


def test_target_module_skeleton_exists() -> None:
    expected = {
        "monitoring": ("domain", "application", "ports", "infrastructure"),
        "crawling": ("domain", "application", "ports", "infrastructure"),
        "documents": ("domain", "application", "ports", "infrastructure"),
        "analysis": ("domain", "application", "ports", "infrastructure"),
        "reports": ("domain", "application", "ports", "infrastructure"),
        "platform_settings": ("application", "ports", "infrastructure"),
    }
    modules_root = BACKEND_ROOT / "app" / "modules"
    missing = [
        str(module_path.relative_to(BACKEND_ROOT))
        for module, layers in expected.items()
        for module_path in (
            modules_root / module,
            *(modules_root / module / layer for layer in layers),
        )
        if not module_path.is_dir()
    ]
    assert not missing, "Missing architecture directories:\n" + "\n".join(missing)


def test_application_layers_do_not_import_framework_or_legacy_infrastructure() -> None:
    violations: list[str] = []
    for root in APPLICATION_ROOTS:
        for path in root.rglob("*.py"):
            for imported in _imports_in(path):
                if imported.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")
    assert not violations, "\n".join(violations)


def test_legacy_services_do_not_execute_persistence_operations_directly() -> None:
    violations: list[str] = []
    for path in LEGACY_SERVICE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and owner.attr == "session":
                owner_name = "session"
            elif isinstance(owner, ast.Name):
                owner_name = owner.id
            else:
                continue
            if (
                owner_name in {"session", "_session"}
                and node.func.attr in FORBIDDEN_SESSION_METHODS
            ):
                violations.append(
                    f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} calls session.{node.func.attr}"
                )
    assert not violations, "\n".join(violations)


def test_http_routes_are_transport_only() -> None:
    forbidden = ("sqlalchemy", "app.db", "app.services", "app.modules")
    violations = [
        f"{path.relative_to(BACKEND_ROOT)} imports {imported}"
        for path in API_ROUTES_ROOT.rglob("*.py")
        for imported in _imports_in(path)
        if imported.startswith(forbidden)
    ]
    assert not violations, "\n".join(violations)


def test_celery_tasks_delegate_to_composed_operations() -> None:
    forbidden = ("sqlalchemy", "app.db", "app.services", "app.crawler", "app.processing")
    violations = [
        f"{path.relative_to(BACKEND_ROOT)} imports {imported}"
        for path in WORKERS_ROOT.glob("*_tasks.py")
        for imported in _imports_in(path)
        if imported.startswith(forbidden)
    ]
    assert not violations, "\n".join(violations)


def test_worker_composition_roots_use_application_boundaries() -> None:
    forbidden = ("app.crawler", "app.processing")
    violations = [
        f"{path.relative_to(BACKEND_ROOT)} imports {imported}"
        for path in WORKER_COMPOSITION_ROOTS
        for imported in _imports_in(path)
        if imported.startswith(forbidden)
    ]
    assert not violations, "\n".join(violations)

    imports = {path.name: set(_imports_in(path)) for path in WORKER_COMPOSITION_ROOTS}
    assert "app.modules.crawling.api" in imports["crawler_worker.py"]
    assert "app.modules.crawling.api" in imports["processing_worker.py"]
    assert "app.modules.documents.api" in imports["processing_worker.py"]


def test_removed_legacy_facades_have_no_definitions_or_callers() -> None:
    violations: list[str] = []
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            symbol = (
                node.name
                if isinstance(node, ast.ClassDef)
                else node.id
                if isinstance(node, ast.Name)
                else None
            )
            if symbol in LEGACY_FACADE_NAMES:
                violations.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} uses {symbol}")
    assert not violations, "\n".join(violations)


def test_routes_use_focused_providers_instead_of_a_general_container() -> None:
    violations = [
        f"{path.relative_to(BACKEND_ROOT)} imports {imported}"
        for path in API_ROUTES_ROOT.rglob("*.py")
        for imported in _imports_in(path)
        if imported == "app.bootstrap.container"
    ]
    assert not violations, "\n".join(violations)


def test_phase_eight_public_module_apis_exist() -> None:
    modules_root = BACKEND_ROOT / "app" / "modules"
    expected = {
        "analysis",
        "crawling",
        "documents",
        "identity",
        "monitoring",
        "platform_settings",
        "reports",
    }
    missing = [
        module for module in sorted(expected) if not (modules_root / module / "api.py").is_file()
    ]
    assert not missing, "Missing public module APIs: " + ", ".join(missing)


def test_public_module_apis_are_importable() -> None:
    for module in (
        "app.modules.analysis.api",
        "app.modules.crawling.api",
        "app.modules.documents.api",
        "app.modules.identity.api",
        "app.modules.monitoring.api",
        "app.modules.platform_settings.api",
        "app.modules.reports.api",
    ):
        __import__(module)


def test_business_modules_do_not_import_each_others_infrastructure() -> None:
    modules_root = BACKEND_ROOT / "app" / "modules"
    violations: list[str] = []
    for module_root in (path for path in modules_root.iterdir() if path.is_dir()):
        for path in module_root.rglob("*.py"):
            for imported in _imports_in(path):
                parts = imported.split(".")
                if len(parts) < 4 or parts[:2] != ["app", "modules"]:
                    continue
                target_module = parts[2]
                targets_infrastructure = parts[3] == "infrastructure"
                documented_platform_dependency = (
                    module_root.name == "analysis" and target_module == "platform_settings"
                )
                if (
                    targets_infrastructure
                    and target_module != module_root.name
                    and not documented_platform_dependency
                ):
                    violations.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")
    assert not violations, "\n".join(violations)


def test_transport_operations_do_not_construct_external_io() -> None:
    operations_root = BACKEND_ROOT / "app" / "api" / "operations"
    forbidden = ("app.db", "app.core.config", "httpx", "redis", "sqlalchemy")
    violations = [
        f"{path.relative_to(BACKEND_ROOT)} imports {imported}"
        for path in operations_root.rglob("*.py")
        for imported in _imports_in(path)
        if imported.startswith(forbidden)
    ]
    assert not violations, "\n".join(violations)


def test_phase_eight_god_adapters_are_removed() -> None:
    violations: list[str] = []
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            symbol = (
                node.name
                if isinstance(node, ast.ClassDef)
                else node.id
                if isinstance(node, ast.Name)
                else None
            )
            if symbol in PHASE_EIGHT_GOD_ADAPTER_NAMES:
                violations.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} uses {symbol}")
    assert not violations, "\n".join(violations)


def test_phase_eight_removed_central_bootstrap_operations() -> None:
    assert not (BACKEND_ROOT / "app" / "bootstrap" / "operations.py").exists()


def test_public_contracts_do_not_use_any() -> None:
    modules_root = BACKEND_ROOT / "app" / "modules"
    roots = [*APPLICATION_ROOTS]
    roots.extend(path for path in modules_root.glob("*/ports") if path.is_dir())
    roots.extend(path for path in modules_root.glob("*/domain") if path.is_dir())
    violations: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "Any":
                    violations.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} uses Any")
    assert not violations, "\n".join(violations)


def test_phase_five_repository_ports_exist() -> None:
    expected = (
        ("analysis/ports/__init__.py", "AnalysisJobReader"),
        ("crawling/ports/__init__.py", "SourceRepository"),
        ("documents/ports/__init__.py", "DocumentRepository"),
        ("identity/ports/__init__.py", "UserRepository"),
        ("monitoring/ports/__init__.py", "BotIdentityRepository"),
        ("monitoring/ports/__init__.py", "SystemLogRepository"),
        ("reports/ports/__init__.py", "ReportRepository"),
    )
    modules_root = BACKEND_ROOT / "app" / "modules"
    missing: list[str] = []
    for relative_path, symbol in expected:
        path = modules_root / relative_path
        if not path.is_file() or symbol not in {
            node.name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ClassDef)
        }:
            missing.append(f"{relative_path}: {symbol}")
    assert not missing, "Missing persistence contracts:\n" + "\n".join(missing)


PORTS_ROOTS = tuple(
    path for path in (BACKEND_ROOT / "app" / "modules").glob("*/ports") if path.is_dir()
)
INFRASTRUCTURE_ROOTS = tuple(
    path for path in (BACKEND_ROOT / "app" / "modules").glob("*/infrastructure") if path.is_dir()
)

LEGACY_DOMAIN_IMPORT_EXCEPTIONS: set[tuple[str, str]] = set()

FORBIDDEN_DOMAIN_FRAMEWORKS = (
    "fastapi",
    "sqlalchemy",
    "celery",
    "redis",
    "httpx",
    "asyncpg",
    "reportlab",
    "docx",
    "app.db",
    "app.api",
    "app.workers",
    "app.services",
    "app.models",
    "app.processing",
    "app.crawler",
)


def test_domain_layer_strict_dependency_rules_with_legacy_baseline() -> None:
    """Domain must not import frameworks, external I/O, entrypoints, models, crawler or processing."""
    domain_files = list(DOMAIN_ROOT.rglob("*.py"))
    for root in MODULE_DOMAIN_ROOTS:
        domain_files.extend(root.rglob("*.py"))

    violations: list[str] = []
    for path in domain_files:
        rel_path = str(path.relative_to(BACKEND_ROOT)).replace("\\", "/")
        for imported in _imports_in(path):
            if (rel_path, imported) in LEGACY_DOMAIN_IMPORT_EXCEPTIONS:
                continue
            if imported.startswith(FORBIDDEN_DOMAIN_FRAMEWORKS):
                violations.append(f"{rel_path} imports forbidden {imported}")
            elif "infrastructure" in imported.split("."):
                violations.append(f"{rel_path} imports infrastructure {imported}")
    assert not violations, "\n".join(violations)


def test_legacy_domain_exceptions_are_strictly_bounded() -> None:
    """Ensure legacy domain exceptions are completely eliminated (Phase 2 requirement)."""
    assert len(LEGACY_DOMAIN_IMPORT_EXCEPTIONS) == 0, (
        f"Domain legacy crawler exceptions must be empty, found: {LEGACY_DOMAIN_IMPORT_EXCEPTIONS}"
    )


def test_ports_layer_has_no_infrastructure_or_framework_imports() -> None:
    """Ports define abstract contracts and must not depend on concrete infrastructure or entrypoints."""
    violations: list[str] = []
    for root in PORTS_ROOTS:
        for path in root.rglob("*.py"):
            rel_path = str(path.relative_to(BACKEND_ROOT)).replace("\\", "/")
            for imported in _imports_in(path):
                if imported.startswith(FORBIDDEN_DOMAIN_FRAMEWORKS) or "infrastructure" in imported.split("."):
                    violations.append(f"{rel_path} imports {imported}")
    assert not violations, "\n".join(violations)


def test_application_layer_has_no_infrastructure_imports() -> None:
    """Application layer orchestrates domain/ports and must not depend on concrete infrastructure or web entrypoints."""
    violations: list[str] = []
    for root in APPLICATION_ROOTS:
        for path in root.rglob("*.py"):
            rel_path = str(path.relative_to(BACKEND_ROOT)).replace("\\", "/")
            for imported in _imports_in(path):
                if "infrastructure" in imported.split(".") or imported.startswith(("fastapi", "app.api")):
                    violations.append(f"{rel_path} imports {imported}")
    assert not violations, "\n".join(violations)


def test_infrastructure_layer_does_not_import_api_entrypoints() -> None:
    """Infrastructure layer implements ports and must not import HTTP API routes/operations."""
    violations: list[str] = []
    for root in INFRASTRUCTURE_ROOTS:
        for path in root.rglob("*.py"):
            rel_path = str(path.relative_to(BACKEND_ROOT)).replace("\\", "/")
            for imported in _imports_in(path):
                if imported.startswith("app.api"):
                    violations.append(f"{rel_path} imports entrypoint {imported}")
    assert not violations, "\n".join(violations)

def test_transport_operations_do_not_import_infrastructure() -> None:
    """Operations layer defines transport orchestration and must not depend on concrete infrastructure."""
    operations_root = BACKEND_ROOT / "app" / "api" / "operations"
    violations: list[str] = []
    for path in operations_root.rglob("*.py"):
        rel_path = str(path.relative_to(BACKEND_ROOT)).replace("\\", "/")
        for imported in _imports_in(path):
            if "infrastructure" in imported.split("."):
                violations.append(f"{rel_path} imports infrastructure {imported}")
    assert not violations, "\n".join(violations)

def test_business_module_domains_do_not_import_other_module_domains() -> None:
    """Domain layer of each business module must be completely isolated from other business modules' domains."""
    modules_root = BACKEND_ROOT / "app" / "modules"
    business_modules = ("monitoring", "crawling", "analysis", "reports", "documents", "identity")
    violations: list[str] = []

    for mod_name in business_modules:
        domain_dir = modules_root / mod_name / "domain"
        if not domain_dir.is_dir():
            continue
        for py_path in domain_dir.rglob("*.py"):
            rel_path = str(py_path.relative_to(BACKEND_ROOT)).replace("\\", "/")
            for imported in _imports_in(py_path):
                parts = imported.split(".")
                if len(parts) >= 3 and parts[:2] == ["app", "modules"]:
                    target_module = parts[2]
                    target_layer = parts[3] if len(parts) > 3 else ""
                    if target_module != mod_name and target_module in business_modules and target_layer == "domain":
                        violations.append(
                            f"{rel_path} illegally imports {imported} from {target_module}.domain"
                        )
    assert not violations, "Cross-bounded-context domain import violations:\n" + "\n".join(violations)

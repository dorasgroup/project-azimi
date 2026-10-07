from app.main import app


EXPECTED_OPERATIONS = {
    "/api/v1/health": {"get"},
    "/api/v1/auth/login": {"post"},
    "/api/v1/auth/refresh": {"post"},
    "/api/v1/auth/logout": {"post"},
    "/api/v1/auth/me": {"get"},
    "/api/v1/auth/profile": {"put"},
    "/api/v1/auth/change-password": {"put"},
    "/api/v1/dashboard/summary": {"get"},
    "/api/v1/sources": {"get", "post"},
    "/api/v1/sources/{source_id}": {"put", "delete"},
    "/api/v1/sources/{source_id}/check": {"post"},
    "/api/v1/bot-identities": {"get", "post"},
    "/api/v1/bot-identities/active": {"get", "put"},
    "/api/v1/bot-identities/{identity_id}": {"put", "delete"},
    "/api/v1/monitoring/jobs": {"get", "post"},
    "/api/v1/monitoring/jobs/{job_id}": {"get"},
    "/api/v1/monitoring/jobs/{job_id}/cancel": {"post"},
    "/api/v1/reports": {"get"},
    "/api/v1/reports/{report_id}": {"get", "delete"},
    "/api/v1/reports/{report_id}/download/pdf": {"get"},
    "/api/v1/reports/{report_id}/download/docx": {"get"},
    "/api/v1/admin/users": {"get", "post"},
    "/api/v1/admin/users/{user_id}": {"put", "delete"},
    "/api/v1/system/logs": {"get"},
}

PUBLIC_OPERATIONS = {
    ("/api/v1/health", "get"),
    ("/api/v1/auth/login", "post"),
    ("/api/v1/auth/refresh", "post"),
}

PAGINATED_OPERATIONS = {
    ("/api/v1/sources", "get"),
    ("/api/v1/bot-identities", "get"),
    ("/api/v1/monitoring/jobs", "get"),
    ("/api/v1/reports", "get"),
    ("/api/v1/admin/users", "get"),
    ("/api/v1/system/logs", "get"),
}


def verify() -> None:
    spec = app.openapi()
    paths = spec["paths"]

    for path, methods in EXPECTED_OPERATIONS.items():
        assert path in paths, f"Missing path: {path}"
        for method in methods:
            assert method in paths[path], f"Missing operation: {method.upper()} {path}"

    operation_ids: list[str] = []
    for path, methods in EXPECTED_OPERATIONS.items():
        for method in methods:
            operation = paths[path][method]
            operation_ids.append(operation["operationId"])
            assert "422" in operation["responses"], (
                f"Missing standard error response: {method} {path}"
            )
            if (path, method) not in PUBLIC_OPERATIONS:
                assert operation.get("security"), f"Missing bearer security: {method} {path}"

    assert len(operation_ids) == len(set(operation_ids)), "Duplicate OpenAPI operation IDs"
    assert "HTTPBearer" in spec["components"]["securitySchemes"], "Missing HTTP bearer scheme"

    for path, method in PAGINATED_OPERATIONS:
        parameters = {item["name"] for item in paths[path][method].get("parameters", [])}
        assert {"page", "page_size"} <= parameters, (
            f"Missing pagination parameters: {method} {path}"
        )

    print(
        f"OpenAPI verification passed: {len(EXPECTED_OPERATIONS)} paths, {len(operation_ids)} operations"
    )


if __name__ == "__main__":
    verify()

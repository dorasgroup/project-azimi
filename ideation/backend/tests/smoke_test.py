from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_admin_login_and_list_users() -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "ChangeMe123!"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        users = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert users.status_code == 200
    assert users.json()["total"] >= 1


def test_admin_can_create_and_delete_user() -> None:
    email = f"delete-smoke-{uuid4().hex}@example.com"
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "ChangeMe123!"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/v1/admin/users",
            headers=headers,
            json={
                "email": email,
                "full_name": "Delete Smoke",
                "password": "DeleteSmoke123!",
                "role": "user",
            },
        )
        assert created.status_code == 201
        user_id = created.json()["id"]

        deleted = client.delete(f"/api/v1/admin/users/{user_id}", headers=headers)

    assert deleted.status_code == 204

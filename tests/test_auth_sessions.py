import pytest
from fastapi.testclient import TestClient

from services.api.app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    return TestClient(app)


def register(client, username):
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "StrongPass123!"},
    )
    assert response.status_code == 200, response.text
    return response


def test_demo_is_public_but_new_projects_require_a_user_session(client):
    demo = client.get("/api/projects", headers={"Authorization": "Bearer test-token"})
    assert demo.status_code == 200
    assert all(project["id"] == "HERO-BAT-HU-001" for project in demo.json()["projects"])

    denied = client.post("/api/projects", headers={"Authorization": "Bearer test-token"}, json={"mode": "LIVE"})
    assert denied.status_code == 401


def test_user_projects_are_isolated_and_archivable(client):
    first = register(client, "first_user")
    created = client.post("/api/projects", json={"name": "첫 사용자 프로젝트", "mode": "LIVE"})
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    second = register(client, "second_user")

    # The TestClient retains the latest session cookie, so explicitly switch users.
    client.cookies.clear()
    client.cookies.update({"replan_session": first.cookies["replan_session"]})
    first_projects = client.get("/api/projects").json()["projects"]
    assert project_id in [project["id"] for project in first_projects]

    client.cookies.clear()
    client.cookies.update({"replan_session": second.cookies["replan_session"]})
    second_projects = client.get("/api/projects").json()["projects"]
    assert project_id not in [project["id"] for project in second_projects]
    assert client.get(f"/api/projects/{project_id}").status_code == 404

    client.cookies.clear()
    client.cookies.update({"replan_session": first.cookies["replan_session"]})
    archived = client.patch(f"/api/projects/{project_id}/archive", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True

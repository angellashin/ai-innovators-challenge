"""Hero workbook and reviewer-authored synthetic supplier messages."""

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.events import normalize_event
from app.importers import parse_upload
from app.main import app, normalize_import_snapshot, ConfirmInput
from app.scheduling import simulate
from app.storage import Store
from app.worker import run_once


ROOT = Path(__file__).resolve().parents[1]
HERO = ROOT / "data/l1_project/hero_battery_factory_project.xlsx"
MESSAGES = json.loads((ROOT / "data/hero_demo/supplier_messages.json").read_text(encoding="utf-8"))
TRUTH = json.loads((ROOT / "data/hero_demo/supplier_message_ground_truth.json").read_text(encoding="utf-8"))


def request(client, method, path, **kwargs):
    response = getattr(client, method)(path, headers={"Authorization": "Bearer test-token"}, **kwargs)
    assert response.status_code < 400, response.text
    return response.json()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    monkeypatch.delenv("API_KEY", raising=False)
    return TestClient(app)


def hero_baseline(client):
    project_id = request(client, "post", "/api/projects", json={"mode": "REPLAY"})["project_id"]
    preview = request(client, "post", f"/api/projects/{project_id}/imports", files={"file": (HERO.name, HERO.read_bytes())})
    request(client, "post", f"/api/projects/{project_id}/imports/{preview['import_id']}/confirm", json={})
    return project_id, request(client, "get", f"/api/projects/{project_id}")


def input_event(item):
    return {**item, "mode": "REPLAY", "data_origin": "SYNTHETIC", "simulation_as_of": item["published_at"]}


def test_hero_import_preserves_operational_fields_and_snapshot(client):
    project_id, result = hero_baseline(client)
    tasks = result["version"]["data"]["tasks"]
    assert len(tasks) == 64
    by_id = {task["task_id"]: task for task in tasks}
    assert result["project"]["status_as_of"] == "2025-08-20"
    assert {status: sum(task["status"] == status for task in tasks)
            for status in ("completed", "in_progress", "planned")} == {
                "completed": 9, "in_progress": 5, "planned": 50}
    assert by_id["T001"]["status"] == "completed"
    assert by_id["T015"]["status"] == "in_progress"
    assert by_id["T036"]["status"] == "planned"
    assert by_id["T036"]["country"] == "South Korea"
    assert by_id["T036"]["supplier_id"] == by_id["T036"]["owner"] == "Equipment Vendor A"
    assert by_id["T037"]["country"] == "Germany"
    assert by_id["T045"]["country"] == "Hungary"
    assert by_id["T036"]["risk_tags"] == "equipment, supply_chain"
    assert by_id["T036"]["planned_cost"] == 2_000_000
    assert by_id["T036"]["currency"] == "USD"
    assert by_id["T040"]["predecessor_ids"] == ["T038", "T020"]
    assert by_id["T045"]["predecessor_ids"] == ["T044", "T034"]
    assert len(result["demo_events"]) == 8
    assert result["demo_events"][0]["event_id"] == "H01"


def test_bundled_hero_demo_uses_the_upload_baseline_path(client):
    project_id = request(client, "post", "/api/projects", json={"mode": "LIVE"})["project_id"]
    loaded = request(client, "post", f"/api/projects/{project_id}/demo/hero-baseline")
    assert loaded["task_count"] == 64
    result = request(client, "get", f"/api/projects/{project_id}")
    assert result["project"]["mode"] == "REPLAY"
    assert result["project"]["data_origin"] == "SYNTHETIC"
    assert result["version"]["data"]["import_id"]
    assert result["demo_events"][0]["event_id"] == "H01"
    repeat = client.post(f"/api/projects/{project_id}/demo/hero-baseline",
                         headers={"Authorization": "Bearer test-token"})
    assert repeat.status_code == 409


def test_hero_supplier_cases_match_reviewed_synthetic_answers():
    parsed = parse_upload(HERO.name, HERO.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "Hero", "mode": "REPLAY"}, ConfirmInput())
    tasks = snapshot["tasks"]
    project = snapshot["project"]
    baseline = {task["task_id"]: task for task in simulate(project, tasks)["schedule"]}
    by_id = {task["task_id"]: task for task in tasks}
    assert TRUTH["label"] == "검토자가 작성한 합성 정답"
    assert len(MESSAGES["events"]) == len(TRUTH["cases"]) == 8
    for source, answer in zip(MESSAGES["events"], TRUTH["cases"]):
        assert source["content"].startswith("[가상 메시지]")
        assert source["mode"] == "SYNTHETIC" and source["channel"] == "supplier_message"
        assert source["event_id"] == answer["event_id"]
        published = datetime.fromisoformat(source["published_at"]).date()
        assert published > date.fromisoformat(MESSAGES["as_of_date"])
        for task_id in answer["direct_task_ids"]:
            assert published < date.fromisoformat(by_id[task_id]["baseline_start"])
        event = normalize_event(input_event(source), project, tasks)
        assert event["patch"] == answer["expected_patch"]
        if source["event_id"] == "H02":
            assert event["verification_required"]
        if answer["expected_outcome"] == "DUPLICATE":
            assert source == MESSAGES["events"][0]
            continue
        if answer["expected_outcome"] == "NEEDS_INPUT":
            assert event["classification_status"] == "NEEDS_INPUT"
        elif answer["expected_outcome"] == "NO_IMPACT":
            assert event["classification_status"] == "NO_SCHEDULE_IMPACT"
        else:
            assert event["classification_status"] == "PATCH_PROPOSED"
        after = {task["task_id"]: task for task in simulate(project, tasks, event=event)["schedule"]}
        changed = [task_id for task_id in after if
                   (after[task_id]["planned_start"], after[task_id]["planned_finish"]) !=
                   (baseline[task_id]["planned_start"], baseline[task_id]["planned_finish"])]
        assert changed == answer["changed_task_ids"]
        for constraint in answer["newly_overlapping_external_constraints"]:
            task_id = constraint["task_id"]
            assert (baseline[task_id]["planned_start"], baseline[task_id]["planned_finish"]) == tuple(constraint["baseline_window"])
            assert (after[task_id]["planned_start"], after[task_id]["planned_finish"]) == tuple(constraint["changed_window"])
            for blocked in constraint["dates"]:
                assert not (baseline[task_id]["planned_start"] <= blocked < baseline[task_id]["planned_finish"])
                assert after[task_id]["planned_start"] <= blocked < after[task_id]["planned_finish"]


def test_hero_upload_message_preview_analysis_result_and_review_gate(client):
    project_id, baseline = hero_baseline(client)
    source = baseline["demo_events"][0]
    event = request(client, "post", f"/api/projects/{project_id}/events", json=source)
    assert event["event"]["patch"] == {"estimated_finish": {"T036": "2026-01-15"}}
    assert event["event"]["mode"] == "REPLAY" and event["event"]["data_origin"] == "SYNTHETIC"
    assert event["event"]["review_status"] == "PENDING"
    queued = request(client, "post", f"/api/projects/{project_id}/analyses",
                     json={"event_id": event["event_id"], "preview_only": True})
    assert run_once(Store())
    result = request(client, "get", f"/api/runs/{queued['run_id']}")
    assert result["run"]["status"] == "succeeded"
    assert result["scenarios"]
    proposed = result["scenarios"][0]
    assert proposed["data"]["provisional"]
    assert {task["task_id"] for task in proposed["data"]["changed_tasks"]} == {
        "T036", "T038", "T040", "T042", "T044"}
    approval = client.post(f"/api/scenarios/{proposed['id']}/approve",
                           headers={"Authorization": "Bearer test-token"}, json={"actor": "operator"})
    assert approval.status_code == 409
    request(client, "patch", f"/api/projects/{project_id}/events/{event['event_id']}/review", json={"confirmed": True})
    still_preview = client.post(f"/api/scenarios/{proposed['id']}/approve",
                                headers={"Authorization": "Bearer test-token"}, json={"actor": "operator"})
    assert still_preview.status_code == 409
    reviewed = request(client, "post", f"/api/projects/{project_id}/analyses", json={"event_id": event["event_id"]})
    assert reviewed["run_id"] != queued["run_id"]
    assert run_once(Store())
    updated = request(client, "get", f"/api/runs/{reviewed['run_id']}")
    assert updated["run"]["status"] == "succeeded"
    assert not updated["scenarios"][0]["data"]["provisional"]


def test_hero_ambiguous_no_impact_duplicate_and_past_task_guard(client):
    project_id, baseline = hero_baseline(client)
    for source, outcome in ((baseline["demo_events"][5], "NEEDS_INPUT"),
                            (baseline["demo_events"][6], "NO_IMPACT")):
        event = request(client, "post", f"/api/projects/{project_id}/events", json=input_event(source))
        queued = request(client, "post", f"/api/projects/{project_id}/analyses",
                         json={"event_id": event["event_id"], "preview_only": True})
        assert run_once(Store())
        result = request(client, "get", f"/api/runs/{queued['run_id']}")
        assert result["run"]["data"]["status"] == outcome
    first = request(client, "post", f"/api/projects/{project_id}/events", json=input_event(baseline["demo_events"][0]))
    repeated = request(client, "post", f"/api/projects/{project_id}/events", json=input_event(baseline["demo_events"][7]))
    assert repeated["duplicate"] and repeated["event_id"] == first["event_id"]
    past = client.post(f"/api/projects/{project_id}/events", headers={"Authorization": "Bearer test-token"},
                       json={"content": "T001 작업 완료일이 2025-02-05에서 2025-08-30로 변경됩니다.",
                             "published_at": "2025-08-21T09:00:00+02:00", "mode": "REPLAY"})
    assert past.status_code == 422

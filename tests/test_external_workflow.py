"""External workflow tests use synthetic fixtures, never live calls or paid models."""
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.adapters.llm import ChatResult
from app.adapters.sources import fetch_holidays, fetch_registered_source
from app.external_risks import combine_patches, holiday_patch, interpret_notice, match_notice, weather_patch
from app.main import app
from app.scheduling import simulate
from app.storage import Store, digest
from app.worker import _record_holiday_risks, _record_weather_risks, _run_scan, run_once

TASKS = [
    {"task_id": "A", "name": "Outdoor equipment lifting", "outdoor": True, "supplier_id": "SUP-A",
     "baseline_start": "2026-10-07", "baseline_finish": "2026-10-08", "duration_workdays": 2,
     "predecessor_ids": [], "location": "HU"},
    {"task_id": "B", "name": "Commissioning", "outdoor": False, "supplier_id": "SUP-B",
     "baseline_start": "2026-10-09", "baseline_finish": "2026-10-09", "duration_workdays": 1,
     "predecessor_ids": ["A"], "location": "HU"},
]
PROJECT = {"name": "Synthetic external regression", "target_finish": "2026-10-20", "mode": "LIVE"}
PLAN = {"enabled": True, "weather_site": {"id": "site", "latitude": 47.5, "longitude": 19.0},
        "weather_task_ids": ["A"], "weather_limits": {"max_wind_speed_kmh": 35}, "source_allowlist": []}


def forecast(wind=40):
    return {"status": "ok", "source_id": "weather", "body_hash": str(wind),
            "fetched_at": "2026-10-06T12:00:00+00:00", "url": "https://api.open-meteo.com/v1/forecast",
            "forecast": {"units": {"wind_speed_10m_max": "km/h"},
                         "data": [{"date": "2026-10-07", "wind_speed_10m_max": wind}]}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test")
    monkeypatch.delenv("API_KEY", raising=False)
    db = Store()
    db.put_json("projects", "P", PROJECT)
    snapshot = {"project": PROJECT, "tasks": TASKS, "options": []}
    db.put_json("versions", "V", snapshot, project_id="P", parent_id=None, status="baseline", content_hash=digest(snapshot))
    db.put_json("watch_plans", "P", PLAN)
    return db, TestClient(app)


def req(client, method, path, expected=200, **kwargs):
    result = getattr(client, method)(path, headers={"Authorization": "Bearer test"}, **kwargs)
    assert result.status_code == expected, result.text
    return result.json()


def test_weather_scope_units_completed_and_exclusive_boundary():
    tasks = [*TASKS, {**TASKS[0], "task_id": "OTHER"}, {**TASKS[0], "task_id": "DONE", "status": "completed"}]
    patch, facts = weather_patch(tasks, PLAN, forecast())
    assert patch == {"blocked_dates": {"A": ["2026-10-07"]}}
    assert facts[0]["measurements"][0]["unit"] == "km/h"
    wrong_unit = forecast()
    wrong_unit["forecast"]["units"]["wind_speed_10m_max"] = "m/s"
    assert weather_patch(tasks, PLAN, wrong_unit)[0] == {}
    assert weather_patch([{**TASKS[0], "baseline_finish": "2026-10-07", "finish_boundary": "exclusive"}], PLAN, forecast())[0] == {}


def test_holiday_scope_subdivision_and_public_type():
    config = {"country_code": "HU", "year": 2026, "task_ids": ["A"]}
    source = {"holidays": [
        {"date": "2026-10-07", "countryCode": "HU", "types": ["Public"], "global": True},
        {"date": "2026-10-08", "countryCode": "HU", "types": ["Public"], "global": False, "counties": ["HU-BU"]},
        {"date": "2026-10-08", "countryCode": "DE", "types": ["Public"], "global": True},
    ]}
    assert holiday_patch(TASKS, config, source)[0] == {"blocked_dates": {"A": ["2026-10-07"]}}
    assert holiday_patch(TASKS, {**config, "subdivision": "HU-BU"}, source)[0]["blocked_dates"]["A"] == ["2026-10-07", "2026-10-08"]


def test_calendar_union_and_finish_conflict():
    assert combine_patches([{"blocked_dates": {"A": ["2026-10-07"]}}] * 2) == {"blocked_dates": {"A": ["2026-10-07"]}}
    with pytest.raises(ValueError):
        combine_patches([{"estimated_finish": {"A": "2026-10-08"}}, {"estimated_finish": {"A": "2026-10-09"}}])


def test_notice_publication_date_never_becomes_schedule_patch():
    result = match_notice(
        {"url": "https://example.org", "content": "2026-10-07: Equipment lifting may be affected by a new permit rule."},
        TASKS, [{"url": "https://example.org", "keywords": ["permit"], "task_ids": ["A"]}])
    assert result["related_task_ids"] == ["A"]
    assert result["patch"] == {} and result["review_status"] == "PENDING"
    assert match_notice({"content": "Unrelated tourism news"}, TASKS, [])["related_task_ids"] == []


def test_llm_candidates_require_existing_tasks_and_verbatim_evidence():
    body = "Equipment lifting may be affected."
    class Gateway:
        def chat(self, *args, **kwargs):
            return ChatResult(content=json.dumps({"candidates": [
                {"task_id": "A", "quote": body, "reason": "equipment"},
                {"task_id": "B", "quote": "Invented regulation text"},
                {"task_id": "UNKNOWN", "quote": body},
            ]}))
    result = interpret_notice({"content": body}, TASKS, Gateway())
    assert [row["task_id"] for row in result["candidates"]] == ["A"]
    assert "patch" not in result


def test_forecast_revision_retires_old_and_reappearance_creates_new(setup):
    db, _ = setup
    first = _record_weather_risks(db, "P", PLAN, forecast(), "S1")[0]
    assert not _record_weather_risks(db, "P", PLAN, forecast(), "S2")
    assert not _record_weather_risks(db, "P", PLAN, forecast(10), "S3")
    assert db.get_json("events", first)["data"]["review_status"] == "SUPERSEDED"
    new = _record_weather_risks(db, "P", PLAN, forecast(), "S4")
    assert len(new) == 1 and new[0] != first
    assert not _record_weather_risks(db, "P", PLAN, forecast(), "S5")


def test_failed_source_is_not_no_risk(setup, monkeypatch):
    db, _ = setup
    event_id = _record_weather_risks(db, "P", PLAN, forecast(), "S")[0]
    monkeypatch.setattr("app.adapters.sources.fetch_weather", lambda site: {"status": "error", "source_id": "weather", "error": "timeout"})
    result = _run_scan(db, {"project_id": "P", "data": {"scope": "weather"}})
    assert result["status"] == "partial_failure" and result["failed_source_count"] == 1
    assert db.get_json("events", event_id)["data"]["review_status"] == "PENDING"


def test_rss_items_are_individual_and_reordering_is_not_new_change(setup, monkeypatch):
    db, _ = setup
    db.put_json("watch_plans", "P", {"enabled": True, "source_allowlist": ["https://example.org/rss"]})
    items = [{"id": "one", "title": "Permit", "summary": "Equipment lifting permit review"},
             {"id": "two", "title": "Notice", "summary": "Commissioning policy update"}]
    monkeypatch.setattr("app.adapters.sources.fetch_registered_source", lambda *args: {
        "status": "ok", "source_id": "feed", "body_hash": digest(items), "feed_items": items,
        "fetched_at": "2026-10-06T00:00:00+00:00"})
    assert len(_run_scan(db, {"project_id": "P", "data": {"scope": "notices"}})["new_event_ids"]) == 2
    items.reverse()
    assert not _run_scan(db, {"project_id": "P", "data": {"scope": "notices"}})["new_event_ids"]


def test_external_scan_to_review_approval_export(setup):
    db, client = setup
    event_id = _record_weather_risks(db, "P", PLAN, forecast(), "S")[0]
    assert run_once(db)
    run = next(row for row in db.list_json("runs", "P") if row["kind"] == "analysis")
    result = req(client, "get", f"/api/runs/{run['id']}")
    scenario = result["scenarios"][0]
    assert scenario["data"]["finish_date"] == "2026-10-12"
    assert scenario["data"]["finish_shift_days"] == 3
    assert {row["task_id"] for row in scenario["data"]["changed_tasks"]} == {"A", "B"}
    assert scenario["data"]["provisional"]
    req(client, "post", f"/api/scenarios/{scenario['id']}/prepare")
    req(client, "post", f"/api/scenarios/{scenario['id']}/approve", expected=409, json={"actor": "team"})
    req(client, "patch", f"/api/projects/P/events/{event_id}/review", json={"confirmed": True})
    req(client, "post", f"/api/scenarios/{scenario['id']}/approve", json={"actor": "team"})
    committed = req(client, "post", f"/api/scenarios/{scenario['id']}/commit")
    exported = client.get("/api/projects/P/export", headers={"Authorization": "Bearer test"})
    book = load_workbook(io.BytesIO(exported.content))
    assert book["변경 근거"]["C2"].value.startswith("https://api.open-meteo.com")
    assert committed["version_id"] != "V"


def test_all_combined_evidence_requires_review(setup):
    db, client = setup
    weather_id = _record_weather_risks(db, "P", PLAN, forecast(), "S")[0]
    config = {"country_code": "HU", "year": 2026, "task_ids": ["A"]}
    source = {"status": "ok", "source_id": "calendar", "holidays": [
        {"date": "2026-10-08", "countryCode": "HU", "types": ["Public"], "global": True}]}
    holiday_id = _record_holiday_risks(db, "P", config, source, "H")[0]
    assert run_once(db)
    result = db.list_json("scenarios", "P")[0]
    assert result["data"]["finish_date"] == "2026-10-13"
    assert len(result["data"]["included_events"]) == 2
    req(client, "post", f"/api/scenarios/{result['id']}/prepare")
    req(client, "patch", f"/api/projects/P/events/{weather_id}/review", json={"confirmed": True})
    req(client, "post", f"/api/scenarios/{result['id']}/approve", expected=409, json={"actor": "team"})
    req(client, "patch", f"/api/projects/P/events/{holiday_id}/review", json={"confirmed": True})
    req(client, "post", f"/api/scenarios/{result['id']}/approve", json={"actor": "team"})
    _record_weather_risks(db, "P", PLAN, forecast(10), "NEW")
    req(client, "post", f"/api/scenarios/{result['id']}/commit", expected=409)


def test_supplier_calendar_scoped_replace_and_project_isolation(setup):
    db, client = setup
    req(client, "post", "/api/projects/P/supplier-calendars", json={"supplier_id": "SUP-A", "label": "A", "unavailable_dates": ["2026-10-07"]})
    project = db.get_json("projects", "P")["data"]
    independent = [{**TASKS[1], "baseline_start": "2026-10-07", "baseline_finish": "2026-10-07", "predecessor_ids": []}]
    assert simulate(project, independent)["finish_date"] == "2026-10-07"
    assert simulate(project, TASKS)["finish_date"] == "2026-10-12"
    req(client, "post", "/api/projects/P/supplier-calendars", json={"supplier_id": "SUP-A", "label": "A", "unavailable_dates": []})
    assert not db.get_json("projects", "P")["data"]["supplier_unavailable_dates"]
    req(client, "post", "/api/projects", json={"project_id": "Q"})
    req(client, "post", "/api/projects/Q/supplier-calendars", json={"supplier_id": "SUP-A", "label": "Q", "unavailable_dates": ["2026-10-08"]})
    assert not db.get_json("projects", "P")["data"]["supplier_unavailable_dates"]
    assert len(db.list_json("supplier_calendars", "P")) == 1


@pytest.mark.parametrize("payload", [
    {**PLAN, "weather_task_ids": []},
    {**PLAN, "weather_site": {"latitude": 91, "longitude": 19}},
    {**PLAN, "weather_task_ids": ["UNKNOWN"]},
    {"enabled": True},
])
def test_watch_configuration_validation(setup, payload):
    _, client = setup
    req(client, "put", "/api/projects/P/watch-plan", expected=422, json=payload)


def test_review_cannot_invent_task_or_accept_undated_notice(setup, monkeypatch):
    db, client = setup
    db.put_json("watch_plans", "P", {"enabled": True, "source_allowlist": ["https://example.org/rss"]})
    monkeypatch.setattr("app.adapters.sources.fetch_registered_source", lambda *args: {
        "status": "ok", "source_id": "feed", "title": "Permit", "content": "Permitting may affect equipment lifting."})
    event_id = _run_scan(db, {"project_id": "P", "data": {"scope": "notices"}})["new_event_ids"][0]
    req(client, "patch", f"/api/projects/P/events/{event_id}/review", expected=422, json={"confirmed": True})
    req(client, "patch", f"/api/projects/P/events/{event_id}/review", expected=422, json={
        "confirmed": True, "patch": {"not_before": {"UNKNOWN": "2026-10-08"}}, "review_note": "confirmed"})


def test_holiday_adapter_and_feed_metadata(monkeypatch):
    payload = [{"date": "2026-10-23", "countryCode": "HU", "global": True, "types": ["Public"]}]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    result = fetch_holidays("HU", 2026, transport=transport)
    assert result["status"] == "ok" and result["holidays"] == payload
    assert fetch_holidays("../", 2026, transport=transport)["status"] == "error"
    monkeypatch.setattr("app.adapters.sources._assert_public_host", lambda host: None)
    xml = '<rss><channel><title>Official feed</title><item><guid>id1</guid><title>Notice</title><link>https://example.org/notice</link><pubDate>Tue, 06 Oct 2026 10:00:00 GMT</pubDate><description>Permit review</description></item></channel></rss>'
    result = fetch_registered_source("https://example.org/rss", ["example.org"], transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml)))
    assert result["feed_items"][0]["id"] == "id1"
    assert result["feed_items"][0]["published_at"].startswith("2026-10-06")
    assert result["feed_items"][0]["url"] == "https://example.org/notice"



def test_supplier_holiday_in_middle_of_task_is_not_ignored():
    project = {"supplier_calendars": [{"supplier_id": "SUP-A", "unavailable_dates": ["2026-10-08"]}]}
    assert simulate(project, TASKS)["finish_date"] == "2026-10-13"
    other = [{**TASKS[0], "supplier_id": "OTHER"}]
    assert simulate(project, other)["finish_date"] == "2026-10-08"


def test_approved_blocked_dates_survive_next_calculation():
    task = {**TASKS[0], "approved_blocked_dates": ["2026-10-08"]}
    assert simulate({}, [task])["finish_date"] == "2026-10-12"

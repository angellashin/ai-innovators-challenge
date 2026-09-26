from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.storage import Store, digest
from app.worker import _record_weather_risks, _reserve_paid_attempt, _run_scan, enqueue_due_scans, run_once


def test_due_scans_follow_independent_intervals(tmp_path):
    db = Store(tmp_path)
    db.put_json("watch_plans", "P", {
        "enabled": True,
        "weather_site": {"latitude": 47.5, "longitude": 19.0},
        "source_allowlist": ["https://example.org/news"],
        "weather_poll_hours": 6,
        "notice_poll_hours": 12,
    })
    start = datetime(2026, 9, 23, 0, tzinfo=timezone.utc)
    assert enqueue_due_scans(db, start) == 2
    assert enqueue_due_scans(db, start) == 0
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET status='succeeded', data=?", (json.dumps({"scope": "weather"}),))
        conn.execute("UPDATE runs SET data=? WHERE id=(SELECT id FROM runs ORDER BY rowid DESC LIMIT 1)", (json.dumps({"scope": "notices"}),))
        conn.execute("UPDATE runs SET created_at=?", (start.isoformat(),))
    assert enqueue_due_scans(db, start + timedelta(hours=7)) == 1


def test_weather_event_requires_explicit_limit_and_overlapping_outdoor_task(tmp_path):
    db = Store(tmp_path)
    project = {"mode": "LIVE", "data_origin": "USER"}
    snapshot = {"project": project, "tasks": [
        {"task_id": "T11", "outdoor": True, "baseline_start": "2026-10-07", "baseline_finish": "2026-10-07"},
        {"task_id": "T12", "outdoor": False, "baseline_start": "2026-10-07", "baseline_finish": "2026-10-07"},
    ]}
    db.put_json("projects", "P", project)
    db.put_json("versions", "V", snapshot, project_id="P", parent_id=None, status="baseline", content_hash=digest(snapshot))
    forecast = {"status": "ok", "source_id": "weather", "fetched_at": "2026-10-06T00:00:00+00:00", "forecast": {
        "units": {"wind_speed_10m_max": "km/h", "precipitation_sum": "mm"},
        "data": [{"date": "2026-10-07", "wind_speed_10m_max": 40, "precipitation_sum": 0}],
    }}
    assert not _record_weather_risks(db, "P", {"weather_limits": {}}, forecast, "S")
    created = _record_weather_risks(db, "P", {"weather_limits": {"max_wind_speed_kmh": 35}}, forecast, "S")
    assert len(created) == 1
    event = db.get_json("events", created[0])["data"]
    assert event["patch"] == {"blocked_dates": {"T11": ["2026-10-07"]}}
    assert event["mode"] == "LIVE" and event["data_origin"] == "PUBLIC"
    assert not _record_weather_risks(db, "P", {"weather_limits": {"max_wind_speed_kmh": 35}}, forecast, "S")


def test_paid_attempt_is_capped_and_not_retried(tmp_path, monkeypatch):
    db = Store(tmp_path)
    monkeypatch.setenv("REPLAN_MAX_PAID_RUNS_PER_DAY", "1")
    assert _reserve_paid_attempt(db, "R1") == "reserved"
    assert _reserve_paid_attempt(db, "R1") == "already_attempted"
    assert _reserve_paid_attempt(db, "R2") == "budget_stopped"


def test_claim_respects_one_active_run_per_project(tmp_path):
    db = Store(tmp_path)
    first = db.create_run("P", "analysis", "E1", "V", "first", {})
    db.create_run("P", "analysis", "E2", "V", "second", {})
    other = db.create_run("Q", "analysis", "E3", "V", "third", {})
    assert db.claim_next_run()["id"] == first["id"]
    assert db.claim_next_run()["id"] == other["id"]
    assert db.claim_next_run() is None


def test_weather_scan_queues_analysis_for_threshold_breach(tmp_path, monkeypatch):
    db = Store(tmp_path)
    project = {"mode": "LIVE", "data_origin": "USER"}
    snapshot = {"project": project, "tasks": [
        {"task_id": "T11", "outdoor": True, "baseline_start": "2026-10-07", "baseline_finish": "2026-10-07"},
    ]}
    db.put_json("projects", "P", project)
    db.put_json("versions", "V", snapshot, project_id="P", parent_id=None, status="baseline", content_hash=digest(snapshot))
    db.put_json("watch_plans", "P", {"enabled": True, "weather_site": {"id": "demo"}, "weather_limits": {"max_wind_speed_kmh": 35}, "source_allowlist": []})
    monkeypatch.setattr("app.adapters.sources.fetch_weather", lambda site: {
        "status": "ok", "source_id": "demo", "body_hash": "hash", "fetched_at": "2026-10-06T00:00:00+00:00",
        "forecast": {"units": {"wind_speed_10m_max": "km/h"}, "data": [{"date": "2026-10-07", "wind_speed_10m_max": 40}]},
    })
    scan = db.create_run("P", "scan", None, None, "test", {"scope": "weather"})
    assert run_once(db)
    result = db.get_json("runs", scan["id"])
    assert result["status"] == "succeeded" and len(result["data"]["new_event_ids"]) == 1, result["data"]
    assert len([item for item in db.list_json("runs", "P") if item["kind"] == "analysis"]) == 1


def test_registered_source_change_is_deduplicated(tmp_path, monkeypatch):
    db = Store(tmp_path)
    project = {"mode": "LIVE", "data_origin": "USER"}
    snapshot = {"project": project, "tasks": [{"task_id": "T01"}]}
    db.put_json("projects", "P", project)
    db.put_json("versions", "V", snapshot, project_id="P", parent_id=None, status="baseline", content_hash=digest(snapshot))
    url = "https://example.org/news"
    db.put_json("watch_plans", "P", {"enabled": True, "source_allowlist": [url]})
    monkeypatch.setattr("app.adapters.sources.fetch_registered_source", lambda *args: {
        "status": "ok", "source_id": "official", "body_hash": "unchanged", "summary": "새 공지",
        "fetched_at": "2026-09-23T00:00:00+00:00",
    })
    first = _run_scan(db, {"project_id": "P", "data": {"scope": "notices"}})
    second = _run_scan(db, {"project_id": "P", "data": {"scope": "notices"}})
    assert first["new_or_changed_count"] == 1 and len(first["new_event_ids"]) == 1
    assert second["new_or_changed_count"] == 0 and not second["new_event_ids"]
    assert len(db.list_json("events", "P")) == 1


def test_detected_change_waits_for_person_before_any_llm_call(tmp_path, monkeypatch):
    from app.adapters.llm import OpenAICompatibleLLM

    db = Store(tmp_path)
    project = {"mode": "LIVE", "data_origin": "USER"}
    snapshot = {"project": project, "tasks": [{"task_id": "T01", "name": "Environmental permit"}]}
    db.put_json("projects", "P", project)
    db.put_json("versions", "V", snapshot, project_id="P", parent_id=None, status="baseline", content_hash=digest(snapshot))
    url = "https://example.org/news"
    db.put_json("watch_plans", "P", {"enabled": True, "source_allowlist": [url]})
    monkeypatch.setattr("app.adapters.sources.fetch_registered_source", lambda *args: {
        "status": "ok", "source_id": "official", "body_hash": "h1", "title": "Environmental permit rule",
        "summary": "Environmental permit reviews may take longer.", "fetched_at": "2026-09-23T00:00:00+00:00"})
    for key, value in {"API_KEY": "k", "LLM_MODEL": "m", "LLM_BASE_URL": "https://gateway.invalid/v1",
                       "REPLAN_PAID_CALLS_ENABLED": "true"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("REPLAN_LLM_MODE", raising=False)

    def no_llm(*_args, **_kwargs):
        raise AssertionError("an automatically detected change must not call the LLM")

    monkeypatch.setattr(OpenAICompatibleLLM, "chat", no_llm)
    db.create_run("P", "scan", None, None, "scan", {"scope": "notices"})
    assert run_once(db)
    analysis = next(item for item in db.list_json("runs", "P") if item["kind"] == "analysis")
    assert analysis["data"]["auto_detected"] is True
    assert run_once(db)
    finished = db.get_json("runs", analysis["id"])
    assert finished["status"] == "succeeded", finished["data"]
    assert finished["data"]["agent"]["status"] == "waiting_review"

    from app.adapters.llm import ChatResult

    calls = []
    monkeypatch.setattr(OpenAICompatibleLLM, "chat", lambda self, *args, **kwargs: calls.append(1) or ChatResult(
        content=json.dumps({"candidates": [{"task_id": "T01", "quote": "Environmental permit reviews may take longer.",
                                            "reason": "permit review"}]}), usage={"prompt_tokens": 5}))
    started = db.create_run("P", "analysis", analysis["event_id"], "V", "person", {"project_context_snapshot": {}})
    assert run_once(db)
    person = db.get_json("runs", started["id"])["data"]
    assert len(calls) == 1 and person["status"] == "NEEDS_INPUT"
    assert person["agent"]["mode"] == "llm_interpretation"
    assert "T01" in person["agent"]["summary"] and "규칙 기반" not in person["agent"]["summary"]
    assert person["agent"]["stop_reason"].startswith("사람 확인 대기")

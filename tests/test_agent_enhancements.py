"""Agent proposals remain grounded, reviewable and isolated from evaluation answers."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.llm import ChatResult, OpenAICompatibleLLM
from app.importers import parse_upload
from app.main import ConfirmInput, app, normalize_import_snapshot, suggest_watch_plan
from app.risk_signals import evidence_for_supplier, search_risk_signals
from app.storage import Store
from app.supplier_interpreter import interpret_supplier_message
from app.task_retrieval import retrieve_related_tasks
from app.watch_suggestions import outdoor_candidate
from app.worker import run_once
from scripts.evaluate_agent import (_calculator_claims, _draft_claims, evaluate, evaluate_l3,
                                    evaluate_workflow, rescore_workflow_status_aliases)
from scripts.evaluation_mocks import MockEvaluationGateway

ROOT = Path(__file__).resolve().parents[1]
HERO = ROOT / "data/l1_project/hero_battery_factory_project.xlsx"


def _hero():
    parsed = parse_upload(HERO.name, HERO.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "Hero", "mode": "REPLAY"}, ConfirmInput())
    return parsed, snapshot["project"], snapshot["tasks"]


def test_supplier_llm_rejects_invented_date_task_and_quote():
    _, project, tasks = _hero()
    event = {"content": "[가상 메시지] T045 현장 반입 목표는 2026-12-28입니다.", "published_at": "2025-08-22T10:00:00+02:00"}

    class Gateway:
        def chat(self, *_args, **_kwargs):
            return ChatResult(content=json.dumps({"proposals": [
                {"task_id": "T999", "kind": "estimated_finish", "date": "2026-12-28", "quote": event["content"]},
                {"task_id": "T045", "kind": "estimated_finish", "date": "2026-12-30", "quote": event["content"]},
                {"task_id": "T045", "kind": "estimated_finish", "date": "2026-12-28", "quote": "invented evidence"},
            ]}))

    result = interpret_supplier_message(event, project, tasks, Gateway())
    assert result["patch"] == {}
    assert result["questions"]
    assert len(result["rejected_claims"]) == 3


def test_l2_search_excludes_same_source_case_and_keeps_citation():
    records = search_risk_signals(risk_type="labor", exclude_source_risk_id="RS-001", limit=10)["results"]
    assert records and all(row["risk_id"] != "RS-001" for row in records)
    assert all(row["source_url"].startswith("https://") and row["published_date"] for row in records)
    _, _, tasks = _hero()
    h02 = json.loads((ROOT / "data/hero_demo/supplier_messages.json").read_text(encoding="utf-8"))["events"][1]
    evidence = evidence_for_supplier(h02["content"], tasks, ["T037"], h02["published_at"])
    assert evidence and all("delay_days" not in row for row in evidence)
    assert all(row["temporal_status"] == "POST_AS_OF_REFERENCE" for row in evidence)


def test_task_retrieval_keeps_citations_and_excludes_weak_candidates():
    narrative = "설비가 현장에 도착한 뒤 반입 작업을 재검토합니다."

    class Gateway:
        def chat(self, *_args, **_kwargs):
            return ChatResult(content=json.dumps({"candidates": [
                {"task_id": "T045", "quote": "설비가 현장에 도착", "relevance": "high", "reason": "설비 반입 단계"},
                {"task_id": "T047", "quote": "반입 작업을 재검토", "relevance": "low", "reason": "설치 작업은 간접 관련"},
            ]}))

    result = retrieve_related_tasks(narrative, [
        {"task_id": "T045", "name": "Equipment delivery to site"},
        {"task_id": "T047", "name": "Module installation"},
    ], Gateway())
    assert result["task_ids"] == ["T045"]
    assert result["candidates"][0]["quote"] in narrative
    assert result["candidates"][0]["reason"]
    assert result["candidates"][1]["relevance"] == "low"


def test_mock_agent_workflow_metrics_cover_hero_and_variants():
    report = evaluate("mock")
    workflow = report["agent_workflow"]
    assert workflow["cases"] == 19
    assert workflow["execution_failures"] == 0
    assert workflow["required_tool_rate"] == 1
    assert workflow["ambiguous_stop_rate"] == 1
    assert workflow["unsupported_draft_numeric_rate"] == 0
    assert workflow["unconfirmed_regulation_as_confirmed_delay_rate"] == 0
    by_id = {row["case_id"]: row for row in workflow["rows"]}
    assert by_id["H02"]["tool_order"] == ["simulate_schedule", "recheck_shifted_schedule",
                                           "search_risk_signals", "simulate_regulatory_condition", "list_response_options"]
    assert by_id["H04"]["tool_order"] == ["simulate_schedule", "recheck_shifted_schedule",
                                           "list_response_options"]
    assert by_id["H04"]["rejected_tool_calls"] == []
    assert by_id["H04"]["ignored_tool_args"] == [{"tool": "list_response_options", "args": {"task_id": "T045"}}]
    assert by_id["V03"]["rejected_tool_calls"][0]["tool"] == "simulate_schedule"
    assert by_id["V03"]["rejected_tool_calls"][0]["args"]["unused_argument"] is True
    assert by_id["V03"]["rejected_tool_calls"][0]["reason"]
    assert by_id["V03"]["execution_failed"] is False
    assert by_id["H06"]["execution_failed"] is False
    assert by_id["H06"]["unresolved_items"]
    assert by_id["H06"]["summary"]
    retrieval = report["l3_affected_task_retrieval"]["agent_included"]
    assert retrieval["precision"] > 0.32
    assert retrieval["recall"] >= 0.75


def test_selected_mock_cases_skip_other_llm_work():
    report = evaluate("mock", {"H04"})
    assert report["selected_case_ids"] == ["H04"]
    assert report["agent_workflow"]["cases"] == 1
    assert report["supplier"]["agent_included"] is None
    assert report["l3_affected_task_retrieval"]["agent_included"] is None


def test_selected_real_cases_skip_supplier_and_l3_paid_calls(monkeypatch):
    import scripts.evaluate_agent as evaluation

    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "true")
    monkeypatch.setenv("REPLAN_MAX_PAID_RUNS_PER_DAY", "20")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setattr(evaluation, "OpenAICompatibleLLM", MockEvaluationGateway)
    report = evaluation.evaluate("real", {"H04"})
    assert report["llm_call_count"] == report["expected_paid_call_count"]
    assert report["agent_workflow"]["cases"] == 1
    assert report["supplier"]["agent_included"] is None
    assert report["l3_affected_task_retrieval"]["agent_included"] is None


def test_known_review_statuses_can_be_rescored_without_llm_calls():
    report = {"agent_workflow": {"rows": [
        {"case_id": "H04", "status": "NEEDS_APPROVAL", "execution_failed": True,
         "required_tools_called": False, "ambiguous_stopped": False,
         "tool_order": ["recheck_shifted_schedule"], "rejected_tool_calls": []},
        {"case_id": "H02", "status": "invalid_tool_args", "execution_failed": True,
         "required_tools_called": False, "ambiguous_stopped": False,
         "tool_order": [], "rejected_tool_calls": [{"tool": "simulate_schedule"}]},
    ]}}
    rescored = rescore_workflow_status_aliases(report)
    assert report["agent_workflow"]["rows"][0]["execution_failed"] is True
    assert rescored["offline_rescored_case_ids"] == ["H04"]
    assert rescored["agent_workflow"]["execution_failures"] == 1
    assert rescored["agent_workflow"]["rows"][0]["status_category"] == "needs_review"


def test_ambiguous_no_tool_stop_and_uppercase_status_are_valid(monkeypatch):
    import scripts.evaluate_agent as evaluation

    _, project, tasks = _hero()
    monkeypatch.setattr(evaluation, "run_agent", lambda _context, _event, _tools, **_kwargs: {
        "status": "NEEDS_INPUT", "tool_log": [], "summary": "작업 확인 필요",
        "unresolved_items": ["작업 ID"]})
    workflow = evaluate_workflow(project, tasks, [], MockEvaluationGateway(), {"H06"})
    assert workflow["execution_failures"] == 0
    assert workflow["rows"][0]["ambiguous_stopped"] is True


def test_workflow_accepts_recheck_as_calculation_and_excludes_empty_tool_runs(monkeypatch):
    import scripts.evaluate_agent as evaluation

    _, project, tasks = _hero()
    monkeypatch.setattr(evaluation, "run_agent", lambda _context, _event, _tools, **_kwargs: {
        "status": "completed", "tool_log": [{"tool": "recheck_shifted_schedule", "status": "ok",
                                              "result": {"finish_date": "2027-01-01"}}]})
    workflow = evaluate_workflow(project, tasks, [], MockEvaluationGateway())
    h04 = next(row for row in workflow["rows"] if row["case_id"] == "H04")
    assert h04["required_tools_called"] is True

    monkeypatch.setattr(evaluation, "run_agent", lambda _context, _event, _tools, **_kwargs: {
        "status": "completed", "tool_log": []})
    failed = evaluate_workflow(project, tasks, [], MockEvaluationGateway())
    assert failed["execution_failures"] == 19
    assert failed["required_tool_rate"] == 0
    assert failed["ambiguous_stop_rate"] == 0


def test_draft_claims_count_dates_and_quantities_without_incidental_digits():
    draft = {"body": "T045 일정 2027-01-03, 추가 9일, 비용 1,200원과 $500. 선택지 1을 검토합니다."}
    claims = _draft_claims(draft)
    assert claims == ["2027-01-03", "9", "1200", "500"]
    allowed = _calculator_claims({"finish_date": "2027-01-03", "finish_shift_days": 9,
                                  "cost": 1200, "foreign_cost": 500, "other_value": 19})
    assert all(claim in allowed for claim in claims)
    assert "90" not in allowed


def test_generic_outdoor_suggestions_and_review_gate(tmp_path, monkeypatch):
    parsed, project, tasks = _hero()
    expected = set(json.loads((ROOT / "data/hero_demo/outdoor_tasks.json").read_text(encoding="utf-8"))["outdoor_task_ids"])
    assert {task["task_id"] for task in parsed["tasks"] if outdoor_candidate(task)} == expected
    generic = [{"task_id": "A", "name": "기초 굴착", "phase": "토목", "country": "Hungary",
                "baseline_start": "2027-01-01", "baseline_finish": "2027-01-20", "supplier_id": "Vendor X", "risk_tags": "weather, permitting"}]
    plan = suggest_watch_plan({"region": "Site"}, generic)
    assert plan["weather_task_ids"] == ["A"]
    assert plan["holiday_calendars"][0] == {"country_code": "HU", "year": 2027, "task_ids": ["A"]}
    assert any(item["kind"] == "supplier_calendar" for item in plan["proposal_items"])

    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    monkeypatch.delenv("API_KEY", raising=False)
    client = TestClient(app)
    header = {"Authorization": "Bearer test-token"}
    project_id = client.post("/api/projects", headers=header, json={"mode": "REPLAY"}).json()["project_id"]
    imported = client.post(f"/api/projects/{project_id}/imports", headers=header,
                           files={"file": (HERO.name, HERO.read_bytes())}).json()
    confirmed = client.post(f"/api/projects/{project_id}/imports/{imported['import_id']}/confirm", headers=header, json={}).json()
    proposal = confirmed["watch_plan_suggestion"]
    assert len(proposal["holiday_calendars"]) == 7
    rejected = client.put(f"/api/projects/{project_id}/watch-plan", headers=header, json={**proposal, "enabled": True})
    assert rejected.status_code == 422
    proposal["proposal_items"] = [{**item, "decision": "accepted"} for item in proposal["proposal_items"]]
    accepted = client.put(f"/api/projects/{project_id}/watch-plan", headers=header, json={**proposal, "enabled": True})
    assert accepted.status_code == 200, accepted.text


def test_hero_variant_llm_fallback_reaches_existing_external_recheck(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    monkeypatch.delenv("API_KEY", raising=False)
    client = TestClient(app)
    header = {"Authorization": "Bearer test-token"}
    project_id = client.post("/api/projects", headers=header, json={"mode": "REPLAY"}).json()["project_id"]
    imported = client.post(f"/api/projects/{project_id}/imports", headers=header,
                           files={"file": (HERO.name, HERO.read_bytes())}).json()
    client.post(f"/api/projects/{project_id}/imports/{imported['import_id']}/confirm", headers=header, json={})
    message = json.loads((ROOT / "data/hero_demo/supplier_message_variants.json").read_text(encoding="utf-8"))["events"][3]
    created = client.post(f"/api/projects/{project_id}/events", headers=header, json=message).json()
    event_id = created["event_id"]
    assert not created["event"]["patch"]
    gateway = MockEvaluationGateway()
    monkeypatch.setattr(OpenAICompatibleLLM, "chat", lambda self, *args, **kwargs: gateway.chat(*args, **kwargs))
    monkeypatch.setenv("API_KEY", "mock-only")
    monkeypatch.setenv("LLM_MODEL", "mock")
    monkeypatch.setenv("LLM_BASE_URL", "https://mock.invalid/v1")
    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "true")
    queued = client.post(f"/api/projects/{project_id}/analyses", headers=header,
                         json={"event_id": event_id, "preview_only": True}).json()
    assert run_once(Store())
    result = client.get(f"/api/runs/{queued['run_id']}", headers=header).json()
    event = Store().get_json("events", event_id, project_id)["data"]
    assert event["patch"] == {"estimated_finish": {"T045": "2026-12-28"}}
    assert event["review_status"] == "PENDING"
    scenario = next(row["data"] for row in result["scenarios"] if row["data"]["option_ids"] == [])
    assert scenario["external_constraints"]
    assert scenario["supplier_finish_shift_days"] > 0
    assert scenario["provisional"]
    assert gateway.calls >= 4
    assert [row["tool"] for row in result["run"]["data"]["agent"]["tool_log"][:2]] == [
        "simulate_schedule", "recheck_shifted_schedule"]
    past = client.post(f"/api/projects/{project_id}/events", headers=header, json={
        "event_id": "PAST", "channel": "supplier_message", "source_label": "가상 검증",
        "content": "[가상 메시지] T001 조사 납기를 2026-12-28로 재통지합니다.",
        "published_at": "2025-08-22T10:00:00+02:00", "mode": "SYNTHETIC"}).json()
    past_run = client.post(f"/api/projects/{project_id}/analyses", headers=header,
                           json={"event_id": past["event_id"], "preview_only": True}).json()
    assert run_once(Store())
    blocked = client.get(f"/api/runs/{past_run['run_id']}", headers=header).json()
    assert blocked["run"]["data"]["status"] == "NEEDS_INPUT"
    assert not Store().get_json("events", past["event_id"], project_id)["data"]["patch"]


def test_l3_agent_input_excludes_answers_and_identical_l2_event():
    _, project, tasks = _hero()

    class Spy(MockEvaluationGateway):
        def __init__(self):
            super().__init__()
            self.payloads = []

        def chat(self, messages, **kwargs):
            self.payloads.append(json.loads(messages[-1]["content"]))
            return super().chat(messages, **kwargs)

    spy = Spy()
    report = evaluate_l3(project, tasks, spy)
    assert report["cases"] == 6
    assert spy.payloads
    for payload in spy.payloads:
        assert "direct_task_ids" not in json.dumps(payload)
        assert "ground_truth" not in json.dumps(payload)
    assert "RS-001" not in next(row["l2_evidence_risk_ids"] for row in report["rows"] if row["case_id"] == "V2-001")


def test_runtime_import_does_not_open_answer_files(monkeypatch):
    original = Path.read_text
    forbidden = {"ground_truth.json", "l3_to_wbs_mapping.json", "supplier_message_ground_truth.json",
                 "supplier_message_variants_ground_truth.json", "outdoor_tasks.json"}

    def guarded(path, *args, **kwargs):
        if path.name in forbidden:
            raise AssertionError(f"runtime read evaluation answer: {path.name}")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    parsed = parse_upload(HERO.name, HERO.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "Hero", "mode": "REPLAY"}, ConfirmInput())
    assert len(snapshot["tasks"]) == 64
    assert snapshot["tasks"][20]["outdoor"] is True
    dockerfile = (ROOT / "services/api/Dockerfile").read_text(encoding="utf-8")
    assert not any(name in dockerfile for name in forbidden)


def test_paid_watch_enrichment_changes_reasons_only(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    monkeypatch.delenv("API_KEY", raising=False)
    client = TestClient(app)
    header = {"Authorization": "Bearer test-token"}
    project_id = client.post("/api/projects", headers=header, json={"mode": "REPLAY"}).json()["project_id"]
    imported = client.post(f"/api/projects/{project_id}/imports", headers=header,
                           files={"file": (HERO.name, HERO.read_bytes())}).json()
    confirmed = client.post(f"/api/projects/{project_id}/imports/{imported['import_id']}/confirm", headers=header, json={}).json()
    db = Store()
    before = db.get_json("watch_plans", project_id)["data"]
    proposal_id = before["proposal_items"][0]["id"]
    monkeypatch.setenv("API_KEY", "mock-only")
    monkeypatch.setenv("LLM_MODEL", "mock")
    monkeypatch.setenv("LLM_BASE_URL", "https://mock.invalid/v1")
    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "true")
    monkeypatch.setattr(OpenAICompatibleLLM, "chat", lambda self, *_args, **_kwargs: ChatResult(
        content=json.dumps({"items": [{"id": proposal_id, "reason": "현장 예보 검토", "keywords": ["weather"]}]}), model="mock"))
    db.create_run(project_id, "watch_plan_enrich", None, confirmed["version_id"], "watch-test", {})
    assert run_once(db)
    after = db.get_json("watch_plans", project_id)["data"]
    assert after["proposal_items"][0]["agent_note"] == "현장 예보 검토"
    assert after["proposal_items"][0]["decision"] == "proposed"
    assert after["holiday_calendars"] == before["holiday_calendars"]
    assert after["enabled"] is False

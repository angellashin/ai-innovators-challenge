"""Baseline briefing, change triage and investigation share one risk register."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.llm import ChatResult, OpenAICompatibleLLM
from app.main import app
from app.storage import Store
from app.worker import run_once

ROOT = Path(__file__).resolve().parents[1]
LOOP = json.loads((ROOT / "data/hero_demo/external_loop_signals.json").read_text(encoding="utf-8"))
NX2_QUOTE = "every shipment of rare-earth permanent magnets and parts containing them"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.adapters import sources

    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    for key in ("REPLAN_LLM_MODE", "API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sources, "fetch_holidays", lambda *_a, **_k: pytest.fail("no network in hero demo"))
    return TestClient(app)


def call(client, method, path, **kwargs):
    response = getattr(client, method)(path, headers={"Authorization": "Bearer test-token"}, **kwargs)
    assert response.status_code < 400, response.text
    return response.json()


def drain():
    while run_once(Store()):
        pass


def enable_agent(monkeypatch, replies):
    for key, value in {"API_KEY": "k", "LLM_MODEL": "m", "LLM_BASE_URL": "https://gateway.invalid/v1",
                       "REPLAN_PAID_CALLS_ENABLED": "true"}.items():
        monkeypatch.setenv(key, value)
    requests = []

    def chat(self, messages, tools=None, response_format=None):
        requests.append({"messages": messages, "tools": [tool["function"]["name"] for tool in tools or []]})
        reply = replies.pop(0)
        if isinstance(reply, list):  # several native tool calls in one turn
            return ChatResult(tool_calls=[{"id": f"c{index}", "name": name, "arguments": args}
                                          for index, (name, args) in enumerate(reply)], usage={"prompt_tokens": 10})
        return ChatResult(content=json.dumps(reply, ensure_ascii=False), usage={"prompt_tokens": 10})

    monkeypatch.setattr(OpenAICompatibleLLM, "chat", chat)
    return requests


def tool(name, **args):
    return {"action": "tool", "tool": name, "args": args}


def hero(client):
    project_id = call(client, "post", "/api/projects", json={"mode": "REPLAY"})["project_id"]
    call(client, "post", f"/api/projects/{project_id}/demo/hero-baseline")
    return project_id


def test_rules_brief_the_rare_earth_items_with_a_critical_path_warning(client):
    project_id = hero(client)
    drain()
    project = call(client, "get", f"/api/projects/{project_id}")
    briefing = project["briefing"]["briefing"]
    assert project["briefing"]["agent"]["mode"] == "rules_only"
    risks = briefing["risks"]
    assert 3 <= len(risks) <= 5
    first = risks[0]
    assert first["risk_id"] == "R-import-CN" and first["item_ids"] == ["P-A1", "P-B", "P-C"]
    by_item = {row["item_id"]: row for row in first["items"]}
    assert by_item["P-C"]["float_days"] == 0 and by_item["P-C"]["vulnerability"]["label"] == "완충 기간 없음 · 지연 시 완료일 영향"
    assert by_item["P-B"]["float_days"] == 245
    assert any(warning.startswith("P-C: 일정 완충 기간이 없습니다.") for warning in first["critical_warnings"])
    assert first["actions"][0] == {"target": "P-C", "what": "P-C의 원산지 증명, 교정 성적서 준비 상황을 "
                                   "Equipment Vendor A에 확인", "by": "2027-03-26", "basis": "도착 예정일"}
    # The workbook states neutral facts only; the rules never read a risk out of it.
    procurement = call(client, "get", f"/api/projects/{project_id}")["version"]["data"]["procurement"]
    assert not any("수출 허가" in str(item.get("permit_or_certification")) for item in procurement)
    assert first["title"] == "중국산 통관 품목" and "linked_cause" not in first
    assert briefing["as_of"] == "2025-08-20" and briefing["evidence_as_of"] == "2025-10-20"
    # No delay estimate anywhere, and missing attributes are named, not hidden.
    text = json.dumps(briefing, ensure_ascii=False)
    assert "delay_days" not in text and "일 지연" not in text and "일 늦" not in text
    assert briefing["coverage"]["missing"]["origin_country"] == 45
    permit = next(risk for risk in risks if risk["cause"] == "permit")
    assert all(row["origin_country"] == "속성 없음" for row in permit["tasks"])
    register = project["risks"]
    assert [row["risk_id"] for row in register] == [risk["risk_id"] for risk in risks]
    assert register[0]["status"] == "EXPECTED" and register[0]["history"][0]["actor"] == "briefing"


def test_briefing_agent_checks_at_most_three_times_and_the_calculator_keeps_the_numbers(client, monkeypatch):
    project_id = hero(client)
    requests = enable_agent(monkeypatch, [
        [("check_schedule_slack", {"reason": "P-C가 필요한 T051 여유를 다시 봅니다.", "task_ids": ["T051"]}),
         ("find_procurement_items", {"reason": "같은 협력사·원산지의 다른 통관 품목이 있는지 봅니다.",
                                     "supplier_id": "Equipment Vendor A", "origin_country": "China"})],
        tool("get_task_facts", reason="한국산 설비 통관 작업의 속성을 봅니다.", task_ids=["T040"]),
        tool("get_task_facts", reason="네 번째 확인", task_ids=["T042"]),
        tool("search_risk_cases", reason="수출 통제 사례를 봅니다.", query="export_control"),
        {"summary": "희토류 자석 품목이 가장 취약합니다.", "status": "completed", "briefing": {"risks": [
            {"risk_key": "import:CN", "decision": "keep", "reason": "P-C가 주공정", "added_item_ids": ["P-Z"],
             "case_query": "export_control", "note": "P-C는 여유 0일이며 999일 늦으면 안 됩니다.",
             "linked_cause": {"text": "희토류 자석 선적별 수출 통제", "case_ids": ["RS-019"]}},
            {"risk_key": "permit", "decision": "keep", "reason": "인허가",
             "linked_cause": {"text": "읽지 않은 사례로 붙인 원인", "case_ids": ["RS-017"]}},
            {"risk_key": "origin:KR", "decision": "exclude", "reason": "제작은 이미 발주된 설비라 원산지 위험이 작습니다."},
            {"risk_key": "customs:KR", "decision": "exclude", "reason": ""}]}},
        {"items": []},  # watch-plan enrichment
    ])
    drain()
    briefing = call(client, "get", f"/api/projects/{project_id}")["briefing"]
    tools = [row["tool"] for row in briefing["agent"]["tool_log"]]
    assert tools == ["check_schedule_slack", "find_procurement_items", "get_task_facts", "get_task_facts",
                     "search_risk_cases"]
    assert briefing["agent"]["tool_log"][3]["result"]["status"] == "limit_reached"
    assert requests[0]["tools"] == ["find_procurement_items", "get_task_facts", "check_schedule_slack", "search_risk_cases"]
    risks = briefing["briefing"]["risks"]
    first = risks[0]
    # An item no tool returned is ignored; a number the calculator never produced is removed from the note.
    assert first["item_ids"] == ["P-A1", "P-B", "P-C"] and first["added_item_ids"] == []
    assert "999" not in first["agent_note"] and first["agent_note"].startswith("P-C는 여유 0일")
    # A cause the workbook does not state stays only with an L2 case the agent read, judged at the evidence date.
    assert first["linked_cause"]["text"] == "희토류 자석 선적별 수출 통제" and first["linked_cause"]["case_ids"] == ["RS-019"]
    assert first["linked_cause"]["cases"][0]["temporal_status"] == "AVAILABLE_AS_OF"
    assert [case["risk_id"] for case in first["cases"]] == ["RS-019"]
    assert "linked_cause" not in next(risk for risk in risks if risk["cause"] == "permit")
    excluded = briefing["briefing"]["excluded"]
    assert [row["risk_key"] for row in excluded] == ["origin:KR"]  # no reason, no exclusion
    assert "R-customs-KR" in [risk["risk_id"] for risk in risks]


def test_triage_sorts_a_detected_notice_links_the_register_and_keeps_related_tasks(client, monkeypatch):
    project_id = hero(client)
    drain()
    requests = enable_agent(monkeypatch, [{
        "candidates": [
            {"task_id": "T051", "relevance": "related", "quote": NX2_QUOTE, "reason": "P-C 희토류 자석 포함"},
            {"task_id": "T058", "relevance": "needs_check", "quote": NX2_QUOTE, "reason": "시뮬레이터 구동부 자석"},
            {"task_id": "T042", "relevance": "unrelated", "reason": "한국산 설비 통관"},
            {"task_id": "T999", "relevance": "related", "quote": NX2_QUOTE, "reason": "없는 작업"}],
        "risk_links": [{"risk_id": "R-import-CN", "quote": NX2_QUOTE}, {"risk_id": "R-nope", "quote": NX2_QUOTE}]}])
    event_id = call(client, "post", f"/api/projects/{project_id}/demo/external-signals/N-X2")["event_ids"][0]
    before = Store().get_json("events", event_id, project_id)["data"]
    drain()
    event = Store().get_json("events", event_id, project_id)["data"]
    triage = event["auto_narrow"]
    assert triage["status"] == "interpreted" and len(requests) == 1
    assert [row["task_id"] for row in triage["related"]] == ["T051"]
    assert [row["task_id"] for row in triage["needs_check"]] == ["T058"]
    assert "T042" in [row["task_id"] for row in triage["unrelated"]]
    # The triage never replaces the rule candidates a later supplier notice links by.
    assert event["related_task_ids"] == before["related_task_ids"] and event["candidates"] == before["candidates"]
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert payload["risk_register"][0]["risk_id"] == "R-import-CN" and "status" not in payload["risk_register"][0]
    assert any(item["item_id"] == "P-C" for item in payload["purchase_items"])
    risks = {row["risk_id"]: row for row in call(client, "get", f"/api/projects/{project_id}")["risks"]}
    assert risks["R-import-CN"]["status"] == "SIGNAL_DETECTED"
    assert risks["R-import-CN"]["links"][0]["actor"] == "triage"
    with Store().connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auto_usage_ledger").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM usage_ledger").fetchone()[0] == 0


def test_triage_without_candidates_or_over_its_limit_leaves_the_rules(client, monkeypatch):
    project_id = hero(client)
    drain()
    enable_agent(monkeypatch, [])
    monkeypatch.setenv("REPLAN_MAX_AUTO_TRIAGE_PER_DAY", "0")
    event_id = call(client, "post", f"/api/projects/{project_id}/demo/external-signals/N-X2")["event_ids"][0]
    drain()
    triage = Store().get_json("events", event_id, project_id)["data"]["auto_narrow"]
    assert triage["status"] == "budget_stopped" and "한도(0회)" in triage["summary"]
    # The person's own limit is untouched by the automatic triage.
    assert call(client, "get", "/api/usage")["auto_triage_today"] == 0

    from app.worker import _auto_narrow
    record = _auto_narrow(Store(), {"id": "r", "event_id": "e", "project_id": project_id}, {"candidates": []}, {})
    assert record["status"] == "no_candidates" and record["related"] == []


def test_supplier_investigation_links_the_expected_risk_and_confirmation_moves_it(client, monkeypatch):
    project_id = hero(client)
    drain()
    call(client, "post", f"/api/projects/{project_id}/demo/external-signals/N-X2")
    message = next(item for item in LOOP["supplier_messages"] if item["event_id"] == "X2")
    event = call(client, "post", f"/api/projects/{project_id}/events", json={
        **message, "mode": "REPLAY", "data_origin": "SYNTHETIC", "simulation_as_of": message["published_at"]})
    event_id = event["event_id"]
    call(client, "patch", f"/api/projects/{project_id}/events/{event_id}/review", json={"confirmed": True})
    drain()
    quote = "Licence review may take up to 60 days after application."
    requests = enable_agent(monkeypatch, [
        tool("find_procurement_items", reason="같은 원인의 이후 품목", supplier_id="Equipment Vendor A",
             origin_country="China", customs_required=True, arriving_after="2026-03-07"),
        tool("check_schedule_slack", reason="여유", task_ids=["T058", "T051"], bound_days=60),
        tool("simulate_conditional", reason="P-C 최악", changes=[
            {"kind": "hold_after_arrival", "item_id": "P-C", "value": 60, "fact_quote": quote}]),
        {"summary": "P-C가 늦으면 2028-02-11", "status": "needs_input", "stop_reason": "P-C 확인",
         "investigation": {"stop": "M4", "question": "P-C도 허가가 필요합니까?", "checks": [],
                           "risk_link": {"risk_id": "R-import-CN",
                                         "reason": "등록 시 예상한 중국산 희토류 자석 품목 수출 허가 위험이 P-A1에서 발생했습니다."}},
         "email_draft": None},
        {"summary": "재계산", "status": "needs_review", "stop_reason": "", "option_explanations": []},
    ])
    queued = call(client, "post", f"/api/projects/{project_id}/events/{event_id}/investigations")
    assert run_once(Store())
    run = call(client, "get", f"/api/runs/{queued['run_id']}")["run"]
    context = json.loads(requests[0]["messages"][1]["content"])["context"]
    assert context["risk_register"][0]["item_ids"] == ["P-A1", "P-B", "P-C"]
    assert "export_control" in context["case_queries"]
    link = run["data"]["risk_link"]
    assert link["risk_id"] == "R-import-CN" and link["status"] == "OCCURRED"
    assert link["expected_by"] == "briefing" and link["previous_status"] == "EXPECTED"
    assert any(warning.startswith("P-C:") for warning in link["critical_warnings"])
    resolved = call(client, "post", f"/api/projects/{project_id}/investigations/{run['id']}/resolve",
                    json={"decision": "applies", "note": "P-C도 대상"})
    assert resolved["analysis_run_id"]
    risk = next(row for row in call(client, "get", f"/api/projects/{project_id}")["risks"]
                if row["risk_id"] == "R-import-CN")
    assert [row["status"] for row in risk["history"]] == ["EXPECTED", "OCCURRED", "RESPONDING"]
    closed = call(client, "patch", f"/api/projects/{project_id}/risks/R-import-CN",
                  json={"status": "CLOSED", "note": "대응안 확정"})["risk"]
    assert closed["status"] == "CLOSED" and closed["history"][-1]["actor"] == "person"


def test_a_notice_investigation_uses_the_triage_instead_of_sorting_again(client, monkeypatch):
    project_id = hero(client)
    drain()
    quote = "[합성] 헝가리 지방정부 인허가 접수분의 처리 기간이 2025-10-01부터 최대 45일 늘어날 수 있습니다."
    requests = enable_agent(monkeypatch, [
        {"candidates": [{"task_id": "T013", "relevance": "related", "quote": quote[5:40], "reason": "지방 인허가"}],
         "risk_links": [{"risk_id": "R-permit", "quote": quote[5:40]}]},
        tool("check_schedule_slack", reason="45일을 흡수하는지 봅니다.", task_ids=["T013"], bound_days=45),
        {"summary": "T013은 45일을 흡수합니다.", "status": "completed", "stop_reason": "",
         "investigation": {"stop": "M2", "applicable_task_ids": ["T013"], "question": "", "checks": [],
                           "risk_link": {"risk_id": "R-permit", "reason": "같은 인허가 처리 지연"}}},
    ])
    event_id = call(client, "post", f"/api/projects/{project_id}/demo/external-signals/X1-B")["event_ids"][0]
    drain()
    queued = call(client, "post", f"/api/projects/{project_id}/events/{event_id}/investigations")
    assert run_once(Store())
    run = call(client, "get", f"/api/runs/{queued['run_id']}")["run"]
    assert run["data"]["status"] == "M2" and len(requests) == 3
    assert "narrow_candidates" not in requests[1]["tools"]
    context = json.loads(requests[1]["messages"][1]["content"])["context"]
    assert [row["task_id"] for row in context["auto_narrow"]["related"]] == ["T013"]
    assert run["data"]["risk_link"]["status"] == "SIGNAL_DETECTED"


def test_needs_check_tasks_are_calculated_only_when_a_person_picks_them(client, monkeypatch):
    project_id = hero(client)
    drain()
    quote = "헝가리 지방정부 인허가 접수분의 처리 기간이"
    enable_agent(monkeypatch, [
        {"candidates": [{"task_id": "T013", "relevance": "related", "quote": quote, "reason": "지방 인허가"},
                        {"task_id": "T012", "relevance": "needs_check", "quote": quote, "reason": "관할 확인 필요"}],
         "risk_links": []}])
    event_id = call(client, "post", f"/api/projects/{project_id}/demo/external-signals/X1-B")["event_ids"][0]
    drain()
    refused = client.post(f"/api/projects/{project_id}/events/{event_id}/investigations",
                          headers={"Authorization": "Bearer test-token"}, json={"include_task_ids": ["T021"]})
    assert refused.status_code == 422

    def investigate(include, replies):
        requests = enable_agent(monkeypatch, replies)
        queued = call(client, "post", f"/api/projects/{project_id}/events/{event_id}/investigations",
                      json={"include_task_ids": include})
        assert run_once(Store())
        return requests, call(client, "get", f"/api/runs/{queued['run_id']}")["run"]

    final = {"summary": "기록", "status": "completed", "investigation": {"stop": "M2", "question": "", "checks": []}}
    requests, run = investigate([], [tool("check_schedule_slack", reason="두 작업 여유", task_ids=["T013", "T012"],
                                          bound_days=45), final])
    context = json.loads(requests[0]["messages"][1]["content"])["context"]["auto_narrow"]
    assert [row["task_id"] for row in context["related"]] == ["T013"]
    assert context["needs_check_left_for_person_count"] == 1 and "needs_check_selected_by_person" not in context
    refused = run["data"]["agent"]["tool_log"][0]["result"]
    assert refused["status"] == "rejected" and refused["outside_scope"] == ["T012"]

    requests, run = investigate(["T012"], [tool("check_schedule_slack", reason="사람이 고른 T012 포함", task_ids=["T013", "T012"],
                                                bound_days=45), final])
    context = json.loads(requests[0]["messages"][1]["content"])["context"]["auto_narrow"]
    assert [row["task_id"] for row in context["needs_check_selected_by_person"]] == ["T012"]
    slack = {row["task_id"]: row for row in run["data"]["agent"]["tool_log"][0]["result"]["tasks"]}
    assert slack["T013"]["absorbs_bound"] is True and slack["T012"]["absorbs_bound"] is False


def test_starting_the_watch_brings_the_demo_notice_in_once(client):
    project_id = hero(client)
    drain()
    assert call(client, "get", f"/api/projects/{project_id}")["events"] == []  # nothing arrives with the baseline
    started = call(client, "post", f"/api/projects/{project_id}/watch/start")
    assert started["mode"] == "demo_simulation" and len(started["event_ids"]) == 1
    event = Store().get_json("events", started["event_ids"][0], project_id)["data"]
    assert event["demo_signal_id"] == "N-X2" and event["channel"] == "registered_public_source"
    again = call(client, "post", f"/api/projects/{project_id}/watch/start")
    assert again["event_ids"] == [] and again["watch_started_at"] == started["watch_started_at"]
    assert call(client, "get", f"/api/projects/{project_id}")["project"]["watch_started_at"] == started["watch_started_at"]

    other = call(client, "post", "/api/projects", json={"mode": "LIVE"})["project_id"]
    assert client.post(f"/api/projects/{other}/watch/start", headers={"Authorization": "Bearer test-token"}).status_code == 409

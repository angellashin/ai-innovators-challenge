"""A person-started investigation runs through the API and worker with a scripted gateway."""

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
QUOTE = "Licence review may take up to 60 days after application."


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.adapters import sources

    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    monkeypatch.delenv("REPLAN_LLM_MODE", raising=False)
    for key in ("API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sources, "fetch_holidays", lambda *_a, **_k: pytest.fail("no network in hero demo"))
    return TestClient(app)


def call(client, method, path, **kwargs):
    response = getattr(client, method)(path, headers={"Authorization": "Bearer test-token"}, **kwargs)
    assert response.status_code < 400, response.text
    return response.json()


def enable_agent(monkeypatch, replies):
    for key, value in {"API_KEY": "k", "LLM_MODEL": "m", "LLM_BASE_URL": "https://gateway.invalid/v1",
                       "REPLAN_PAID_CALLS_ENABLED": "true"}.items():
        monkeypatch.setenv(key, value)
    requests = []

    def chat(self, messages, tools=None, response_format=None):
        requests.append({"messages": messages, "tools": [tool["function"]["name"] for tool in tools or []]})
        return ChatResult(content=json.dumps(replies.pop(0), ensure_ascii=False), usage={"prompt_tokens": 10})

    monkeypatch.setattr(OpenAICompatibleLLM, "chat", chat)
    return requests


def load(client, message_id):
    project_id = call(client, "post", "/api/projects", json={"mode": "REPLAY"})["project_id"]
    call(client, "post", f"/api/projects/{project_id}/demo/hero-baseline")
    call(client, "post", f"/api/projects/{project_id}/demo/external-signals/N-X2")
    message = next(item for item in LOOP["supplier_messages"] if item["event_id"] == message_id)
    event = call(client, "post", f"/api/projects/{project_id}/events", json={
        **message, "mode": "REPLAY", "data_origin": "SYNTHETIC", "simulation_as_of": message["published_at"]})
    call(client, "patch", f"/api/projects/{project_id}/events/{event['event_id']}/review", json={"confirmed": True})
    while run_once(Store()):
        pass
    return project_id, event["event_id"]


def tool(name, **args):
    return {"action": "tool", "tool": name, "args": args}


def test_x2_investigation_finds_c_and_leaves_one_request(client, monkeypatch):
    project_id, event_id = load(client, "X2")
    project = call(client, "get", f"/api/projects/{project_id}")
    assert project["related_signals"][event_id][0]["overlapping_task_ids"] == ["T042"]

    requests = enable_agent(monkeypatch, [
        tool("find_procurement_items", reason="통보 사유가 공지와 같다면 같은 협력사·원산지의 이후 통관 품목도 서류가 필요합니다.",
             supplier_id="Equipment Vendor A", origin_country="China", customs_required=True,
             arriving_after="2026-03-07"),
        tool("check_schedule_slack", reason="찾은 품목이 필요한 작업이 30일 검토를 흡수하는지 봅니다.",
             task_ids=["T058", "T051"], bound_days=60),
        tool("simulate_conditional", reason="값이 원문에 없는 보류는 거절되는지 확인", changes=[
            {"kind": "hold_after_arrival", "item_id": "P-C", "value": 45, "fact_quote": QUOTE}]),
        tool("simulate_conditional", reason="T051은 여유가 없어 서류가 늦을 때의 완료일과 기한을 계산합니다.", changes=[
            {"kind": "hold_after_arrival", "item_id": "P-C", "value": 60, "fact_quote": QUOTE}]),
        tool("compare_responses", reason="대응안을 비교합니다."),
        {"summary": "선적마다 수출 허가가 필요해 P-C가 늦으면 완료일이 2028-02-11로 밀립니다.", "status": "needs_input",
         "stop_reason": "P-B·P-C 서류 필요 여부 확인 필요",
         "investigation": {"stop": "M4", "cause_link": {"signal_event_id": "x", "supplier_quote": "현지 세관의 수입 서류 보완 요청",
                                                        "signal_quote": "requires additional environmental due-diligence documentation"},
                           "items": [{"item_id": "P-B", "task_id": "T058", "absorbs": True},
                                     {"item_id": "P-C", "task_id": "T051", "absorbs": False,
                                      "latest_action_date": "2027-02-08", "worst_case_finish": "2028-02-11"}],
                           "question": "P-C 선적에도 별도 수출 허가가 필요합니까?",
                           "checks": ["P-B·P-C를 찾음", "T058 여유 245일, T051 여유 0일", "P-C 지연 시 2028-02-11"]},
         "email_draft": {"to": "Equipment Vendor A", "subject": "P-B·P-C 수입 서류 확인 요청",
                         "body": "P-C 수출 허가 신청 일정을 알려 주세요."}},
    ])
    queued = call(client, "post", f"/api/projects/{project_id}/events/{event_id}/investigations")
    assert run_once(Store())
    run = call(client, "get", f"/api/runs/{queued['run_id']}")["run"]
    assert run["status"] == "succeeded" and run["data"]["status"] == "M4"
    log = run["data"]["agent"]["tool_log"]
    assert [row["tool"] for row in log] == ["find_procurement_items", "check_schedule_slack",
                                            "simulate_conditional", "simulate_conditional", "compare_responses"]
    assert log[4]["result"]["status"] == "rejected" and log[4]["result"]["inferred_items"] == ["P-C"]
    assert [row["item_id"] for row in log[0]["result"]["items"]] == ["P-B", "P-C"]
    assert log[0]["args"]["reason"].startswith("통보 사유가 공지와 같다면")
    assert log[2]["result"]["status"] == "rejected"
    assert (log[3]["result"]["finish_date"], log[3]["result"]["changes"][0]["latest_action_date"]) == ("2028-02-11", "2027-02-08")
    context = json.loads(requests[0]["messages"][1]["content"])["context"]
    assert context["reported_change"]["finish_date"] == "2027-12-21"
    assert [row["item_id"] for row in context["mentioned_items"]] == ["P-A1"]
    assert "narrow_candidates" not in requests[0]["tools"]
    action = Store().get_json("actions", run["data"]["action_ids"][0], project_id)["data"]
    # The model left the date out; the question carries the deadline the calculator produced.
    assert action["request"] == "P-C 선적에도 별도 수출 허가가 필요합니까? (서류 제출 기한: P-C 2027-02-08)"
    assert action["source"] == "investigation"
    basis = run["data"]["real_case_basis"]
    assert [row["risk_id"] for row in basis] == ["RS-019"] and basis[0]["source_url"].startswith("https://")
    assert basis[0]["published_date"] == "2025-10-15"
    assert "서류 제출 기한: P-C 2027-02-08" in run["data"]["agent"]["email_draft"]["body"]
    assert run["data"]["rules_only"]["finish_date"] == "2027-12-21" and run["data"]["rules_only"]["finish_shift_days"] == 0
    assert "Licence review may take up to 60 days" in run["data"]["linked_notices"][0]["content"]
    assert run["data"]["agent"]["email_draft"]["to"] == "Equipment Vendor A"
    project = call(client, "get", f"/api/projects/{project_id}")
    assert any(row["data"]["event_type"] == "investigation_ready" for row in project["notifications"])


def test_x2_control_stops_without_request_or_notification(client, monkeypatch):
    project_id, event_id = load(client, "X2-C")
    assert call(client, "get", f"/api/projects/{project_id}")["related_signals"][event_id][0]["overlapping_task_ids"] == ["T054"]
    enable_agent(monkeypatch, [{"summary": "통보 사유(시험 인력)는 공지의 수입 서류 요건과 관련이 없습니다.", "status": "completed",
                                "stop_reason": "", "investigation": {"stop": "M1", "cause_link": None, "items": [],
                                                                     "question": "", "checks": []}}])
    queued = call(client, "post", f"/api/projects/{project_id}/events/{event_id}/investigations")
    assert run_once(Store())
    run = call(client, "get", f"/api/runs/{queued['run_id']}")["run"]
    assert run["data"]["status"] == "M1" and run["data"]["action_ids"] == []
    notifications = call(client, "get", f"/api/projects/{project_id}")["notifications"]
    assert not any(row["data"]["event_type"] == "investigation_ready" for row in notifications)
    assert Store().get_json("events", event_id, project_id)["data"]["investigation"]["stop"] == "M1"


def test_investigation_needs_the_agent_on(client):
    project_id, event_id = load(client, "X2")
    response = client.post(f"/api/projects/{project_id}/events/{event_id}/investigations",
                           headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 409


def load_notice(client, signal_id):
    project_id = call(client, "post", "/api/projects", json={"mode": "REPLAY"})["project_id"]
    call(client, "post", f"/api/projects/{project_id}/demo/hero-baseline")
    event_id = call(client, "post", f"/api/projects/{project_id}/demo/external-signals/{signal_id}")["event_ids"][0]
    while run_once(Store()):
        pass
    return project_id, event_id


def test_x1b_stops_early_without_notification(client, monkeypatch):
    project_id, event_id = load_notice(client, "X1-B")
    requests = enable_agent(monkeypatch, [
        tool("check_schedule_slack", reason="후보가 하나뿐이라 지연 상한 45일을 흡수하는지만 봅니다.", task_ids=["T013"], bound_days=45),
        {"summary": "T013은 45일 지연을 흡수해 완료일 영향이 없습니다.", "status": "completed", "stop_reason": "",
         "investigation": {"stop": "M2", "applicable_task_ids": ["T013"], "question": "", "checks": ["T013 여유가 45일 이상"]}},
    ])
    queued = call(client, "post", f"/api/projects/{project_id}/events/{event_id}/investigations")
    assert run_once(Store())
    run = call(client, "get", f"/api/runs/{queued['run_id']}")["run"]
    assert run["data"]["status"] == "M2" and len(requests) == 2
    assert run["data"]["agent"]["tool_log"][0]["result"]["tasks"][0]["absorbs_bound"] is True
    assert "narrow_candidates" in requests[0]["tools"]
    assert not any(row["data"]["event_type"] == "investigation_ready"
                   for row in call(client, "get", f"/api/projects/{project_id}")["notifications"])


def test_x1a_narrows_checks_facts_and_computes_the_deadline(client, monkeypatch):
    project_id, event_id = load_notice(client, "X1-A")
    sentence = "Authorities may take up to 30 days to verify an application."
    quote = ("companies bringing technicians from outside the EU to install or commission production equipment "
             "must obtain a work-permit verification for each technician before site work starts")
    enable_agent(monkeypatch, [
        tool("narrow_candidates", reason="규칙 후보가 39개라 원문 인용으로 줄입니다."),
        {"candidates": [{"task_id": "T046", "quote": quote, "reason": "installation"},
                        {"task_id": "T053", "quote": quote, "reason": "commissioning"},
                        {"task_id": "T047", "quote": quote, "reason": "installation"}]},
        tool("get_task_facts", reason="EU 역외 협력사 기술자가 현장 작업을 하는지 협력사 원산지로 확인합니다.",
             task_ids=["T046", "T053", "T047"]),
        tool("check_schedule_slack", reason="해당 작업이 30일 검토를 흡수하는지 봅니다.", task_ids=["T046", "T053"], bound_days=30),
        tool("simulate_conditional", reason="흡수하지 못하므로 최악 조건과 제출 기한을 계산합니다.", changes=[
            {"kind": "hold_after_start", "task_id": "T046", "value": 30, "fact_quote": sentence}]),
        {"summary": "서류가 늦으면 완료일이 2028-02-01로 밀립니다.", "status": "needs_input", "stop_reason": "과도기 적용 여부 확인",
         "investigation": {"stop": "M4", "applicable_task_ids": ["T046", "T053"],
                           "excluded": [{"task_id": "T047", "reason": "독일(EU 역내) 협력사"}],
                           "question": "Vendor A 기술자의 취업 허가 확인을 신청할 수 있는지 확인해 주세요.", "checks": []},
         "email_draft": {"to": "Equipment Vendor A", "subject": "설비 서류 제출 일정 확인",
                         "body": "2026-11-05까지 제출 가능한지 알려 주세요."}},
    ])
    queued = call(client, "post", f"/api/projects/{project_id}/events/{event_id}/investigations")
    assert run_once(Store())
    run = call(client, "get", f"/api/runs/{queued['run_id']}")["run"]
    log = run["data"]["agent"]["tool_log"]
    assert [row["tool"] for row in log] == ["narrow_candidates", "get_task_facts", "check_schedule_slack", "simulate_conditional"]
    facts = {row["task_id"]: row for row in log[1]["result"]["tasks"]}
    assert facts["T046"]["supplier_origin_countries"] == ["South Korea"] and facts["T047"]["supplier_origin_countries"] == ["Germany"]
    assert (log[3]["result"]["finish_date"], log[3]["result"]["changes"][0]["latest_action_date"]) == ("2028-02-01", "2026-11-05")
    assert run["data"]["status"] == "M4" and run["data"]["agent"]["investigation"]["applicable_task_ids"] == ["T046", "T053"]
    assert run["data"]["agent"]["usage"]["llm_calls"] == 6
    assert run["data"]["real_case_basis"][0]["risk_id"] == "RS-001"
    assert "2026-11-05" in Store().get_json("actions", run["data"]["action_ids"][0], project_id)["data"]["request"]


def run_x2_investigation(client, monkeypatch):
    project_id, event_id = load(client, "X2")
    enable_agent(monkeypatch, [
        tool("find_procurement_items", reason="같은 원인의 이후 품목 확인", supplier_id="Equipment Vendor A",
             origin_country="China", customs_required=True, arriving_after="2026-03-07"),
        tool("check_schedule_slack", reason="여유 확인", task_ids=["T058", "T051"], bound_days=60),
        tool("simulate_conditional", reason="P-C 최악 조건", changes=[
            {"kind": "hold_after_arrival", "item_id": "P-C", "value": 60, "fact_quote": QUOTE}]),
        {"summary": "P-C가 늦으면 2028-02-11", "status": "needs_input", "stop_reason": "P-C 확인",
         "investigation": {"stop": "M4", "question": "P-C도 허가가 필요합니까?", "checks": []},
         "email_draft": {"to": "Equipment Vendor A", "subject": "P-C 확인", "body": "P-C 허가 여부를 알려 주세요."}},
        # The explanation after recalculation.
        {"summary": "P-C 지연을 반영하면 2028-02-11이며 HOPT-05와 HOPT-06 조합이 가장 많이 회복합니다.",
         "status": "needs_review", "stop_reason": "", "option_explanations": [], "email_draft": None, "unresolved_items": []},
    ])
    queued = call(client, "post", f"/api/projects/{project_id}/events/{event_id}/investigations")
    assert run_once(Store())
    return project_id, event_id, call(client, "get", f"/api/runs/{queued['run_id']}")["run"]


def test_confirming_the_hidden_item_recalculates_and_recommends_the_best_pair(client, monkeypatch):
    project_id, event_id, investigation = run_x2_investigation(client, monkeypatch)
    resolved = call(client, "post", f"/api/projects/{project_id}/investigations/{investigation['id']}/resolve",
                    json={"decision": "applies", "note": "협력사 회신: P-C도 선적별 허가 대상"})
    assert run_once(Store())
    result = call(client, "get", f"/api/runs/{resolved['analysis_run_id']}")
    by_options = {tuple(row["data"]["option_ids"]): row["data"] for row in result["scenarios"]}
    assert by_options[()]["finish_date"] == "2028-02-11"
    best = max(result["scenarios"], key=lambda row: row["data"]["recovery_days_vs_no_response"] or 0)["data"]
    assert best["option_ids"] == ["HOPT-05", "HOPT-06"] and best["finish_date"] == "2028-01-25"
    assert best["recovery_days_vs_no_response"] == 17
    event = Store().get_json("events", event_id, project_id)["data"]
    assert event["patch"]["not_before"] == {"T051": "2027-05-25"} and event["patch"]["estimated_finish"] == {"T042": "2026-03-14"}
    assert event["investigation"]["resolution"]["decision"] == "applies"
    action = Store().get_json("actions", investigation["data"]["action_ids"][0], project_id)["data"]
    assert action["state"] == "DONE" and action["decision"] == "applies"
    again = client.post(f"/api/projects/{project_id}/investigations/{investigation['id']}/resolve",
                        headers={"Authorization": "Bearer test-token"}, json={"decision": "not_applicable"})
    assert again.status_code == 409


def test_not_applicable_keeps_the_reported_result(client, monkeypatch):
    project_id, event_id, investigation = run_x2_investigation(client, monkeypatch)
    resolved = call(client, "post", f"/api/projects/{project_id}/investigations/{investigation['id']}/resolve",
                    json={"decision": "not_applicable"})
    assert resolved["analysis_run_id"] is None and not run_once(Store())
    event = Store().get_json("events", event_id, project_id)["data"]
    assert event["patch"] == {"estimated_finish": {"T042": "2026-03-14"}}
    assert event["investigation"]["resolution"]["note"] == "확인 결과 해당 없음"

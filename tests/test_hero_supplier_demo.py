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
from app.shifted_external import bundled_hero_calendars, bundled_hu_calendars, recheck_shifted_schedule
from app.supplier_interpreter import interpret_supplier_message
from scripts.evaluation_mocks import MockEvaluationGateway


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
    from app.adapters import sources

    def network_forbidden(*_args, **_kwargs):
        raise AssertionError("hero demo attempted an external holiday request")

    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(sources, "fetch_holidays", network_forbidden)
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
    # Nine hero notices, then the investigation scenarios: two supplier notices and three external notices.
    assert [item["event_id"] for item in result["demo_events"][9:]] == ["X2", "X2-C", "N-X2", "X1-A", "X1-B"]
    assert {item["channel"] for item in result["demo_events"][11:]} == {"registered_public_source"}
    assert by_id["T042"]["origin_country"] == "South Korea" and by_id["T042"]["customs_required"] is True
    assert by_id["T043"]["customs_required"] is False and by_id["T013"]["permit_required"] is True
    procurement = {item["item_id"]: item for item in result["version"]["data"]["procurement"]}
    assert procurement["P-C"]["needed_for_task_id"] == "T051" and procurement["P-C"]["planned_arrival"] == "2027-03-26"
    assert by_id["T021"]["outdoor"] and by_id["T045"]["outdoor"]
    assert by_id["T021"]["outdoor_data_origin"] == "SYNTHETIC"
    assert not by_id["T036"]["outdoor"]
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


def test_hero_response_catalog_is_synthetic_and_h04_recovery_is_calculated(client):
    project_id, baseline = hero_baseline(client)
    options = baseline["version"]["data"]["options"]
    assert len(options) == 6
    assert all(option["data_origin"] == "SYNTHETIC" and option["currency"] == "KRW"
               and option["target_ids"] and option["reduction_workdays"] > 0
               and option["extra_cost_krw"] > 0 and option["conditions"]
               and option["decision_deadline"] for option in options)
    selected = next(item for item in baseline["demo_events"] if item["event_id"] == "H04")
    event = request(client, "post", f"/api/projects/{project_id}/events", json=selected)
    queued = request(client, "post", f"/api/projects/{project_id}/analyses",
                     json={"event_id": event["event_id"], "preview_only": True})
    assert run_once(Store())
    scenarios = request(client, "get", f"/api/runs/{queued['run_id']}")["scenarios"]
    by_option = {tuple(row["data"]["option_ids"]): row["data"] for row in scenarios}
    assert by_option[()]["finish_date"] == "2028-01-25"
    assert by_option[()]["extra_cost_krw"] == 0
    assert by_option[()]["recovery_days_vs_no_response"] == 0
    assert by_option[("HOPT-05",)]["finish_date"] == "2028-01-11"
    assert by_option[("HOPT-05",)]["extra_cost_krw"] == 13_000_000
    assert by_option[("HOPT-05",)]["recovery_days_vs_no_response"] == 14
    assert all(row["data"]["mode"] == "REPLAY" for row in scenarios)


def test_existing_hero_baseline_without_options_gets_catalog_without_reupload(client):
    project_id, baseline = hero_baseline(client)
    version = baseline["version"]
    Store().put_json("versions", version["id"], {**version["data"], "options": []},
                     project_id=project_id, parent_id=version["parent_id"], status=version["status"],
                     content_hash=version["content_hash"], created_at=version["created_at"])
    shown = request(client, "get", f"/api/projects/{project_id}")
    assert len(shown["version"]["data"]["options"]) == 6
    assert Store().current_version(project_id)["data"]["options"] == []
    selected = next(item for item in baseline["demo_events"] if item["event_id"] == "H04")
    event = request(client, "post", f"/api/projects/{project_id}/events", json=selected)
    queued = request(client, "post", f"/api/projects/{project_id}/analyses",
                     json={"event_id": event["event_id"], "preview_only": True})
    assert run_once(Store())
    scenarios = request(client, "get", f"/api/runs/{queued['run_id']}")["scenarios"]
    assert any(row["data"]["option_ids"] == ["HOPT-05"] for row in scenarios)


def test_hero_supplier_cases_match_reviewed_synthetic_answers():
    parsed = parse_upload(HERO.name, HERO.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "Hero", "mode": "REPLAY"}, ConfirmInput())
    tasks = snapshot["tasks"]
    project = snapshot["project"]
    baseline = {task["task_id"]: task for task in simulate(project, tasks)["schedule"]}
    by_id = {task["task_id"]: task for task in tasks}
    assert TRUTH["label"] == "검토자가 작성한 합성 정답"
    assert len(MESSAGES["events"]) == len(TRUTH["cases"]) == 9
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
        elif answer["expected_outcome"] == "RETRACTION":
            assert event["corrects_event_id"] == "H04"
        else:
            assert event["classification_status"] == "PATCH_PROPOSED"
        after = {task["task_id"]: task for task in simulate(project, tasks, event=event)["schedule"]}
        changed = [task_id for task_id in after if
                   (after[task_id]["planned_start"], after[task_id]["planned_finish"]) !=
                   (baseline[task_id]["planned_start"], baseline[task_id]["planned_finish"])]
        assert changed == answer["changed_task_ids"]
        for constraint in answer["newly_overlapping_external_constraints"]:
            task_id = constraint["task_id"]
            if "baseline_window" in constraint:
                assert (baseline[task_id]["planned_start"], baseline[task_id]["planned_finish"]) == tuple(constraint["baseline_window"])
                assert (after[task_id]["planned_start"], after[task_id]["planned_finish"]) == tuple(constraint["changed_window"])
            for blocked in constraint["dates"]:
                assert not (baseline[task_id]["planned_start"] <= blocked < baseline[task_id]["planned_finish"])


def test_numberless_supplier_variants_offer_quoted_candidates_without_patch():
    parsed = parse_upload(HERO.name, HERO.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "Hero", "mode": "REPLAY"}, ConfirmInput())
    variants = json.loads((ROOT / "data/hero_demo/supplier_message_variants.json").read_text(encoding="utf-8"))["events"][-4:]
    truths = json.loads((ROOT / "data/hero_demo/supplier_message_variants_ground_truth.json").read_text(encoding="utf-8"))["cases"][-4:]
    for source, truth in zip(variants, truths):
        result = interpret_supplier_message(source, snapshot["project"], snapshot["tasks"], MockEvaluationGateway())
        assert result["status"] == "task_confirmation_required"
        assert result["patch"] == {}
        assert result["related_task_ids"] == truth["direct_task_ids"]
        assert all(candidate["quote"] in source["content"] for candidate in result["task_candidates"])
        assert result["questions"]


def test_reviewed_external_truth_matches_independent_calendar_dates():
    parsed = parse_upload(HERO.name, HERO.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "Hero", "mode": "REPLAY"}, ConfirmInput())
    calendars = bundled_hu_calendars(snapshot["tasks"])
    by_id = {task["task_id"]: task for task in snapshot["tasks"]}
    for source, answer in zip(MESSAGES["events"], TRUTH["cases"]):
        event = normalize_event(input_event(source), snapshot["project"], snapshot["tasks"])
        result = recheck_shifted_schedule(snapshot["project"], snapshot["tasks"], event, [], None,
                                          calendars, [], {})
        assert result["recheck_status"] == "CONVERGED"
        expected = {(row["task_id"], day) for row in answer["newly_overlapping_external_constraints"]
                    for day in row["dates"]}
        for row in answer["newly_overlapping_external_constraints"]:
            stored = json.loads((ROOT / row["source"]).read_text(encoding="utf-8"))
            source_dates = {holiday["date"] for holiday in stored["holidays"] if "Public" in holiday["types"]}
            assert set(row["dates"]) <= source_dates
            assert by_id[row["task_id"]]["country"] == "Hungary"
        observed = {(row["task_id"], row["date"]) for row in result["external_constraints"]
                    if row["kind"] == "public_holiday"}
        assert observed == expected, answer["case_id"]
        assert not {row["task_id"] for row in result["external_constraints"]} & {"T036", "T037", "T038", "T039"}


def test_h04_upload_to_preview_separates_supplier_and_new_holiday_delay(client):
    project_id, baseline = hero_baseline(client)
    selected = next(item for item in baseline["demo_events"] if item["event_id"] == "H04")
    event = request(client, "post", f"/api/projects/{project_id}/events", json=selected)
    queued = request(client, "post", f"/api/projects/{project_id}/analyses",
                     json={"event_id": event["event_id"], "preview_only": True})
    assert run_once(Store())
    result = request(client, "get", f"/api/runs/{queued['run_id']}")
    scenario = result["scenarios"][0]["data"]
    assert result["run"]["status"] == "succeeded"
    assert scenario["supplier_finish_shift_days"] == 21
    assert scenario["external_additional_shift_days"] == 14
    assert scenario["finish_date"] == "2028-01-25"
    assert scenario["recheck_status"] == "CONVERGED"
    assert scenario["required_confirmations"]
    truth = next(case for case in TRUTH["cases"] if case["case_id"] == "H04")
    expected = {(row["task_id"], day) for row in truth["newly_overlapping_external_constraints"] for day in row["dates"]}
    assert {(row["task_id"], row["date"]) for row in scenario["external_constraints"]} == expected
    assert scenario["applied_patch"]["calendar_nonworking_dates"]["T049"] == ["2027-03-15", "2027-03-26", "2027-03-28", "2027-03-29"]


def test_h08_retraction_supersedes_h04(client):
    project_id, baseline = hero_baseline(client)
    old = request(client, "post", f"/api/projects/{project_id}/events", json=baseline["demo_events"][3])
    correction = request(client, "post", f"/api/projects/{project_id}/events", json=baseline["demo_events"][8])
    assert correction["event"]["patch"] == {"estimated_finish": {"T045": "2026-12-05"}}
    assert Store().get_json("events", old["event_id"])["data"]["review_status"] == "SUPERSEDED"
    denied = client.post(f"/api/projects/{project_id}/analyses", headers={"Authorization": "Bearer test-token"},
                         json={"event_id": old["event_id"], "preview_only": True})
    assert denied.status_code == 409
    queued = request(client, "post", f"/api/projects/{project_id}/analyses",
                     json={"event_id": correction["event_id"], "preview_only": True})
    assert run_once(Store())
    scenario = request(client, "get", f"/api/runs/{queued['run_id']}")["scenarios"][0]["data"]
    assert scenario["supplier_finish_shift_days"] == 0
    assert scenario["external_additional_shift_days"] == 0


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


def test_h04_agent_reviews_calculated_scenarios_once(client, monkeypatch):
    import json as _json

    from app.adapters.llm import ChatResult, OpenAICompatibleLLM

    project_id, _ = hero_baseline(client)
    h04 = next(item for item in MESSAGES["events"] if item["event_id"] == "H04")
    event = request(client, "post", f"/api/projects/{project_id}/events", json=input_event(h04))
    request(client, "patch", f"/api/projects/{project_id}/events/{event['event_id']}/review", json={"confirmed": True})
    requests = []

    def fake_chat(self, messages, tools=None, response_format=None):
        requests.append({"messages": messages, "tools": [tool["function"]["name"] for tool in tools or []]})
        return ChatResult(content=_json.dumps({
            "summary": "T045 반입 지연으로 무대응 완료일이 늦어지고, 설치팀 추가 투입안이 가장 많이 회복합니다.",
            "status": "needs_review", "stop_reason": "새 기간 공휴일의 현장 적용 확인",
            "option_explanations": [{"option_ids": ["HOPT-01"], "text": "HOPT-01은 2028-01-11로 14일 회복합니다."}],
            "regulatory_assessment": None, "email_draft": None, "unresolved_items": []}, ensure_ascii=False),
            usage={"prompt_tokens": 100, "completion_tokens": 20})

    for key, value in {"API_KEY": "k", "LLM_MODEL": "m", "LLM_BASE_URL": "https://gateway.invalid/v1",
                       "REPLAN_PAID_CALLS_ENABLED": "true"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(OpenAICompatibleLLM, "chat", fake_chat)
    queued = request(client, "post", f"/api/projects/{project_id}/analyses", json={"event_id": event["event_id"]})
    assert run_once(Store())
    result = request(client, "get", f"/api/runs/{queued['run_id']}")

    by_options = {tuple(row["data"]["option_ids"]): row["data"] for row in result["scenarios"]}
    assert by_options[()]["finish_date"] == "2028-01-25"
    assert by_options[()]["supplier_finish_shift_days"] == 21
    assert by_options[()]["external_additional_shift_days"] == 14
    assert by_options[("HOPT-01",)]["finish_date"] == "2028-01-11"

    assert len(requests) == 1
    assert not {"recheck_shifted_schedule", "simulate_schedule", "get_project_context",
                "list_response_options"} & set(requests[0]["tools"])
    context = _json.loads(requests[0]["messages"][1]["content"])["context"]
    no_response = next(row for row in context["scenario_summaries"] if row["option_ids"] == [])
    assert (no_response["finish_date"], no_response["supplier_finish_shift_days"],
            no_response["external_additional_shift_days"]) == ("2028-01-25", 21, 14)
    assert len(context["scenario_summaries"]) == 8
    assert len(requests[0]["messages"][1]["content"]) < 30_000
    agent = result["run"]["data"]["agent"]
    assert agent["status"] == "needs_review"
    assert "2028-01-11" in agent["option_explanations"][0]["text"]


def test_named_commissioning_task_is_not_replaced_by_keyword_matches():
    """X2-control: 'commissioning' is not a test keyword, but the named T054 must still be used."""
    parsed = parse_upload(HERO.name, HERO.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "Hero", "mode": "REPLAY"}, ConfirmInput())
    project, tasks = snapshot["project"], snapshot["tasks"]
    published = "2026-03-05T09:00:00+01:00"
    event = normalize_event({
        "content": "[가상 메시지] T054 모듈 라인 시운전 착수를 2027-05-24 이후로 늦춥니다. 시험 인력 교대 일정 때문입니다.",
        "channel": "supplier_message", "published_at": published, "mode": "REPLAY",
        "data_origin": "SYNTHETIC", "simulation_as_of": published}, project, tasks)
    assert event["patch"] == {"not_before": {"T054": "2027-05-24"}}
    assert event["related_task_ids"] == ["T054"]
    result = recheck_shifted_schedule(project, tasks, {**event, "related_task_ids": ["T054"]}, [], None,
                                      bundled_hero_calendars(tasks), [], {})
    assert result["finish_date"] == "2027-12-28"

    # A named manufacturing task whose FAT is not named still moves the matching test task.
    fat = normalize_event({
        "content": "[가상 메시지] T036 셀 설비 제작 완료일이 2026-01-15로 변경됩니다. FAT는 2026-01-16부터 가능합니다.",
        "channel": "supplier_message", "published_at": published, "mode": "REPLAY",
        "data_origin": "SYNTHETIC", "simulation_as_of": published}, project, tasks)
    assert fat["patch"]["estimated_finish"] == {"T036": "2026-01-15"}
    assert "T038" in fat["patch"]["not_before"]


def test_demo_external_notice_enters_through_the_scan_path(client):
    project_id, _ = hero_baseline(client)
    loaded = request(client, "post", f"/api/projects/{project_id}/demo/external-signals/N-X2")
    assert len(loaded["event_ids"]) == 1
    again = request(client, "post", f"/api/projects/{project_id}/demo/external-signals/N-X2")
    assert again["duplicate"] is True
    event = Store().get_json("events", loaded["event_ids"][0], project_id)["data"]
    assert event["channel"] == "registered_public_source" and event["data_origin"] == "SYNTHETIC"
    assert event["published_at"] == "2026-02-16T08:00:00+00:00"
    assert "T042" in event["related_task_ids"]
    runs = [row for row in Store().list_json("runs", project_id) if row["kind"] == "analysis"]
    assert len(runs) == 1 and runs[0]["data"]["auto_detected"] is True

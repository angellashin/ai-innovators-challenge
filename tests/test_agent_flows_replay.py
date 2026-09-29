"""The recorded agent flows replay through the API with no key, network or cost."""

from scripts import agent_flows


def test_every_recorded_flow_replays_in_any_project(tmp_path, monkeypatch):
    for key in ("API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("REPLAN_LLM_MODE", "replay")
    monkeypatch.setenv("REPLAN_LLM_CASSETTE", str(agent_flows.CASSETTE))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "true")
    agent_flows._patch_sources(monkeypatch.setattr)
    counter = agent_flows._count_calls(0, monkeypatch.setattr)

    # Recorded under another project name: the display name is never sent to the model.
    runs = {}
    for flow in agent_flows.FLOWS:
        monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path / flow))
        runs[flow] = agent_flows.run_flow(flow, "replay", project_name=f"화면에서 만든 {flow} 프로젝트")

    assert counter["misses"] == [] and counter["gateway"] == 0
    h04 = runs["H04"]
    assert h04["preview"]["agent"]["status"] == "skipped_preview"
    no_response = next(row for row in h04["confirmed"]["scenarios"] if row["option_ids"] == [])
    assert (no_response["finish_date"], no_response["supplier_finish_shift_days"],
            no_response["external_additional_shift_days"]) == ("2028-01-25", 21, 14)
    explanations = h04["confirmed"]["agent"]["option_explanations"]
    assert all(isinstance(row["text"], str) and "HOPT-은" not in row["text"] for row in explanations)
    regulation = runs["H02"]["confirmed"]["agent"]["regulatory_assessment"]
    assert regulation["likelihood"] == "불확실" and regulation["reason"] and regulation["human_check"]

    x2 = runs["X2"]["investigation"]
    assert runs["X2"]["confirmed"]["scenarios"][0]["finish_date"] == "2027-12-21"  # rules alone: no change
    assert x2["status"] == "M4" and len(x2["action_ids"]) == 1
    log = x2["agent"]["tool_log"]
    found = next(row for row in log if row["tool"] == "find_procurement_items")["result"]["items"]
    assert [row["item_id"] for row in found] == ["P-B", "P-C"]
    worst = next(row for row in log if row["tool"] == "simulate_conditional")["result"]
    assert (worst["finish_date"], worst["changes"][0]["latest_action_date"]) == ("2028-02-11", "2027-02-08")
    assert "2027-02-08" in x2["agent"]["investigation"]["question"]
    assert all(row["args"].get("reason") for row in log)

    assert runs["X2-C"]["investigation"]["status"] == "M1" and runs["X2-C"]["investigation"]["action_ids"] == []
    assert runs["X1-B"]["investigation"]["status"] == "M2"
    assert runs["X1-A"]["investigation"]["status"] == "M4"
    resolved = runs["X2-resolved"]
    if resolved["investigation"]["status"] in {"M3", "M4"}:
        recalculated = {tuple(row["option_ids"]): row for row in resolved["recalculated"]["scenarios"]}
        assert recalculated[()]["finish_date"] == "2028-02-11"
        assert recalculated[("HOPT-05", "HOPT-06")]["finish_date"] == "2028-01-25"
        assert recalculated[("HOPT-05", "HOPT-06")]["recovery_days_vs_no_response"] == 17
    else:
        assert "recalculated" not in resolved

    # The baseline briefing, the change triage and the register link replay as recorded.
    brief = runs["BRIEF"]["briefing"]
    assert brief["risks"][0]["risk_id"] == "R-import-CN" and brief["risks"][0]["item_ids"] == ["P-A1", "P-B", "P-C"]
    assert any(warning.startswith("P-C: 일정 완충 기간이 없습니다.") for warning in brief["risks"][0]["critical_warnings"])
    triage = runs["X2"]["triage"]
    assert triage["status"] == "interpreted" and "T051" in triage["related"]
    assert triage["risk_links"] == ["R-import-CN"]
    link = x2["risk_link"]
    assert link["risk_id"] == "R-import-CN" and link["status"] == "OCCURRED" and link["expected_by"] == "briefing"
    assert runs["X2-C"]["investigation"]["risk_link"] is None
    # A notice is investigated on its related tasks only; needs-check tasks wait for a person.
    x1b = runs["X1-B"]["investigation"]["agent"]["tool_log"]
    assert {task for row in x1b for task in row["args"].get("task_ids", [])} == {"T013"}
    assert runs["EXT_NOTICE"]["person"]["agent"]["usage"] == {"llm_calls": 0}

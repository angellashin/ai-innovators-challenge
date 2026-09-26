"""The recorded agent flows replay through the API with no key, network or cost."""

from scripts import agent_flows


def test_recorded_hero_flows_replay_without_misses(tmp_path, monkeypatch):
    for key in ("API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("REPLAN_LLM_MODE", "replay")
    monkeypatch.setenv("REPLAN_LLM_CASSETTE", str(agent_flows.CASSETTE))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test-token")
    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "true")
    agent_flows._patch_sources(monkeypatch.setattr)
    counter = agent_flows._count_calls(0, monkeypatch.setattr)

    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path / "h04"))
    h04 = agent_flows.run_flow("H04", "replay")
    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path / "h02"))
    h02 = agent_flows.run_flow("H02", "replay")

    assert counter["misses"] == [] and counter["gateway"] == 0 and counter["replayed"] == 3
    assert h04["preview"]["agent"]["status"] == "skipped_preview"
    no_response = next(row for row in h04["confirmed"]["scenarios"] if row["option_ids"] == [])
    assert (no_response["finish_date"], no_response["supplier_finish_shift_days"],
            no_response["external_additional_shift_days"]) == ("2028-01-25", 21, 14)
    assert any(row["finish_date"] == "2028-01-11" for row in h04["confirmed"]["scenarios"])
    explanations = h04["confirmed"]["agent"]["option_explanations"]
    assert all(isinstance(row["text"], str) and "HOPT-은" not in row["text"] for row in explanations)
    assert ["HOPT-01"] in [row["option_ids"] for row in explanations]
    regulation = h02["confirmed"]["agent"]["regulatory_assessment"]
    assert regulation["likelihood"] == "불확실" and regulation["reason"] and regulation["human_check"]

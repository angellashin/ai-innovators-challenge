"""Run the hero agent flows through the API and worker, the way the workspace does.

    python scripts/agent_flows.py --mode replay            # no key, no network, no cost
    python scripts/agent_flows.py --mode record --max-calls 10 X2    # paid only for requests not yet recorded
    python scripts/agent_flows.py --mode replay --prune               # also drop recordings no flow uses

Replay answers from data/llm_replay/hero_demo.json. A replay miss means a prompt, tool or input
changed since recording; record that flow again. Record mode reads LLM settings from .env without
printing them and stops after --max-calls gateway requests. External sources are local fixtures:
bundled holiday snapshots and one synthetic regulation notice; weather is not fetched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
CASSETTE = ROOT / "data" / "llm_replay" / "hero_demo.json"
FLOWS = ("H04", "H02", "V08", "EXT_NOTICE", "EXT_HOLIDAY", "X2", "X2-C", "X1-A", "X1-B")
SYNTHETIC_NOTICE = {
    "status": "ok", "source_id": "https://environment.ec.europa.eu/news_en", "provider": "registered_source",
    "url": "https://environment.ec.europa.eu/news_en", "fetched_at": "2025-08-27T08:00:00+00:00",
    "body_hash": "synthetic-audit-notice-1",
    "feed_items": [{
        "id": "audit-synthetic-1", "url": "https://environment.ec.europa.eu/news_en#audit-synthetic-1",
        "title": "[합성 감사용] Battery regulation: new due-diligence documentation for imported battery production equipment",
        "content": ("[합성 감사용 공지] From 2026-11-01, operators installing battery cell production equipment imported "
                    "from outside the EU must submit additional environmental due-diligence documentation before "
                    "site installation and commissioning. Competent authorities may take up to 30 days to review. "
                    "Transitional arrangements are not yet confirmed."),
        "published": "2025-08-27T08:00:00+00:00"}],
}


class CallLimit(RuntimeError):
    pass


def _load_llm_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in {"LLM_BASE_URL", "LLM_MODEL", "API_KEY"}:
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _patch_sources(patch: Any = setattr) -> None:
    from app.adapters import sources

    def local_holidays(country_code: str, year: int, **_kwargs: Any) -> dict:
        path = ROOT / "data" / "external" / f"{country_code.lower()}-{year}-holidays.json"
        if not path.exists():
            return {"status": "error", "source_id": f"nager:{country_code}:{year}", "error": "no local snapshot"}
        return json.loads(path.read_text(encoding="utf-8"))

    def no_weather(*_args: Any, **_kwargs: Any) -> dict:
        return {"status": "failed", "source_id": "open-meteo", "fetched_at": "2025-08-27T00:00:00+00:00",
                "error": "weather is not fetched in agent flows"}

    patch(sources, "fetch_holidays", local_holidays)
    patch(sources, "fetch_registered_source", lambda *_args, **_kwargs: dict(SYNTHETIC_NOTICE))
    patch(sources, "fetch_weather", no_weather)
    patch(sources, "fetch_seasonal_statistics", no_weather)


def _count_calls(limit: int | None, patch: Any = setattr) -> dict:
    from app.adapters import llm

    counter = {"gateway": 0, "replayed": 0, "misses": [], "used_keys": set()}
    request, replay, recorded = (llm.OpenAICompatibleLLM._request, llm.OpenAICompatibleLLM._replay,
                                 llm.OpenAICompatibleLLM._recorded)

    def counted_request(self: Any, *args: Any) -> Any:
        if limit is not None and counter["gateway"] >= limit:
            raise CallLimit(f"stopped before gateway call {counter['gateway'] + 1}: --max-calls {limit}")
        counter["gateway"] += 1
        return request(self, *args)

    def counted_replay(self: Any, payload: dict) -> Any:
        try:
            result = replay(self, payload)
        except llm.LLMUnavailable as exc:
            counter["misses"].append(str(exc))
            raise
        counter["replayed"] += 1
        counter["used_keys"].add(llm.request_key(payload))
        return result

    def counted_recorded(self: Any, payload: dict) -> Any:
        result = recorded(self, payload)
        counter["used_keys"].add(llm.request_key(payload))
        if result is not None:
            counter["replayed"] += 1
        return result

    patch(llm.OpenAICompatibleLLM, "_request", counted_request)
    patch(llm.OpenAICompatibleLLM, "_replay", counted_replay)
    patch(llm.OpenAICompatibleLLM, "_recorded", counted_recorded)
    return counter


def run_flow(flow: str, mode: str, project_name: str = "에이전트 흐름 확인") -> dict:
    """One flow in a fresh data directory; returns each analysis run's agent output and scenarios."""
    from fastapi.testclient import TestClient
    from app import main as api
    from app.storage import Store
    from app.worker import run_once

    llm_mode = os.environ["REPLAN_LLM_MODE"]
    client = TestClient(api.app)
    headers = {"Authorization": f"Bearer {os.environ['REPLAN_DEMO_TOKEN']}"}

    def call(method: str, path: str, body: Any = None) -> Any:
        response = client.request(method, path, json=body, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()

    def drain() -> None:
        while run_once(Store()):
            pass

    def analyse(event_id: str, preview: bool) -> dict:
        queued = call("POST", f"/api/projects/{project_id}/analyses", {"event_id": event_id, "preview_only": preview})
        drain()
        run = call("GET", f"/api/runs/{queued['run_id']}")
        return {"agent": run["run"]["data"].get("agent"), "status": run["run"]["data"].get("status"),
                "scenarios": [{key: row["data"].get(key) for key in (
                    "label", "option_ids", "finish_date", "supplier_finish_shift_days",
                    "external_additional_shift_days", "recovery_days_vs_no_response")} for row in run["scenarios"]]}

    def investigate(event_id: str) -> dict:
        queued = call("POST", f"/api/projects/{project_id}/events/{event_id}/investigations")
        drain()
        run = call("GET", f"/api/runs/{queued['run_id']}")["run"]
        return {"agent": run["data"].get("agent"), "status": run["data"].get("status"),
                "action_ids": run["data"].get("action_ids"), "run_status": run["status"]}

    # The display name never reaches the model, so any name replays (as a project made on screen does).
    project_id = call("POST", "/api/projects", {"name": project_name, "mode": "REPLAY"})["project_id"]
    # Watch-plan enrichment is not part of these flows.
    os.environ["REPLAN_LLM_MODE"], os.environ["REPLAN_PAID_CALLS_ENABLED"] = "live", "false"
    call("POST", f"/api/projects/{project_id}/demo/hero-baseline")
    drain()
    os.environ["REPLAN_LLM_MODE"], os.environ["REPLAN_PAID_CALLS_ENABLED"] = llm_mode, "true"
    project = call("GET", f"/api/projects/{project_id}")
    runs: dict[str, dict] = {}

    if flow in {"X2", "X2-C"}:
        call("POST", f"/api/projects/{project_id}/demo/external-signals/N-X2")
        drain()
    if flow in {"H04", "H02", "V08", "X2", "X2-C"}:
        if flow == "V08":
            variants = json.loads((ROOT / "data/hero_demo/supplier_message_variants.json").read_text(encoding="utf-8"))
            item = next(row for row in variants["events"] if row["event_id"] == "V08")
            payload = {"content": item["content"], "channel": "supplier_message", "source_label": "직접 입력한 메시지",
                       "mode": project["project"].get("mode", "LIVE"),
                       "data_origin": project["project"].get("data_origin", "USER"),
                       "simulation_as_of": f"{project['project']['status_as_of']}T09:00:00+02:00"}
            review = {"confirmed": True, "related_task_ids": ["T036"], "review_note": "작업·날짜를 사람이 지정함",
                      "patch": {"estimated_finish": {"T036": "2026-01-15"}}}
        else:
            item = next(row for row in project["demo_events"] if row.get("event_id") == flow)
            payload = {"event_id": item.get("event_id"), "content": item.get("body") or item.get("content"),
                       "channel": item.get("channel", "supplier_message"), "source_label": item.get("source_label"),
                       "published_at": item.get("published_at"), "mode": item.get("mode", "SYNTHETIC"),
                       "data_origin": "SYNTHETIC", "simulation_as_of": item.get("published_at")}
            review = {"confirmed": True}
        event_id = call("POST", f"/api/projects/{project_id}/events", payload)["event_id"]
        runs["preview"] = analyse(event_id, True)
        call("PATCH", f"/api/projects/{project_id}/events/{event_id}/review", review)
        runs["confirmed"] = analyse(event_id, False)
        if flow in {"X2", "X2-C"}:
            runs["investigation"] = investigate(event_id)
    elif flow in {"X1-A", "X1-B"}:
        event_id = call("POST", f"/api/projects/{project_id}/demo/external-signals/{flow}")["event_ids"][0]
        drain()
        runs["investigation"] = investigate(event_id)
    else:
        plan = dict(project["watch_plan"])
        keep = "holiday:HU:2027" if flow == "EXT_HOLIDAY" else "source:"
        plan["proposal_items"] = [{**row, "decision": "accepted" if str(row.get("id")).startswith(keep) else "excluded"}
                                  for row in plan.get("proposal_items", [])]
        plan.update(enabled=True, weather_site=None, weather_task_ids=[])
        if flow == "EXT_NOTICE":
            plan["holiday_calendars"] = []
        else:
            plan["source_allowlist"], plan["source_rules"] = [], []
        call("PUT", f"/api/projects/{project_id}/watch-plan",
             {key: value for key, value in plan.items() if key in api.WatchPlanInput.model_fields})
        call("POST", f"/api/projects/{project_id}/scan")
        drain()
        project = call("GET", f"/api/projects/{project_id}")
        auto = next(row for row in project["runs"] if row.get("kind") == "analysis")
        runs["auto_detected"] = {"agent": auto["data"].get("agent"), "status": auto["data"].get("status")}
        event_id = next(row["id"] for row in project["events"] if row["data"].get("evidence"))
        if flow == "EXT_HOLIDAY":
            call("PATCH", f"/api/projects/{project_id}/events/{event_id}/review",
                 {"confirmed": True, "review_note": "현장 휴무 달력과 대조해 적용 확인"})
        runs["person"] = analyse(event_id, False)
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flows", nargs="*", help=f"any of {', '.join(FLOWS)} (default: all)")
    parser.add_argument("--mode", choices=["replay", "record"], default="replay")
    parser.add_argument("--max-calls", type=int, default=None, help="record mode: stop before this many gateway calls")
    parser.add_argument("--output", type=Path, help="write the flow results as JSON")
    parser.add_argument("--prune", action="store_true", help="replay mode: drop cassette entries no flow used")
    parser.add_argument("--project-name", default="에이전트 흐름 확인", help="any name; it is not sent to the model")
    args = parser.parse_args()
    if set(args.flows) - set(FLOWS):
        parser.error(f"unknown flow: {', '.join(sorted(set(args.flows) - set(FLOWS)))}")
    if args.mode == "record":
        _load_llm_env()
        if args.max_calls is None:
            parser.error("record mode is paid; pass --max-calls")
    os.environ.update(REPLAN_LLM_MODE=args.mode, REPLAN_LLM_CASSETTE=str(CASSETTE),
                      REPLAN_DEMO_TOKEN=os.environ.get("REPLAN_DEMO_TOKEN") or "agent-flows")
    _patch_sources()
    counter = _count_calls(args.max_calls)
    results = {}
    for flow in args.flows or FLOWS:
        with tempfile.TemporaryDirectory() as data_dir:
            os.environ["REPLAN_DATA_DIR"] = data_dir
            results[flow] = run_flow(flow, args.mode, args.project_name)
        for name, run in results[flow].items():
            agent = run.get("agent") or {}
            state = run.get('status') if name == 'investigation' else agent.get('status', '-')
            print(f"{flow:<12} {name:<14} {str(state):<16} {str(agent.get('summary') or '')[:70]}")
    report = {"mode": args.mode, "gateway_calls": counter["gateway"], "replayed_calls": counter["replayed"],
              "replay_misses": counter["misses"], "flows": results}
    # Keep every recording some flow still uses; flows that miss are listed for re-recording.
    if args.prune and args.mode == "replay" and set(args.flows or FLOWS) == set(FLOWS):
        cassette = json.loads(CASSETTE.read_text(encoding="utf-8"))
        before = len(cassette["entries"])
        cassette["entries"] = {key: value for key, value in cassette["entries"].items() if key in counter["used_keys"]}
        CASSETTE.write_text(json.dumps(cassette, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        report["pruned_entries"] = before - len(cassette["entries"])
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("mode", "gateway_calls", "replayed_calls", "replay_misses",
                                                    "pruned_entries") if key in report}, ensure_ascii=False))
    return 1 if counter["misses"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

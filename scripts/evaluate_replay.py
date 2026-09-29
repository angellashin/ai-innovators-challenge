"""Deterministic, no-network REPLAY evaluation against the supplied workbook."""

from __future__ import annotations

import json
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKBOOK = PROJECT_ROOT / "REPLAN_demo_inputs.xlsx"
sys.path.insert(0, str(PROJECT_ROOT / "services" / "api"))

from app.events import normalize_event  # noqa: E402
from app.importers import parse_upload  # noqa: E402
from app.main import ConfirmInput, normalize_import_snapshot  # noqa: E402
from app.scheduling import simulate  # noqa: E402


def evaluate(workbook_path: Path = DEFAULT_WORKBOOK) -> dict:
    """Run the deterministic replay against a workbook independent of cwd."""
    workbook_path = Path(workbook_path).expanduser().resolve()
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Replay workbook not found: {workbook_path}")

    parsed = parse_upload(workbook_path.name, workbook_path.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "새 프로젝트", "mode": "REPLAY"}, ConfirmInput())
    project, tasks, options = snapshot["project"], snapshot["tasks"], snapshot["options"]
    expected = {
        (): "2026-10-30",
        ("OPT-01",): "2026-10-30",
        ("OPT-02",): "2026-10-29",
        ("OPT-03",): "2026-10-28",
        ("OPT-02", "OPT-03"): "2026-10-27",
    }
    cases = []
    baseline = simulate(project, tasks)
    cases.append({"case": "baseline", "expected_finish": "2026-10-27", "actual_finish": baseline["finish_date"], "pass": baseline["finish_date"] == "2026-10-27"})
    events = {row["event_id"]: row for row in parsed["events"]}
    for event_id in ("E01", "E02", "E03", "E04", "E05"):
        raw = events[event_id]
        event = normalize_event({"content": raw["body"], "channel": raw["input_type"], "published_at": raw["received_at"], "mode": "REPLAY", "data_origin": "SYNTHETIC"}, project, tasks)
        cases.append({"case": event_id + "_classification", "actual": event["classification_status"], "pass": event["classification_status"] == {"E01": "PATCH_PROPOSED", "E02": "PATCH_PROPOSED", "E03": "NEEDS_INPUT", "E04": "PATCH_PROPOSED", "E05": "NO_SCHEDULE_IMPACT"}[event_id]})
        if event_id == "E01":
            for option_ids, finish in expected.items():
                selected = [item for item in options if item["option_id"] in option_ids]
                started = time.perf_counter()
                result = simulate(project, tasks, event=event, options=selected)
                cases.append({"case": "E01:" + ("+".join(option_ids) or "none"), "expected_finish": finish, "actual_finish": result["finish_date"], "target_met": result["target_met"], "elapsed_ms": round((time.perf_counter() - started) * 1000, 2), "pass": result["finish_date"] == finish})
            result_target = simulate(project, tasks, event=event, options=[item for item in options if item["option_id"] == "OPT-03"])
            cases.append({"case": "E01:OPT-03:target", "pass": result_target["finish_date"] == "2026-10-28" and result_target["target_met"]})
        if event_id == "E02":
            cases.append({"case": "E02:scope", "pass": set(event["patch"].get("blocked_dates", {})) == {"T11"}})
        if event_id == "E04":
            cases.append({"case": "E04:scope", "pass": set(event["patch"].get("resource_unavailable", {})) == {"기계팀"}})
        if event_id in {"E03", "E05"}:
            cases.append({"case": event_id + ":no_patch", "pass": not event["patch"]})
    return {"mode": "REPLAY", "data_origin": "SYNTHETIC", "model": None, "paid_calls": 0, "cost_status": "NOT_APPLICABLE", "cases": cases, "passed": sum(bool(case["pass"]) for case in cases), "total": len(cases)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help="workbook to evaluate (defaults to the repository demo workbook)",
    )
    parser.add_argument("--output", type=Path, help="write the same JSON result to a file")
    args = parser.parse_args()
    result = evaluate(args.input)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["passed"] == result["total"] else 1)

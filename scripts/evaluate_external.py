
"""Offline external-risk regression using one captured public calendar and synthetic schedules."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
from app.external_risks import holiday_patch, weather_patch, match_notice, combine_patches
from app.scheduling import simulate


def evaluate():
    source = json.loads((ROOT / "data/external/hu-2026-holidays.json").read_text(encoding="utf-8"))
    tasks = [
        {"task_id": "LIFT", "name": "Equipment lifting", "supplier_id": "VENDOR", "outdoor": True, "country": "Hungary",
         "baseline_start": "2026-10-22", "baseline_finish": "2026-10-23", "duration_workdays": 2, "predecessor_ids": []},
        {"task_id": "SAT", "name": "Site acceptance test", "outdoor": False, "country": "Hungary",
         "baseline_start": "2026-10-26", "baseline_finish": "2026-10-26", "duration_workdays": 1, "predecessor_ids": ["LIFT"]},
    ]
    config = {"country_code": "HU", "year": 2026, "task_ids": ["LIFT"]}
    patch, _ = holiday_patch(tasks, config, source)
    calculated = simulate({}, tasks, event={"patch": patch})
    # Non-preemptive lifting needs two consecutive available workdays: Mon 26/Tue 27; SAT Wed 28.
    rows = [{
        "case": "observed_hu_holiday_synthetic_project", "source_origin": "PUBLIC_API_SNAPSHOT",
        "schedule_origin": "SYNTHETIC", "expected_finish": "2026-10-28",
        "actual_finish": calculated["finish_date"],
        "pass": patch == {"blocked_dates": {"LIFT": ["2026-10-23"]}} and calculated["finish_date"] == "2026-10-28",
    }]
    forecast = {"forecast": {"units": {"wind_speed_10m_max": "km/h"}, "data": [{"date": "2026-10-23", "wind_speed_10m_max": 45}]}}
    weather, _ = weather_patch(tasks, {"weather_task_ids": ["LIFT"], "weather_limits": {"max_wind_speed_kmh": 35}}, forecast)
    combined = simulate({}, tasks, event={"patch": combine_patches([patch, weather])})
    rows.append({"case": "synthetic_weather_same_day_not_double_counted", "expected_finish": "2026-10-28",
                 "actual_finish": combined["finish_date"], "pass": combined["finish_date"] == "2026-10-28"})
    for text, expected in [("2026-10-23 publication: Equipment lifting permit review", ["LIFT"]),
                           ("Unrelated tourism notice", [])]:
        result = match_notice({"content": text}, tasks, [])
        rows.append({"case": "synthetic_notice_relevance_and_abstention", "expected_tasks": expected,
                     "actual_tasks": result["related_task_ids"],
                     "pass": result["related_task_ids"] == expected and result["patch"] == {}})
    return {"mode": "OFFLINE_EXTERNAL_REGRESSION", "paid_calls": 0,
            "interpretation": "Small engineering regression, not a general news benchmark or LLM accuracy score.",
            "public_source": {key: source[key] for key in ("url", "fetched_at", "body_hash")},
            "cases": rows, "passed": sum(row["pass"] for row in rows), "total": len(rows)}


if __name__ == "__main__":
    result = evaluate()
    (ROOT / "artifacts/external_risk_regression.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] == result["total"] else 1)


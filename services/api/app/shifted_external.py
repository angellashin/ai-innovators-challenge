"""Deterministic fixed-point recheck of calendar evidence after a supplier delay."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import json
from typing import Any

from .external_risks import combine_patches, holiday_patch, overlaps, weather_patch
from .scheduling import simulate

ROOT = Path(__file__).resolve().parents[3]
MAX_RECHECK_ITERATIONS = 12


def bundled_hu_calendars(tasks: list[dict[str, Any]]) -> list[tuple[dict, dict]]:
    """The synthetic hero has a reproducible, explicitly scoped calendar."""
    task_ids = [str(task["task_id"]) for task in tasks
                if str(task.get("country") or task.get("country_code") or "").casefold() in {"hungary", "hu", "헝가리"}]
    rows = []
    # H04 can carry the final launch tasks into January 2028.
    for year in (2025, 2026, 2027, 2028):
        source = json.loads((ROOT / "data" / "external" / f"hu-{year}-holidays.json").read_text(encoding="utf-8"))
        rows.append(({"country_code": "HU", "year": year, "task_ids": task_ids}, source))
    return rows


def _schedule_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(task["task_id"]): task for task in result.get("schedule", [])}


def _days(start: str, finish: str, exclusive: bool) -> list[str]:
    current = date.fromisoformat(start[:10])
    end = date.fromisoformat(finish[:10])
    result = []
    while current < end if exclusive else current <= end:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def recheck_shifted_schedule(
    project: dict[str, Any], tasks: list[dict[str, Any]], event: dict[str, Any],
    options: list[dict[str, Any]], budget: int | None,
    calendars: list[tuple[dict, dict]], weather_sources: list[dict], weather_plan: dict,
    *, max_iterations: int = MAX_RECHECK_ITERATIONS,
) -> dict[str, Any]:
    """Monotonically add newly encountered blocks, stopping at a stable schedule."""
    baseline = simulate(project, tasks, options=options, budget_krw=budget)
    supplier = simulate(project, tasks, event=event, options=options, budget_krw=budget)
    if baseline.get("violations") or supplier.get("violations"):
        return {**supplier, "recheck_status": "NEEDS_INPUT", "recheck_reason": "기준 일정 계산 오류"}
    base_by_id = _schedule_map(baseline)
    task_by_id = {str(task["task_id"]): task for task in tasks}
    approved = {str(task["task_id"]): set(task.get("approved_blocked_dates") or []) | set(task.get("approved_calendar_nonworking_dates") or []) for task in tasks}
    already_blocked = {key: set(value) for key, value in (event.get("patch") or {}).get("blocked_dates", {}).items()}
    external_patch: dict[str, Any] = {}
    constraints: dict[tuple[str, str, str], dict[str, Any]] = {}
    current = supplier
    seasonal: dict[str, dict[str, Any]] = {}
    for iteration in range(1, max_iterations + 1):
        current_by_id = _schedule_map(current)
        additions: list[dict] = []
        for config, source in calendars:
            patch, facts = holiday_patch(list(current_by_id.values()), config, source)
            for fact in facts:
                task_id, day = fact["task_ids"][0], fact["value"]
                if overlaps(base_by_id[task_id], day) or day in approved[task_id] or day in already_blocked.get(task_id, set()):
                    continue
                key = (task_id, day, "public_holiday")
                if key not in constraints:
                    constraints[key] = {**fact, "task_id": task_id, "date": day,
                                        "source_id": source.get("source_id"), "source_url": source.get("url"),
                                        "source_hash": source.get("body_hash"), "basis": "public_calendar",
                                        "requires_confirmation": True}
                    additions.append({"calendar_nonworking_dates": {task_id: [day]}})
        for source in weather_sources:
            patch, facts = weather_patch(list(current_by_id.values()), weather_plan, source)
            for fact in facts:
                task_id, day = fact["task_ids"][0], fact["date"]
                if overlaps(base_by_id[task_id], day) or day in approved[task_id] or day in already_blocked.get(task_id, set()):
                    continue
                key = (task_id, day, "forecast_threshold")
                if key not in constraints:
                    constraints[key] = {**fact, "task_id": task_id, "source_id": source.get("source_id"),
                                        "source_url": source.get("url"), "source_hash": source.get("body_hash"),
                                        "basis": "forecast", "requires_confirmation": True}
                    additions.append({"calendar_nonworking_dates": {task_id: [day]}})
        if additions:
            external_patch = combine_patches([external_patch, *additions])
            combined = combine_patches([event.get("patch") or {}, external_patch])
            current = simulate(project, tasks, event={**event, "patch": combined}, options=options, budget_krw=budget)
            if current.get("violations"):
                return {**current, "recheck_status": "NEEDS_INPUT", "recheck_reason": "외부 제약 재계산 오류"}
            continue
        break
    else:
        return {**current, "recheck_status": "NEEDS_INPUT", "recheck_reason": f"외부 제약 재점검이 {max_iterations}회 안에 수렴하지 않았습니다."}

    if calendars:
        years_by_country = {}
        for config, _ in calendars:
            years_by_country.setdefault(config["country_code"], set()).add(config["year"])
        for row in current["schedule"]:
            country = str(row.get("country_code") or row.get("country") or "").casefold()
            code = "HU" if country in {"hu", "hungary", "헝가리"} else "DE" if country in {"de", "germany", "독일"} else None
            if code in years_by_country:
                span = range(date.fromisoformat(row["planned_start"]).year, date.fromisoformat(row["planned_finish"]).year + 1)
                if any(year not in years_by_country[code] for year in span):
                    return {**current, "recheck_status": "NEEDS_INPUT", "recheck_reason": f"{code} 후속 일정의 공휴일 데이터가 적용 연도를 모두 덮지 못합니다."}

    if weather_plan.get("weather_limits"):
        forecast_days = {row.get("date") for source in weather_sources
                         for row in source.get("forecast", {}).get("data", [])
                         if source.get("status") == "ok"
                         and (not source.get("forecast", {}).get("validity", {}).get("start")
                              or row.get("date") >= source["forecast"]["validity"]["start"])
                         and (not source.get("forecast", {}).get("validity", {}).get("end")
                              or row.get("date") <= source["forecast"]["validity"]["end"])}
        scope = set(weather_plan.get("weather_task_ids") or [])
        for task_id, shifted in _schedule_map(current).items():
            task = task_by_id[task_id]
            if not task.get("outdoor") or (scope and task_id not in scope):
                continue
            if (shifted["planned_start"], shifted["planned_finish"]) == (base_by_id[task_id]["planned_start"], base_by_id[task_id]["planned_finish"]):
                continue
            outside = [day for day in _days(shifted["planned_start"], shifted["planned_finish"], task.get("finish_boundary") == "exclusive")
                       if day not in forecast_days]
            if outside:
                seasonal_source = next((source for source in weather_sources if source.get("seasonal_statistics")), None)
                monthly = (seasonal_source or {}).get("seasonal_statistics") or {}
                statistics = {month: monthly.get("by_month", {}).get(month)
                              for month in sorted({str(date.fromisoformat(day).month) for day in outside})
                              if monthly.get("by_month", {}).get(month)}
                seasonal[task_id] = {"task_id": task_id, "start": outside[0], "finish": outside[-1],
                                     "basis": "seasonal_statistics" if statistics else "seasonal_statistics_unavailable",
                                     "statistics": statistics or None,
                                     "source_url": (seasonal_source or {}).get("url"),
                                     "source_hash": (seasonal_source or {}).get("body_hash"),
                                     "status": "CONDITIONAL" if statistics else "NEEDS_INPUT",
                                     "reason": "예보 범위 밖 계절 통계 위험입니다. 현장 적용 여부 확인 전 확정 지연은 계산하지 않았습니다."
                                               if statistics else "예보 범위 밖입니다. 계절 통계와 현장 중단 기준을 확인해야 합니다. 확정 지연은 계산하지 않았습니다."}

    supplier_by_id = _schedule_map(supplier)
    external_by_id = _schedule_map(current)
    return {**current, "recheck_status": "CONVERGED", "recheck_iterations": iteration,
            "external_source_hashes": {str(source.get("source_id")): source.get("body_hash")
                                       for _, source in calendars
                                       if source.get("source_id")}
                                      | {str(source.get("source_id")): source.get("body_hash")
                                         for source in weather_sources if source.get("source_id")},
            "supplier_finish_date": supplier["finish_date"],
            "supplier_finish_shift_days": (date.fromisoformat(supplier["finish_date"]) - date.fromisoformat(baseline["finish_date"])).days,
            "external_additional_shift_days": (date.fromisoformat(current["finish_date"]) - date.fromisoformat(supplier["finish_date"])).days,
            "supplier_schedule": supplier["schedule"], "combined_patch": combine_patches([event.get("patch") or {}, external_patch]),
            "external_constraints": sorted(constraints.values(), key=lambda row: (row["task_id"], row["date"], row["kind"])),
            "seasonal_risks": sorted(seasonal.values(), key=lambda row: row["task_id"]),
            "delay_breakdown": [
                {"task_id": task_id, "before_start": base_by_id[task_id]["planned_start"],
                 "supplier_start": supplier_by_id[task_id]["planned_start"],
                 "final_start": external_by_id[task_id]["planned_start"],
                 "before_finish": base_by_id[task_id]["planned_finish"],
                 "supplier_finish": supplier_by_id[task_id]["planned_finish"],
                 "final_finish": external_by_id[task_id]["planned_finish"],
                 "supplier_delay_days": (date.fromisoformat(supplier_by_id[task_id]["planned_finish"]) - date.fromisoformat(base_by_id[task_id]["planned_finish"])).days,
                 "external_additional_days": (date.fromisoformat(external_by_id[task_id]["planned_finish"]) - date.fromisoformat(supplier_by_id[task_id]["planned_finish"])).days}
                for task_id in base_by_id if external_by_id[task_id]["planned_finish"] != base_by_id[task_id]["planned_finish"]
            ]}

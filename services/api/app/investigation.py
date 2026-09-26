"""Deterministic tools for investigating an external signal; no LLM here.

Each function returns the compact summary the agent sees. Dates, float and conditional
schedules come from the calculators; purchase-list rows are facts, never schedule inputs
unless a conditional change is requested explicitly.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from .external_risks import combine_patches
from .scheduling import simulate

EXTERNAL_CHANNELS = {"registered_public_source", "public_holiday", "weather_forecast"}
FLOAT_SEARCH_DAYS = 400
# Concepts that can tie a supplier's stated reason to a notice, in either language.
REASON_TERMS = {
    "통관": ("통관", "세관", "customs"),
    "수출 허가": ("수출 허가", "export licence", "export license"),
    "수입 서류": ("수입 서류", "서류", "documentation", "document", "import"),
    "인허가": ("인허가", "허가", "permit", "approval", "license"),
    "환경 규정": ("환경", "environmental", "due-diligence", "규정", "regulation"),
    "인력": ("인력", "workforce", "staff", "crew"),
    "공휴일": ("공휴일", "holiday"),
    "기상": ("기상", "weather", "wind", "rain"),
}
_DURATION = [re.compile(r"up to (\d+) days", re.I), re.compile(r"최대 (\d+)\s*일"), re.compile(r"(\d+) days", re.I)]


def _day(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s*", text) if part.strip()]


def notice_facts(text: str) -> list[dict[str, Any]]:
    """Dates and duration bounds stated literally in a notice, each with its sentence as quote."""
    facts: list[dict[str, Any]] = []
    for sentence in _sentences(text):
        for day in re.findall(r"\d{4}-\d{2}-\d{2}", sentence):
            facts.append({"kind": "date", "value": day, "quote": sentence})
        for pattern in _DURATION:
            match = pattern.search(sentence)
            if match:
                facts.append({"kind": "max_duration_days", "value": int(match.group(1)), "quote": sentence})
                break
    return facts


def reason_terms(*texts: str) -> list[str]:
    """Concepts present in every given text."""
    lowered = [text.lower() for text in texts]
    return [name for name, words in REASON_TERMS.items()
            if all(any(word.lower() in text for word in words) for text in lowered)]


def _published(data: dict[str, Any]) -> str:
    evidence = data.get("evidence") or {}
    return str(data.get("published_at") or evidence.get("published_at") or evidence.get("fetched_at")
               or data.get("received_at") or "")[:10]


def related_signals(event: dict[str, Any], rows: list[dict[str, Any]], version_id: str,
                    window_days: int = 60) -> dict[str, Any]:
    """External changes near the event's date that touch the same tasks."""
    anchor = _published(event)
    tasks = set(event.get("related_task_ids") or [])
    signals = []
    for row in rows:
        data = row["data"]
        if (data.get("channel") not in EXTERNAL_CHANNELS or data.get("version_id") != version_id
                or data.get("review_status") in {"SUPERSEDED", "REJECTED"}):
            continue
        published = _published(data)
        if not anchor or not published:
            continue
        apart = abs((_day(anchor) - _day(published)).days)
        overlap = sorted(tasks & set(data.get("related_task_ids") or []))
        if apart > window_days or not overlap:
            continue
        signals.append({"event_id": row["id"], "channel": data.get("channel"), "title": data.get("title"),
                        "published_at": published, "days_apart": apart, "overlapping_task_ids": overlap,
                        "reason_terms": reason_terms(str(event.get("content") or ""), str(data.get("content") or "")),
                        "quote": str(data.get("content") or "")[:300]})
    signals.sort(key=lambda row: (-len(row["reason_terms"]), row["days_apart"]))
    return {"signals": signals[:5]}


def task_facts(tasks: list[dict[str, Any]], procurement: list[dict[str, Any]],
               task_ids: list[str]) -> dict[str, Any]:
    by_id = {str(task["task_id"]): task for task in tasks}
    origins: dict[str, set[str]] = {}
    for task in tasks:
        if task.get("supplier_id") and task.get("origin_country"):
            origins.setdefault(str(task["supplier_id"]), set()).add(str(task["origin_country"]))
    rows = []
    for task_id in task_ids[:10]:
        task = by_id.get(str(task_id))
        if not task:
            rows.append({"task_id": task_id, "status": "unknown_task"})
            continue
        rows.append({
            **{key: task.get(key) for key in ("task_id", "name", "phase", "status", "supplier_id", "country",
                                              "origin_country", "customs_required", "permit_required",
                                              "baseline_start", "baseline_finish")},
            "supplier_origin_countries": sorted(origins.get(str(task.get("supplier_id")), set())),
            "items": [_item(item) for item in procurement if item.get("needed_for_task_id") == task_id][:5],
        })
    return {"tasks": rows}


def _item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in ("item_id", "item_name", "supplier_id", "origin_country", "customs_required",
                                           "permit_or_certification", "planned_arrival", "needed_for_task_id")}


def mentioned_items(text: str, procurement: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Purchase items a notice names by item ID (e.g. "P-A1")."""
    return [_item(item) for item in procurement
            if item.get("item_id") and re.search(rf"(?<![\w-]){re.escape(str(item['item_id']))}(?![\w-])", text)]


def procurement_items(tasks: list[dict[str, Any]], procurement: list[dict[str, Any]], supplier_id: str = "",
                      origin_country: str = "", customs_required: bool | None = None,
                      arriving_after: str = "") -> dict[str, Any]:
    """Purchase-list items matching the filters, with the date their task needs them."""
    starts = {str(task["task_id"]): str(task.get("baseline_start"))[:10] for task in tasks}
    rows = []
    for item in procurement:
        if supplier_id and item.get("supplier_id") != supplier_id:
            continue
        if origin_country and item.get("origin_country") != origin_country:
            continue
        if customs_required is not None and bool(item.get("customs_required")) != customs_required:
            continue
        if arriving_after and str(item.get("planned_arrival") or "") <= arriving_after:
            continue
        rows.append({**_item(item), "needed_by": starts.get(str(item.get("needed_for_task_id")))})
    rows.sort(key=lambda row: str(row.get("planned_arrival") or ""))
    return {"items": rows[:8]}


def schedule_slack(project: dict[str, Any], tasks: list[dict[str, Any]], task_ids: list[str],
                   base_patch: dict[str, Any] | None = None, bound_days: int | None = None) -> dict[str, Any]:
    """How many calendar days each task's start can slip before the project finish moves."""
    by_id = {str(task["task_id"]): task for task in tasks}
    base_patch = base_patch or {}
    finish = simulate(project, tasks, event={"patch": base_patch})["finish_date"]

    pinned = base_patch.get("estimated_finish") or {}

    def finish_with(task_id: str, days: int) -> str:
        if task_id in pinned:
            patch = {**base_patch, "estimated_finish": {**pinned, task_id: (_day(pinned[task_id]) + timedelta(days=days)).isoformat()}}
        else:
            start = (_day(by_id[task_id]["baseline_start"]) + timedelta(days=days)).isoformat()
            patch = combine_patches([base_patch, {"not_before": {task_id: start}}])
        return simulate(project, tasks, event={"patch": patch})["finish_date"]

    rows = []
    for task_id in task_ids[:10]:
        if task_id not in by_id:
            rows.append({"task_id": task_id, "status": "unknown_task"})
            continue
        low, high = 0, FLOAT_SEARCH_DAYS
        while low < high:
            middle = (low + high + 1) // 2
            if finish_with(task_id, middle) == finish:
                low = middle
            else:
                high = middle - 1
        row = {"task_id": task_id, "name": by_id[task_id].get("name"), "float_calendar_days": low,
               "on_critical_path": low == 0, "float_at_least": low == FLOAT_SEARCH_DAYS}
        if bound_days is not None:
            row["absorbs_bound"] = low >= bound_days
        rows.append(row)
    return {"project_finish": finish, "target_finish": project.get("target_finish"), "bound_days": bound_days,
            "tasks": rows}


def conditional_changes(tasks: list[dict[str, Any]], procurement: list[dict[str, Any]],
                        changes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Turn stated holds into a not-before patch; date arithmetic happens here, not in the model."""
    by_id = {str(task["task_id"]): task for task in tasks}
    items = {str(item.get("item_id")): item for item in procurement}
    not_before: dict[str, str] = {}
    applied = []
    for change in changes:
        kind, value = change.get("kind"), change.get("value")
        item = items.get(str(change.get("item_id") or ""))
        task_id = str(change.get("task_id") or (item or {}).get("needed_for_task_id") or "")
        if task_id not in by_id:
            raise ValueError(f"unknown task {task_id!r}")
        start = _day(by_id[task_id]["baseline_start"])
        if kind == "hold_after_arrival":
            if not item or not item.get("planned_arrival"):
                raise ValueError("hold_after_arrival needs a purchase-list item with planned_arrival")
            earliest = _day(item["planned_arrival"]) + timedelta(days=int(value))
            latest_action = start - timedelta(days=int(value))
        elif kind == "hold_after_start":
            earliest = start + timedelta(days=int(value))
            latest_action = start - timedelta(days=int(value))
        elif kind == "not_before":
            earliest, latest_action = _day(value), None
        else:
            raise ValueError(f"unsupported change kind {kind!r}")
        if earliest > start:
            not_before[task_id] = max(not_before.get(task_id, ""), earliest.isoformat())
        applied.append({"task_id": task_id, "item_id": (item or {}).get("item_id"), "kind": kind, "value": value,
                        "not_before": earliest.isoformat(), "needed_by": start.isoformat(),
                        "latest_action_date": latest_action.isoformat() if latest_action else None})
    return ({"not_before": not_before} if not_before else {}), applied


def conditional_summary(result: dict[str, Any], base_finish: str, baseline_finish: str,
                        applied: list[dict[str, Any]], target_finish: str | None) -> dict[str, Any]:
    finish = result.get("finish_date")
    constraints = result.get("external_constraints") or []
    changed = [row for row in result.get("delay_breakdown") or []
               if row.get("supplier_delay_days") or row.get("external_additional_days")]
    return {
        "status": result.get("recheck_status") or ("CONVERGED" if finish else "FAILED"),
        "finish_date": finish,
        "finish_shift_days": (_day(finish) - _day(baseline_finish)).days if finish else None,
        "added_shift_days_vs_reported_change": (_day(finish) - _day(base_finish)).days if finish and base_finish else None,
        "finish_with_reported_change_only": base_finish,
        "target_met": bool(finish and target_finish and finish <= str(target_finish)[:10]),
        "external_additional_shift_days": result.get("external_additional_shift_days"),
        "changes": applied,
        "new_calendar_constraints": [{key: row.get(key) for key in ("task_id", "date", "name")} for row in constraints[:8]],
        "changed_task_count": len(changed),
        "top_changed_tasks": [{key: row.get(key) for key in ("task_id", "before_finish", "final_finish")}
                              for row in changed[:5]],
        "conditional": True,
    }

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from typing import Any, Optional, Union

from ..events import infer_event_patch


DateLike = Union[date, str]


def validate_tasks(tasks: list[dict[str, Any]]) -> list[str]:
    """Return deterministic validation errors for normalized schedule tasks."""
    errors: list[str] = []
    seen: set[str] = set()
    task_ids: set[str] = set()

    for index, task in enumerate(tasks):
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            errors.append(f"task[{index}] missing task_id")
            continue
        if task_id in seen:
            errors.append(f"duplicate task_id: {task_id}")
        seen.add(task_id)
        task_ids.add(task_id)

        duration = _int_value(task.get("duration_workdays"), default=0)
        if duration <= 0:
            errors.append(f"{task_id} duration_workdays must be positive")

        demand = _int_value(task.get("resource_demand"), default=1)
        capacity = _int_value(task.get("resource_capacity"), default=1)
        if demand <= 0:
            errors.append(f"{task_id} resource_demand must be positive")
        if capacity <= 0:
            errors.append(f"{task_id} resource_capacity must be positive")
        if demand > capacity:
            errors.append(f"{task_id} resource_demand exceeds resource_capacity")

        if str(task.get("dependency_type") or "FS").upper() not in {"FS", "SS"}:
            errors.append(f"{task_id} dependency_type must be FS or SS")

        for field in ("baseline_start", "baseline_finish"):
            if task.get(field) is None:
                errors.append(f"{task_id} missing {field}")
                continue
            try:
                _parse_date(task[field])
            except ValueError as exc:
                errors.append(f"{task_id} invalid {field}: {exc}")

    for task in tasks:
        task_id = str(task.get("task_id") or "").strip()
        for predecessor_id in _predecessor_ids(task):
            if predecessor_id not in task_ids:
                errors.append(f"{task_id} references missing predecessor: {predecessor_id}")

    errors.extend(_cycle_errors(tasks))
    return errors


def simulate(
    project: dict[str, Any],
    tasks: list[dict[str, Any]],
    event: Optional[dict[str, Any]] = None,
    options: Optional[list[dict[str, Any]]] = None,
    budget_krw: Optional[int] = None,
) -> dict[str, Any]:
    """Simulate a deterministic day-level schedule with FS/SS links and capacities."""
    validation_errors = validate_tasks(tasks)
    if validation_errors:
        return {
            "finish_date": None,
            "schedule": [],
            "extra_cost_krw": 0,
            "target_met": False,
            "budget_met": budget_krw is None or budget_krw >= 0,
            "budget_status": "UNSET" if budget_krw is None else "SET",
            "conditional": [],
            "violations": validation_errors,
            "scenario_hash": _stable_hash({"errors": validation_errors}),
        }

    task_map = {str(task["task_id"]): deepcopy(task) for task in tasks}
    conditional: list[str] = []
    violations: list[str] = []
    extra_cost_krw = 0
    allow_earlier: set[str] = set()
    blocked_dates = _project_blocked_dates(project)
    scoped_blocked_dates: list[dict[str, Any]] = [
        {"task_id": str(task["task_id"]), "dates": task.get("approved_blocked_dates", [])}
        for task in tasks if task.get("approved_blocked_dates")
    ]
    for calendar in project.get("supplier_calendars", []):
        for task in tasks:
            supplier = str(task.get("supplier_id") or task.get("supplier") or task.get("owner") or "")
            if supplier == str(calendar.get("supplier_id")):
                scoped_blocked_dates.append({"task_id": str(task["task_id"]), "dates": calendar.get("unavailable_dates", [])})
    resource_unavailable: list[dict[str, Any]] = []

    _apply_event(project, task_map, event, resource_unavailable, blocked_dates, scoped_blocked_dates)

    for option in options or []:
        option_id = str(option.get("option_id") or "").strip()
        operation = str(option.get("operation") or "").strip().upper()
        target_ids = [str(value) for value in option.get("target_ids") or []]
        if option_id == "OPT-04" or operation == "REQUEST_TARGET_CHANGE":
            conditional.append(_option_condition(option))
            continue

        if operation == "SHIFT_EARLIER":
            allow_earlier.update(target_ids)
        elif operation == "SET_DURATION_WORKDAYS":
            for task_id in target_ids:
                if task_id in task_map:
                    task_map[task_id]["duration_workdays"] = _int_value(option.get("new_value"), default=1)
        elif operation == "REDUCE_DURATION_WORKDAYS":
            reduction = _int_value(option.get("reduction_workdays"), default=0)
            if reduction <= 0:
                violations.append(f"{option_id or 'option'} reduction_workdays must be positive")
                continue
            for task_id in target_ids:
                if task_id in task_map:
                    current = _int_value(task_map[task_id].get("duration_workdays"), default=1)
                    task_map[task_id]["duration_workdays"] = max(1, current - reduction)
        else:
            violations.append(f"{option_id or 'option'} unsupported operation: {operation or '<missing>'}")
            continue

        extra_cost_krw += _int_value(option.get("extra_cost_krw"), default=0)
        condition = _option_condition(option)
        if condition:
            conditional.append(condition)

    ordered_ids = _topological_order(list(task_map.values()))
    scheduled: dict[str, dict[str, Any]] = {}
    occupancy: dict[tuple[str, str], dict[date, int]] = defaultdict(lambda: defaultdict(int))

    for task_id in ordered_ids:
        task = task_map[task_id]
        planned = _schedule_task(
            project=project,
            task=task,
            scheduled=scheduled,
            occupancy=occupancy,
            blocked_dates=blocked_dates,
            scoped_blocked_dates=scoped_blocked_dates,
            resource_unavailable=resource_unavailable,
            may_start_before_baseline=task_id in allow_earlier,
        )
        scheduled[task_id] = planned

    schedule = [scheduled[task_id] for task_id in ordered_ids]
    finish = max(_parse_date(item["planned_finish"]) for item in schedule)
    target_finish = project.get("target_finish")
    target_met = True if target_finish is None else finish <= _parse_date(target_finish)
    resolved_budget = budget_krw
    budget_met = True if resolved_budget is None else extra_cost_krw <= int(resolved_budget)

    scenario = {
        "project_id": project.get("project_id"),
        "event_id": None if event is None else event.get("event_id"),
        "option_ids": [option.get("option_id") for option in options or []],
        "finish_date": finish.isoformat(),
        "extra_cost_krw": extra_cost_krw,
        "schedule": [(item["task_id"], item["planned_start"], item["planned_finish"]) for item in schedule],
    }

    return {
        "finish_date": finish.isoformat(),
        "schedule": schedule,
        "extra_cost_krw": extra_cost_krw,
        "target_met": target_met,
        "budget_met": budget_met,
        "budget_status": "UNSET" if resolved_budget is None else "WITHIN_LIMIT" if budget_met else "OVER_LIMIT",
        "budget_limit_krw": resolved_budget,
        "conditional": conditional,
        "violations": violations,
        "scenario_hash": _stable_hash(scenario),
    }


def _schedule_task(
    *,
    project: dict[str, Any],
    task: dict[str, Any],
    scheduled: dict[str, dict[str, Any]],
    occupancy: dict[tuple[str, str], dict[date, int]],
    blocked_dates: set[date],
    scoped_blocked_dates: list[dict[str, Any]],
    resource_unavailable: list[dict[str, Any]],
    may_start_before_baseline: bool,
) -> dict[str, Any]:
    task_id = str(task["task_id"])
    duration = _int_value(task.get("duration_workdays"), default=1)
    baseline_start = _parse_date(task["baseline_start"])
    earliest = baseline_start if not may_start_before_baseline else _project_start(project, baseline_start)

    relationship = str(task.get("dependency_type") or "FS").upper()
    predecessor_constraints: list[date] = []
    for predecessor_id in _predecessor_ids(task):
        if predecessor_id not in scheduled:
            continue
        predecessor = scheduled[predecessor_id]
        if relationship == "SS":
            predecessor_constraints.append(_parse_date(predecessor["planned_start"]))
        else:
            predecessor_finish = _parse_date(predecessor["planned_finish"])
            predecessor_constraints.append(
                predecessor_finish if predecessor.get("finish_boundary") == "exclusive" else _next_day(predecessor_finish)
            )
    if predecessor_constraints:
        earliest = max(earliest, max(predecessor_constraints))

    if task.get("not_before"):
        earliest = max(earliest, _parse_date(task["not_before"]))

    if task.get("fixed_finish") or _is_fixed(task):
        finish = _parse_date(task.get("fixed_finish") or task.get("planned_finish") or task["baseline_finish"])
        start = _parse_date(task.get("fixed_start") or task.get("planned_start") or task["baseline_start"])
        _reserve(
            task,
            _workdays_between(project, task, start, finish, blocked_dates, scoped_blocked_dates, resource_unavailable),
            occupancy,
        )
        return _scheduled_task(task, start, finish)

    current = earliest
    while True:
        start = (
            current
            if task.get("finish_boundary") == "exclusive"
            else _next_allowed_workday(project, task, current, blocked_dates, scoped_blocked_dates, resource_unavailable)
        )
        workdays = _collect_workdays_nonpreemptive(
            project,
            task,
            start,
            duration,
            blocked_dates,
            scoped_blocked_dates,
            resource_unavailable,
        )
        if workdays and _resource_capacity_available(task, workdays, occupancy):
            finish = (
                workdays[-1] + timedelta(days=_int_value(task.get("finish_boundary_offset_days"), default=1))
                if task.get("finish_boundary") == "exclusive"
                else workdays[-1]
            )
            _reserve(task, workdays, occupancy)
            return _scheduled_task(task, start, finish)
        current = _next_day(start)


def _apply_event(
    project: dict[str, Any],
    task_map: dict[str, dict[str, Any]],
    event: Optional[dict[str, Any]],
    resource_unavailable: list[dict[str, Any]],
    blocked_dates: set[date],
    scoped_blocked_dates: list[dict[str, Any]],
) -> None:
    if not event:
        return

    patch = event.get("patch") or {}
    if not patch and event.get("content"):
        inferred = infer_event_patch(event, project, list(task_map.values()))
        patch = inferred.get("patch") or {}

    _apply_typed_patch(task_map, patch, scoped_blocked_dates, resource_unavailable)

    for patch_item in event.get("patches") or []:
        task_id = str(patch_item.get("task_id") or "")
        if task_id and task_id in task_map:
            task_map[task_id].update({key: value for key, value in patch_item.items() if key != "task_id"})

    for task_id, patch in (event.get("task_patches") or {}).items():
        if task_id in task_map and isinstance(patch, dict):
            task_map[task_id].update(patch)

    for raw_date in event.get("blocked_dates") or []:
        blocked_dates.add(_parse_date(raw_date))

    resource_unavailable.extend(event.get("resource_unavailable") or [])


def _apply_typed_patch(
    task_map: dict[str, dict[str, Any]],
    patch: dict[str, Any],
    scoped_blocked_dates: list[dict[str, Any]],
    resource_unavailable: list[dict[str, Any]],
) -> None:
    for task_id, value in (patch.get("estimated_finish") or {}).items():
        if task_id in task_map:
            task_map[task_id]["fixed_finish"] = _parse_date(value).isoformat()

    for task_id, value in (patch.get("not_before") or {}).items():
        if task_id in task_map:
            task_map[task_id]["not_before"] = _parse_date(value).isoformat()

    for task_id, values in (patch.get("blocked_dates") or {}).items():
        scoped_blocked_dates.append({"task_id": str(task_id), "dates": [_parse_date(value).isoformat() for value in values]})

    for task_id, values in (patch.get("calendar_nonworking_dates") or {}).items():
        if task_id in task_map:
            task_map[task_id]["calendar_nonworking_dates"] = sorted(set(task_map[task_id].get("calendar_nonworking_dates") or []) | set(values))

    for resource_group, values in (patch.get("resource_unavailable") or {}).items():
        resource_unavailable.append(
            {"resource_group": str(resource_group), "dates": [_parse_date(value).isoformat() for value in values]}
        )


def _project_blocked_dates(project: dict[str, Any]) -> set[date]:
    dates = list(project.get("nonworking_dates") or [])
    # Old snapshots without scoped calendars retain their previous meaning.
    if "supplier_calendars" not in project:
        dates += list(project.get("supplier_unavailable_dates") or [])
    return {_parse_date(value) for value in dates}


def _is_fixed(task: dict[str, Any]) -> bool:
    return str(task.get("status") or "").strip() in {"완료", "completed", "COMPLETE"}


def _scheduled_task(task: dict[str, Any], start: date, finish: date) -> dict[str, Any]:
    result = deepcopy(task)
    result["planned_start"] = start.isoformat()
    result["planned_finish"] = finish.isoformat()
    return result


def _workdays_between(
    project: dict[str, Any],
    task: dict[str, Any],
    start: date,
    finish: date,
    blocked_dates: set[date],
    scoped_blocked_dates: list[dict[str, Any]],
    resource_unavailable: list[dict[str, Any]],
) -> list[date]:
    days: list[date] = []
    current = start
    finish_inclusive = finish - timedelta(days=1) if task.get("finish_boundary") == "exclusive" else finish
    while current <= finish_inclusive:
        if _is_workday(project, task, current, blocked_dates, scoped_blocked_dates, resource_unavailable):
            days.append(current)
        current += timedelta(days=1)
    return days


def _collect_workdays_nonpreemptive(
    project: dict[str, Any],
    task: dict[str, Any],
    start: date,
    duration: int,
    blocked_dates: set[date],
    scoped_blocked_dates: list[dict[str, Any]],
    resource_unavailable: list[dict[str, Any]],
) -> Optional[list[date]]:
    days: list[date] = []
    current = start
    while len(days) < duration:
        if (_is_calendar_workday(project, current, blocked_dates)
                and current.isoformat() not in task.get("calendar_nonworking_dates", [])
                and current.isoformat() not in task.get("approved_calendar_nonworking_dates", [])):
            days.append(current)
        current += timedelta(days=1)
    finish = days[-1]
    current = start
    while current <= finish:
        if _is_calendar_workday(project, current, blocked_dates) and _is_scoped_unavailable(
            task,
            current,
            scoped_blocked_dates,
            resource_unavailable,
        ):
            return None
        current += timedelta(days=1)
    return days


def _is_workday(
    project: dict[str, Any],
    task: dict[str, Any],
    current: date,
    blocked_dates: set[date],
    scoped_blocked_dates: list[dict[str, Any]],
    resource_unavailable: list[dict[str, Any]],
) -> bool:
    if current.isoformat() in task.get("calendar_nonworking_dates", []) or current.isoformat() in task.get("approved_calendar_nonworking_dates", []):
        return False
    supplier = str(task.get("supplier_id") or task.get("supplier") or task.get("owner") or "")
    for calendar in project.get("supplier_calendars", []):
        if supplier == str(calendar.get("supplier_id")) and current.isoformat() in calendar.get("unavailable_dates", []):
            return False
    return _is_calendar_workday(project, current, blocked_dates) and not _is_scoped_unavailable(
        task,
        current,
        scoped_blocked_dates,
        resource_unavailable,
    )


def _is_calendar_workday(project: dict[str, Any], current: date, blocked_dates: set[date]) -> bool:
    weekend_days = set(project.get("weekend_days") or [5, 6])
    if current.weekday() in weekend_days:
        return False
    if current in blocked_dates:
        return False
    return True


def _is_scoped_unavailable(
    task: dict[str, Any],
    current: date,
    scoped_blocked_dates: list[dict[str, Any]],
    resource_unavailable: list[dict[str, Any]],
) -> bool:
    for item in scoped_blocked_dates:
        if _date_applies_to_task(task, current, item):
            return True
    for item in resource_unavailable:
        if _date_applies_to_task(task, current, item):
            return True
    return False


def _date_applies_to_task(task: dict[str, Any], current: date, item: dict[str, Any]) -> bool:
    dates = {_parse_date(value) for value in item.get("dates") or []}
    if item.get("date"):
        dates.add(_parse_date(item["date"]))
    if item.get("start") and item.get("finish"):
        start = _parse_date(item["start"])
        finish = _parse_date(item["finish"])
        if start <= current <= finish:
            dates.add(current)
    if dates and current not in dates:
        return False
    if item.get("task_id") and str(item["task_id"]) != str(task.get("task_id")):
        return False
    if item.get("location_id") and str(item["location_id"]) != str(task.get("location_id")):
        return False
    if item.get("resource_group") and str(item["resource_group"]) != str(task.get("resource_group")):
        return False
    return bool(dates)


def _resource_capacity_available(
    task: dict[str, Any],
    workdays: list[date],
    occupancy: dict[tuple[str, str], dict[date, int]],
) -> bool:
    if not task.get("resource_group"):
        return True
    key = _resource_key(task)
    demand = _int_value(task.get("resource_demand"), default=1)
    capacity = _int_value(task.get("resource_capacity"), default=1)
    return all(occupancy[key][day] + demand <= capacity for day in workdays)


def _reserve(
    task: dict[str, Any],
    workdays: list[date],
    occupancy: dict[tuple[str, str], dict[date, int]],
) -> None:
    if not task.get("resource_group"):
        return
    key = _resource_key(task)
    demand = _int_value(task.get("resource_demand"), default=1)
    for day in workdays:
        occupancy[key][day] += demand


def _resource_key(task: dict[str, Any]) -> tuple[str, str]:
    return (str(task.get("location_id") or ""), str(task.get("resource_group") or ""))


def _next_allowed_workday(
    project: dict[str, Any],
    task: dict[str, Any],
    current: date,
    blocked_dates: set[date],
    scoped_blocked_dates: list[dict[str, Any]],
    resource_unavailable: list[dict[str, Any]],
) -> date:
    while not _is_workday(project, task, current, blocked_dates, scoped_blocked_dates, resource_unavailable):
        current += timedelta(days=1)
    return current


def _topological_order(tasks: list[dict[str, Any]]) -> list[str]:
    by_id = {str(task["task_id"]): task for task in tasks}
    indegree = {task_id: 0 for task_id in by_id}
    children: dict[str, list[str]] = defaultdict(list)
    for task_id, task in by_id.items():
        for predecessor_id in _predecessor_ids(task):
            if predecessor_id in by_id:
                children[predecessor_id].append(task_id)
                indegree[task_id] += 1

    baseline_rank = {
        task_id: (_rank_date(task.get("baseline_start")), task_id)
        for task_id, task in by_id.items()
    }
    ready = deque(sorted([task_id for task_id, count in indegree.items() if count == 0], key=baseline_rank.get))
    ordered: list[str] = []
    while ready:
        task_id = ready.popleft()
        ordered.append(task_id)
        for child_id in sorted(children[task_id], key=baseline_rank.get):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
        ready = deque(sorted(ready, key=baseline_rank.get))
    return ordered


def _cycle_errors(tasks: list[dict[str, Any]]) -> list[str]:
    by_id = {str(task.get("task_id")): task for task in tasks if task.get("task_id")}
    ordered = _topological_order(list(by_id.values())) if by_id else []
    if len(ordered) == len(by_id):
        return []
    acyclic = set(ordered)
    cyclic = sorted(set(by_id) - acyclic)
    return [f"cycle detected involving: {','.join(cyclic)}"]


def _predecessor_ids(task: dict[str, Any]) -> list[str]:
    raw = task.get("predecessor_ids")
    if raw is None:
        return []
    if isinstance(raw, str):
        if not raw.strip():
            return []
        return [value.strip() for value in raw.split(",") if value.strip()]
    return [str(value).strip() for value in raw if str(value).strip()]


def _project_start(project: dict[str, Any], fallback: date) -> date:
    if project.get("baseline_start"):
        return _parse_date(project["baseline_start"])
    return fallback


def _next_day(value: date) -> date:
    return value + timedelta(days=1)


def _parse_date(value: DateLike) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"expected ISO date string, got {value!r}")
    return date.fromisoformat(value[:10])


def _rank_date(value: Any) -> date:
    try:
        return _parse_date(value)
    except (TypeError, ValueError):
        return date.min


def _int_value(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _option_condition(option: dict[str, Any]) -> str:
    option_id = str(option.get("option_id") or "OPTION")
    approval_state = str(option.get("approval_state") or "").strip()
    conditions = str(option.get("conditions") or "").strip()
    details = " / ".join(value for value in (approval_state, conditions) if value)
    return f"{option_id}: {details}" if details else ""


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()

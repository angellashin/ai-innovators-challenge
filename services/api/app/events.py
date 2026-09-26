"""Normalize external change messages into reviewable, task-scoped facts.

The event layer deliberately stops at a proposed interpretation. A proposed
patch is not a confirmed schedule change until a project operator reviews it.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _year(value: dict[str, Any], project: dict[str, Any]) -> int:
    timestamp = value.get("published_at") or value.get("received_at") or value.get("simulation_as_of")
    if timestamp:
        try:
            return datetime.fromisoformat(str(timestamp)).year
        except ValueError:
            pass
    baseline = project.get("baseline_start") or project.get("baseline_start_date")
    if baseline:
        try:
            return date.fromisoformat(str(baseline)[:10]).year
        except ValueError:
            pass
    return datetime.now().year


def _extract_dates(content: str, year: int) -> list[str]:
    """Return dates in source order without guessing relative day labels."""
    matches: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []

    for match in re.finditer(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)", content):
        try:
            value = date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            continue
        matches.append((match.start(), value))
        occupied.append(match.span())

    for match in re.finditer(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일?", content):
        try:
            value = date(year, int(match.group(1)), int(match.group(2))).isoformat()
        except ValueError:
            continue
        matches.append((match.start(), value))
        occupied.append(match.span())

    month_pattern = "|".join(MONTHS)
    for match in re.finditer(rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", content, re.IGNORECASE):
        try:
            value = date(year, MONTHS[match.group(1).lower()], int(match.group(2))).isoformat()
        except ValueError:
            continue
        matches.append((match.start(), value))
        occupied.append(match.span())

    for match in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})\b", content):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        try:
            value = date(year, int(match.group(1)), int(match.group(2))).isoformat()
        except ValueError:
            continue
        matches.append((match.start(), value))

    return [value for _, value in sorted(matches)]


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("task_id") or task.get("id") or "").strip()


def _task_text(task: dict[str, Any]) -> str:
    fields = (
        "task_id", "id", "name", "task_name", "activity", "phase", "task_type",
        "category", "equipment", "equipment_name", "supplier", "supplier_name",
        "location_id", "location", "resource_group", "resource_name",
    )
    return " ".join(str(task.get(field) or "") for field in fields).lower()


def _task_name_text(task: dict[str, Any]) -> str:
    return " ".join(str(task.get(field) or "") for field in ("name", "task_name", "activity", "equipment", "equipment_name")).lower()


def _direct_task_ids(content: str, tasks: list[dict[str, Any]]) -> list[str]:
    lowered = content.lower()
    found = []
    for task in tasks:
        task_id = _task_id(task)
        if task_id and re.search(rf"(?<![\w-]){re.escape(task_id.lower())}(?![\w-])", lowered):
            found.append(task_id)
    return found


def _matching_tasks(tasks: list[dict[str, Any]], direct_ids: list[str], terms: tuple[str, ...]) -> list[dict[str, Any]]:
    if direct_ids:
        by_id = {_task_id(task): task for task in tasks}
        return [by_id[item] for item in direct_ids if item in by_id]
    scored = []
    for task in tasks:
        haystack = _task_text(task)
        score = sum(1 for term in terms if term.lower() in haystack)
        if score:
            scored.append((score, str(task.get("baseline_start") or ""), _task_id(task), task))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in scored[:3]]


def _dates_for_range(content: str, dates: list[str]) -> list[str]:
    """Handle Korean ranges such as 10월 8~9일 before falling back to dates."""
    iso_range = re.search(r"(20\d{2}-\d{2}-\d{2})\s*(?:부터|[~–-])\s*(20\d{2}-\d{2}-\d{2})", content)
    if iso_range:
        first, last = (date.fromisoformat(value) for value in iso_range.groups())
        span = (last - first).days
        if 0 <= span <= 90:
            from datetime import timedelta

            return [(first + timedelta(days=offset)).isoformat() for offset in range(span + 1)]
    match = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*[~\-]\s*(\d{1,2})\s*일?", content)
    if match:
        year = date.fromisoformat(dates[-1]).year if dates else datetime.now().year
        month, first, last = map(int, match.groups())
        try:
            return [date(year, month, day).isoformat() for day in range(first, last + 1)]
        except ValueError:
            return []
    return dates[-2:] if len(dates) >= 2 else dates


def infer_event_patch(value: dict[str, Any], project: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Infer a small, explainable patch from a message.

    This is intentionally conservative. It uses explicit task references and
    task metadata, never a fixed demo ID or a literal event date. Ambiguous
    messages return no patch plus missing fields for the human review queue.
    """
    content = str(value.get("content") or value.get("message") or "")
    lowered = content.lower()
    direct_ids = _direct_task_ids(content, tasks)
    related = list(direct_ids)
    dates = _extract_dates(content, _year(value, project))
    patch: dict[str, Any] = {}
    facts: list[dict[str, Any]] = []

    if ("전화번호" in content or "연락처" in content) and (
        "변경되지" in content
        or not any(word in content for word in ("지연", "불가", "변경", "늦", "delay", "unavailable"))
    ):
        return {"patch": {}, "related_task_ids": related, "facts": [], "classification_status": "NO_SCHEDULE_IMPACT"}

    production_tasks = _matching_tasks(
        tasks, direct_ids,
        ("fabrication", "manufactur", "production", "제작", "제조", "생산", "완료"),
    )
    test_tasks = _matching_tasks(
        tasks, [], ("fat", "test", "inspection", "acceptance", "시험", "인수시험", "시운전"),
    )
    arrival_tasks = _matching_tasks(
        tasks, direct_ids,
        ("arrival", "delivery", "shipping", "shipment", "반입", "도착", "납품", "출하", "운송"),
    )

    if not direct_ids:
        if "설비 제작" in content:
            named = [task for task in production_tasks if "설비 제작" in _task_name_text(task)]
            if named:
                production_tasks = named
        elif "fabrication" in lowered or "manufactur" in lowered:
            grouped = [task for task in production_tasks if any(term in _task_text(task) for term in ("fabrication", "manufactur"))]
            if grouped:
                production_tasks = grouped

        if "fat" in lowered:
            named = [task for task in test_tasks if "fat" in _task_name_text(task) or "test" in str(task.get("resource_group") or "").lower()]
            if named:
                test_tasks = named
        elif "시운전" in content:
            named = [task for task in test_tasks if "시운전" in _task_name_text(task)]
            if named:
                test_tasks = named

    has_change_language = any(word in lowered for word in ("변경", "지연", "늦", "moves", "moved", "delay", "late", "unavailable", "불가능", "수행하지", "않는", "중단", "취소"))
    if dates and has_change_language:
        has_test_start = "fat" in lowered or "시운전" in content or any(word in content for word in ("FAT", "인수시험", "시험 시작", "시험은"))
        if any(word in content for word in ("야외", "outdoor")) and any(word in lowered for word in ("인양", "lifting")):
            lifting_tasks = _matching_tasks(tasks, direct_ids, ("인양", "lifting", "outdoor"))
            if dates:
                impacted_date = date.fromisoformat(dates[-1])
                same_day = []
                for task in lifting_tasks:
                    start = task.get("baseline_start") or task.get("planned_start")
                    finish = task.get("baseline_finish") or task.get("planned_finish")
                    try:
                        if start and finish and date.fromisoformat(str(start)[:10]) <= impacted_date <= date.fromisoformat(str(finish)[:10]):
                            same_day.append(task)
                    except ValueError:
                        continue
                lifting_tasks = same_day or lifting_tasks
            task_ids = [_task_id(task) for task in lifting_tasks if _task_id(task)]
            if task_ids:
                patch["blocked_dates"] = {task_id: [dates[-1]] for task_id in task_ids}
                related.extend(task_ids)
                facts.append({"kind": "blocked_dates", "task_ids": task_ids, "value": dates[-1], "confidence": "suggested"})
        if production_tasks and ("제작" in content or "fabrication" in lowered or "manufactur" in lowered):
            finish = dates[-2] if has_test_start and len(dates) >= 2 else dates[-1]
            task_ids = [_task_id(task) for task in production_tasks if _task_id(task)]
            if task_ids:
                patch["estimated_finish"] = {task_id: finish for task_id in task_ids}
                related.extend(task_ids)
                facts.append({"kind": "estimated_finish", "task_ids": task_ids, "value": finish, "confidence": "suggested"})
        if has_test_start and dates:
            fat_start = dates[-1]
            fat_candidates = test_tasks
            if direct_ids:
                direct_test = [task for task in tasks if _task_id(task) in direct_ids and task in test_tasks]
                fat_candidates = direct_test or test_tasks
            task_ids = [_task_id(task) for task in fat_candidates if _task_id(task)]
            if task_ids:
                patch["not_before"] = {task_id: fat_start for task_id in task_ids}
                related.extend(task_ids)
                facts.append({"kind": "not_before", "task_ids": task_ids, "value": fat_start, "confidence": "suggested"})
        has_delivery_change = any(word in lowered for word in ("arrival", "delivery", "shipping", "shipment", "customs", "반입", "도착", "납품", "출하", "운송", "통관"))
        if arrival_tasks and has_delivery_change and "estimated_finish" not in patch:
            finish = dates[-2] if has_test_start and len(dates) >= 2 else dates[-1]
            task_ids = [_task_id(task) for task in arrival_tasks if _task_id(task)]
            patch["estimated_finish"] = {task_id: finish for task_id in task_ids}
            related.extend(task_ids)
            facts.append({"kind": "estimated_finish", "task_ids": task_ids, "value": finish, "confidence": "suggested"})
        if direct_ids and "estimated_finish" not in patch and "완료일" in content and not has_test_start:
            patch["estimated_finish"] = {task_id: dates[-1] for task_id in direct_ids}
            facts.append({"kind": "estimated_finish", "task_ids": direct_ids, "value": dates[-1], "confidence": "suggested"})

    resource_terms = ("resource", "engineer", "team", "인력", "기사", "팀", "투입", "가용", "불가능", "unavailable")
    if any(term in lowered for term in resource_terms) and ("불가능" in content or "unavailable" in lowered or "가용" in content):
        range_dates = _dates_for_range(content, dates)
        resource_groups = []
        for task in tasks:
            group = str(task.get("resource_group") or "").strip()
            if group and group.lower() in lowered and group not in resource_groups:
                resource_groups.append(group)
        if not resource_groups and direct_ids:
            resource_groups = list(dict.fromkeys(str(task.get("resource_group") or "") for task in tasks if _task_id(task) in direct_ids and task.get("resource_group")))
        if range_dates and resource_groups:
            patch["resource_unavailable"] = {group: range_dates for group in resource_groups}
            affected = [_task_id(task) for task in tasks if str(task.get("resource_group") or "") in resource_groups]
            related.extend(affected)
            facts.append({"kind": "resource_unavailable", "resource_groups": resource_groups, "dates": range_dates, "confidence": "suggested"})
        elif range_dates and direct_ids:
            patch["blocked_dates"] = {task_id: range_dates for task_id in direct_ids}
            facts.append({"kind": "blocked_dates", "task_ids": direct_ids, "dates": range_dates, "confidence": "suggested"})

    task_order = {_task_id(task): index for index, task in enumerate(tasks)}
    related = sorted(dict.fromkeys(item for item in related if item), key=lambda item: task_order.get(item, len(task_order)))
    ambiguous_facts = [fact for fact in facts if len(fact.get("task_ids") or []) > 1]
    if patch and not direct_ids and ambiguous_facts:
        return {"patch": {}, "related_task_ids": [], "facts": [],
                "classification_status": "NEEDS_INPUT",
                "missing_fields": ["후보 작업을 검토하고 기준 일정의 작업을 선택해 주세요."]}
    if patch:
        return {
            "patch": patch,
            "related_task_ids": related,
            "facts": facts,
            "classification_status": "PATCH_PROPOSED",
        }

    missing = []
    if dates or has_change_language:
        if not related:
            missing.append("기준 일정에서 영향받는 작업 ID를 지정해 주세요.")
        if not dates:
            target = ", ".join(related) if related else "해당 작업"
            missing.append(f"{target}의 변경된 시작일 또는 완료일을 날짜로 알려주세요.")
        if not any(word in lowered for word in ("도착", "납품", "출하", "제작", "설치", "시운전", "fat", "시험", "인력", "resource")):
            missing.append("변경이 제작·시험·운송·설치 중 어느 단계에 해당하는지 알려주세요.")
    return {
        "patch": {},
        "related_task_ids": related,
        "facts": facts,
        "classification_status": "NEEDS_INPUT" if missing and (dates or related) else "NEEDS_REVIEW",
        "missing_fields": missing,
    }


def normalize_event(value: dict[str, Any], project: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize input while keeping the interpretation visible for review."""
    event = dict(value)
    event.setdefault("mode", project.get("mode", "LIVE"))
    event.setdefault("data_origin", "SYNTHETIC" if event["mode"] == "REPLAY" else "USER")
    content = str(event.get("content") or event.get("message") or "")
    event["content"] = content
    event.setdefault("title", content[:90] or "변경 이벤트")
    event.setdefault("related_task_ids", [])
    event.setdefault("patch", {})
    event.setdefault("classification_status", "NEEDS_REVIEW")

    if event["patch"]:
        event["classification_status"] = event.get("classification_status") or "PATCH_PROVIDED"
        event.setdefault("review_status", "CONFIRMED")
        event.setdefault("extracted_facts", [])
        return event

    inferred = infer_event_patch(event, project, tasks)
    event["patch"] = inferred["patch"]
    event["related_task_ids"] = list(dict.fromkeys([*event["related_task_ids"], *inferred["related_task_ids"]]))
    event["extracted_facts"] = inferred["facts"]
    event["classification_status"] = inferred["classification_status"]
    if event.get("channel") == "supplier_message" and any(word in content for word in ("규정", "규제")) and "적용 여부" in content:
        event["verification_required"] = ["협력사가 언급한 규정의 실제 적용 여부 확인"]
    if inferred.get("missing_fields"):
        event["missing_fields"] = inferred["missing_fields"]
    event.setdefault("review_status", "PENDING")
    return event

"""Risk briefing when a baseline is registered, before any change signal arrives.

The calculator groups purchase items and tasks by cause (origin, customs, permits, outdoor work)
and ranks the groups by schedule float. An agent may then spend at most three checks deciding
which candidates to look at more closely: it can add a same-cause purchase item the rules missed
or drop a candidate the facts do not support. Floats, ranks and dates always come from the
calculator; the briefing never states delay days, only how exposed a task is.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from . import investigation as inv
from .risk_signals import CASE_QUERIES, search_cases
from .storage import digest

TOP_RISKS = 5
MIN_RISKS = 3
AGENT_CANDIDATES = 6
MAX_CHECKS = 3
NO_ATTRIBUTE = "속성 없음"
EU = {"Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia", "Finland", "France",
      "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands",
      "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden"}
COUNTRY_KO = {"China": "중국", "South Korea": "한국", "Germany": "독일", "Hungary": "헝가리", "Japan": "일본",
              "United States": "미국"}
CODE = {"China": "CN", "South Korea": "KR", "Germany": "DE", "Hungary": "HU", "Japan": "JP", "United States": "US"}
CAUSE_LABEL = {"import": "역외 원산지 품목 통관", "customs": "역외 원산지 설비 통관",
               "permit": "인허가", "outdoor": "야외 작업", "origin": "역외 협력사 작업"}
ATTRIBUTES = ("origin_country", "customs_required", "permit_required", "outdoor")

BRIEFING_PROMPT = (
    "You review a newly registered baseline schedule's risk candidates before any change signal arrives. "
    "Deterministic rules grouped purchase items and tasks by cause (origin outside the EU, customs, permits, outdoor "
    "work) and ranked the groups by schedule float; context.briefing_candidates lists them. Decide which candidates "
    "deserve a closer look and use at most three checks in total: find_procurement_items to find other purchase items "
    "with the same cause (same origin, supplier or customs need) that a group is missing, get_task_facts to see whether "
    "a task really carries an attribute, check_schedule_slack for a task's float. Exclude a candidate only when a tool "
    "result or the given facts show its cause does not apply, and say why. The workbook states only neutral facts "
    "(item names, origin, customs need, documents); it never states a risk. For each risk choose the L2 case query "
    "from context.case_queries that fits those facts best, and call search_risk_cases once to read the cases of the "
    "query that matters most. When the item facts together with a case you read point to a specific cause the "
    "workbook does not state (for example an export control on a material the items contain), name it in "
    "linked_cause with the ids of the cases you read; never name a cause without such a case. "
    "Every tool call must include reason: one Korean sentence saying why this check is needed. "
    "Never state delay days or invent items, tasks, dates or numbers; floats and the ranking come from the calculator. "
    'Return only JSON: {"summary": one Korean sentence, "status": "completed", "briefing": {"risks": [{"risk_key", '
    '"decision": "keep|exclude", "reason": Korean string, "added_item_ids": [], "case_query": id or "", '
    '"linked_cause": {"text": short Korean phrase, "case_ids": []} or null, '
    '"note": one Korean line for the project team}]}}. Never request commit or send actions.'
)


def _country(value: Any) -> str:
    return str(value or "").strip()


def _outside_eu(country: str) -> bool:
    return bool(country) and country not in EU


def _ko(country: str) -> str:
    return COUNTRY_KO.get(country, country)


def _requirement(item: dict[str, Any]) -> str:
    return str(item.get("permit_or_certification") or "").split(",")[0].strip()


def _shown(value: Any) -> Any:
    return NO_ATTRIBUTE if value is None or value == "" else value


def vulnerability(float_days: int | None) -> dict[str, Any]:
    """Exposure in plain language; no delay estimate.

    ``float_days`` is the buffer between a task slipping and the project finish
    slipping.  The UI calls it an 일정 완충 기간 instead of the scheduling term
    '여유' so that the signal is understandable without CPM vocabulary.
    """
    if float_days is None:
        return {"level": "unknown", "label": "일정 완충 기간 계산 불가"}
    if float_days == 0:
        return {"level": "critical", "label": "완충 기간 없음 · 지연 시 완료일 영향"}
    if float_days <= 14:
        return {"level": "high", "label": f"일정 완충 {float_days}일 · 영향 가능성 높음"}
    if float_days <= 60:
        return {"level": "medium", "label": f"일정 완충 {float_days}일 · 확인 필요"}
    if float_days >= inv.FLOAT_SEARCH_DAYS:
        return {"level": "low", "label": f"일정 완충 {inv.FLOAT_SEARCH_DAYS}일 이상 · 일정 내 흡수 가능"}
    return {"level": "low", "label": f"일정 완충 {float_days}일 · 일정 내 흡수 가능"}


_FLOAT_CACHE: dict[str, dict[str, int]] = {}


def task_floats(project: dict[str, Any], tasks: list[dict[str, Any]], task_ids: list[str]) -> dict[str, int]:
    """Float per task from the calculator; the same schedule is not searched twice in one process."""
    ids = sorted(set(task_ids))
    # Names and IDs do not change a schedule, so another project with the same workbook reuses the search.
    key = digest({"project": {name: value for name, value in project.items()
                              if name not in {"project_id", "name", "project_name", "description"}},
                  "tasks": tasks, "ids": ids})
    if key in _FLOAT_CACHE:
        return dict(_FLOAT_CACHE[key])
    floats: dict[str, int] = {}
    for start in range(0, len(ids), 10):
        for row in inv.schedule_slack(project, tasks, ids[start:start + 10])["tasks"]:
            if "float_calendar_days" in row:
                floats[row["task_id"]] = row["float_calendar_days"]
    if len(_FLOAT_CACHE) >= 32:
        _FLOAT_CACHE.pop(next(iter(_FLOAT_CACHE)))
    _FLOAT_CACHE[key] = dict(floats)
    return floats


def _groups(tasks: list[dict[str, Any]], procurement: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_tasks = {str(task["task_id"]): task for task in tasks if task.get("status") != "completed"}
    groups: list[dict[str, Any]] = []
    covered: set[str] = set()

    # Import items are grouped by the workbook's neutral facts only (origin outside the EU, customs needed).
    # A specific cause such as an export control is the agent's to name, from an L2 case it read.
    imports: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in procurement:
        origin = _country(item.get("origin_country"))
        if item.get("customs_required") and _outside_eu(origin) and str(item.get("needed_for_task_id")) in open_tasks:
            imports[origin].append(item)
    for origin, items in sorted(imports.items()):
        documents = any(_requirement(item) for item in items)
        ids = sorted({str(item["needed_for_task_id"]) for item in items})
        covered.update(ids)
        groups.append({"risk_key": f"import:{CODE.get(origin, origin)}", "cause": "import",
                       "attributes": ["원산지 역외", "통관 필요"] + (["허가·인증 서류"] if documents else []),
                       "title": f"{_ko(origin)}산 통관 품목", "item_ids": [item["item_id"] for item in items],
                       "task_ids": ids, "case_query": "logistics",
                       "basis": f"구매 목록: 원산지 {origin}, 통관 필요"
                                + (", 허가·인증 서류 있음" if documents else "")})

    def add(cause: str, key: str, title: str, ids: list[str], query: str, basis: str, attributes: list[str]) -> None:
        ids = sorted(set(ids) - covered) if cause in {"customs", "origin"} else sorted(set(ids))
        if not ids:
            return
        # Only items that carry the same attribute belong to the group (a truck for an outdoor task does not).
        carries = {"customs": lambda item: bool(item.get("customs_required")),
                   "permit": lambda item: bool(item.get("permit_or_certification"))}.get(cause, lambda item: False)
        items = [item["item_id"] for item in procurement if str(item.get("needed_for_task_id")) in ids and carries(item)]
        groups.append({"risk_key": key, "cause": cause, "title": title, "item_ids": items, "task_ids": ids,
                       "case_query": query, "basis": basis, "attributes": attributes})

    customs: dict[str, list[str]] = defaultdict(list)
    origin_work: dict[str, list[str]] = defaultdict(list)
    for task_id, task in open_tasks.items():
        origin = _country(task.get("origin_country"))
        if task.get("customs_required") and _outside_eu(origin):
            customs[origin].append(task_id)
        elif _outside_eu(origin):
            origin_work[origin].append(task_id)
    for origin, ids in sorted(customs.items()):
        add("customs", f"customs:{CODE.get(origin, origin)}", f"{_ko(origin)}산 설비 통관 작업", ids, "logistics",
            f"작업 속성: 원산지 {origin}, 통관 필요", ["원산지 역외", "통관 필요"])
    add("permit", "permit", "인허가가 필요한 작업",
        [task_id for task_id, task in open_tasks.items() if task.get("permit_required")], "permitting",
        "작업 속성: 인허가 필요", ["인허가 필요"])
    add("outdoor", "outdoor", "야외 작업(기상 영향)",
        [task_id for task_id, task in open_tasks.items() if task.get("outdoor")], "weather", "작업 속성: 야외 작업", ["야외 작업"])
    for origin, ids in sorted(origin_work.items()):
        add("origin", f"origin:{CODE.get(origin, origin)}", f"{_ko(origin)} 협력사 작업(역외 원산지)", ids, "trade",
            f"작업 속성: 원산지 {origin}", ["원산지 역외"])
    return groups


def _item_row(item: dict[str, Any], by_id: dict[str, dict[str, Any]], floats: dict[str, int]) -> dict[str, Any]:
    task_id = str(item.get("needed_for_task_id") or "")
    days = floats.get(task_id)
    return {"item_id": item["item_id"], "item_name": item.get("item_name"), "supplier_id": _shown(item.get("supplier_id")),
            "origin_country": _shown(item.get("origin_country")), "customs_required": _shown(item.get("customs_required")),
            "requirement": _shown(item.get("permit_or_certification")),
            "planned_arrival": _shown(item.get("planned_arrival")), "task_id": task_id,
            "task_name": (by_id.get(task_id) or {}).get("name"), "float_days": days,
            "vulnerability": vulnerability(days)}


def _task_row(task: dict[str, Any], floats: dict[str, int]) -> dict[str, Any]:
    days = floats.get(str(task["task_id"]))
    return {"task_id": task["task_id"], "name": task.get("name"), "supplier_id": _shown(task.get("supplier_id")),
            "baseline_start": str(task.get("baseline_start") or "")[:10],
            **{key: _shown(task.get(key)) for key in ATTRIBUTES}, "float_days": days, "vulnerability": vulnerability(days)}


def _action(cause: str, row: dict[str, Any], as_of: str) -> dict[str, Any]:
    """What to do and by which workbook date; nothing is estimated. Work already under way is checked now."""
    action = _action_at(cause, row)
    if action["by"] and as_of and str(action["by"]) <= as_of:
        action.update(by=None, basis="이미 시작된 작업 · 바로 확인")
    return action


def _action_at(cause: str, row: dict[str, Any]) -> dict[str, Any]:
    if row.get("item_id"):
        requirement = row["requirement"] if row["requirement"] != NO_ATTRIBUTE else "통관 서류"
        arrival = row["planned_arrival"]
        return {"target": row["item_id"],
                "what": f"{row['item_id']}의 {requirement} 준비 상황을 {row['supplier_id']}에 확인",
                "by": arrival if arrival != NO_ATTRIBUTE else row.get("needed_by"),
                "basis": "도착 예정일" if arrival != NO_ATTRIBUTE else "필요 작업 착수 예정일"}
    what = {"customs": "통관 서류 준비 상태 확인", "permit": "인허가 접수·처리 일정 확인",
            "outdoor": "작업 중단 기상 기준과 예보 확인", "origin": "협력사 일정·인력 투입 계획 확인"}.get(cause, "상태 확인")
    return {"target": row["task_id"], "what": f"{row['task_id']} {row.get('name') or ''} {what}".replace("  ", " "),
            "by": row["baseline_start"], "basis": "착수 예정일"}


def assemble(group: dict[str, Any], project: dict[str, Any], tasks: list[dict[str, Any]],
             procurement: list[dict[str, Any]], floats: dict[str, int], as_of: str,
             evidence_as_of: str = "") -> dict[str, Any]:
    by_id = {str(task["task_id"]): task for task in tasks}
    items = {str(item["item_id"]): item for item in procurement}
    item_rows = [_item_row(items[item_id], by_id, floats) for item_id in group["item_ids"] if item_id in items]
    for row in item_rows:
        row["needed_by"] = str((by_id.get(row["task_id"]) or {}).get("baseline_start") or "")[:10]
    item_tasks = {row["task_id"] for row in item_rows}
    # The agent reads every task of the group with its attributes; the screen lists an item's task once, under it.
    context_rows = [_task_row(by_id[task_id], floats) for task_id in group["task_ids"] if task_id in by_id]
    task_rows = [row for row in context_rows if not (group["cause"] == "import" and row["task_id"] in item_tasks)]
    rows = item_rows + task_rows
    days = [row["float_days"] for row in rows if row["float_days"] is not None]
    lowest = min(days) if days else None
    ordered = sorted(rows, key=lambda row: (row["float_days"] if row["float_days"] is not None else 10_000,
                                            str(row.get("item_id") or row.get("task_id"))))
    warnings = [f"{row.get('item_id') or row['task_id']}: 일정 완충 기간이 없습니다. "
                f"{row.get('task_id')} 착수가 늦어지면 프로젝트 완료일이 바로 밀립니다."
                for row in ordered if row["float_days"] == 0]
    cases = search_cases(group["case_query"], evidence_as_of or as_of, limit=3)
    risk = {**{key: group[key] for key in ("risk_key", "cause", "title", "basis", "case_query", "attributes")},
            "cause_label": CAUSE_LABEL.get(group["cause"], group["cause"]),
            "item_ids": [row["item_id"] for row in item_rows], "task_ids": sorted({row["task_id"] for row in rows}),
            "items": item_rows, "tasks": task_rows[:8], "context_tasks": context_rows[:8], "task_count": len(task_rows),
            "min_float_days": lowest, "vulnerability": vulnerability(lowest), "critical_warnings": warnings,
            "actions": [_action(group["cause"], row, as_of) for row in ordered[:3]],
            "cases": [{key: row.get(key) for key in ("risk_id", "title", "published_date", "source_url",
                                                     "temporal_status", "usage_note")} for row in cases["results"]],
            "case_label": cases.get("label")}
    return {**risk, "score": _score(risk)}


EXPOSURE = {"critical": 3, "high": 2, "medium": 1, "low": 0, "unknown": 0}


def _score(risk: dict[str, Any]) -> dict[str, Any]:
    """Stacked attributes x exposure: a gate with three attributes on the critical path ranks first."""
    exposure = EXPOSURE[risk["vulnerability"]["level"]]
    return {"attribute_count": len(risk["attributes"]), "exposure": exposure,
            "value": len(risk["attributes"]) * exposure,
            "label": f"속성 {len(risk['attributes'])}개({'·'.join(risk['attributes'])}) × {risk['vulnerability']['label']}"}


def _rank(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    critical = lambda row: sum(1 for item in row["items"] + row["tasks"] if item["float_days"] == 0)
    return sorted(risks, key=lambda row: (-row["score"]["value"],
                                          row["min_float_days"] if row["min_float_days"] is not None else 10_000,
                                          -critical(row), row["risk_key"]))


def coverage(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    open_tasks = [task for task in tasks if task.get("status") != "completed"]
    return {"open_task_count": len(open_tasks),
            "missing": {key: sum(task.get(key) is None for task in open_tasks) for key in ATTRIBUTES}}


def rule_briefing(project: dict[str, Any], tasks: list[dict[str, Any]], procurement: list[dict[str, Any]]) -> dict[str, Any]:
    as_of = str(project.get("status_as_of") or "")[:10]
    # Evidence is judged as of the briefing's own date; the schedule's status date stays as it is.
    evidence_as_of = str(project.get("evidence_as_of") or as_of)[:10]
    groups = _groups(tasks, procurement)
    floats = task_floats(project, tasks, [task_id for group in groups for task_id in group["task_ids"]])
    ranked = _rank([assemble(group, project, tasks, procurement, floats, as_of, evidence_as_of) for group in groups])
    return {"as_of": as_of, "evidence_as_of": evidence_as_of, "candidates": ranked, "coverage": coverage(tasks),
            "floats": floats}


def _model_candidate(risk: dict[str, Any]) -> dict[str, Any]:
    return {"risk_key": risk["risk_key"], "title": risk["title"], "cause": risk["cause"], "basis": risk["basis"],
            "min_float_days": risk["min_float_days"], "default_case_query": risk["case_query"],
            "items": [{key: row.get(key) for key in ("item_id", "item_name", "supplier_id", "origin_country",
                                                      "customs_required", "requirement", "planned_arrival", "task_id",
                                                      "float_days")} for row in risk["items"]],
            "tasks": [{key: row.get(key) for key in ("task_id", "name", "supplier_id", *ATTRIBUTES, "baseline_start",
                                                      "float_days")} for row in risk["context_tasks"]]}


def agent_review(project: dict[str, Any], tasks: list[dict[str, Any]], procurement: list[dict[str, Any]],
                 rules: dict[str, Any], run_agent: Callable[..., dict[str, Any]], gateway: Any) -> dict[str, Any]:
    """At most three checks on the rule candidates; returns the agent output with its tool log."""
    state = {"checks": 0, "searches": 0}

    def counted(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        state["checks"] += 1
        if state["checks"] > MAX_CHECKS:
            return {"status": "limit_reached", "reason": f"확인은 최대 {MAX_CHECKS}회입니다. 지금까지의 결과로 답하세요."}
        return call()

    def find_procurement_items(reason: str, supplier_id: str = "", origin_country: str = "",
                               customs_required: bool = True, arriving_after: str = "") -> dict[str, Any]:
        """Purchase-list items by supplier, origin, customs need and arrival after a date."""
        return counted(lambda: inv.procurement_items(tasks, procurement, supplier_id, origin_country,
                                                     customs_required, arriving_after))

    def get_task_facts(reason: str, task_ids: list[str]) -> dict[str, Any]:
        """Task origin, customs and permit attributes and the purchase items each task needs."""
        return counted(lambda: inv.task_facts(tasks, procurement, task_ids))

    def check_schedule_slack(reason: str, task_ids: list[str]) -> dict[str, Any]:
        """Calendar days each task's start can slip before the project finish moves."""
        return counted(lambda: inv.schedule_slack(project, tasks, task_ids))

    def search_risk_cases(reason: str, query: str) -> dict[str, Any]:
        """Stored real-world L2 cases for one query id from context.case_queries; never a delay estimate."""
        state["searches"] += 1
        if state["searches"] > 1:
            return {"status": "limit_reached"}
        return search_cases(query, rules["evidence_as_of"])

    search_risk_cases.parameters_schema = {  # type: ignore[attr-defined]
        "type": "object", "required": ["reason", "query"], "additionalProperties": False,
        "properties": {"reason": {"type": "string"}, "query": {"type": "string", "enum": sorted(CASE_QUERIES)}}}
    tools = {"find_procurement_items": find_procurement_items, "get_task_facts": get_task_facts,
             "check_schedule_slack": check_schedule_slack, "search_risk_cases": search_risk_cases}
    context = {
        "_llm_gateway": gateway,
        "project": {key: project.get(key) for key in ("country", "target_finish", "status_as_of", "evidence_as_of")
                    if project.get(key)},
        "briefing_candidates": [_model_candidate(risk) for risk in rules["candidates"][:AGENT_CANDIDATES]],
        "attribute_coverage": rules["coverage"],
        "case_queries": {key: value["label"] for key, value in CASE_QUERIES.items()},
    }
    event = {"event_id": "baseline-briefing", "channel": "baseline_registration"}
    return run_agent(context, event, tools, max_steps=MAX_CHECKS + 2, system_prompt=BRIEFING_PROMPT)


def finalize(project: dict[str, Any], tasks: list[dict[str, Any]], procurement: list[dict[str, Any]],
             rules: dict[str, Any], agent: dict[str, Any] | None) -> dict[str, Any]:
    """Apply only what the tools support; the calculator re-ranks. Returns the risks to show and register."""
    candidates = [dict(row) for row in rules["candidates"]]
    reviewed = {row["risk_key"] for row in candidates[:AGENT_CANDIDATES]}
    decisions: dict[str, dict[str, Any]] = {}
    found: set[str] = set()
    read_cases: dict[str, dict[str, Any]] = {}
    if agent:
        for entry in agent.get("tool_log") or []:
            if entry.get("tool") == "find_procurement_items" and entry.get("status") == "ok":
                found.update(str(row.get("item_id")) for row in (entry.get("result") or {}).get("items") or [])
            if entry.get("tool") == "search_risk_cases" and entry.get("status") == "ok":
                read_cases.update({str(row.get("risk_id")): row for row in (entry.get("result") or {}).get("results") or []})
        for row in ((agent.get("briefing") or {}).get("risks") or []):
            if isinstance(row, dict) and row.get("risk_key") in reviewed:
                decisions[str(row["risk_key"])] = row
    assigned = {item_id for row in candidates for item_id in row["item_ids"]}
    kept, excluded = [], []
    floats = dict(rules["floats"])
    for risk in candidates:
        decision = decisions.get(risk["risk_key"]) or {}
        reason = str(decision.get("reason") or "").strip()
        if decision.get("decision") == "exclude" and reason:
            excluded.append({"risk_key": risk["risk_key"], "title": risk["title"], "reason": reason[:300]})
            continue
        added = [str(item_id) for item_id in decision.get("added_item_ids") or []
                 if str(item_id) in found and str(item_id) not in assigned]
        if added or decision.get("case_query") in CASE_QUERIES:
            group = {**{key: risk[key] for key in ("risk_key", "cause", "title", "basis", "case_query", "attributes")},
                     "item_ids": risk["item_ids"] + added, "task_ids": risk["task_ids"]}
            if decision.get("case_query") in CASE_QUERIES:
                group["case_query"] = decision["case_query"]
            new_tasks = [str(item["needed_for_task_id"]) for item in procurement if str(item.get("item_id")) in added]
            missing = [task_id for task_id in new_tasks if task_id not in floats]
            if missing:
                floats.update(task_floats(project, tasks, missing))
            group["task_ids"] = sorted(set(group["task_ids"]) | set(new_tasks))
            risk = {**assemble(group, project, tasks, procurement, floats, rules["as_of"], rules["evidence_as_of"]),
                    "added_item_ids": added}
            assigned.update(added)
        linked = decision.get("linked_cause") if isinstance(decision.get("linked_cause"), dict) else {}
        case_ids = [str(case_id) for case_id in linked.get("case_ids") or [] if str(case_id) in read_cases]
        if str(linked.get("text") or "").strip() and case_ids:
            risk = {**risk, "linked_cause": {
                "text": str(linked["text"]).strip()[:120], "case_ids": case_ids,
                "cases": [{key: read_cases[case_id].get(key) for key in ("risk_id", "title", "published_date", "source_url",
                                                                          "temporal_status")} for case_id in case_ids]}}
        note = str(decision.get("note") or "").strip()
        if note:
            risk["agent_note"] = note[:200]
        if decision:
            risk["agent_decision"] = "keep"
        kept.append(risk)
    ranked = _rank(kept)
    top = ranked[:TOP_RISKS] if len(ranked) >= MIN_RISKS else ranked
    for rank, risk in enumerate(top, start=1):
        risk["rank"] = rank
        risk["risk_id"] = "R-" + risk["risk_key"].replace(":", "-").replace("_", "-")
    return {"as_of": rules["as_of"], "evidence_as_of": rules["evidence_as_of"], "risks": top, "excluded": excluded, "coverage": rules["coverage"],
            "more_count": max(0, len(ranked) - len(top))}

"""Single worker for scans and analyses.

Run with: PYTHONPATH=services/api python -m app.worker
"""

from __future__ import annotations

import os
import time
import json
from datetime import date, datetime, timezone
from itertools import combinations
from typing import Any
from urllib.parse import urlparse

from .events import normalize_event
from .storage import Store, digest, identifier, utcnow


def _scenario_record(run: dict[str, Any], event: dict[str, Any], version: dict[str, Any], label: str, option_ids: list[str], result: dict[str, Any]) -> dict[str, Any]:
    required = []
    if option_ids:
        options_by_id = {item.get("option_id"): item for item in version["data"].get("options", [])}
        for option_id in option_ids:
            option = options_by_id.get(option_id, {})
            state = str(option.get("approval_state") or "")
            if any(word in state for word in ("미확보", "미예약", "승인", "협의", "조건부")):
                required.append(f"{option_id}: {state}")
    return {
        **result,
        "project_id": run["project_id"],
        "event_id": event["id"],
        "event_patch_hash": digest(event.get("patch", {})),
        "evidence": event.get("evidence"),
        "included_events": event.get("included_events", []),
        "applied_patch": event.get("patch", {}),
        "provisional": bool(event.get("evidence") and event.get("review_status") != "CONFIRMED"),
        "version_id": version["id"],
        "input_version_hash": version["content_hash"],
        "label": label,
        "option_ids": option_ids,
        "required_confirmations": required,
        "mode": event.get("mode"),
        "data_origin": event.get("data_origin"),
        "simulation_as_of": event.get("simulation_as_of"),
        "project_context_hash": (run.get("data") or {}).get("project_context_snapshot", {}).get("content_hash"),
    }


def _record_usage(db: Store, run_id: str, output: dict[str, Any]) -> None:
    usage = output.get("usage") or {}
    with db.transaction() as conn:
        conn.execute(
            "UPDATE usage_ledger SET model=?, input_tokens=?, output_tokens=? WHERE run_id=?",
            (
                usage.get("model") or os.environ.get("LLM_MODEL"), usage.get("prompt_tokens"),
                usage.get("completion_tokens"), run_id,
            ),
        )


def _can_combine(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Respect explicit option compatibility metadata; default to combinable."""
    left_id = str(left.get("option_id") or "")
    right_id = str(right.get("option_id") or "")
    left_exclusive = {str(item) for item in left.get("mutually_exclusive_with") or []}
    right_exclusive = {str(item) for item in right.get("mutually_exclusive_with") or []}
    if right_id in left_exclusive or left_id in right_exclusive:
        return False
    left_group = left.get("exclusive_group")
    right_group = right.get("exclusive_group")
    return not left_group or not right_group or left_group != right_group


def _candidate_options(options: list[dict[str, Any]], unavailable: set[str]) -> list[tuple[str, list[str]]]:
    """Build response combinations from declared compatibility, not demo IDs."""
    usable = [
        option for option in options
        if option.get("option_id")
        and str(option.get("option_id")) not in unavailable
        and str(option.get("operation") or "").upper() != "REQUEST_TARGET_CHANGE"
    ]
    candidates: list[tuple[str, list[str]]] = [("무대응", [])]
    for option in usable:
        option_id = str(option["option_id"])
        candidates.append((str(option.get("name") or option_id), [option_id]))
    for size in range(2, min(3, len(usable)) + 1):
        for group in combinations(usable, size):
            if all(_can_combine(left, right) for left, right in combinations(group, 2)):
                option_ids = [str(option["option_id"]) for option in group]
                candidates.append((" + ".join(str(option.get("name") or option["option_id"]) for option in group), option_ids))
    return candidates[:8]


def _reserve_paid_attempt(db: Store, run_id: str) -> str:
    """Reserve before network I/O so crash recovery cannot silently double-charge."""
    limit = max(0, int(os.environ.get("REPLAN_MAX_PAID_RUNS_PER_DAY", "20")))
    today = utcnow()[:10]
    with db.transaction() as conn:
        existing = conn.execute("SELECT id FROM usage_ledger WHERE run_id=?", (run_id,)).fetchone()
        if existing:
            return "already_attempted"
        count = conn.execute("SELECT COUNT(*) FROM usage_ledger WHERE created_at LIKE ?", (today + "%",)).fetchone()[0]
        if count >= limit:
            return "budget_stopped"
        conn.execute(
            "INSERT INTO usage_ledger VALUES (?,?,?,?,?,?,?,?)",
            (identifier(), run_id, os.environ.get("LLM_MODEL"), None, None, None, "UNKNOWN", utcnow()),
        )
    return "reserved"


def _run_analysis(db: Store, run: dict[str, Any]) -> dict[str, Any]:
    from .scheduling import simulate

    event_row = db.get_json("events", run["event_id"], run["project_id"])
    version = db.get_json("versions", run["version_id"], run["project_id"])
    if not event_row or not version:
        raise ValueError("event or version was removed")
    event = event_row["data"]
    snapshot = version["data"]
    context_snapshot = (run.get("data") or {}).get("project_context_snapshot") or {}
    if context_snapshot.get("project") is not None:
        project = dict(context_snapshot["project"])
    else:
        # Compatibility for runs created before context snapshots were introduced.
        current_profile = db.get_json("projects", run["project_id"])
        project = dict(current_profile["data"] if current_profile else snapshot["project"])
    tasks = snapshot["tasks"]
    options = snapshot.get("options", [])
    if event.get("review_status") in {"REJECTED", "SUPERSEDED"}:
        return {"status": "SUPERSEDED", "summary": "보류되었거나 최신 근거로 대체된 변경입니다.", "scenario_ids": []}
    if event.get("version_id") and event["version_id"] != version["id"]:
        return {"status": "STALE", "summary": "기준 일정이 바뀌었습니다. 외부 소스를 다시 확인하세요.", "scenario_ids": []}
    if not event.get("patch") and event.get("channel") == "registered_public_source":
        # Interpret source evidence before the early NEEDS_INPUT return.
        if all(os.environ.get(key) for key in ("API_KEY", "LLM_MODEL", "LLM_BASE_URL")) and os.environ.get("REPLAN_PAID_CALLS_ENABLED", "false").lower() == "true":
            paid_state = _reserve_paid_attempt(db, run["id"])
            if paid_state == "reserved":
                from .external_risks import interpret_notice
                from .adapters.llm import OpenAICompatibleLLM
                result = interpret_notice(event, tasks, OpenAICompatibleLLM())
                _record_usage(db, run["id"], result)
                event["interpretation"] = result
                if result.get("candidates"):
                    event["candidates"] = result["candidates"]
                    event["related_task_ids"] = [item["task_id"] for item in result["candidates"]]
                db.put_json("events", event_row["id"], event, project_id=run["project_id"],
                            fingerprint=event_row["fingerprint"], created_at=event_row["created_at"])

    budget = run["data"].get("budget_krw")
    if event.get("classification_status") == "NO_SCHEDULE_IMPACT":
        return {"status": "NO_IMPACT", "summary": "일정에 영향을 주는 변경이 아닙니다.", "scenario_ids": [], "agent_status": "not_needed"}
    if not event.get("patch"):
        action_id = digest({"run_id": run["id"], "kind": "needs_input"})[:32]
        missing = event.get("missing_fields") or ["적용 대상", "일정 영향"]
        action = {
            "owner": "프로젝트 운영팀", "state": "OPEN", "request": ", ".join(missing) + " 확인",
            "due_at": None, "event_id": event["id"], "mode": event.get("mode"),
        }
        db.put_json("actions", action_id, action, project_id=run["project_id"], event_id=event["id"], scenario_id=None)
        return {"status": "NEEDS_INPUT", "summary": "근거를 확인했습니다. 영향 작업과 효력일·중단 기간을 확인하면 계산할 수 있습니다.",
                "action_ids": [action_id], "scenario_ids": [], "candidates": event.get("candidates", []),
                "evidence": event.get("evidence"), "missing_fields": missing,
                "agent_status": event.get("interpretation", {}).get("status", "rules_only")}

    if event.get("evidence"):
        from .external_risks import combine_patches
        inputs = [row for row in db.list_json("events", run["project_id"], 1000)
                  if row["data"].get("evidence") and row["data"].get("patch")
                  and row["data"].get("version_id") == version["id"]
                  and row["data"].get("review_status") not in {"SUPERSEDED", "REJECTED"}]
        try:
            patch = combine_patches([row["data"]["patch"] for row in inputs])
        except ValueError:
            return {"status": "NEEDS_INPUT", "summary": "같은 작업의 완료일 근거가 서로 다릅니다. 변경을 확인하거나 보류한 뒤 다시 분석하세요.", "scenario_ids": []}
        event = {**event, "patch": patch,
                 "related_task_ids": sorted({task_id for row in inputs for task_id in row["data"].get("related_task_ids", [])}),
                 "review_status": "CONFIRMED" if all(row["data"].get("review_status") == "CONFIRMED" for row in inputs) else "PENDING",
                 "included_events": [{"event_id": row["id"], "patch_hash": digest(row["data"]["patch"]),
                                      "title": row["data"].get("title"), "evidence": row["data"].get("evidence")}
                                     for row in inputs]}

    unavailable = set(run["data"].get("unavailable_option_ids") or [])
    candidates = _candidate_options(options, unavailable)

    scenario_ids = []
    scenario_results = []
    for label, selected in candidates:
        chosen = [item for item in options if item.get("option_id") in selected]
        result = simulate(project, tasks, event=event, options=chosen, budget_krw=budget)
        baseline_finish = max(str(task["baseline_finish"])[:10] for task in tasks)
        by_id = {str(task["task_id"]): task for task in tasks}
        changed_tasks = [
            {"task_id": item["task_id"], "name": by_id[str(item["task_id"])].get("name"),
             "before_start": by_id[str(item["task_id"])]["baseline_start"],
             "before_finish": by_id[str(item["task_id"])]["baseline_finish"],
             "after_start": item["planned_start"], "after_finish": item["planned_finish"],
             "direct": str(item["task_id"]) in event.get("related_task_ids", [])}
            for item in result.get("schedule", [])
            if (str(item["planned_start"]), str(item["planned_finish"])) !=
               (str(by_id[str(item["task_id"])]["baseline_start"]), str(by_id[str(item["task_id"])]["baseline_finish"]))
        ]
        result.update({"baseline_finish": baseline_finish, "changed_tasks": changed_tasks,
                       "finish_shift_days": (date.fromisoformat(result["finish_date"]) - date.fromisoformat(baseline_finish)).days if result.get("finish_date") else None})
        record = _scenario_record(run, event, version, label, selected, result)
        scenario_id = digest({"run_id": run["id"], "option_ids": selected})[:32]
        db.put_json("scenarios", scenario_id, record, project_id=run["project_id"], run_id=run["id"], version_id=version["id"])
        scenario_ids.append(scenario_id)
        scenario_results.append({"id": scenario_id, **record})

    agent_output: dict[str, Any] = {"status": "llm_unavailable", "summary": "LLM 설정이 없어 계산 결과만 제공합니다."}
    if os.environ.get("API_KEY") and os.environ.get("LLM_MODEL") and os.environ.get("LLM_BASE_URL") and os.environ.get("REPLAN_PAID_CALLS_ENABLED", "false").lower() == "true":
        paid_state = _reserve_paid_attempt(db, run["id"])
        if paid_state != "reserved":
            agent_output = {"status": paid_state, "summary": "유료 호출 한도 또는 중복 실행 방지로 계산 결과만 제공합니다."}
        else:
          try:
            from .agent import run_agent

            def get_project_context() -> dict[str, Any]:
                return {"project": project, "related_tasks": [task for task in tasks if task.get("task_id") in event.get("related_task_ids", [])], "version_id": version["id"]}

            def list_response_options() -> dict[str, Any]:
                return {"options": options, "budget_krw": budget}

            def simulate_schedule(option_ids: list[str]) -> dict[str, Any]:
                selected = set(option_ids)
                if not selected.issubset({item.get("option_id") for item in options}):
                    return {"status": "invalid_input", "error": "unknown option"}
                chosen = [item for item in options if item.get("option_id") in selected]
                return simulate(project, tasks, event=event, options=chosen, budget_krw=budget)

            source_calls = {"search": 0, "fetch": 0, "weather": 0}
            snapshots = db.list_json("source_snapshots", run["project_id"], limit=30)

            def search_public_sources(query: str) -> dict[str, Any]:
                source_calls["search"] += 1
                if source_calls["search"] > 2:
                    return {"status": "limit_reached", "results": []}
                terms = [part.lower() for part in query.split() if part]
                matches = [item for item in snapshots if item["status"] == "ok" and all(
                    term in str(item["data"].get("title", "") + " " + item["data"].get("summary", "")).lower() for term in terms
                )]
                return {"status": "ok", "results": [{"snapshot_id": item["id"], "source_id": item["source_id"], "title": item["data"].get("title"), "summary": item["data"].get("summary")} for item in matches[:3]]}

            def fetch_allowed_source(snapshot_id: str) -> dict[str, Any]:
                source_calls["fetch"] += 1
                if source_calls["fetch"] > 3:
                    return {"status": "limit_reached"}
                match = next((item for item in snapshots if item["id"] == snapshot_id and item["status"] == "ok"), None)
                return {"status": "ok", "snapshot": match["data"]} if match else {"status": "not_found"}

            def fetch_weather() -> dict[str, Any]:
                source_calls["weather"] += 1
                if source_calls["weather"] > 1:
                    return {"status": "limit_reached"}
                match = next((item for item in snapshots if item["data"].get("provider") == "open_meteo" and item["status"] == "ok"), None)
                return {"status": "ok", "snapshot": match["data"]} if match else {"status": "not_found"}

            def prepare_change_package(scenario_id: str) -> dict[str, Any]:
                match = next((item for item in scenario_results if item["id"] == scenario_id), None)
                if not match:
                    return {"status": "invalid_scenario"}
                return {"status": "draft_only", "scenario_id": scenario_id, "required_confirmations": match.get("required_confirmations", []), "budget_met": match.get("budget_met"), "target_met": match.get("target_met")}

            agent_output = run_agent(
                {"project": project, "version_id": version["id"], "scenario_results": [{k: v for k, v in item.items() if k != "schedule"} for item in scenario_results]},
                event,
                {
                    "get_project_context": get_project_context,
                    "list_response_options": list_response_options,
                    "simulate_schedule": simulate_schedule,
                    "search_public_sources": search_public_sources,
                    "fetch_allowed_source": fetch_allowed_source,
                    "fetch_weather": fetch_weather,
                    "prepare_change_package": prepare_change_package,
                },
            )
            _record_usage(db, run["id"], agent_output)
          except Exception as exc:
            agent_output = {"status": "error", "error": type(exc).__name__, "summary": "LLM 분석에 실패해 계산 결과만 제공합니다."}

    meeting = [item for item in scenario_results if item.get("target_met") and item.get("budget_met") and not item.get("violations")]
    if not meeting:
        summary = "현재 등록된 선택지 범위에서는 목표일을 만족하는 안을 찾지 못했습니다."
    elif budget is None:
        summary = "목표일을 만족하는 계산안을 찾았습니다. 비용은 아직 미정이므로 대응안 비교에서 확인하세요."
    else:
        summary = "등록된 선택지에서 목표일과 비용 한도를 만족하는 계산안을 찾았습니다. 실행 조건을 확인하세요."
    return {"status": "succeeded", "summary": summary, "scenario_ids": scenario_ids, "agent_status": agent_output.get("status"), "agent": agent_output, "budget_krw": budget}


def _run_document_ingest(db: Store, run: dict[str, Any]) -> dict[str, Any]:
    """Parse an uploaded document off the request path and create its review event."""
    from .importers import extract_document
    from .main import notify_project

    document_id = str((run.get("data") or {}).get("document_id") or "")
    document = db.get_json("documents", document_id, run["project_id"])
    if not document:
        raise ValueError("document was removed")
    record = dict(document["data"])
    record["status"] = "PROCESSING"
    db.put_json("documents", document_id, record, project_id=run["project_id"], created_at=document["created_at"])
    try:
        content = db.upload_path(document_id).read_bytes()
        parsed = extract_document(record.get("filename") or "input.txt", content)
        record.update(parsed)
        record["status"] = "SUCCEEDED"
        record["processed_at"] = utcnow()

        event_id: str | None = None
        version = db.current_version(run["project_id"])
        if version and parsed.get("text", "").strip():
            project = db.get_json("projects", run["project_id"])
            raw = {
                "channel": "email" if parsed["input_type"] == "email" else "document",
                "source_label": record.get("filename") or "문서 입력",
                "content": parsed["text"],
                "mode": project["data"].get("mode", "LIVE") if project else "LIVE",
                "data_origin": "USER",
            }
            event = normalize_event(raw, project["data"] if project else {}, version["data"]["tasks"])
            fingerprint = digest({"document_id": document_id, "text": parsed["text"]})
            existing_event = db.find_event_by_fingerprint(run["project_id"], fingerprint)
            if existing_event:
                event_id = existing_event["id"]
            else:
                event_id = identifier()
                event["id"] = event_id
                db.put_json("events", event_id, event, project_id=run["project_id"], fingerprint=fingerprint)
                notify_project(
                    db,
                    run["project_id"],
                    "document_received",
                    "새 문서 입력",
                    f"{record.get('filename') or '문서'}에서 이벤트를 추출했습니다.",
                    data={"event_id": event_id, "document_id": document_id},
                )
            record["event_id"] = event_id
        db.put_json("documents", document_id, record, project_id=run["project_id"], created_at=document["created_at"])
        return {"status": "succeeded", "document_id": document_id, "event_id": event_id}
    except Exception as exc:
        record["status"] = "FAILED"
        record["error"] = str(exc)[:500]
        record["failed_at"] = utcnow()
        db.put_json("documents", document_id, record, project_id=run["project_id"], created_at=document["created_at"])
        raise


def _store_source_snapshot(db: Store, project_id: str, result: dict[str, Any]) -> tuple[str, bool]:
    source_id = str(result.get("source_id") or "unknown")
    with db.connection() as conn:
        prior = conn.execute(
            "SELECT body_hash FROM source_snapshots WHERE project_id=? AND source_id=? AND status='ok' ORDER BY fetched_at DESC LIMIT 1",
            (project_id, source_id),
        ).fetchone()
    changed = result.get("status") == "ok" and (prior is None or prior["body_hash"] != result.get("body_hash"))
    snapshot_id = identifier()
    db.put_json(
        "source_snapshots", snapshot_id, result,
        project_id=project_id, source_id=source_id,
        body_hash=result.get("body_hash"), status=result.get("status", "failed"),
        fetched_at=result.get("fetched_at") or utcnow(),
    )
    return snapshot_id, changed


def _save_external_event(db: Store, project_id: str, version: dict, identity: str, event: dict | None) -> list[str]:
    """Deduplicate by content and replace obsolete proposals, never mutate a baseline."""
    source_key = digest({"identity": identity, "version": version["id"]})
    observation_hash = digest({key: value for key, value in (event or {}).items()
                               if key not in {"evidence", "fetched_at"}})
    history = [row for row in db.list_json("events", project_id, 1000) if row["data"].get("source_key") == source_key]
    for old in history:
        if old["data"].get("review_status") != "SUPERSEDED" and old["data"].get("observation_hash") == observation_hash:
            return []
    fingerprint = digest({"source_key": source_key, "observation_hash": observation_hash,
                          "previous": sorted(row["id"] for row in history)})
    for old in history:
        data = old["data"]
        if data.get("review_status") != "SUPERSEDED":
            data = {**data, "review_status": "SUPERSEDED", "superseded_at": utcnow()}
            db.put_json("events", old["id"], data, project_id=project_id,
                        fingerprint=old["fingerprint"], created_at=old["created_at"])
    if event is None:
        return []
    event_id = identifier()
    event = {**event, "id": event_id, "event_id": event_id, "source_key": source_key,
             "version_id": version["id"], "observation_hash": observation_hash, "mode": "LIVE", "data_origin": "PUBLIC",
             "review_status": "PENDING"}
    db.put_json("events", event_id, event, project_id=project_id, fingerprint=fingerprint)
    db.create_run(project_id, "analysis", event_id, version["id"], f"external:{event_id}",
                  {"project_context_snapshot": db.project_context_snapshot(project_id)})
    from .main import notify_project
    notify_project(db, project_id, "external_change", "외부 변화 검토", event["title"],
                   data={"event_id": event_id})
    return [event_id]


def _record_weather_risks(db: Store, project_id: str, plan: dict[str, Any], forecast: dict[str, Any], snapshot_id: str) -> list[str]:
    from .external_risks import evidence, weather_patch
    if forecast.get("status") != "ok" or not plan.get("weather_limits"):
        return []
    version = db.current_version(project_id)
    if not version:
        return []
    patch, facts = weather_patch(version["data"]["tasks"], plan, forecast)
    event = None
    if patch:
        event = {
            "title": "현장 기상 예보: 작업 중단 기준 초과",
            "content": "등록한 야외 작업·기간·기상 기준에 해당하는 날짜를 조건부로 제외해 계산합니다. 예보는 실제 중단 확정이 아닙니다.",
            "source_label": "Open-Meteo 기상 예보", "channel": "weather_forecast",
            "patch": patch, "classification_status": "PATCH_PROPOSED",
            "related_task_ids": list(patch["blocked_dates"]), "extracted_facts": facts,
            "evidence": {**evidence(forecast, snapshot_id, "FORECAST"),
                         "validity": forecast.get("forecast", {}).get("validity"), "limits": plan["weather_limits"]},
        }
    return _save_external_event(db, project_id, version, f"weather:{forecast.get('source_id')}", event)


def _record_holiday_risks(db: Store, project_id: str, config: dict, source: dict, snapshot_id: str) -> list[str]:
    from .external_risks import evidence, holiday_patch
    if source.get("status") != "ok":
        return []
    version = db.current_version(project_id)
    if not version:
        return []
    patch, facts = holiday_patch(version["data"]["tasks"], config, source)
    event = None
    if patch:
        event = {
            "title": f"{config['country_code']} 공휴일과 작업 일정 중첩",
            "content": "공개 휴일 달력과 지정한 작업의 날짜가 겹칩니다. 해당 현장·공급사가 실제로 쉬는 날인지 확인하세요.",
            "source_label": "Nager.Date 공개 휴일 달력", "channel": "public_holiday",
            "patch": patch, "classification_status": "PATCH_PROPOSED",
            "related_task_ids": list(patch["blocked_dates"]), "extracted_facts": facts,
            "evidence": {**evidence(source, snapshot_id, "CALENDAR"), "scope": config},
        }
    identity = f"holiday:{config['country_code']}:{config['year']}:{config.get('subdivision')}"
    return _save_external_event(db, project_id, version, identity, event)


def _record_public_risks(db: Store, project_id: str, plan: dict, result: dict, snapshot_id: str, url: str) -> list[str]:
    from .external_risks import evidence, match_notice
    version = db.current_version(project_id)
    if not version:
        return []
    rows = result.get("feed_items") or [result]
    created = []
    for row in rows:
        source = {**row, "content": row.get("content") or row.get("summary") or row.get("title", ""),
                  "url": row.get("url") or url, "feed_url": url,
                  "source_id": result.get("source_id"), "fetched_at": result.get("fetched_at")}
        source["body_hash"] = digest({"title": source.get("title"), "content": source["content"]})
        identity = row.get("id") or row.get("url") or row.get("title") or url
        event = {
            "title": source.get("title") or "등록 출처의 새 공지",
            "content": source["content"], "source_label": source["url"],
            "channel": "registered_public_source",
            "evidence": evidence(source, snapshot_id, "PUBLIC_NOTICE"),
            **match_notice(source, version["data"]["tasks"], plan.get("source_rules", [])),
        }
        created.extend(_save_external_event(db, project_id, version, f"notice:{url}:{identity}", event))
    return created


def _run_scan(db: Store, run: dict[str, Any]) -> dict[str, Any]:
    from .adapters.sources import fetch_registered_source, fetch_weather, fetch_holidays
    watch = db.get_json("watch_plans", run["project_id"])
    if not watch or not watch["data"].get("enabled"):
        return {"status": "disabled", "sources": []}
    plan = watch["data"]
    scope = run["data"].get("scope", "all")
    source_results = []
    new_event_ids = []

    def record(result: dict) -> str:
        snapshot_id, changed = _store_source_snapshot(db, run["project_id"], result)
        source_results.append({"snapshot_id": snapshot_id, "source_id": result.get("source_id"),
                               "status": result.get("status"), "error": result.get("error"), "changed": changed})
        return snapshot_id

    site = plan.get("weather_site")
    if site and scope in {"all", "weather"}:
        try:
            result = fetch_weather(site)
        except Exception as exc:
            result = {"status": "failed", "source_id": "open-meteo", "fetched_at": utcnow(), "error": type(exc).__name__}
        snapshot_id = record(result)
        new_event_ids.extend(_record_weather_risks(db, run["project_id"], plan, result, snapshot_id))
    if scope in {"all", "holidays"}:
        for config in plan.get("holiday_calendars", []):
            result = fetch_holidays(config["country_code"], config["year"])
            snapshot_id = record(result)
            new_event_ids.extend(_record_holiday_risks(db, run["project_id"], config, result, snapshot_id))
    allowed_urls = plan.get("source_allowlist") or []
    allowed_hosts = [urlparse(url).hostname for url in allowed_urls]
    for url in allowed_urls if scope in {"all", "notices"} else []:
        try:
            result = fetch_registered_source(url, [host for host in allowed_hosts if host])
        except Exception as exc:
            result = {"status": "failed", "source_id": url, "fetched_at": utcnow(), "error": type(exc).__name__}
        snapshot_id = record(result)
        if result.get("status") == "ok":
            new_event_ids.extend(_record_public_risks(db, run["project_id"], plan, result, snapshot_id, url))
    failures = sum(item["status"] != "ok" for item in source_results)
    return {"status": "partial_failure" if failures else "succeeded", "scope": scope,
            "sources": source_results, "failed_source_count": failures,
            "new_or_changed_count": sum(bool(item["changed"]) for item in source_results),
            "new_event_ids": new_event_ids,
            "summary": "수집 실패 출처가 있습니다. 위험 없음으로 판단하지 않습니다." if failures else "등록 출처 확인 완료"}

def enqueue_due_scans(db: Store, now: datetime | None = None) -> int:
    """Queue each enabled source only when its own approved polling interval is due."""
    now = now or datetime.now(timezone.utc)
    queued = 0
    with db.connection() as conn:
        plans = conn.execute("SELECT project_id, data FROM watch_plans").fetchall()
    for row in plans:
        plan = json.loads(row["data"])
        if not plan.get("enabled"):
            continue
        for scope, configured, field in (
            ("weather", bool(plan.get("weather_site")), "weather_poll_hours"),
            ("notices", bool(plan.get("source_allowlist")), "notice_poll_hours"),
            ("holidays", bool(plan.get("holiday_calendars")), "holiday_poll_hours"),
        ):
            if not configured:
                continue
            hours = max(1, min(int(plan.get(field, 6)), 168))
            with db.connection() as conn:
                recent = conn.execute(
                    "SELECT status, data, created_at FROM runs WHERE project_id=? AND kind='scan' ORDER BY created_at DESC LIMIT 100",
                    (row["project_id"],),
                ).fetchall()
            relevant = [item for item in recent if json.loads(item["data"]).get("scope", "all") in {"all", scope}]
            if relevant:
                latest = relevant[0]
                if latest["status"] in {"queued", "running"}:
                    continue
                last_at = datetime.fromisoformat(latest["created_at"])
                if (now - last_at).total_seconds() < hours * 3600:
                    continue
            key = f"scheduled:{scope}:{int(now.timestamp()) // (hours * 3600)}"
            created = db.create_run(row["project_id"], "scan", None, None, key, {"scope": scope})
            if created["status"] == "queued":
                queued += 1
    return queued


def run_once(db: Store | None = None) -> bool:
    db = db or Store()
    run = db.claim_next_run()
    if not run:
        return False
    try:
        if run["kind"] == "analysis":
            result = _run_analysis(db, run)
        elif run["kind"] == "scan":
            result = _run_scan(db, run)
        elif run["kind"] == "document_ingest":
            result = _run_document_ingest(db, run)
        else:
            raise ValueError("unsupported run kind")
        db.update_run(run["id"], "succeeded", result)
        if run["kind"] == "analysis":
            from .main import notify_project
            notify_project(db, run["project_id"], "analysis_ready", "영향 분석 결과",
                           result.get("summary", "분석을 완료했습니다."), data={"run_id": run["id"], "event_id": run["event_id"]})
    except Exception as exc:
        db.update_run(run["id"], "failed", {"status": "failed", "error": type(exc).__name__, "detail": str(exc)[:500]})
    return True


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    db = Store()
    db.recover_interrupted_runs()
    if args.once:
        enqueue_due_scans(db)
        run_once(db)
        return
    try:
        while True:
            enqueue_due_scans(db)
            if not run_once(db):
                time.sleep(2)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()

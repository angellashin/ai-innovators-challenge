"""Compare deterministic rules with a guarded agent fallback on synthetic notices."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.adapters.llm import OpenAICompatibleLLM  # noqa: E402
from app.agent import run_agent  # noqa: E402
from app.events import _dates_for_range, _extract_dates, normalize_event  # noqa: E402
from app.importers import parse_upload  # noqa: E402
from app.main import ConfirmInput, normalize_import_snapshot  # noqa: E402
from app.scheduling import simulate  # noqa: E402
from app.shifted_external import bundled_hero_calendars, recheck_shifted_schedule  # noqa: E402
from app.supplier_interpreter import interpret_supplier_message  # noqa: E402
from app.task_retrieval import retrieve_related_tasks  # noqa: E402
from app.risk_signals import mentioned_risk_types, search_risk_signals  # noqa: E402
from app.watch_suggestions import outdoor_candidate  # noqa: E402
from scripts.evaluation_mocks import MockEvaluationGateway  # noqa: E402


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _patch_dates(patch: dict) -> set[str]:
    return {str(day) for targets in patch.values() for value in targets.values()
            for day in (value if isinstance(value, list) else [value])}


def _scores(predicted: set[str], expected: set[str]) -> tuple[int, int, int]:
    return len(predicted & expected), len(predicted), len(expected)


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


_DRAFT_CLAIM = re.compile(
    r"(?<![\w])\d{4}-\d{2}-\d{2}(?![\d-])|"
    r"(?<![\w])(?:[$€£₩]\s*\d[\d,]*(?:\.\d+)?|"
    r"\d[\d,]*(?:\.\d+)?\s*(?:일간?|days?|개월|months?|원|달러|USD|KRW|EUR|%))",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")


def _draft_claims(draft: dict) -> list[str]:
    claims = []
    for match in _DRAFT_CLAIM.finditer(json.dumps(draft, ensure_ascii=False)):
        value = match.group().strip()
        claims.append(value if _DATE.fullmatch(value) else _NUMBER.search(value).group().replace(",", ""))
    return claims


def _calculator_claims(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_calculator_claims(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_calculator_claims(child) for child in value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {str(value)}
    if isinstance(value, str):
        dates = set(_DATE.findall(value))
        return dates | {match.group().replace(",", "") for match in _NUMBER.finditer(_DATE.sub("", value))}
    return set()


def _summarize(rows: list[dict]) -> dict:
    tp = sum(row["impact_counts"][0] for row in rows)
    predicted = sum(row["impact_counts"][1] for row in rows)
    expected = sum(row["impact_counts"][2] for row in rows)
    precision, recall = _rate(tp, predicted), _rate(tp, expected)
    return {"cases": len(rows), "change_date_accuracy": _rate(sum(row["date_correct"] for row in rows), len(rows)),
            "direct_task_accuracy": _rate(sum(row["task_correct"] for row in rows), len(rows)),
            "outcome_accuracy": _rate(sum(row["outcome_correct"] for row in rows), len(rows)),
            "impact_precision": precision, "impact_recall": recall,
            "impact_f1": _rate(2 * precision * recall, precision + recall),
            "clarification_accuracy": _rate(sum(row["clarification_correct"] for row in rows), len(rows)),
            "unsupported_date_rate": _rate(sum(row["unsupported_dates"] for row in rows),
                                            sum(row["proposed_dates"] for row in rows))}


def _evaluate_one(raw: dict, truth: dict, project: dict, tasks: list[dict], baseline: dict,
                  gateway: object | None, seen: set[tuple[str, str]]) -> dict:
    event = normalize_event(raw, project, tasks)
    agent_error = False
    identity = (str(raw.get("event_id")), str(raw.get("content")))
    duplicate = identity in seen
    seen.add(identity)
    if gateway and not duplicate and not event.get("patch") and event["classification_status"] != "NO_SCHEDULE_IMPACT":
        interpreted = interpret_supplier_message(event, project, tasks, gateway)
        agent_error = interpreted.get("status") == "interpretation_failed"
        if interpreted.get("patch"):
            event["patch"] = interpreted["patch"]
            event["related_task_ids"] = interpreted["related_task_ids"]
        elif interpreted.get("task_candidates"):
            event["related_task_ids"] = interpreted["related_task_ids"]
            event["task_candidates"] = interpreted["task_candidates"]
        elif interpreted.get("no_schedule_impact"):
            event["classification_status"] = "NO_SCHEDULE_IMPACT"
    patch = event.get("patch") or {}
    if duplicate:
        outcome, changed = "DUPLICATE", set()
    elif raw.get("corrects_event_id") and ("철회" in raw["content"] or "정정" in raw["content"]):
        outcome, changed = "RETRACTION", set()
    elif event["classification_status"] == "NO_SCHEDULE_IMPACT":
        outcome, changed = "NO_IMPACT", set()
    elif not patch:
        outcome, changed = "NEEDS_INPUT", set()
    else:
        outcome = "IMPACT"
        result = simulate(project, tasks, event={"patch": patch})
        original = {item["task_id"]: item for item in baseline["schedule"]}
        changed = {item["task_id"] for item in result["schedule"] if
                   (item["planned_start"], item["planned_finish"]) !=
                   (original[item["task_id"]]["planned_start"], original[item["task_id"]]["planned_finish"])}
    source_dates = set(_extract_dates(raw["content"], int(raw["published_at"][:4])))
    allowed_dates = source_dates | set(_dates_for_range(raw["content"], list(source_dates)))
    dates = _patch_dates(patch)
    expected_patch = truth.get("expected_patch") or {}
    return {"case_id": truth["case_id"], "outcome": outcome, "agent_error": agent_error,
            "outcome_correct": int(outcome == truth["expected_outcome"]),
            "date_correct": int(_patch_dates(expected_patch) == dates),
            "task_correct": int(set(event.get("related_task_ids") or []) == set(truth.get("direct_task_ids") or [])),
            "clarification_correct": int((outcome == "NEEDS_INPUT") == (truth["expected_outcome"] == "NEEDS_INPUT")),
            "impact_counts": _scores(changed, set(truth.get("changed_task_ids") or [])),
            "unsupported_dates": len(dates - allowed_dates), "proposed_dates": len(dates)}


def evaluate_supplier(project: dict, tasks: list[dict], gateway: object | None) -> dict:
    baseline = simulate(project, tasks)
    reports = {}
    for corpus, answer in (("supplier_messages.json", "supplier_message_ground_truth.json"),
                           ("supplier_message_variants.json", "supplier_message_variants_ground_truth.json")):
        messages = _load(ROOT / "data" / "hero_demo" / corpus)["events"]
        truths = _load(ROOT / "data" / "hero_demo" / answer)["cases"]
        if len(messages) != len(truths):
            raise ValueError(f"fixture and truth length differ: {corpus}")
        seen: set[tuple[str, str]] = set()
        rows = [_evaluate_one(raw, truth, project, tasks, baseline, gateway, seen)
                for raw, truth in zip(messages, truths)]
        reports[corpus] = {"summary": _summarize(rows), "cases": rows}
    return reports


def evaluate_outdoor(raw_tasks: list[dict]) -> dict:
    expected = set(_load(ROOT / "data" / "hero_demo" / "outdoor_tasks.json")["outdoor_task_ids"])
    predicted = {str(task["task_id"]) for task in raw_tasks if outdoor_candidate(task)}
    tp, p, e = _scores(predicted, expected)
    tn = len(raw_tasks) - len(predicted | expected)
    return {"accuracy": _rate(tp + tn, len(raw_tasks)), "precision": _rate(tp, p),
            "recall": _rate(tp, e), "predicted_task_ids": sorted(predicted)}


def evaluate_l3(project: dict, tasks: list[dict], gateway: object | None) -> dict:
    truth = _load(ROOT / "data" / "l3_ground_truth" / "ground_truth.json")
    mappings = _load(ROOT / "data" / "l1_project" / "l3_to_wbs_mapping.json")["mappings"]
    cases = {item["case_id"]: item for item in truth["cases"]}
    rows = []
    for item in mappings:
        if str(item.get("mapping_confidence") or "").lower() not in {"high", "medium"} or not item.get("direct_task_ids"):
            continue
        case = cases[item["case_id"]]
        text = "\n".join(str(value).strip() for value in (case.get("project_context"),
                          ((case.get("risk") or {}).get("cause") or {}).get("value")) if value)
        rules = normalize_event({"content": text, "mode": "REPLAY"}, project, tasks)
        predicted = set(rules.get("related_task_ids") or [])
        risk_types = mentioned_risk_types(text)
        analogous = search_risk_signals(risk_type=risk_types[0] if risk_types else "",
                                         exclude_source_risk_id=str(case.get("source_risk_id") or ""), limit=5)["results"]
        if any(row["risk_id"] == case.get("source_risk_id") for row in analogous):
            raise AssertionError("evaluated source case leaked into L2 evidence")
        agent_error = False
        if gateway and not predicted:
            retrieved = retrieve_related_tasks(text, tasks, gateway, analogous)
            predicted = set(retrieved["task_ids"])
            agent_error = bool(retrieved.get("error"))
        tp, p, e = _scores(predicted, set(item["direct_task_ids"]))
        rows.append({"case_id": item["case_id"], "predicted_task_ids": sorted(predicted),
                     "l2_evidence_risk_ids": [row["risk_id"] for row in analogous],
                     "agent_error": agent_error,
                     "impact_counts": [tp, p, e], "date_correct": 0, "task_correct": 0,
                     "clarification_correct": 0, "unsupported_dates": 0, "proposed_dates": 0})
    tp = sum(row["impact_counts"][0] for row in rows)
    p = sum(row["impact_counts"][1] for row in rows)
    e = sum(row["impact_counts"][2] for row in rows)
    precision, recall = _rate(tp, p), _rate(tp, e)
    return {"cases": len(rows), "precision": precision, "recall": recall,
            "f1": _rate(2 * precision * recall, precision + recall), "rows": rows}


def evaluate_workflow(project: dict, tasks: list[dict], options: list[dict], gateway: object) -> dict:
    """Exercise H01-H08 and V01-V11 with mockable calculator tools."""
    from app.risk_signals import evidence_for_supplier

    rows = []
    calendars = bundled_hero_calendars(tasks)
    for corpus, answer in (("supplier_messages.json", "supplier_message_ground_truth.json"),
                           ("supplier_message_variants.json", "supplier_message_variants_ground_truth.json")):
        messages = _load(ROOT / "data" / "hero_demo" / corpus)["events"]
        truths = _load(ROOT / "data" / "hero_demo" / answer)["cases"]
        for raw, truth in zip(messages, truths):
            case_id = str(raw.get("event_id") or "")
            if case_id not in {f"H{index:02d}" for index in range(1, 9)} | {f"V{index:02d}" for index in range(1, 12)}:
                continue
            if case_id == "H01" and any(row["case_id"] == "H01" for row in rows):
                continue
            event = normalize_event(raw, project, tasks)
            if not event.get("patch") and event.get("classification_status") != "NO_SCHEDULE_IMPACT":
                interpreted = interpret_supplier_message(event, project, tasks, gateway)
                if interpreted.get("patch"):
                    event["patch"] = interpreted["patch"]
                    event["related_task_ids"] = interpreted["related_task_ids"]
                else:
                    event["task_candidates"] = interpreted.get("task_candidates") or []
            event["risk_signal_evidence"] = evidence_for_supplier(
                event.get("content", ""), tasks, event.get("related_task_ids") or [], raw.get("published_at") or "")

            def find_task_candidates() -> dict:
                """Return ranked, quoted work candidates."""
                return retrieve_related_tasks(event.get("content", ""), tasks, gateway)

            def simulate_schedule(option_ids: list[str]) -> dict:
                """Calculate a supplier-only schedule."""
                if not event.get("patch"):
                    return {"status": "NEEDS_INPUT"}
                if option_ids:
                    return {"status": "invalid_option"}
                return simulate(project, tasks, event=event)

            def recheck_shifted_schedule_tool(option_ids: list[str]) -> dict:
                """Calculate shifted calendar constraints."""
                if not event.get("patch"):
                    return {"status": "NEEDS_INPUT"}
                if option_ids:
                    return {"status": "invalid_option"}
                return recheck_shifted_schedule(project, tasks, event, [], None, calendars, [], {})

            def search_risk_signals_tool(risk_type: str = "") -> dict:
                """Return L2 examples with publication timing."""
                result = search_risk_signals(risk_type=risk_type, limit=5)
                as_of = str(raw.get("published_at") or "")[:10]
                for item in result["results"]:
                    item["temporal_status"] = ("POST_AS_OF_REFERENCE" if item["published_date"] > as_of
                                                else "AVAILABLE_AS_OF")
                return result

            def simulate_regulatory_condition(option_ids: list[str]) -> dict:
                """Calculate an explicit conditional regulation patch only."""
                conditional = event.get("conditional_regulatory_patch") or {}
                if not conditional or option_ids:
                    return {"status": "NEEDS_INPUT", "conditional": True,
                            "reason": "규제 적용 시 추가 날짜와 기간 확인 필요"}
                from app.external_risks import combine_patches
                return {**simulate(project, tasks,
                                   event={**event, "patch": combine_patches([event.get("patch") or {}, conditional])}),
                        "conditional": True, "approval_required": True}

            output = run_agent({"_llm_gateway": gateway, "project": project,
                                "related_tasks": [task for task in tasks
                                if task["task_id"] in event.get("related_task_ids", [])],
                                "task_candidates": event.get("task_candidates") or [],
                                "risk_signal_evidence": event.get("risk_signal_evidence") or []}, event,
                               {"find_task_candidates": find_task_candidates,
                                "simulate_schedule": simulate_schedule,
                                "recheck_shifted_schedule": recheck_shifted_schedule_tool,
                                "simulate_regulatory_condition": simulate_regulatory_condition,
                                "search_risk_signals": search_risk_signals_tool,
                                "list_response_options": lambda: {"options": options}}, max_steps=8)
            tool_log = output.get("tool_log") or []
            tools = [entry["tool"] for entry in tool_log if entry.get("status") == "ok"]
            execution_failed = (not tools or output.get("status") not in {"completed", "needs_input"}
                                or any(entry.get("status") == "error" for entry in tool_log))
            needs_calculation = truth["expected_outcome"] == "IMPACT" and bool(event.get("patch"))
            ambiguous = not event.get("patch") and event.get("classification_status") != "NO_SCHEDULE_IMPACT"
            draft = output.get("email_draft") or {}
            calculator_claims = _calculator_claims([entry.get("result") for entry in tool_log
                                                    if entry.get("status") == "ok" and entry["tool"] in
                                                    {"simulate_schedule", "recheck_shifted_schedule", "simulate_regulatory_condition"}])
            numeric = _draft_claims(draft)
            unsupported = [token for token in numeric if token not in calculator_claims]
            unconfirmed = "확인되지" in str(raw.get("content") or "")
            assessment = output.get("regulatory_assessment") or {}
            rows.append({"case_id": case_id, "tool_order": tools,
                         "execution_failed": execution_failed,
                         "required_tools_called": not execution_failed and (not needs_calculation or any(name in tools for name in
                                                   ("simulate_schedule", "recheck_shifted_schedule"))),
                         "ambiguous_stopped": not execution_failed and (not ambiguous or not any(name in tools for name in
                                               ("simulate_schedule", "recheck_shifted_schedule"))),
                         "draft_numeric_claims": len(numeric), "unsupported_draft_numeric_claims": len(unsupported),
                         "unconfirmed_regulation": unconfirmed,
                         "unconfirmed_as_confirmed_delay": bool(unconfirmed and assessment.get("confirmed_delay_days")),
                         "status": output.get("status"), "summary": output.get("summary"),
                         "stop_reason": output.get("stop_reason"),
                         "unresolved_items": output.get("unresolved_items") or [],
                         "rejected_tool_calls": [{"tool": entry["tool"], "args": entry.get("args"),
                                                  "reason": entry.get("error")}
                                                 for entry in tool_log if entry.get("status") == "invalid_tool_args"]})
    return {"cases": len(rows), "required_tool_rate": _rate(sum(row["required_tools_called"] for row in rows), len(rows)),
            "ambiguous_stop_rate": _rate(sum(row["ambiguous_stopped"] for row in rows), len(rows)),
            "execution_failures": sum(row["execution_failed"] for row in rows),
            "execution_failure_rate": _rate(sum(row["execution_failed"] for row in rows), len(rows)),
            "unsupported_draft_numeric_rate": _rate(sum(row["unsupported_draft_numeric_claims"] for row in rows),
                                                    sum(row["draft_numeric_claims"] for row in rows)),
            "unconfirmed_regulation_as_confirmed_delay_rate": _rate(sum(row["unconfirmed_as_confirmed_delay"] for row in rows),
                                                                    sum(row["unconfirmed_regulation"] for row in rows)),
            "rows": rows}


def evaluate(mode: str) -> dict:
    project_file = ROOT / "data" / "l1_project" / "hero_battery_factory_project.xlsx"
    parsed = parse_upload(project_file.name, project_file.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "새 프로젝트", "mode": "REPLAY"}, ConfirmInput())
    project, tasks = snapshot["project"], snapshot["tasks"]
    rules = evaluate_supplier(project, tasks, None)
    l3_rules = evaluate_l3(project, tasks, None)
    if mode == "real":
        if os.environ.get("REPLAN_PAID_CALLS_ENABLED", "false").lower() != "true":
            raise RuntimeError("Set REPLAN_PAID_CALLS_ENABLED=true explicitly for paid evaluation")
        if not all(os.environ.get(key) for key in ("LLM_BASE_URL", "LLM_MODEL", "API_KEY")):
            raise RuntimeError("LLM_BASE_URL, LLM_MODEL and API_KEY are required")
        preview_gateway = MockEvaluationGateway()
        evaluate_supplier(project, tasks, preview_gateway)
        evaluate_l3(project, tasks, preview_gateway)
        evaluate_workflow(project, tasks, snapshot.get("options", []), preview_gateway)
        expected_calls = preview_gateway.calls
        if expected_calls > int(os.environ.get("REPLAN_MAX_PAID_RUNS_PER_DAY", "20")):
            raise RuntimeError("Expected paid calls exceed REPLAN_MAX_PAID_RUNS_PER_DAY")
        gateway = OpenAICompatibleLLM()
    else:
        expected_calls = 0
        gateway = MockEvaluationGateway()
    class Counter:
        def __init__(self, delegate: object) -> None:
            self.delegate, self.calls = delegate, 0

        def chat(self, *args: object, **kwargs: object) -> object:
            if mode == "real" and self.calls >= int(os.environ.get("REPLAN_MAX_PAID_RUNS_PER_DAY", "20")):
                raise RuntimeError("Paid evaluation reached REPLAN_MAX_PAID_RUNS_PER_DAY")
            self.calls += 1
            return self.delegate.chat(*args, **kwargs)
    counting = Counter(gateway)
    agent = evaluate_supplier(project, tasks, counting)
    l3_agent = evaluate_l3(project, tasks, counting)
    workflow = evaluate_workflow(project, tasks, snapshot.get("options", []), counting)
    if mode == "real" and (any(row["agent_error"] for corpus in agent.values() for row in corpus["cases"])
                           or any(row["agent_error"] for row in l3_agent["rows"])
                           or any(row["status"] in {"failed", "llm_unavailable"} for row in workflow["rows"])):
        raise RuntimeError("One or more paid LLM calls failed; no evaluation result was written")
    return {"mode": mode, "model": os.environ.get("LLM_MODEL") if mode == "real" else "offline-semantic-fixture",
            "executed_at": datetime.now(timezone.utc).isoformat(), "llm_call_count": counting.calls if mode == "real" else 0,
            "mock_call_count": counting.calls if mode == "mock" else 0,
            "expected_paid_call_count": expected_calls,
            "note": "Mock scores validate wiring and fixtures, not actual model quality." if mode == "mock" else "Actual model run; no ground truth was sent to the model.",
            "supplier": {"rules_only": rules, "agent_included": agent},
            "l3_affected_task_retrieval": {"rules_only": l3_rules, "agent_included": l3_agent},
            "agent_workflow": workflow,
            "outdoor_classification": evaluate_outdoor(parsed["tasks"])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "agent_evaluation.json")
    args = parser.parse_args()
    result = evaluate(args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": result["mode"], "model": result["model"], "llm_call_count": result["llm_call_count"],
                      "mock_call_count": result["mock_call_count"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

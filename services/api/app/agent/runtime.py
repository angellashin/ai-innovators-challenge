"""Guarded single-agent loop for schedule replanning assistance."""

from __future__ import annotations

import inspect
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..adapters.llm import ChatResult, LLMUnavailable, OpenAICompatibleLLM

MAX_TOOL_CALLS = 8
MAX_INVALID_ARG_RETRIES = 2
MAX_ARG_BYTES = 8_192
BLOCKED_TOOL_PARTS = ("commit", "send")
SCHEDULE_TOOLS = {"simulate_schedule", "recheck_shifted_schedule", "simulate_regulatory_condition"}


def run_agent(
    context: Dict[str, Any],
    event: Dict[str, Any],
    tools: Dict[str, Callable[..., Any]],
    max_steps: int = 8,
) -> Dict[str, Any]:
    """Run a bounded LLM loop and return a normalized agent result."""

    allowed_tools = _allowed_tools(tools)
    if not event.get("patch"):
        allowed_tools = {name: func for name, func in allowed_tools.items() if name not in SCHEDULE_TOOLS}
    if not allowed_tools and tools:
        if all(name in SCHEDULE_TOOLS for name in tools):
            return _result(status="needs_input", summary="Schedule calculation requires confirmed inputs.",
                           unresolved_items=["changed task and date require confirmation before schedule calculation"])
        return _result(
            status="blocked_action",
            summary="No provided tools are allowed for agent execution.",
            unresolved_items=["commit/send actions are not executable by the agent"],
        )

    messages = _initial_messages(context, event, allowed_tools)
    tool_schemas = [_tool_schema(name, func) for name, func in allowed_tools.items()]
    tool_log: List[Dict[str, Any]] = []
    seen_calls = set()
    usage: Dict[str, Any] = {}
    max_calls = min(MAX_TOOL_CALLS, max(0, max_steps) * 2)
    invalid_arg_retries = 0

    try:
        gateway = _gateway(context)
    except LLMUnavailable as exc:
        return _unavailable(str(exc))

    for step in range(max(1, max_steps)):
        try:
            reply = gateway.chat(messages, tools=tool_schemas, response_format={"type": "json_object"})
        except LLMUnavailable as exc:
            return _result(status="llm_unavailable", summary="LLM agent is unavailable because the gateway cannot be reached.",
                           unresolved_items=[str(exc)], tool_log=tool_log, usage=usage)
        except Exception as exc:  # pragma: no cover - exercised through runtime behavior, exact clients vary.
            return _result(
                status="failed",
                summary="LLM gateway request failed.",
                unresolved_items=[str(exc)],
                tool_log=tool_log,
                usage=usage,
            )

        usage = _merge_usage(usage, {**(reply.usage or {}), "llm_calls": 1})
        action = _next_action(reply)
        if action:
            name = str(action.get("tool") or "")
            if name in SCHEDULE_TOOLS and not event.get("patch"):
                reason = "changed task and date require confirmation before schedule calculation"
                tool_log.append({"tool": name, "args": action.get("args", {}),
                                 "status": "blocked_ambiguous", "error": reason})
                return _result(status="needs_input", summary="Schedule calculation requires confirmed inputs.",
                               unresolved_items=[reason], tool_log=tool_log, usage=usage)
            outcome = _execute_action(action, allowed_tools, seen_calls, tool_log, max_calls, context)
            if outcome:
                if (outcome["status"] == "invalid_tool_args"
                        and invalid_arg_retries < MAX_INVALID_ARG_RETRIES
                        and len(tool_log) < max_calls and step + 1 < max_steps):
                    invalid_arg_retries += 1
                    messages.extend(_tool_result_messages(action, tool_log[-1]))
                    continue
                return _result(tool_log=tool_log, usage=usage, **outcome)
            messages.extend(_tool_result_messages(action, tool_log[-1]))
            continue

        final = _parse_final(reply.content)
        final["tool_log"] = tool_log
        final["usage"] = usage
        final.setdefault("status", "completed")
        final["regulatory_assessment"] = _regulatory_fields(final.get("regulatory_assessment"))
        final = _ground_final(final, event, tool_log, _calculated_context(context))
        if not isinstance(final.get("summary"), str):
            final["summary"] = _summary_sentence(final.get("summary"), tool_log, context.get("scenario_summaries"))
        return _normalize_result(final)

    return _result(
        status="max_steps_reached",
        summary="Agent stopped after reaching the step limit.",
        unresolved_items=["review required because the agent did not produce a final answer"],
        tool_log=tool_log,
        usage=usage,
    )


def _gateway(context: Dict[str, Any]) -> Any:
    injected = context.get("_llm_gateway")
    if injected is not None:
        return injected
    return OpenAICompatibleLLM()


def _initial_messages(
    context: Dict[str, Any],
    event: Dict[str, Any],
    tools: Dict[str, Callable[..., Any]],
) -> List[Dict[str, Any]]:
    tool_names = sorted(tools)
    system = (
        "You review a schedule change for a project team. Return only one JSON object with these keys: "
        "summary (Korean string, 1-2 sentences: what changed and what it means for the finish date), "
        "status (one of completed, needs_input, needs_review), "
        "stop_reason (Korean string: the one thing a person must confirm next, or empty), "
        'option_explanations (array of {"option_ids": [...], "text": Korean string}; option_ids copied from '
        "context.scenario_summaries, [] for no response), "
        'regulatory_assessment (null unless the notice mentions a regulation or permit, else {"likelihood": '
        '"높음|중간|낮음|불확실", "reason": string, "human_check": string, "evidence_risk_ids": [...]}), '
        'email_draft ({"to", "subject", "body"} in Korean, or null), unresolved_items (array of concrete '
        "questions for a person). Do not add other keys and do not restate task lists or schedules. "
        "When context.scenario_summaries is present, deterministic calculators have already produced those "
        "scenarios: compare and explain them, cite them by option_ids, and never say they were not calculated. "
        "Use calculator values exclusively for all dates, durations and costs; do not alter them. "
        "Tools are optional; call one only when it adds evidence, one at a time, using native tool calls or "
        '{"action":"tool","tool":"name","args":{...}}. Pass {} to tools that declare no arguments. '
        "If the task or changed date is ambiguous, ask a concrete question and stop before schedule tools. "
        "L2 cases show analogies, not legal applicability or project delay. POST_AS_OF_REFERENCE is reference "
        "only, never reasoning evidence. Unconfirmed regulation is a conditional scenario only; call "
        "simulate_regulatory_condition when available and report needs_input if its date or duration is missing. "
        "A draft never sends or confirms a plan. Never request commit or send actions."
    )
    user = {
        "context": _public_context(context),
        "event": event,
        "allowed_tools": tool_names,
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": _encode(user)}]


def _public_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in context.items() if not str(key).startswith("_")}


def _allowed_tools(tools: Dict[str, Callable[..., Any]]) -> Dict[str, Callable[..., Any]]:
    return {
        name: func
        for name, func in tools.items()
        if callable(func) and not _is_blocked_tool_name(name)
    }


def _is_blocked_tool_name(name: str) -> bool:
    lowered = str(name).lower()
    return any(part in lowered for part in BLOCKED_TOOL_PARTS)


def _tool_schema(name: str, func: Callable[..., Any]) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required = []
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    if signature:
        for param_name, param in signature.parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            properties[param_name] = {"type": _json_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": (getattr(func, "__doc__", None) or "Project planning tool").strip()[:512],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": not signature,
            },
        },
    }


def _json_type(annotation: Any) -> str:
    if isinstance(annotation, str):
        if annotation.startswith("list") or annotation.startswith("List"):
            return "array"
        if annotation.startswith("dict") or annotation.startswith("Dict"):
            return "object"
    if annotation in (int, "int"):
        return "integer"
    if annotation in (float, "float"):
        return "number"
    if annotation in (bool, "bool"):
        return "boolean"
    if annotation in (list, List, "list"):
        return "array"
    if annotation in (dict, Dict, "dict"):
        return "object"
    return "string"


def _next_action(reply: ChatResult) -> Optional[Dict[str, Any]]:
    if reply.tool_calls:
        call = reply.tool_calls[0]
        return {
            "tool": call.get("name"),
            "args": call.get("arguments", {}),
            "tool_call_id": call.get("id"),
        }
    try:
        payload = json.loads(reply.content or "{}")
    except json.JSONDecodeError:
        return None
    if payload.get("action") == "tool":
        return {"tool": payload.get("tool"), "args": payload.get("args", {})}
    tool_call = payload.get("tool_call")
    if isinstance(tool_call, dict):
        return {"tool": tool_call.get("name") or tool_call.get("tool"), "args": tool_call.get("args", {})}
    return None


def _tool_result_messages(action: Dict[str, Any], result: Dict[str, Any]) -> List[Dict[str, Any]]:
    tool_call_id = action.get("tool_call_id")
    if tool_call_id:
        return [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {"name": action["tool"], "arguments": _encode(action.get("args"))},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": tool_call_id, "content": _encode(model_view(result))},
        ]
    return [{"role": "user", "content": "Tool result: %s" % _encode(model_view(result))}]


MODEL_VIEW_DROP = {"schedule", "supplier_schedule", "combined_patch", "external_source_hashes", "scenario_hash"}
MODEL_VIEW_LIST_LIMIT = 12
MODEL_VIEW_TEXT_LIMIT = 1_500


def model_view(value: Any, depth: int = 0) -> Any:
    """What the model sees from a tool: full per-task schedules are dropped and long lists cut."""
    if isinstance(value, dict):
        return {key: model_view(item, depth + 1) for key, item in value.items() if key not in MODEL_VIEW_DROP}
    if isinstance(value, list):
        kept = [model_view(item, depth + 1) for item in value[:MODEL_VIEW_LIST_LIMIT]]
        if len(value) > MODEL_VIEW_LIST_LIMIT:
            kept.append({"omitted_items": len(value) - MODEL_VIEW_LIST_LIMIT})
        return kept
    if isinstance(value, str) and len(value) > MODEL_VIEW_TEXT_LIMIT:
        return value[:MODEL_VIEW_TEXT_LIMIT] + "…"
    return value


def _execute_action(
    action: Dict[str, Any],
    tools: Dict[str, Callable[..., Any]],
    seen_calls: set,
    tool_log: List[Dict[str, Any]],
    max_calls: int,
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    name = str(action.get("tool") or "")
    args = action.get("args", {})
    if len(tool_log) >= max_calls:
        return {
            "status": "max_tool_calls_reached",
            "summary": "Agent stopped after reaching the tool-call limit.",
            "unresolved_items": ["review required before additional tool execution"],
        }
    if name not in tools:
        return {
            "status": "blocked_action",
            "summary": "Agent requested a disallowed tool.",
            "unresolved_items": ["tool '%s' is not allowed" % name],
        }
    ok, reason = _validate_arg_payload(args)
    if not ok:
        return _invalid_args(tool_log, name, args, reason)
    # These closures are already scoped to the active project. Some gateways
    # echo its ID even though it is absent from the advertised tool schema.
    if isinstance(args, dict) and "project_id" in args:
        try:
            accepts_project_id = "project_id" in inspect.signature(tools[name]).parameters
        except (TypeError, ValueError):
            accepts_project_id = True
        if not accepts_project_id:
            project = context.get("project") or {}
            expected = str(project.get("project_id") or "") if isinstance(project, dict) else ""
            if not expected or str(args["project_id"]) != expected:
                return _invalid_args(tool_log, name, args, "project_id does not match the active project")
            args = {key: value for key, value in args.items() if key != "project_id"}
            action["args"] = args
    ignored_args = None
    try:
        no_arguments = not inspect.signature(tools[name]).parameters
    except (TypeError, ValueError):
        no_arguments = False
    if no_arguments and args:
        ignored_args = args
        args = {}
        action["args"] = args
    ok, reason = _validate_args(tools[name], args)
    if not ok:
        return _invalid_args(tool_log, name, args, reason)
    fingerprint = (name, _encode(args))
    if fingerprint in seen_calls:
        return {
            "status": "repeated_tool_call",
            "summary": "Agent repeated the same tool call.",
            "unresolved_items": ["repeated call blocked: %s" % name],
        }
    seen_calls.add(fingerprint)

    try:
        result = tools[name](**args)
        entry = {"tool": name, "args": args, "status": "ok", "result": result}
        if ignored_args:
            entry["ignored_args"] = ignored_args
        tool_log.append(entry)
    except Exception as exc:
        tool_log.append({"tool": name, "args": args, "status": "error", "error": str(exc)})
    return None


def _invalid_args(tool_log: List[Dict[str, Any]], name: str, args: Any, reason: str) -> Dict[str, Any]:
    tool_log.append({"tool": name, "args": args, "status": "invalid_tool_args", "error": reason})
    return {"status": "invalid_tool_args", "summary": "Agent requested a tool with invalid arguments.",
            "unresolved_items": [reason]}


def _validate_args(func: Callable[..., Any], args: Any) -> Tuple[bool, str]:
    ok, reason = _validate_arg_payload(args)
    if not ok:
        return ok, reason
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return True, ""
    try:
        signature.bind(**args)
    except TypeError as exc:
        return False, str(exc)
    return True, ""


def _validate_arg_payload(args: Any) -> Tuple[bool, str]:
    if not isinstance(args, dict):
        return False, "tool arguments must be an object"
    if len(_encode(args).encode("utf-8")) > MAX_ARG_BYTES:
        return False, "tool arguments exceed size limit"
    if not _is_json_safe(args):
        return False, "tool arguments must be JSON-compatible"
    return True, ""


def _is_json_safe(value: Any, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_safe(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_safe(item, depth + 1) for key, item in value.items())
    return False


def _parse_final(content: str) -> Dict[str, Any]:
    try:
        payload = json.loads(content or "{}")
    except json.JSONDecodeError:
        payload = {"summary": content or "Agent did not return JSON.", "status": "needs_review"}
    if not isinstance(payload, dict):
        payload = {"summary": str(payload), "status": "needs_review"}
    return payload


def _normalize_result(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "summary": _summary_sentence(value.get("summary"), _list(value.get("tool_log"))),
        "impacted_tasks": _list(value.get("impacted_tasks")),
        "options": _list(value.get("options")),
        "option_explanations": [_option_explanation(item) for item in _list(value.get("option_explanations"))
                                if _option_explanation(item)["text"]],
        "regulatory_assessment": value.get("regulatory_assessment") if isinstance(value.get("regulatory_assessment"), dict) else None,
        "conditional_scenario": value.get("conditional_scenario") if isinstance(value.get("conditional_scenario"), dict) else None,
        "email_draft": ({key: str(value["email_draft"].get(key) or "") for key in ("to", "subject", "body")}
                        if isinstance(value.get("email_draft"), dict) else None),
        "stop_reason": str(value.get("stop_reason") or ""),
        "required_actions": _list(value.get("required_actions")),
        "unresolved_items": [_text(item) for item in _list(value.get("unresolved_items")) if _text(item)],
        "tool_log": _list(value.get("tool_log")),
        "status": _normalize_status(value.get("status")),
        "usage": dict(value.get("usage") or {}),
    }


EXPLANATION_TEXT_KEYS = ("text", "explanation", "tradeoff", "description", "reason", "summary")


def _text(value: Any) -> str:
    """Model prose as one readable string, never a serialized object."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = [_text(value[key]) for key in EXPLANATION_TEXT_KEYS + ("question", "item") if key in value]
        return " ".join(part for part in parts if part)
    if isinstance(value, list):
        return " · ".join(part for part in (_text(item) for item in value) if part)
    return "" if value is None else str(value)


def _option_explanation(item: Any) -> Dict[str, Any]:
    """One explanation per scenario: {option_ids, text}, whatever shape the model used."""
    if not isinstance(item, dict):
        return {"option_ids": [], "text": _text(item)}
    ids = item.get("option_ids")
    if ids is None and item.get("option_id") is not None:
        ids = [item.get("option_id")]
    ids = [str(value) for value in ids] if isinstance(ids, list) else []
    text = next((_text(item[key]) for key in EXPLANATION_TEXT_KEYS if _text(item.get(key))), "")
    label = _text(item.get("option") or item.get("label") or item.get("name"))
    if label and text and label not in text:
        text = f"{label}: {text}"
    return {"option_ids": ids, "text": text or label}


LIKELIHOOD = {"높음": "높음", "high": "높음", "likely": "높음", "confirmed": "높음",
              "중간": "중간", "medium": "중간", "possible": "중간",
              "낮음": "낮음", "low": "낮음", "unlikely": "낮음", "not_applicable": "낮음"}


def _regulatory_fields(value: Any) -> Optional[Dict[str, Any]]:
    """Map whatever regulation keys the model used onto the fields the screen shows."""
    if not isinstance(value, dict):
        return None
    raw = str(value.get("likelihood") or value.get("applicability") or "").strip().lower()
    reason = _text(value.get("reason") or value.get("assessment") or value.get("rationale"))
    human_check = _text(value.get("human_check") or value.get("required_check") or value.get("next_step")
                        or value.get("missing_inputs"))
    return {
        "likelihood": LIKELIHOOD.get(raw, "불확실"),
        "reason": reason,
        "human_check": human_check,
        "evidence_risk_ids": [str(item) for item in _list(value.get("evidence_risk_ids"))],
        "reference_only_risk_ids": [str(item) for item in _list(value.get("reference_only_risk_ids"))],
    }


def _normalize_status(value: Any) -> str:
    if isinstance(value, dict):
        states = {str(item).lower() for item in value.values()}
        if states & {"error", "failed", "invalid_tool_args"}:
            return "failed"
        for state in ("needs_input", "needs_review", "no_schedule_impact"):
            if state in states:
                return state
        if states and states <= {"converged", "patch_proposed", "completed", "ok"}:
            return "completed"
        return "invalid_status"
    state = str(value or "completed").strip().lower()
    if state in KNOWN_STATUSES:
        return state
    if "input" in state:
        return "needs_input"
    if any(word in state for word in ("review", "pending", "approval", "confirm")):
        return "needs_review"
    if any(word in state for word in ("fail", "error")):
        return "failed"
    return "completed"


KNOWN_STATUSES = {"completed", "needs_input", "needs_review", "no_schedule_impact", "failed", "llm_unavailable",
                  "blocked_action", "invalid_tool_args", "max_tool_calls_reached", "repeated_tool_call",
                  "max_steps_reached", "invalid_status"}


def _summary_sentence(summary: Any, tool_log: List[Any], scenarios: Any = None) -> str:
    if not isinstance(summary, dict):
        return str(summary or "")
    calculator = next((entry.get("result") for entry in reversed(tool_log)
                       if isinstance(entry, dict) and entry.get("status") == "ok"
                       and entry.get("tool") in {"recheck_shifted_schedule", "simulate_schedule"}
                       and isinstance(entry.get("result"), dict)), None)
    if calculator is None and isinstance(scenarios, list):
        calculator = next((row for row in scenarios if isinstance(row, dict) and not row.get("option_ids")), None)
    if calculator and calculator.get("finish_date"):
        target = "목표일을 충족합니다." if calculator.get("target_met") else "목표일을 충족하지 못합니다."
        return f"통보의 일정 영향을 검토했습니다. 계산된 완료 예정일은 {calculator['finish_date']}이며, {target}"
    return "통보 내용을 검토했습니다. 일정 계산에 필요한 조건을 추가로 확인해야 합니다."


_NUMBER_OR_DATE = re.compile(r"(?<![A-Za-z0-9_-])(?:\d{4}-\d{2}-\d{2}|\d[\d,]*(?:\.\d+)?)(?![A-Za-z0-9_-])")
_LIST_MARKER = re.compile(r"(?:^|(?<=\s)|(?<=\())\d{1,2}(?=[.)]\s|\))", re.MULTILINE)


def _calculated_context(context: Dict[str, Any]) -> List[Any]:
    """Calculator output and registered option data the caller put in context."""
    return [context[key] for key in ("scenario_summaries", "changed_tasks_without_response", "response_options",
                                     "baseline_finish", "related_tasks") if context.get(key) is not None]


def _ground_final(value: Dict[str, Any], event: Dict[str, Any], tool_log: List[Dict[str, Any]],
                  calculated: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Treat model prose as untrusted; numeric claims need calculator provenance."""
    calculator = [row["result"] for row in tool_log if row.get("status") == "ok"
                  and row.get("tool") in {"simulate_schedule", "recheck_shifted_schedule", "simulate_regulatory_condition"}]
    calculator.extend(calculated or [])
    allowed: set[str] = set()

    def collect(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            allowed.add(str(item))
            if isinstance(item, int):
                allowed.add(f"{item:,}")
        elif isinstance(item, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item):
            allowed.add(item)
            allowed.add(item[:4])  # "2027년" refers to a calculated date's year

    for result in calculator:
        collect(result)
    for entry in tool_log:
        if entry.get("tool") == "simulate_regulatory_condition" and entry.get("status") == "ok":
            value["conditional_scenario"] = entry.get("result")

    def clean(item: Any) -> Any:
        if isinstance(item, str):
            markers = {match.start() for match in _LIST_MARKER.finditer(item)}
            return _NUMBER_OR_DATE.sub(lambda match: match.group() if match.group() in allowed
                                       or match.start() in markers else "", item)
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items()}
        return item

    for key in ("email_draft", "option_explanations"):
        if key in value:
            value[key] = clean(value[key])

    assessment = value.get("regulatory_assessment")
    notice = str(event.get("content") or "").lower()
    regulatory_terms = ("규제", "인허가", "허가", "법령", "법규", "규정", "regulation", "regulatory", "permit", "license")
    if isinstance(assessment, dict) and not any(term in notice for term in regulatory_terms):
        value["regulatory_assessment"] = None
        assessment = None
    if isinstance(assessment, dict):
        as_of = str(event.get("published_at") or event.get("received_at") or "")[:10]
        referenced = {row["risk_id"]: row for entry in tool_log
                      if entry.get("tool") == "search_risk_signals" and entry.get("status") == "ok"
                      for row in (entry.get("result") or {}).get("results", [])
                      if isinstance(row, dict) and row.get("risk_id")}
        cited = [str(identifier) for identifier in assessment.get("evidence_risk_ids") or []]
        assessment["evidence_risk_ids"] = [identifier for identifier in cited
                                           if identifier in referenced and
                                           (not as_of or str(referenced[identifier].get("published_date") or "") <= as_of)]
        assessment["reference_only_risk_ids"] = [identifier for identifier in cited if identifier in referenced
                                                  and identifier not in assessment["evidence_risk_ids"]]
        if not assessment["evidence_risk_ids"] and assessment.get("likelihood") == "높음":
            assessment["likelihood"] = "불확실"
        if any(term in notice for term in ("확인되지", "미확정", "불확실", "unconfirmed", "not confirmed", "unknown")) and assessment.get("likelihood") == "높음":
            assessment["likelihood"] = "불확실"
        for field in ("confirmed_delay_days", "confirmed_finish_date"):
            assessment.pop(field, None)
    return value


def _result(
    summary: str = "",
    status: str = "completed",
    impacted_tasks: Optional[Iterable[Any]] = None,
    options: Optional[Iterable[Any]] = None,
    required_actions: Optional[Iterable[Any]] = None,
    unresolved_items: Optional[Iterable[Any]] = None,
    tool_log: Optional[Iterable[Any]] = None,
    usage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _normalize_result(
        {
            "summary": summary,
            "status": status,
            "impacted_tasks": list(impacted_tasks or []),
            "options": list(options or []),
            "required_actions": list(required_actions or []),
            "unresolved_items": list(unresolved_items or []),
            "tool_log": list(tool_log or []),
            "usage": usage or {},
        }
    )


def _unavailable(reason: str) -> Dict[str, Any]:
    return _result(
        status="llm_unavailable",
        summary="LLM agent is unavailable because API_KEY is not configured or the gateway cannot be reached.",
        unresolved_items=[reason],
    )


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _merge_usage(current: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(current)
    for key, value in (new or {}).items():
        if isinstance(value, (int, float)) and isinstance(merged.get(key), (int, float)):
            merged[key] += value
        else:
            merged[key] = value
    return merged


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

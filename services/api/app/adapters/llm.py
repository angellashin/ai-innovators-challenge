"""OpenAI-compatible chat-completions gateway for the agent runtime.

REPLAN_LLM_MODE selects how requests are served:
- live (default): call the gateway.
- record: call the gateway and save each response to REPLAN_LLM_CASSETTE.
- replay: answer from REPLAN_LLM_CASSETTE only; no key, no network, no cost.
Replay matches on the request body with run-specific IDs and timestamps masked,
so a prompt or tool change is a replay miss and needs a new recording.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LLM_MODES = {"live", "record", "replay"}
_CASSETTE_LOCK = threading.Lock()
_VOLATILE = [
    (re.compile(r"\b[0-9a-f]{64}\b"), "<hash>"),
    (re.compile(r"\b[0-9a-f]{32}\b"), "<id>"),
    (re.compile(r"\bP-[0-9a-f]{10}\b"), "<project>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+00:00"), "<now>"),
]


def llm_mode() -> str:
    mode = os.environ.get("REPLAN_LLM_MODE", "live").strip().lower()
    return mode if mode in LLM_MODES else "live"


def agent_enabled() -> bool:
    """Whether analyses may use the LLM: replay always may, live/record need keys and opt-in."""
    if llm_mode() == "replay":
        return bool(os.environ.get("REPLAN_LLM_CASSETTE"))
    return (all(os.environ.get(key) for key in ("API_KEY", "LLM_MODEL", "LLM_BASE_URL"))
            and os.environ.get("REPLAN_PAID_CALLS_ENABLED", "false").lower() == "true")


def request_key(payload: Dict[str, Any]) -> str:
    """Stable replay key: the request body without the model name and run-specific values."""
    body = json.dumps({key: value for key, value in payload.items() if key != "model"},
                      ensure_ascii=False, sort_keys=True, default=str)
    for pattern, placeholder in _VOLATILE:
        body = pattern.sub(placeholder, body)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _cassette_path() -> Path:
    configured = os.environ.get("REPLAN_LLM_CASSETTE")
    if not configured:
        raise LLMUnavailable("REPLAN_LLM_CASSETTE is not configured")
    return Path(configured)


def _load_cassette(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _summarize_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    messages = payload.get("messages") or []
    return {"messages": len(messages), "chars": len(json.dumps(messages, ensure_ascii=False, default=str)),
            "tools": [tool.get("function", {}).get("name") for tool in payload.get("tools") or []],
            "system": str((messages[0] or {}).get("content") or "")[:160] if messages else ""}


class LLMUnavailable(RuntimeError):
    """Raised when the configured LLM gateway cannot be used."""


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    model: Optional[str] = None
    finish_reason: Optional[str] = None


class OpenAICompatibleLLM:
    """Small synchronous wrapper around an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("API_KEY")
        self.model = model if model is not None else os.environ.get("LLM_MODEL")
        configured_base_url = base_url if base_url is not None else os.environ.get("LLM_BASE_URL")
        self.base_url = configured_base_url.rstrip("/") if configured_base_url else None
        self.timeout = timeout

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> ChatResult:
        mode = llm_mode()
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format

        if mode == "replay":
            data = self._replay(payload)
        else:
            if not self.api_key:
                raise LLMUnavailable("API_KEY is not configured")
            if not self.model:
                raise LLMUnavailable("LLM_MODEL is not configured")
            if not self.base_url:
                raise LLMUnavailable("LLM_BASE_URL is not configured")
            headers = {"Authorization": "Bearer %s" % self.api_key, "Content-Type": "application/json"}
            try:
                import httpx
            except ImportError as exc:
                raise LLMUnavailable("httpx is required for LLM gateway requests") from exc

            data = self._post_chat(httpx, payload, headers, allow_json_fallback=bool(tools or response_format))
            if mode == "record":
                self._record(payload, data)

        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailable("LLM response did not include choices")
        choice = choices[0]
        message = choice.get("message") or {}
        return ChatResult(
            content=str(message.get("content") or ""),
            tool_calls=_parse_tool_calls(message.get("tool_calls") or []),
            usage=dict(data.get("usage") or {}),
            model=data.get("model"),
            finish_reason=choice.get("finish_reason"),
        )

    def _replay(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        key = request_key(payload)
        entry = _load_cassette(_cassette_path()).get("entries", {}).get(key)
        if entry is None:
            raise LLMUnavailable("replay miss: no recorded response for request %s" % key[:12])
        return dict(entry["response"])

    def _record(self, payload: Dict[str, Any], data: Dict[str, Any]) -> None:
        path = _cassette_path()
        with _CASSETTE_LOCK:
            cassette = _load_cassette(path)
            cassette.setdefault("entries", {})[request_key(payload)] = {
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": data.get("model") or self.model,
                "request": _summarize_request(payload),
                "response": {key: data.get(key) for key in ("model", "choices", "usage") if key in data},
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cassette, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    def _post_chat(
        self,
        httpx_module: Any,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        allow_json_fallback: bool,
    ) -> Dict[str, Any]:
        try:
            return self._request(httpx_module, payload, headers)
        except httpx_module.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code not in (400, 422) or not allow_json_fallback:
                raise
            fallback_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"tools", "tool_choice", "response_format"}
            }
            return self._request(httpx_module, fallback_payload, headers)

    def _request(self, httpx_module: Any, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        with httpx_module.Client(timeout=self.timeout) as client:
            response = client.post("%s/chat/completions" % self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            cost = getattr(response, "headers", {}).get("x-litellm-response-cost")
            if cost and isinstance(data, dict):
                try:
                    data.setdefault("usage", {})["cost_usd"] = float(cost)
                except (TypeError, ValueError):
                    pass
            return data


def _parse_tool_calls(raw_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parsed = []
    for raw in raw_calls:
        function = raw.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                arguments = {"__invalid_json__": arguments}
        parsed.append(
            {
                "id": raw.get("id"),
                "name": function.get("name") or raw.get("name"),
                "arguments": arguments,
            }
        )
    return parsed

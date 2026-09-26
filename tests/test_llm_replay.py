"""Recorded LLM responses replay without a key, network or cost."""

import json

import pytest

from app.adapters.llm import LLMUnavailable, OpenAICompatibleLLM, agent_enabled, request_key


def _live_response(content):
    return {"model": "recorded-model", "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def test_record_then_replay_without_key(tmp_path, monkeypatch):
    cassette = tmp_path / "cassette.json"
    monkeypatch.setenv("REPLAN_LLM_CASSETTE", str(cassette))
    monkeypatch.setenv("REPLAN_LLM_MODE", "record")
    sent = []
    monkeypatch.setattr(OpenAICompatibleLLM, "_request",
                        lambda self, _httpx, payload, _headers: sent.append(payload) or _live_response('{"ok": 1}'))
    recording = OpenAICompatibleLLM(api_key="k", model="m", base_url="https://gateway.invalid/v1")
    messages = [{"role": "user", "content": "run 0123456789abcdef0123456789abcdef at 2026-09-26T09:05:38.797199+00:00"}]
    assert recording.chat(messages).content == '{"ok": 1}'
    assert len(sent) == 1
    stored = json.loads(cassette.read_text(encoding="utf-8"))["entries"]
    assert len(stored) == 1 and "API_KEY" not in cassette.read_text(encoding="utf-8")

    monkeypatch.setenv("REPLAN_LLM_MODE", "replay")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(OpenAICompatibleLLM, "_request", lambda *_args: pytest.fail("replay must not call the gateway"))
    assert agent_enabled()
    replaying = OpenAICompatibleLLM(api_key="", model=None, base_url=None)
    # Another run of the same flow has a different run ID and timestamp.
    again = [{"role": "user", "content": "run fedcba9876543210fedcba9876543210 at 2026-09-27T01:02:03.000001+00:00"}]
    result = replaying.chat(again)
    assert result.content == '{"ok": 1}'
    assert result.usage["prompt_tokens"] == 10

    with pytest.raises(LLMUnavailable, match="replay miss"):
        replaying.chat([{"role": "user", "content": "a changed prompt"}])


def test_request_key_ignores_model_but_not_prompt():
    base = {"model": "a", "messages": [{"role": "user", "content": "x"}]}
    assert request_key(base) == request_key({**base, "model": "b"})
    assert request_key(base) != request_key({**base, "messages": [{"role": "user", "content": "y"}]})


def test_agent_enabled_requires_opt_in_for_live(monkeypatch):
    monkeypatch.delenv("REPLAN_LLM_MODE", raising=False)
    for key in ("API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.setenv(key, "set")
    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "false")
    assert not agent_enabled()
    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "true")
    assert agent_enabled()

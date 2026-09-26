import json

from app.adapters.llm import ChatResult, OpenAICompatibleLLM
from app.agent.runtime import run_agent


class FakeGateway:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def chat(self, messages, tools=None, response_format=None):
        self.requests.append({"messages": messages, "tools": tools, "response_format": response_format})
        if not self.replies:
            raise AssertionError("unexpected gateway call")
        return self.replies.pop(0)


def test_run_agent_returns_llm_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    calls = []

    def inspect_task(task_id):
        calls.append(task_id)

    result = run_agent({}, {"content": "일정 영향 확인"}, {"inspect_task": inspect_task})

    assert result["status"] == "llm_unavailable"
    assert result["tool_log"] == []
    assert calls == []


def test_json_action_fallback_executes_allowed_tool_then_returns_final():
    gateway = FakeGateway(
        [
            ChatResult(
                content=json.dumps(
                    {"action": "tool", "tool": "inspect_task", "args": {"task_id": "T03"}},
                    ensure_ascii=False,
                ),
                usage={"prompt_tokens": 10},
            ),
            ChatResult(
                content=json.dumps(
                    {
                        "summary": "T03 영향 확인 완료",
                        "impacted_tasks": ["T03"],
                        "options": [{"id": "delay_check"}],
                        "required_actions": [],
                        "unresolved_items": [],
                        "status": "completed",
                    },
                    ensure_ascii=False,
                ),
                usage={"completion_tokens": 5},
            ),
        ]
    )

    def inspect_task(task_id):
        return {"task_id": task_id, "days": 2}

    result = run_agent({"_llm_gateway": gateway}, {"content": "T03 지연"}, {"inspect_task": inspect_task})

    assert result["status"] == "completed"
    assert result["summary"] == "T03 영향 확인 완료"
    assert result["impacted_tasks"] == ["T03"]
    assert result["tool_log"] == [
        {"tool": "inspect_task", "args": {"task_id": "T03"}, "status": "ok", "result": {"task_id": "T03", "days": 2}}
    ]
    assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}


def test_native_tool_call_is_supported():
    gateway = FakeGateway(
        [
            ChatResult(tool_calls=[{"name": "inspect_task", "arguments": {"task_id": "T04"}}]),
            ChatResult(content=json.dumps({"summary": "done", "status": "completed"})),
        ]
    )

    def inspect_task(task_id):
        return {"task_id": task_id}

    result = run_agent({"_llm_gateway": gateway}, {"content": "FAT 확인"}, {"inspect_task": inspect_task})

    assert result["status"] == "completed"
    assert result["tool_log"][0]["tool"] == "inspect_task"
    assert result["tool_log"][0]["result"] == {"task_id": "T04"}


def test_commit_and_send_tools_are_never_exposed_or_executed():
    gateway = FakeGateway(
        [
            ChatResult(
                content=json.dumps(
                    {"action": "tool", "tool": "send_update", "args": {"message": "go"}},
                    ensure_ascii=False,
                )
            )
        ]
    )
    calls = []

    def send_update(message):
        calls.append(message)

    def inspect_task(task_id):
        return {"task_id": task_id}

    result = run_agent(
        {"_llm_gateway": gateway},
        {"content": "업데이트 발송"},
        {"send_update": send_update, "inspect_task": inspect_task},
    )

    exposed_names = [tool["function"]["name"] for tool in gateway.requests[0]["tools"]]
    assert "send_update" not in exposed_names
    assert result["status"] == "blocked_action"
    assert calls == []


def test_repeated_tool_call_guard_blocks_duplicate_call():
    gateway = FakeGateway(
        [
            ChatResult(content=json.dumps({"action": "tool", "tool": "inspect_task", "args": {"task_id": "T03"}})),
            ChatResult(content=json.dumps({"action": "tool", "tool": "inspect_task", "args": {"task_id": "T03"}})),
        ]
    )

    def inspect_task(task_id):
        return {"task_id": task_id}

    result = run_agent({"_llm_gateway": gateway}, {"content": "반복 확인"}, {"inspect_task": inspect_task})

    assert result["status"] == "repeated_tool_call"
    assert len(result["tool_log"]) == 1


def test_invalid_tool_args_are_rejected_before_execution():
    gateway = FakeGateway(
        [ChatResult(content=json.dumps({"action": "tool", "tool": "inspect_task",
                                        "args": {"unknown": "T03"}})) for _ in range(3)]
    )
    calls = []

    def inspect_task(task_id):
        calls.append(task_id)

    result = run_agent({"_llm_gateway": gateway}, {"content": "bad args"}, {"inspect_task": inspect_task})

    assert result["status"] == "invalid_tool_args"
    assert calls == []
    assert len(gateway.requests) == 3
    assert len(result["tool_log"]) == 3
    assert all(entry["status"] == "invalid_tool_args" and entry["tool"] == "inspect_task"
               and entry["args"] == {"unknown": "T03"} and entry["error"] for entry in result["tool_log"])


def test_invalid_tool_args_can_be_corrected_within_tool_limit():
    gateway = FakeGateway([
        ChatResult(content=json.dumps({"action": "tool", "tool": "inspect_task",
                                       "args": {"task_id": "T03", "unused": True}})),
        ChatResult(content=json.dumps({"action": "tool", "tool": "inspect_task",
                                       "args": {"task_id": "T03"}})),
        ChatResult(content=json.dumps({"status": "completed", "summary": "done"})),
    ])
    calls = []

    def inspect_task(task_id):
        calls.append(task_id)
        return {"task_id": task_id}

    result = run_agent({"_llm_gateway": gateway}, {"content": "T03 확인"},
                       {"inspect_task": inspect_task}, max_steps=3)

    assert result["status"] == "completed"
    assert calls == ["T03"]
    assert [entry["status"] for entry in result["tool_log"]] == ["invalid_tool_args", "ok"]
    assert any("unused" in message.get("content", "") and "invalid_tool_args" in message.get("content", "")
               for message in gateway.requests[1]["messages"])


def test_invalid_arg_retry_respects_tool_call_limit():
    gateway = FakeGateway([
        ChatResult(content=json.dumps({"action": "tool", "tool": "inspect_task",
                                       "args": {"unused": True}})) for _ in range(2)
    ])
    result = run_agent({"_llm_gateway": gateway}, {}, {"inspect_task": lambda task_id: None}, max_steps=1)
    assert result["status"] == "invalid_tool_args"
    assert len(result["tool_log"]) == 1
    assert len(gateway.requests) == 1


def test_matching_redundant_project_id_is_ignored_for_project_scoped_tool():
    gateway = FakeGateway([
        ChatResult(content=json.dumps({"action": "tool", "tool": "get_project_context",
                                       "args": {"project_id": "P-123"}})),
        ChatResult(content=json.dumps({"summary": "done", "status": "completed"})),
    ])

    result = run_agent({"_llm_gateway": gateway, "project": {"project_id": "P-123"}},
                       {"content": "일정 확인"}, {"get_project_context": lambda: {"task_count": 2}})

    assert result["status"] == "completed"
    assert result["tool_log"][0]["args"] == {}
    assert result["tool_log"][0]["result"] == {"task_count": 2}


def test_other_project_id_is_rejected_before_tool_execution():
    gateway = FakeGateway([
        ChatResult(content=json.dumps({"action": "tool", "tool": "get_project_context",
                                       "args": {"project_id": "P-OTHER"}})),
        ChatResult(content=json.dumps({"action": "tool", "tool": "get_project_context",
                                       "args": {"project_id": "P-OTHER"}})),
        ChatResult(content=json.dumps({"action": "tool", "tool": "get_project_context",
                                       "args": {"project_id": "P-OTHER"}})),
    ])
    calls = []

    def get_project_context():
        calls.append(True)
        return {}

    result = run_agent({"_llm_gateway": gateway, "project": {"project_id": "P-123"}},
                       {"content": "일정 확인"}, {"get_project_context": get_project_context})

    assert result["status"] == "invalid_tool_args"
    assert calls == []
    assert result["tool_log"][0]["args"] == {"project_id": "P-OTHER"}
    assert result["tool_log"][0]["error"] == "project_id does not match the active project"


def test_draft_numbers_and_post_notice_regulatory_evidence_are_filtered():
    gateway = FakeGateway([
        ChatResult(content=json.dumps({"action": "tool", "tool": "simulate_schedule", "args": {}})),
        ChatResult(content=json.dumps({"action": "tool", "tool": "search_risk_signals", "args": {}})),
        ChatResult(content=json.dumps({
            "status": "completed", "summary": "검토 완료",
            "email_draft": {"subject": "협의 초안", "body": "완료일 2026-12-28, 추가 9일과 999원 검토"},
            "regulatory_assessment": {"likelihood": "높음", "reason": "규정 사례 참고",
                                      "human_check": "적용 조항 확인", "evidence_risk_ids": ["RS-FUTURE"]},
        }, ensure_ascii=False)),
    ])

    def simulate_schedule():
        return {"finish_date": "2026-12-28", "finish_shift_days": 9}

    def search_risk_signals():
        return {"results": [{"risk_id": "RS-FUTURE", "published_date": "2026-01-01"}]}

    result = run_agent({"_llm_gateway": gateway},
                       {"published_at": "2025-08-21", "content": "규정 적용 여부는 확인되지 않았습니다."},
                       {"simulate_schedule": simulate_schedule, "search_risk_signals": search_risk_signals})
    assert "2026-12-28" in result["email_draft"]["body"]
    assert "9일" in result["email_draft"]["body"]
    assert "999" not in result["email_draft"]["body"]
    assert result["regulatory_assessment"]["likelihood"] == "불확실"
    assert result["regulatory_assessment"]["evidence_risk_ids"] == []
    assert result["regulatory_assessment"]["reference_only_risk_ids"] == ["RS-FUTURE"]


def test_structured_agent_summary_uses_calculator_and_unrelated_regulation_is_hidden():
    gateway = FakeGateway([
        ChatResult(content=json.dumps({"action": "tool", "tool": "recheck_shifted_schedule", "args": {}})),
        ChatResult(content=json.dumps({"summary": {"change": "untrusted 99 days"},
                                       "regulatory_assessment": {"likelihood": "높음", "reason": "unrelated"},
                                       "status": "completed"})),
    ])

    def recheck_shifted_schedule():
        return {"finish_date": "2028-01-25", "target_met": False, "supplier_finish_shift_days": 21}

    result = run_agent({"_llm_gateway": gateway}, {"content": "설비 반입이 지연됩니다."},
                       {"recheck_shifted_schedule": recheck_shifted_schedule})

    assert result["summary"] == "통보의 일정 영향을 검토했습니다. 계산된 완료 예정일은 2028-01-25이며, 목표일을 충족하지 못합니다."
    assert result["regulatory_assessment"] is None
    assert "99" not in result["summary"]


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeHTTPStatusError(Exception):
    def __init__(self, status_code):
        super().__init__("status %s" % status_code)
        self.response = FakeResponse(status_code)


class FakeHttpx:
    HTTPStatusError = FakeHTTPStatusError


def test_llm_gateway_requires_explicit_model_and_base_url(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    result = run_agent({}, {"content": "일정 영향 확인"}, {})

    assert result["status"] == "llm_unavailable"
    assert "LLM_MODEL is not configured" in result["unresolved_items"]


def test_llm_gateway_retries_400_without_unsupported_fields():
    gateway = OpenAICompatibleLLM(api_key="key", model="approved-model", base_url="https://gateway.test")
    calls = []

    def fake_request(_httpx, payload, _headers):
        calls.append(payload)
        if len(calls) == 1:
            raise FakeHTTPStatusError(400)
        return {"choices": [{"message": {"content": "{}"}}]}

    gateway._request = fake_request

    result = gateway._post_chat(
        FakeHttpx,
        {
            "model": "approved-model",
            "messages": [],
            "tools": [{"type": "function"}],
            "tool_choice": "auto",
            "response_format": {"type": "json_object"},
        },
        {"Authorization": "Bearer key"},
        allow_json_fallback=True,
    )

    assert result == {"choices": [{"message": {"content": "{}"}}]}
    assert "tools" in calls[0]
    assert "tools" not in calls[1]
    assert "tool_choice" not in calls[1]
    assert "response_format" not in calls[1]


def test_llm_gateway_does_not_fallback_on_auth_or_rate_limit_errors():
    gateway = OpenAICompatibleLLM(api_key="key", model="approved-model", base_url="https://gateway.test")
    calls = []

    def fake_request(_httpx, payload, _headers):
        calls.append(payload)
        raise FakeHTTPStatusError(429)

    gateway._request = fake_request

    try:
        gateway._post_chat(
            FakeHttpx,
            {"model": "approved-model", "messages": [], "tools": [{"type": "function"}]},
            {"Authorization": "Bearer key"},
            allow_json_fallback=True,
        )
    except FakeHTTPStatusError:
        pass
    else:
        raise AssertionError("expected rate-limit error")

    assert len(calls) == 1

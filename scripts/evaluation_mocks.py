"""Offline gateway fixtures for CI; not a measurement of an actual model."""

from __future__ import annotations

import json
import re

from app.adapters.llm import ChatResult
from app.events import _extract_dates


class MockEvaluationGateway:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], **_: object) -> ChatResult:
        self.calls += 1
        initial = json.loads(messages[1]["content"]) if len(messages) > 1 and messages[0].get("role") == "system" else {}
        if "allowed_tools" in initial and "event" in initial:
            output = self._agent(initial, messages)
        else:
            payload = json.loads(messages[-1]["content"])
            if "message" in payload:
                output = self._supplier(payload)
            else:
                output = self._related(payload)
        return ChatResult(content=json.dumps(output, ensure_ascii=False), model="offline-semantic-fixture")

    def _agent(self, initial: dict, messages: list[dict]) -> dict:
        event = initial["event"]
        called = [json.loads(row["content"][13:]) for row in messages
                  if row.get("role") == "user" and row.get("content", "").startswith("Tool result: ")]
        completed = [row for row in called if row.get("status") == "ok"]
        if not event.get("patch"):
            return {"status": "needs_input", "summary": "작업과 변경 날짜 확인이 필요합니다.",
                    "stop_reason": "작업 또는 날짜가 불명확합니다.",
                    "unresolved_items": ["영향 작업과 변경 날짜를 확인해 주세요."]}
        risk = any(word in event.get("content", "") for word in ("규제", "규정", "인허가", "허가"))
        sequence = ["simulate_schedule", "recheck_shifted_schedule"] + (["search_risk_signals", "simulate_regulatory_condition"] if risk else []) + ["list_response_options"]
        if len(completed) < len(sequence):
            name = sequence[len(completed)]
            args = {"option_ids": []} if name in {"simulate_schedule", "recheck_shifted_schedule", "simulate_regulatory_condition"} else {"risk_type": "regulation"} if name == "search_risk_signals" else {}
            case_id = event.get("event_id")
            if case_id == "H04" and not completed:
                args["project_id"] = initial["context"]["project"]["project_id"]
            if case_id == "V03" and not completed and not called:
                args["unused_argument"] = True
            return {"action": "tool", "tool": name, "args": args}
        calculator = completed[1].get("result") or {}
        draft = f"변경 통보를 확인했습니다. 계산 결과의 완료 예정일 {calculator.get('finish_date', '')}을 기준으로 대응안을 협의하고 싶습니다."
        return {"status": "completed", "summary": "도구 결과를 검토했습니다.",
                "email_draft": {"subject": "일정 변경 협의 초안", "body": draft},
                "option_explanations": ["일정 단축안은 승인 조건과 비용을 확인해야 합니다."],
                "regulatory_assessment": {"likelihood": "불확실", "reason": "적용 여부가 확인되지 않았습니다.",
                    "human_check": "관할 기관과 적용 조항을 확인해 주세요.",
                    "evidence_risk_ids": [row["risk_id"] for row in (completed[2].get("result") or {}).get("results", [])] if risk else []}}

    def _supplier(self, payload: dict) -> dict:
        body = payload["message"]
        ids = [task["task_id"] for task in payload["tasks"] if re.search(rf"(?<!\w){re.escape(task['task_id'])}(?!\w)", body)]
        dates = _extract_dates(body, 2026)
        contact = any(word in body.lower() for word in ("연락처", "이메일", "email", "contact"))
        unchanged = any(word in body.lower() for word in ("변경되지", "그대로", "동일", "unchanged"))
        if not ids or not dates or contact and unchanged:
            return {"proposals": [], "no_schedule_impact": contact and unchanged,
                    "questions": [] if contact and unchanged else ["변경할 작업 ID와 날짜를 명확히 알려주세요."]}
        kind = "not_before" if any(word in body.lower() for word in ("착수", "시작", "개시", "start")) else "estimated_finish"
        return {"proposals": [{"task_id": task_id, "kind": kind, "date": dates[-1], "quote": body}
                              for task_id in ids], "questions": [], "no_schedule_impact": False}

    def _related(self, payload: dict) -> dict:
        text = payload["narrative"]
        lowered = text.lower()
        tasks = payload["tasks"]
        selected = []
        if "셀 설비 제작" in text:
            selected = [row for row in tasks if str(row.get("name") or "").lower() == "cell equipment manufacturing"]
            quote = "셀 설비 제작"
        elif "모듈 설비 제작" in text:
            selected = [row for row in tasks if str(row.get("name") or "").lower() == "module/pack equipment manufacturing"]
            quote = "모듈 설비 제작"
        elif "셀 설비 인수시험" in text:
            selected = [row for row in tasks if str(row.get("name") or "").lower() == "factory acceptance test - cell equipment"]
            quote = "셀 설비 인수시험"
        elif "설비가 현장에 도착" in text:
            selected = [row for row in tasks if str(row.get("name") or "").lower() == "equipment delivery to site"]
            quote = "설비가 현장에 도착"
        elif "installation" in lowered or "commissioning work" in lowered:
            selected = [row for row in tasks if "equipment installation" in str(row.get("name") or "").lower()]
            quote = "installation" if "installation" in lowered else "commissioning work"
        elif "clearing trees" in lowered:
            selected = [row for row in tasks if "site preparation" in str(row.get("name") or "").lower()]
            quote = "clearing trees"
        elif "cell-production lines" in lowered:
            selected = [row for row in tasks if "trial production - cell" in str(row.get("name") or "").lower()]
            quote = "cell-production lines"
        elif "construction" in lowered and ("labor" in lowered or "funding" in lowered):
            selected = [row for row in tasks if any(word in str(row.get("name") or "").lower() for word in ("foundation", "structural steel"))]
            quote = "construction"
        else:
            return {"candidates": []}
        # Whole public narrative is always an exact supporting excerpt.
        return {"candidates": [{"task_id": row["task_id"], "quote": text,
                 "relevance": "high", "reason": f"인용한 공정이 {row.get('name')} 작업 단계와 직접 관련됩니다."}
                for row in selected[:3]]}

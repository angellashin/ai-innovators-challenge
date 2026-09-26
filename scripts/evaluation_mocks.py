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
        payload = json.loads(messages[-1]["content"])
        if "message" in payload:
            output = self._supplier(payload)
        else:
            output = self._related(payload)
        return ChatResult(content=json.dumps(output, ensure_ascii=False), model="offline-semantic-fixture")

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
        return {"candidates": [{"task_id": row["task_id"], "quote": text} for row in selected[:5]]}

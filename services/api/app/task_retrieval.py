"""Quote-validated semantic task suggestions for real-case evaluation."""

from __future__ import annotations

import json
from typing import Any


def retrieve_related_tasks(narrative: str, tasks: list[dict], gateway: Any,
                           risk_cases: list[dict] | None = None) -> dict:
    public_tasks = [{"task_id": str(task["task_id"]), "name": task.get("name"),
                     "phase": task.get("phase"), "risk_tags": task.get("risk_tags")}
                    for task in tasks]
    try:
        reply = gateway.chat([
            {"role": "system", "content": (
                "Map a real-world risk narrative to analogous project work stages. "
                "Return JSON {candidates:[{task_id,quote}]}. Use only given task IDs. "
                "Each quote must be an exact substring of narrative supporting the stage. "
                "Do not use delay amounts or dates and do not claim the real case happened in this project. "
                "Return [] when no analogous task is supported."
            )},
            {"role": "user", "content": json.dumps({"narrative": narrative, "tasks": public_tasks,
                                                 "analogous_risk_cases": risk_cases or []}, ensure_ascii=False)},
        ], response_format={"type": "json_object"})
        payload = json.loads(reply.content)
        allowed = {item["task_id"] for item in public_tasks}
        cases = []
        for item in payload.get("candidates", [])[:10]:
            if not isinstance(item, dict):
                continue
            task_id, quote = str(item.get("task_id") or ""), str(item.get("quote") or "")
            if task_id in allowed and len(quote) >= 8 and quote in narrative:
                cases.append({"task_id": task_id, "quote": quote})
        return {"task_ids": sorted({item["task_id"] for item in cases}), "candidates": cases,
                "usage": reply.usage, "model": reply.model}
    except Exception as exc:
        return {"task_ids": [], "candidates": [], "error": type(exc).__name__, "usage": {}}

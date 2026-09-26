"""The reviewed synthetic hero demo, kept separate from REPLAY test inputs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


HERO_PROJECT_ID = "HERO-BAT-HU-001"
_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "hero_demo" / "supplier_messages.json"
_OPTIONS = _FIXTURE.with_name("response_options.json")
HERO_WORKBOOK = _FIXTURE.parents[1] / "l1_project" / "hero_battery_factory_project.xlsx"


def hero_fixture(parsed: dict[str, Any]) -> dict[str, Any] | None:
    project = parsed.get("project") or {}
    tasks = parsed.get("tasks") or []
    if (project.get("project_id") != HERO_PROJECT_ID
            or project.get("data_origin") != "SYNTHETIC"
            or {str(task.get("task_id")) for task in tasks} != {f"T{number:03d}" for number in range(1, 65)}):
        return None
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def hero_response_options(project: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return synthetic options only for the recognized, complete hero baseline."""
    if (project.get("hero_fixture_id") != HERO_PROJECT_ID
            or project.get("data_origin") != "SYNTHETIC"
            or {str(task.get("task_id")) for task in tasks} != {f"T{number:03d}" for number in range(1, 65)}):
        return []
    return json.loads(_OPTIONS.read_text(encoding="utf-8"))


def status_at(task: dict[str, Any], as_of: str) -> str:
    point = date.fromisoformat(as_of)
    start = date.fromisoformat(str(task["baseline_start"])[:10])
    finish = date.fromisoformat(str(task["baseline_finish"])[:10])
    if finish <= point:
        return "completed"
    if start <= point:
        return "in_progress"
    return "planned"

"""Search curated L2 cases as context, never as project delay estimates."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[3] / "data" / "l2_risk_signals" / "risk_signals.json"
CAUSE_WORDS = {
    "regulation": ("규제", "규정", "환경", "배터리법", "regulation", "environmental"),
    "permitting": ("인허가", "허가", "승인", "permit", "license"),
    "labor": ("인력", "노동", "기사", "비자", "인원", "labor", "worker", "engineer"),
    "logistics": ("물류", "운송", "통관", "선적", "항만", "shipping", "customs", "transport"),
}
STAGE_WORDS = {
    "installation": ("설치", "투입", "installation", "commissioning", "시운전"),
    "construction": ("건설", "공사", "construction", "building"),
    "production": ("제작", "생산", "manufacturing", "production"),
    "logistics": ("운송", "통관", "반입", "shipment", "shipping", "logistics"),
}
COUNTRY_ALIASES = {"HU": "Hungary", "DE": "Germany", "KR": "South Korea"}


@lru_cache(maxsize=1)
def _records() -> tuple[dict, ...]:
    return tuple(json.loads(CORPUS.read_text(encoding="utf-8"))["records"])


def mentioned_risk_types(content: str) -> list[str]:
    lowered = content.lower()
    return [kind for kind, words in CAUSE_WORDS.items() if any(word in lowered for word in words)]


def search_risk_signals(risk_type: str = "", country: str = "", stage: str = "",
                        keywords: str = "", limit: int = 5,
                        exclude_source_risk_id: str = "") -> dict:
    """Filter L2 real cases by risk type, country, project stage and keywords."""
    tokens = [term.lower() for term in re.split(r"[\s,]+", keywords) if term]
    requested_type = risk_type.lower().strip()
    requested_stage = stage.lower().strip()
    result = []
    for row in _records():
        if exclude_source_risk_id and str(row.get("risk_id")) == exclude_source_risk_id:
            continue
        # A displayed citation needs both a resolvable URL and publication date.
        # Null publication dates in L2 are not inferred from event dates.
        if not str(row.get("source_url") or "").startswith("https://") or not row.get("published_date"):
            continue
        text = " ".join(str(row.get(key) or "") for key in
                        ("title", "event_summary", "risk_category", "risk_subcategory", "project_type", "country", "region")).lower()
        category = str(row.get("risk_category") or "").lower()
        if requested_type and requested_type not in category and not any(word in text for word in CAUSE_WORDS.get(requested_type, (requested_type,))):
            continue
        normalized_country = COUNTRY_ALIASES.get(country.upper(), country)
        if normalized_country and normalized_country.lower() not in text:
            continue
        if requested_stage and not any(word in text for word in STAGE_WORDS.get(requested_stage, (requested_stage,))):
            continue
        if tokens and not all(token in text for token in tokens):
            continue
        result.append({"risk_id": row["risk_id"], "title": row["title"],
                       "risk_type": category, "country": row.get("country"),
                       "project_stage": requested_stage or None,
                       "summary": row.get("event_summary"), "source_url": row.get("source_url"),
                       "published_date": row.get("published_date"), "verification_grade": row.get("verification_grade"),
                       "usage_note": "유사 위험 유형의 실제 사례이며 이 프로젝트의 적용 여부나 지연 일수를 뜻하지 않습니다."})
    return {"results": result[:max(0, min(limit, 10))], "total": len(result)}


def evidence_for_supplier(content: str, tasks: list[dict], related_task_ids: list[str],
                          as_of: str = "") -> list[dict]:
    kinds = mentioned_risk_types(content)
    if not kinds:
        return []
    linked = [task for task in tasks if str(task.get("task_id")) in related_task_ids]
    stage_text = " ".join(str(task.get("name") or "") + " " + str(task.get("phase") or "") for task in linked).lower()
    stage = next((key for key, words in STAGE_WORDS.items() if any(word in stage_text for word in words)), "")
    country = str(linked[0].get("country") or "") if linked else ""
    cases = []
    for kind in kinds:
        # Country is a relevance boost, not a hard requirement: cross-border
        # examples remain useful and must never be treated as local law.
        rows = search_risk_signals(risk_type=kind, stage=stage, limit=10)["results"]
        if not rows:
            rows = search_risk_signals(risk_type=kind, limit=10)["results"]
        rows.sort(key=lambda item: (country.lower() not in str(item.get("country") or "").lower(), item["risk_id"]))
        cases.extend(rows[:2])
    unique = list({item["risk_id"]: item for item in cases}.values())
    for item in unique:
        future = bool(as_of and str(item["published_date"]) > str(as_of)[:10])
        item["temporal_status"] = "POST_AS_OF_REFERENCE" if future else "AVAILABLE_AS_OF"
        if future:
            item["usage_note"] = "통보 시점 이후 발행된 후향적 참고 사례입니다. 당시 판단·일정 계산의 근거로 사용하지 않습니다."
    return unique


KIND_LABEL = {"regulation": "규제", "permitting": "인허가·허가", "labor": "인력", "logistics": "물류·통관"}


def case_relevance(content: str, risk_id: str) -> dict:
    """Outlet name and a one-line reason a stored case sits next to this notice (display only)."""
    record = next((row for row in _records() if row.get("risk_id") == risk_id), None)
    if not record:
        return {}
    lowered = content.lower()
    text = " ".join(str(record.get(key) or "") for key in
                    ("title", "event_summary", "risk_category", "risk_subcategory", "project_type", "country", "region")).lower()
    category = str(record.get("risk_category") or "").lower()
    why = ""
    for kind in mentioned_risk_types(content):
        if kind in category or any(word in text for word in CAUSE_WORDS[kind]):
            words = [word for word in CAUSE_WORDS[kind] if word in lowered][:2]
            quoted = "·".join(f"‘{word}’" for word in words)
            why = f"통보에 나온 {quoted} → 같은 {KIND_LABEL[kind]} 위험 유형의 실제 사례"
            break
    return {"source_name": record.get("source_name"), "why": why}

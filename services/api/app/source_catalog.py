"""Small, explicit catalog of server-approved public sources.

The catalog is a product policy, not an LLM output.  It lets a new project
start with a useful, auditable collection plan while keeping arbitrary URLs
out of the collector.  A source is included only when the imported schedule
contains matching work and country context.
"""

from __future__ import annotations

from typing import Any

EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}
COUNTRY_CODES = {"Hungary": "HU", "South Korea": "KR", "Korea": "KR", "Germany": "DE", "헝가리": "HU", "한국": "KR", "독일": "DE"}

CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "eu-environment-news",
        "title": "EU 환경총국 공지",
        "url": "https://environment.ec.europa.eu/news_en",
        "countries": EU_COUNTRIES,
        "risk_tags": {"regulation", "permitting", "environmental"},
        "keywords": ["environmental", "battery", "permit"],
        "reason": "환경·인허가 관련 일정 작업과 연결된 EU 공식 공지를 확인합니다.",
    },
    {
        "id": "eu-trade-news",
        "title": "EU 통상총국 공지",
        "url": "https://policy.trade.ec.europa.eu/news_en",
        "countries": EU_COUNTRIES,
        "risk_tags": {"customs", "export", "trade", "supply_chain"},
        "keywords": ["customs", "export", "trade", "battery"],
        "reason": "통관·수출입·공급망 관련 일정 작업과 연결된 EU 공식 공지를 확인합니다.",
    },
)


def catalog_hosts() -> set[str]:
    from urllib.parse import urlparse

    return {str(urlparse(item["url"]).hostname).lower() for item in CATALOG}


def source_catalog_for(project: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only catalog sources that match concrete schedule work."""
    selected: list[dict[str, Any]] = []
    default_country = COUNTRY_CODES.get(str(project.get("country") or ""), str(project.get("country_code") or "").upper())
    for entry in CATALOG:
        task_ids: list[str] = []
        for task in tasks:
            country = COUNTRY_CODES.get(str(task.get("country") or ""), str(task.get("country_code") or default_country).upper())
            raw_tags = task.get("risk_tags") or []
            tags = {str(tag).strip().lower() for tag in (raw_tags.split(",") if isinstance(raw_tags, str) else raw_tags)}
            if country in entry["countries"] and tags.intersection(entry["risk_tags"]):
                task_ids.append(str(task["task_id"]))
        if task_ids:
            selected.append({**entry, "task_ids": sorted(set(task_ids))})
    return selected

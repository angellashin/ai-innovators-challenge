"""Project-scoped evidence retrieval for external-risk review.

This module deliberately retrieves only text the server has already collected or
received as an uploaded document. It never gives an LLM a browser or arbitrary
HTTP access. SQLite storage uses transparent lexical ranking first; the returned
passage contract is stable enough for a later vector-store implementation.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .storage import Store, digest, utcnow

MAX_PASSAGE_CHARS = 1_200
MAX_RETRIEVED_PASSAGES = 8
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣_-]+")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tokens(value: Any) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(_clean_text(value)) if len(token) >= 2]


def _unique(values: list[str], limit: int = 160) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _clean_text(value)
        key = normalized.casefold()
        if normalized and key not in seen:
            result.append(normalized)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _chunks(text: str) -> list[str]:
    """Make citation-sized, deterministic chunks without losing original wording."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-Z가-힣])", text) if part.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > MAX_PASSAGE_CHARS:
            parts = [paragraph[index:index + MAX_PASSAGE_CHARS] for index in range(0, len(paragraph), MAX_PASSAGE_CHARS)]
        else:
            parts = [paragraph]
        for part in parts:
            candidate = f"{current}\n\n{part}".strip() if current else part
            if current and len(candidate) > MAX_PASSAGE_CHARS:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def index_evidence(
    db: Store,
    project_id: str,
    source: dict[str, Any],
    *,
    origin: str,
) -> dict[str, Any]:
    """Persist immutable source content and citation passages once per content hash."""
    content = _clean_text(source.get("content") or source.get("summary") or source.get("title"))
    if not content:
        raise ValueError("evidence source has no readable content")
    source_id = str(source.get("source_id") or source.get("url") or source.get("document_id") or "unknown")
    content_hash = str(source.get("body_hash") or digest(content))
    existing = db.find_evidence_document(project_id, source_id, content_hash)
    if existing:
        return {"document_id": existing["id"], **existing["data"], "indexed": False}

    document_id = digest({"project_id": project_id, "source_id": source_id, "content_hash": content_hash})[:32]
    document = {
        "document_id": document_id,
        "source_id": source_id,
        "source_url": source.get("url"),
        "title": _clean_text(source.get("title")) or "외부 근거 문서",
        "published_at": source.get("published_at"),
        "fetched_at": source.get("fetched_at") or utcnow(),
        "content_hash": content_hash,
        "origin": origin,
        "language": source.get("language"),
        "passage_count": 0,
    }
    chunks = _chunks(content)
    document["passage_count"] = len(chunks)
    db.put_json("evidence_documents", document_id, document, project_id=project_id,
                source_id=source_id, content_hash=content_hash)
    for index, text in enumerate(chunks):
        passage_hash = digest(text)
        passage_id = digest({"document_id": document_id, "passage_hash": passage_hash})[:32]
        passage = {
            "passage_id": passage_id,
            "citation_id": f"P-{passage_id[:12]}",
            "document_id": document_id,
            "index": index,
            "text": text,
            "title": document["title"],
            "source_url": document["source_url"],
            "published_at": document["published_at"],
        }
        db.put_json("evidence_passages", passage_id, passage, project_id=project_id,
                    document_id=document_id, passage_hash=passage_hash)
    return {"document_id": document_id, **document, "indexed": True}


def build_project_query(tasks: list[dict[str, Any]], watch_plan: dict[str, Any] | None = None) -> list[str]:
    """Build auditable retrieval terms from the approved schedule and watch plan."""
    values: list[str] = []
    for task in tasks:
        for key in ("task_id", "name", "task_name", "equipment_id", "supplier_id", "country", "country_code", "location", "phase"):
            values.append(str(task.get(key) or ""))
        tags = task.get("risk_tags") or []
        values.extend(str(tag) for tag in (tags.split(",") if isinstance(tags, str) else tags))
    plan = watch_plan or {}
    values.extend(str(term) for term in plan.get("public_search_terms") or [])
    for rule in plan.get("source_rules") or []:
        values.extend(str(term) for term in rule.get("keywords") or [])
    return _unique(values)


def _task_matches(text: str, tasks: list[dict[str, Any]]) -> list[str]:
    lowered = text.casefold()
    matches: list[str] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        phrases = [task_id, str(task.get("name") or task.get("task_name") or ""), str(task.get("equipment_id") or "")]
        tags = task.get("risk_tags") or []
        phrases.extend(tags.split(",") if isinstance(tags, str) else tags)
        if any(len(_clean_text(phrase)) >= 3 and _clean_text(phrase).casefold() in lowered for phrase in phrases):
            if task_id:
                matches.append(task_id)
    return sorted(set(matches))


def _entity_hits(text: str, tasks: list[dict[str, Any]]) -> list[str]:
    """Prefer exact project entities over generic keyword overlap."""
    lowered = text.casefold()
    hits: list[str] = []
    for task in tasks:
        for key in ("task_id", "equipment_id", "supplier_id", "country", "country_code", "location"):
            value = _clean_text(task.get(key))
            if len(value) >= 2 and value.casefold() in lowered:
                hits.append(value)
    return _unique(hits, 32)


def retrieve_evidence(
    db: Store,
    project_id: str,
    *,
    query_terms: list[str],
    tasks: list[dict[str, Any]],
    document_ids: set[str] | None = None,
    limit: int = MAX_RETRIEVED_PASSAGES,
    legacy_lexical: bool = False,
) -> dict[str, Any]:
    """Return project-scoped passages with transparent entity and lexical ranking."""
    terms = _unique(query_terms)
    query_tokens = _tokens(" ".join(terms))
    query_counts = Counter(query_tokens)
    ranked: list[dict[str, Any]] = []
    for row in db.list_json("evidence_passages", project_id, 2_000):
        passage = row["data"]
        if document_ids is not None and str(passage.get("document_id")) not in document_ids:
            continue
        text = _clean_text(passage.get("text"))
        text_lower = text.casefold()
        counts = Counter(_tokens(text))
        matching_tokens = sorted(token for token in query_counts if token in counts)
        phrase_hits = [term for term in terms if len(term) >= 3 and term.casefold() in text_lower]
        lexical_score = sum(min(counts[token], 3) for token in matching_tokens) + 4 * len(phrase_hits)
        task_ids = _task_matches(text, tasks)
        entity_hits = _entity_hits(text, tasks) if not legacy_lexical else []
        entity_score = 5 * len(entity_hits) + 3 * len(task_ids)
        score = lexical_score + entity_score
        if score <= 0:
            continue
        row = {
            "passage_id": passage["passage_id"], "citation_id": passage["citation_id"],
            "document_id": passage["document_id"], "text": text,
            "title": passage.get("title"), "source_url": passage.get("source_url"),
            "published_at": passage.get("published_at"), "score": score,
            "match_terms": phrase_hits[:12] or matching_tokens[:12], "task_ids": task_ids,
        }
        if not legacy_lexical:
            row.update({"entity_hits": entity_hits, "lexical_hits": phrase_hits[:12] or matching_tokens[:12],
                        "entity_score": entity_score, "lexical_score": lexical_score})
        ranked.append(row)
    ranked.sort(key=lambda item: (-item["score"], str(item["citation_id"])))
    selected = ranked[:max(1, min(limit, MAX_RETRIEVED_PASSAGES))]
    retrieval_id = digest({"project_id": project_id, "terms": terms, "passages": [item["passage_id"] for item in selected]})[:32]
    db.put_json("retrieval_runs", retrieval_id, {
        "retrieval_id": retrieval_id, "strategy": "lexical-v1" if legacy_lexical else "hybrid-entity-lexical-v1", "query_terms": terms,
        "passage_ids": [item["passage_id"] for item in selected],
        "citation_ids": [item["citation_id"] for item in selected],
        "created_at": utcnow(),
    }, project_id=project_id)
    strategy = "lexical-v1" if legacy_lexical else "hybrid-entity-lexical-v1"
    return {"retrieval_id": retrieval_id, "strategy": strategy, "query_terms": terms, "passages": selected}


def _merge_candidates(existing: list[dict[str, Any]], retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {str(row.get("task_id")): dict(row) for row in existing if row.get("task_id")}
    for passage in retrieved:
        for task_id in passage.get("task_ids") or []:
            candidate = merged.setdefault(task_id, {"task_id": task_id, "reasons": [], "confidence": "candidate"})
            reasons = candidate.setdefault("reasons", [])
            reason = "검색 근거 문단과 작업 맥락 일치: " + ", ".join(passage.get("match_terms") or ["작업 정보"])
            if reason not in reasons:
                reasons.append(reason)
            citations = candidate.setdefault("citation_ids", [])
            if passage["citation_id"] not in citations:
                citations.append(passage["citation_id"])
            candidate.setdefault("quote", passage["text"][:300])
    return sorted(merged.values(), key=lambda item: (-len(item.get("reasons") or []), str(item["task_id"])))


def ground_external_source(
    db: Store,
    project_id: str,
    source: dict[str, Any],
    *,
    tasks: list[dict[str, Any]],
    watch_plan: dict[str, Any] | None,
    evidence_kind: str,
    snapshot_id: str | None,
    origin: str,
    legacy_lexical: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Index one collected document, retrieve citations, and make review-only task candidates."""
    from .external_risks import evidence, match_notice

    document = index_evidence(db, project_id, source, origin=origin)
    retrieval = retrieve_evidence(
        db, project_id, query_terms=build_project_query(tasks, watch_plan), tasks=tasks,
        document_ids={document["document_id"]}, legacy_lexical=legacy_lexical,
    )
    matched = match_notice(source, tasks, (watch_plan or {}).get("source_rules", []))
    candidates = _merge_candidates(list(matched.get("candidates") or []), retrieval["passages"])
    matched["candidates"] = candidates
    matched["related_task_ids"] = [row["task_id"] for row in candidates]
    if candidates:
        matched["classification_status"] = "NEEDS_INPUT"
    proof = evidence(source, snapshot_id or document["document_id"], evidence_kind)
    proof.update({
        "document_id": document["document_id"], "document_hash": document["content_hash"],
        "retrieval_id": retrieval["retrieval_id"], "retrieval_strategy": retrieval["strategy"],
        "passages": retrieval["passages"],
    })
    return proof, matched

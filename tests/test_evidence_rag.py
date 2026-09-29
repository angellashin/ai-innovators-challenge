"""RAG regression tests use captured-style documents and a contract LLM, never paid network calls."""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from app.adapters.llm import ChatResult
from app.evidence_rag import ground_external_source
from app.main import app
from app.storage import Store, digest
from app.worker import run_once


PROJECT = {"name": "Battery plant construction", "target_finish": "2026-10-20", "mode": "LIVE"}
TASKS = [
    {"task_id": "T042", "name": "Cathode material customs clearance", "equipment_id": "CAM-01",
     "risk_tags": ["export license", "customs"], "baseline_start": "2026-10-07",
     "baseline_finish": "2026-10-08", "duration_workdays": 2, "predecessor_ids": []},
    {"task_id": "T043", "name": "Battery line commissioning", "baseline_start": "2026-10-09",
     "baseline_finish": "2026-10-09", "duration_workdays": 1, "predecessor_ids": ["T042"]},
]
WATCH = {"enabled": True, "public_search_terms": ["cathode material", "export license"],
         "source_rules": [{"url": "https://authority.example/notices", "keywords": ["export license"], "task_ids": ["T042"]}]}
NOTICE = (
    "Official notice: Cathode material customs clearance for battery plant equipment now requires an export license. "
    "The authority says the requirement applies from 2026-10-07."
)


def make_store(tmp_path) -> Store:
    db = Store(tmp_path)
    db.put_json("projects", "P", PROJECT)
    snapshot = {"project": PROJECT, "tasks": TASKS, "options": []}
    db.put_json("versions", "V", snapshot, project_id="P", parent_id=None, status="baseline", content_hash=digest(snapshot))
    db.put_json("watch_plans", "P", WATCH)
    return db


def authenticated_client(db: Store) -> TestClient:
    client = TestClient(app)
    registered = client.post("/api/auth/register", json={"username": "evidence_user", "password": "StrongPass123!"})
    assert registered.status_code == 200, registered.text
    project = db.get_json("projects", "P")
    db.put_json("projects", "P", {**project["data"], "owner_user_id": registered.json()["user"]["id"]}, created_at=project["created_at"])
    return client


def test_collected_notice_is_indexed_and_returns_cited_project_passages(tmp_path):
    db = make_store(tmp_path)
    source = {"source_id": "authority:notice-42", "url": "https://authority.example/notices/42",
              "title": "Export licence notice", "content": NOTICE, "body_hash": digest(NOTICE),
              "published_at": "2026-10-01T09:00:00+00:00", "fetched_at": "2026-10-01T10:00:00+00:00"}
    proof, matched = ground_external_source(
        db, "P", source, tasks=TASKS, watch_plan=WATCH, evidence_kind="PUBLIC_NOTICE",
        snapshot_id="snapshot-42", origin="PUBLIC",
    )

    assert proof["document_id"]
    assert proof["document_hash"] == digest(NOTICE)
    assert proof["passages"] and proof["passages"][0]["citation_id"].startswith("P-")
    assert proof["passages"][0]["source_url"] == source["url"]
    candidate = next(row for row in matched["candidates"] if row["task_id"] == "T042")
    assert candidate["citation_ids"] == [proof["passages"][0]["citation_id"]]
    assert db.get_json("evidence_documents", proof["document_id"], "P")


def test_uploaded_document_enters_the_same_cited_evidence_flow(tmp_path):
    db = make_store(tmp_path)
    document_id = "a" * 32
    content = NOTICE.encode()
    db.save_upload(document_id, content)
    db.put_json("documents", document_id, {
        "document_id": document_id, "filename": "authority-notice.txt", "status": "QUEUED",
        "sha256": __import__("hashlib").sha256(content).hexdigest(), "size_bytes": len(content),
    }, project_id="P")
    db.create_run("P", "document_ingest", None, "V", "document-rag", {"document_id": document_id})

    assert run_once(db)
    event = db.list_json("events", "P")[0]["data"]
    assert event["channel"] == "evidence_document"
    assert event["data_origin"] == "USER_DOCUMENT"
    assert event["patch"] == {}
    assert event["evidence"]["kind"] == "UPLOADED_DOCUMENT"
    assert event["evidence"]["passages"]
    assert "T042" in event["related_task_ids"]


class ContractLLM:
    """Exercises the live code path without a provider key or an external model call."""

    def __init__(self, **_settings):
        """Accepts the adapter's constructor settings, such as a longer read timeout."""

    def chat(self, messages, **_kwargs):
        system = str(messages[0].get("content") or "")
        if "Read external evidence" in system:
            source_text = json.loads(messages[1]["content"])["source_text"]
            citation = re.search(r"\[(P-[0-9a-f]+)\]", source_text).group(1)
            return ChatResult(content=json.dumps({"candidates": [{
                "task_id": "T042", "quote": "Cathode material customs clearance",
                "reason": "통관 작업과 수출허가 대상이 같은 문단에 명시됨", "citation_ids": [citation],
            }]}), usage={"prompt_tokens": 11, "completion_tokens": 7})
        return ChatResult(content=json.dumps({
            "summary": "근거 확인 후 계산된 대응안을 검토하세요.", "status": "completed", "stop_reason": "",
            "option_explanations": [], "regulatory_assessment": None, "email_draft": None, "unresolved_items": [],
        }), usage={"prompt_tokens": 13, "completion_tokens": 9})


def test_document_rag_llm_review_then_approval_commit_and_export(tmp_path, monkeypatch):
    """The full product path keeps a human confirmation between RAG and schedule calculation."""
    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test")
    monkeypatch.setenv("API_KEY", "contract-test")
    monkeypatch.setenv("LLM_MODEL", "contract-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "true")
    monkeypatch.setattr("app.adapters.llm.OpenAICompatibleLLM", ContractLLM)
    db = make_store(tmp_path)
    document_id = "b" * 32
    content = NOTICE.encode()
    db.save_upload(document_id, content)
    db.put_json("documents", document_id, {
        "document_id": document_id, "filename": "authority-notice.txt", "status": "QUEUED",
        "sha256": __import__("hashlib").sha256(content).hexdigest(), "size_bytes": len(content),
    }, project_id="P")
    db.create_run("P", "document_ingest", None, "V", "document-rag-live", {"document_id": document_id})
    assert run_once(db)
    event_id = db.list_json("events", "P")[0]["id"]

    client = authenticated_client(db)
    headers = {"Authorization": "Bearer test"}
    first = client.post(f"/api/projects/P/analyses", headers=headers, json={"event_id": event_id})
    assert first.status_code == 202
    assert run_once(db)
    interpreted = db.get_json("events", event_id, "P")["data"]
    assert interpreted["interpretation"]["status"] == "interpreted"
    assert interpreted["candidates"][0]["citation_ids"]
    assert db.get_json("runs", first.json()["run_id"], "P")["data"]["status"] == "NEEDS_INPUT"

    reviewed = client.patch(
        f"/api/projects/P/events/{event_id}/review", headers=headers,
        json={"confirmed": True, "related_task_ids": ["T042"], "review_note": "대상 설비와 효력일을 담당자가 확인함",
              "patch": {"not_before": {"T042": "2026-10-10"}}},
    )
    assert reviewed.status_code == 200
    second = client.post(f"/api/projects/P/analyses", headers=headers, json={"event_id": event_id})
    assert second.status_code == 202
    assert run_once(db)
    scenarios = client.get(f"/api/runs/{second.json()['run_id']}", headers=headers).json()["scenarios"]
    assert scenarios and scenarios[0]["data"]["finish_shift_days"] > 0

    scenario_id = scenarios[0]["id"]
    prepared = client.post(f"/api/scenarios/{scenario_id}/prepare", headers=headers).json()
    action_id = prepared["actions"][0]["id"]
    assert client.patch(f"/api/actions/{action_id}", headers=headers, json={"state": "ACCEPTED"}).status_code == 200
    assert client.post(f"/api/scenarios/{scenario_id}/approve", headers=headers, json={"actor": "프로젝트 운영팀"}).status_code == 200
    committed = client.post(f"/api/scenarios/{scenario_id}/commit", headers=headers)
    assert committed.status_code == 200
    assert client.get(f"/api/projects/P/export?version_id={committed.json()['version_id']}", headers=headers).status_code == 200


def test_document_candidate_selection_gates_agent_research_scope(tmp_path, monkeypatch):
    """A raw uploaded document cannot start costly research until a person selects its cited scope."""
    monkeypatch.setenv("REPLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAN_DEMO_TOKEN", "test")
    monkeypatch.setenv("API_KEY", "contract-test")
    monkeypatch.setenv("LLM_MODEL", "contract-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("REPLAN_PAID_CALLS_ENABLED", "true")
    monkeypatch.setattr("app.adapters.llm.OpenAICompatibleLLM", ContractLLM)
    db = make_store(tmp_path)
    document_id = "c" * 32
    content = NOTICE.encode()
    db.save_upload(document_id, content)
    db.put_json("documents", document_id, {
        "document_id": document_id, "filename": "new-export-rule.txt", "status": "QUEUED",
        "sha256": __import__("hashlib").sha256(content).hexdigest(), "size_bytes": len(content),
    }, project_id="P")
    db.create_run("P", "document_ingest", None, "V", "document-rag-selection", {"document_id": document_id})
    assert run_once(db)
    event_row = db.list_json("events", "P")[0]
    event_id = event_row["id"]
    event = event_row["data"]
    assert event["document_triage"]["status"] == "interpreted"
    assert event["candidate_selection"]["status"] == "REVIEW_REQUIRED"
    assert event["candidate_selection"]["candidate_task_ids"] == ["T042"]

    client = authenticated_client(db)
    headers = {"Authorization": "Bearer test"}
    blocked = client.post(f"/api/projects/P/events/{event_id}/investigations", headers=headers)
    assert blocked.status_code == 409
    assert "먼저 선택" in blocked.json()["detail"]

    confirmed = client.patch(
        f"/api/projects/P/events/{event_id}/candidate-selection", headers=headers,
        json={"task_ids": ["T042"], "review_note": "이번 공지의 적용 대상을 확인함"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["event"]["candidate_selection"]["status"] == "CONFIRMED"

    rejected_scope = client.post(
        f"/api/projects/P/events/{event_id}/investigations", headers=headers,
        json={"include_task_ids": ["T043"]},
    )
    assert rejected_scope.status_code == 422
    queued = client.post(f"/api/projects/P/events/{event_id}/investigations", headers=headers)
    assert queued.status_code == 202
    assert db.get_json("runs", queued.json()["run_id"], "P")["data"]["include_task_ids"] == ["T042"]

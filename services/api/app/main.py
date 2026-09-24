"""RE:PLAN MVP HTTP API."""

from __future__ import annotations

import io
import hashlib
import json
import os
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

from .events import normalize_event
from .storage import Store, digest, identifier, utcnow


app = FastAPI(title="RE:PLAN API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.environ.get("REPLAN_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")],
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


def store() -> Store:
    return Store()


def authorize(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("REPLAN_DEMO_TOKEN")
    if not expected:
        raise HTTPException(503, "REPLAN_DEMO_TOKEN must be configured")
    if authorization != f"Bearer {expected}":
        raise HTTPException(401, "invalid bearer token")


def project_or_404(db: Store, project_id: str) -> dict[str, Any]:
    project = db.get_json("projects", project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def scenario_or_404(db: Store, scenario_id: str) -> dict[str, Any]:
    scenario = db.get_json("scenarios", scenario_id)
    if scenario is None:
        raise HTTPException(404, "scenario not found")
    project_or_404(db, scenario["project_id"])
    return scenario


def notify_project(db: Store, project_id: str, event_type: str, title: str, message: str, *, data: dict[str, Any] | None = None) -> str:
    """Create an in-app notification; external delivery remains opt-in and draft-only."""
    notification_id = identifier()
    payload = {
        "notification_id": notification_id, "event_type": event_type, "title": title,
        "message": message, "channel": "in_app", "status": "UNREAD", "read_at": None,
        "data": data or {}, "created_at": utcnow(),
    }
    db.put_json("notifications", notification_id, payload, project_id=project_id)
    return notification_id


def decision_deadline(project: dict[str, Any], scenario: dict[str, Any], option_ids: list[str], options: list[dict[str, Any]]) -> str | None:
    """Calculate a review deadline from an option lead time and scenario start."""
    chosen = [item for item in options if item.get("option_id") in option_ids]
    lead_days = max([int(item.get("decision_lead_days") or item.get("decision_deadline_days") or 0) for item in chosen] or [0])
    lead_days = lead_days or int(project.get("decision_lead_days") or 3)
    starts = [str(item.get("planned_start"))[:10] for item in scenario.get("schedule", []) if item.get("planned_start")]
    anchor = min(starts) if starts else project.get("target_finish")
    if not anchor:
        return None
    try:
        return (date.fromisoformat(str(anchor)[:10]) - timedelta(days=lead_days)).isoformat()
    except ValueError:
        return None


def normalize_import_snapshot(parsed: dict[str, Any], current_project: dict[str, Any], overrides: "ConfirmInput") -> dict[str, Any]:
    """Bridge workbook labels to the Task and Option fields used by tools."""
    raw_project = {**parsed.get("project", {}), **(overrides.project or {})}
    profile = {**current_project, **raw_project}
    profile["name"] = profile.get("name") if profile.get("name") != "새 프로젝트" else profile.get("project_name", "새 프로젝트")
    profile["region"] = profile.get("region") or profile.get("site_region")
    profile["baseline_start"] = profile.get("baseline_start") or profile.get("planned_start")
    profile["target_finish"] = profile.get("target_finish") or profile.get("planned_completion")
    profile["mode"] = profile.get("input_mode") or profile.get("mode", "LIVE")
    profile["data_origin"] = profile.get("data_origin") or ("SYNTHETIC" if profile["mode"] == "REPLAY" else "USER")
    calendars = overrides.calendars if overrides.calendars is not None else parsed.get("calendars", [])
    profile["nonworking_dates"] = [item.get("calendar_date") for item in calendars if item.get("scope") == profile.get("site_id") and item.get("calendar_date")]
    raw_tasks = overrides.tasks if overrides.tasks is not None else parsed.get("tasks", [])
    tasks = []
    for original in raw_tasks:
        task = dict(original)
        task["baseline_start"] = task.get("baseline_start") or task.get("planned_start")
        task["baseline_finish"] = task.get("baseline_finish") or task.get("planned_finish")
        task["dependency_type"] = str(task.get("dependency_type") or task.get("relationship") or "FS").upper()
        task["location_id"] = task.get("location_id") or task.get("location")
        task["resource_demand"] = task.get("resource_demand") or task.get("demand_teams") or 1
        task["resource_capacity"] = task.get("resource_capacity") or task.get("capacity_teams") or 1
        task["predecessor_ids"] = task.get("predecessor_ids") or []
        if task.get("duration_workdays") is None and task.get("duration_days") is not None:
            start = date.fromisoformat(str(task["baseline_start"])[:10])
            finish = date.fromisoformat(str(task["baseline_finish"])[:10])
            weekend_days = set(profile.get("weekend_days") or [5, 6])
            blocked = {date.fromisoformat(str(value)[:10]) for value in profile.get("nonworking_dates") or []}
            workdays = [
                start + timedelta(days=offset) for offset in range((finish - start).days)
                if (start + timedelta(days=offset)).weekday() not in weekend_days
                and (start + timedelta(days=offset)) not in blocked
            ]
            task["duration_workdays"] = len(workdays)
            task["finish_boundary"] = "exclusive"
            task["finish_boundary_offset_days"] = (finish - workdays[-1]).days if workdays else 0
            task["duration_semantics"] = "calendar_days_elapsed"
        tasks.append(task)
    raw_options = overrides.options if overrides.options is not None else parsed.get("options", [])
    options = []
    for original in raw_options:
        option = dict(original)
        option["name"] = option.get("name") or option.get("method")
        targets = option.get("target_ids") or option.get("target_id") or []
        option["target_ids"] = [targets] if isinstance(targets, str) else targets
        option["conditions"] = option.get("conditions") or option.get("condition")
        option["approval_state"] = option.get("approval_state") or option.get("execution_status")
        options.append(option)
    return {"project": profile, "tasks": tasks, "options": options, "calendars": calendars, "demo_events": parsed.get("events", []), "data_origin": profile["data_origin"]}


def suggest_watch_plan(project: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    site = None
    if project.get("site_id") == "SITE-H1":
        site = {
            "id": "BUDAPEST-DEMO", "label": "부다페스트 공개 데모 좌표",
            "latitude": 47.4979, "longitude": 19.0402,
            "timezone": "Europe/Budapest", "is_exact_project_site": False,
        }
    return {
        "enabled": False,
        "template_id": "equipment_installation_v1",
        "weather_site": site if any(task.get("outdoor") for task in tasks) else None,
        "weather_poll_hours": 6,
        "notice_poll_hours": 12,
        "source_allowlist": ["https://environment.ec.europa.eu/news_en"],
        "public_search_terms": ["industrial emissions", "equipment import"],
        "weather_limits": {},
        "approval_required_for": ["schedule_commit", "extra_cost", "external_send"],
    }


class ProjectInput(BaseModel):
    project_id: str | None = None
    name: str = "새 프로젝트"
    site_id: str | None = None
    region: str | None = None
    timezone: str = "Asia/Seoul"
    target_finish: date | None = None
    extra_budget_krw: int | None = Field(default=None, ge=0)
    mode: str = "LIVE"
    data_origin: str = "USER"


class ConfirmInput(BaseModel):
    project: dict[str, Any] | None = None
    tasks: list[dict[str, Any]] | None = None
    options: list[dict[str, Any]] | None = None
    calendars: list[dict[str, Any]] | None = None


class WatchPlanInput(BaseModel):
    enabled: bool = False
    weather_site: dict[str, Any] | None = None
    weather_poll_hours: int = Field(default=6, ge=1, le=168)
    notice_poll_hours: int = Field(default=12, ge=1, le=168)
    source_allowlist: list[str] = Field(default_factory=list)
    public_search_terms: list[str] = Field(default_factory=list)
    weather_limits: dict[str, float] = Field(default_factory=dict)


class EventInput(BaseModel):
    event_id: str | None = None
    channel: str = "supplier_message"
    source_label: str = "프로젝트 운영팀 입력"
    content: str = Field(min_length=1, max_length=10000)
    published_at: str | None = None
    received_at: str | None = None
    mode: str | None = None
    data_origin: str | None = None
    simulation_as_of: str | None = None
    patch: dict[str, Any] = Field(default_factory=dict)


class EventReviewInput(BaseModel):
    confirmed: bool = True
    patch: dict[str, Any] | None = None
    related_task_ids: list[str] | None = None


class AnalysisInput(BaseModel):
    event_id: str
    version_id: str | None = None
    budget_krw: int | None = Field(default=None, ge=0)


class ReplanInput(BaseModel):
    budget_krw: int = Field(ge=0)
    unavailable_option_ids: list[str] = Field(default_factory=list)


class ApprovalInput(BaseModel):
    actor: str = Field(min_length=1)
    decision: str = "APPROVED"
    confirmed_conditions: list[str] = Field(default_factory=list)


class ActionUpdate(BaseModel):
    state: str
    note: str | None = None


class MailAccountInput(BaseModel):
    provider: str = "imap"
    host: str
    username: str
    folder: str = "INBOX"
    enabled: bool = True


class FeedInput(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    url: str
    kind: str = "rss"
    enabled: bool = True


class SupplierCalendarInput(BaseModel):
    supplier_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)
    unavailable_dates: list[date] = Field(default_factory=list)
    timezone: str = "Asia/Seoul"


class NotificationChannelInput(BaseModel):
    channel: str
    label: str = "알림 채널"
    target: str | None = None
    enabled: bool = True


class SitePrepInput(BaseModel):
    template_id: str = "equipment_installation_v1"
    owner: str = "프로젝트 운영팀"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/projects", dependencies=[Depends(authorize)])
def create_project(value: ProjectInput) -> dict[str, Any]:
    db = store()
    data = value.model_dump(mode="json")
    if data["mode"] not in {"LIVE", "REPLAY"}:
        raise HTTPException(422, "mode must be LIVE or REPLAY")
    project_id = data.get("project_id") or f"P-{identifier()[:10]}"
    if db.get_json("projects", project_id):
        raise HTTPException(409, "project already exists")
    data["project_id"] = project_id
    db.put_json("projects", project_id, data)
    return {"project_id": project_id, "project": data}


@app.get("/api/projects", dependencies=[Depends(authorize)])
def list_projects() -> dict[str, Any]:
    db = store()
    with db.connection() as conn:
        rows = conn.execute("SELECT id, data FROM projects ORDER BY created_at DESC").fetchall()
    return {"projects": [{"id": row["id"], **json.loads(row["data"])} for row in rows]}




@app.post("/api/projects/{project_id}/documents", status_code=202, dependencies=[Depends(authorize)])
async def upload_document(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "document exceeds 20 MiB")
    document_id = identifier()
    db.save_upload(document_id, content)
    filename = file.filename or "input.txt"
    content_hash = hashlib.sha256(content).hexdigest()
    existing = db.find_document_by_hash(project_id, content_hash)
    if existing:
        existing_data = existing["data"]
        run = db.get_json("runs", existing_data.get("run_id"), project_id) if existing_data.get("run_id") else None
        return {
            "document_id": existing["id"],
            "job_id": existing_data.get("run_id"),
            "status": run["status"] if run else existing_data.get("status", "FAILED").lower(),
            "duplicate": True,
            "document": existing_data,
            "event": None,
        }
    record = {
        "document_id": document_id,
        "filename": filename,
        "status": "QUEUED",
        "input_type": None,
        "sha256": content_hash,
        "size_bytes": len(content),
        "text": None,
        "metadata": {},
        "event_id": None,
        "error": None,
        "uploaded_at": utcnow(),
    }
    db.put_json("documents", document_id, record, project_id=project_id)
    version = db.current_version(project_id)
    run = db.create_run(
        project_id,
        "document_ingest",
        None,
        version["id"] if version else None,
        f"document:{document_id}",
        {"document_id": document_id},
    )
    record["run_id"] = run["id"]
    db.put_json("documents", document_id, record, project_id=project_id, created_at=db.get_json("documents", document_id, project_id)["created_at"])
    return {"document_id": document_id, "job_id": run["id"], "status": run["status"], "document": record, "event": None}


@app.get("/api/projects/{project_id}/documents", dependencies=[Depends(authorize)])
def list_documents(project_id: str) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    return {"documents": db.list_json("documents", project_id)}


@app.get("/api/projects/{project_id}/documents/{document_id}", dependencies=[Depends(authorize)])
def get_document(project_id: str, document_id: str) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    document = db.get_json("documents", document_id, project_id)
    if not document:
        raise HTTPException(404, "document not found")
    run_id = document["data"].get("run_id")
    run = db.get_json("runs", run_id, project_id) if run_id else None
    return {"document": document, "run": run}


@app.post("/api/projects/{project_id}/documents/{document_id}/retry", status_code=202, dependencies=[Depends(authorize)])
def retry_document(project_id: str, document_id: str) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    document = db.get_json("documents", document_id, project_id)
    if not document:
        raise HTTPException(404, "document not found")
    record = dict(document["data"])
    if record.get("status") != "FAILED":
        run = db.get_json("runs", record.get("run_id"), project_id) if record.get("run_id") else None
        return {"document_id": document_id, "job_id": record.get("run_id"), "status": run["status"] if run else record.get("status", "UNKNOWN").lower(), "duplicate": True, "document": record}
    retry_count = int(record.get("retry_count") or 0) + 1
    version = db.current_version(project_id)
    run = db.create_run(
        project_id,
        "document_ingest",
        None,
        version["id"] if version else None,
        f"document:{document_id}:retry:{retry_count}",
        {"document_id": document_id, "retry_count": retry_count},
    )
    record.update({"status": "QUEUED", "error": None, "retry_count": retry_count, "run_id": run["id"]})
    db.put_json("documents", document_id, record, project_id=project_id, created_at=document["created_at"])
    return {"document_id": document_id, "job_id": run["id"], "status": run["status"], "duplicate": False, "document": record}


@app.put("/api/projects/{project_id}/mail-account", dependencies=[Depends(authorize)])
def connect_mail_account(project_id: str, value: MailAccountInput) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    if value.provider not in {"imap", "gmail", "outlook"}:
        raise HTTPException(422, "unsupported mail provider")
    # Store connection metadata only. Passwords/OAuth tokens are deliberately out of scope.
    account = {**value.model_dump(mode="json"), "status": "CONFIGURED", "credentials_required": True}
    account_id = f"mail-{project_id}"
    db.put_json("mail_accounts", account_id, account, project_id=project_id)
    return {"account_id": account_id, "account": account}


@app.get("/api/projects/{project_id}/mail-account", dependencies=[Depends(authorize)])
def get_mail_account(project_id: str) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    account = db.get_json("mail_accounts", f"mail-{project_id}", project_id)
    return {"account": account["data"] if account else None}


@app.post("/api/projects/{project_id}/public-feeds", dependencies=[Depends(authorize)])
def register_public_feed(project_id: str, value: FeedInput) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    parsed_url = urlparse(value.url)
    approved_hosts = {host.strip().lower() for host in os.environ.get("REPLAN_ALLOWED_SOURCE_HOSTS", "environment.ec.europa.eu").split(",") if host.strip()}
    if parsed_url.scheme != "https" or parsed_url.hostname not in approved_hosts or parsed_url.username or parsed_url.password:
        raise HTTPException(422, "registered source host is not server-approved")
    feed_id = identifier()
    feed = {**value.model_dump(mode="json"), "feed_id": feed_id}
    db.put_json("public_feeds", feed_id, feed, project_id=project_id)
    watch = db.get_json("watch_plans", project_id)
    plan = dict(watch["data"] if watch else suggest_watch_plan(project_or_404(db, project_id)["data"], []))
    plan["source_allowlist"] = sorted(set(plan.get("source_allowlist", [])) | {value.url})
    db.put_json("watch_plans", project_id, plan)
    return {"feed": feed, "watch_plan": plan}


@app.get("/api/projects/{project_id}/public-feeds", dependencies=[Depends(authorize)])
def list_public_feeds(project_id: str) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    return {"feeds": db.list_json("public_feeds", project_id)}


@app.post("/api/projects/{project_id}/supplier-calendars", dependencies=[Depends(authorize)])
def save_supplier_calendar(project_id: str, value: SupplierCalendarInput) -> dict[str, Any]:
    db = store()
    project = project_or_404(db, project_id)
    calendar = value.model_dump(mode="json")
    calendar_id = f"supplier-{value.supplier_id}"
    db.put_json("supplier_calendars", calendar_id, calendar, project_id=project_id)
    profile = dict(project["data"])
    unavailable = set(profile.get("supplier_unavailable_dates", [])) | set(calendar["unavailable_dates"])
    profile["supplier_unavailable_dates"] = sorted(unavailable)
    db.put_json("projects", project_id, profile, created_at=project["created_at"])
    return {"calendar_id": calendar_id, "calendar": calendar, "supplier_unavailable_dates": profile["supplier_unavailable_dates"]}


@app.get("/api/projects/{project_id}/supplier-calendars", dependencies=[Depends(authorize)])
def list_supplier_calendars(project_id: str) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    return {"calendars": db.list_json("supplier_calendars", project_id)}


@app.post("/api/projects/{project_id}/notification-channels", dependencies=[Depends(authorize)])
def save_notification_channel(project_id: str, value: NotificationChannelInput) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    if value.channel not in {"in_app", "email", "webhook", "slack"}:
        raise HTTPException(422, "unsupported notification channel")
    channel = {**value.model_dump(mode="json"), "delivery_mode": "DRAFT" if value.channel != "in_app" else "ACTIVE"}
    channel_id = f"channel-{value.channel}"
    db.put_json("notification_channels", channel_id, channel, project_id=project_id)
    return {"channel_id": channel_id, "channel": channel}


@app.get("/api/projects/{project_id}/notifications", dependencies=[Depends(authorize)])
def list_notifications(project_id: str, unread_only: bool = False) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    notifications = db.list_json("notifications", project_id)
    if unread_only:
        notifications = [item for item in notifications if item["data"].get("status") == "UNREAD"]
    return {"notifications": notifications}


@app.patch("/api/notifications/{notification_id}", dependencies=[Depends(authorize)])
def mark_notification(notification_id: str) -> dict[str, Any]:
    db = store()
    record = db.get_json("notifications", notification_id)
    if not record:
        raise HTTPException(404, "notification not found")
    data = {**record["data"], "status": "READ", "read_at": utcnow()}
    db.put_json("notifications", notification_id, data, project_id=record["project_id"], created_at=record["created_at"])
    return {"notification": data}


@app.get("/api/site-prep/templates", dependencies=[Depends(authorize)])
def site_prep_templates() -> dict[str, Any]:
    return {"templates": [{"id": "equipment_installation_v1", "label": "설비 설치 현장 준비", "items": [
        {"key": "permit", "label": "작업허가·안전서류 확인", "lead_days": 7},
        {"key": "laydown", "label": "반입 동선·적치장 확보", "lead_days": 5},
        {"key": "crane", "label": "크레인·양중 장비 예약", "lead_days": 10},
        {"key": "briefing", "label": "현장 안전 브리핑 일정 확정", "lead_days": 2},
    ]}]}


@app.post("/api/projects/{project_id}/site-prep", dependencies=[Depends(authorize)])
def instantiate_site_prep(project_id: str, value: SitePrepInput) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    templates = site_prep_templates()["templates"]
    template = next((item for item in templates if item["id"] == value.template_id), None)
    if not template:
        raise HTTPException(404, "site preparation template not found")
    items = []
    for item in template["items"]:
        item_id = identifier()
        data = {"template_id": value.template_id, "key": item["key"], "label": item["label"], "owner": value.owner, "state": "OPEN", "lead_days": item["lead_days"], "created_at": utcnow()}
        db.put_json("site_prep_items", item_id, data, project_id=project_id)
        items.append({"id": item_id, "data": data})
    notify_project(db, project_id, "site_prep_created", "현장 준비 체크리스트 생성", f"{template['label']} {len(items)}개 항목을 생성했습니다.", data={"template_id": value.template_id})
    return {"template": template, "items": items}


@app.get("/api/projects/{project_id}/decision-deadlines", dependencies=[Depends(authorize)])
def list_decision_deadlines(project_id: str) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    return {"deadlines": [{"id": item["id"], **item["data"]} for item in db.list_json("actions", project_id) if item["data"].get("due_at")]}


@app.get("/api/projects/{project_id}", dependencies=[Depends(authorize)])
def get_project(project_id: str) -> dict[str, Any]:
    db = store()
    project = project_or_404(db, project_id)
    version = db.current_version(project_id)
    watch = db.get_json("watch_plans", project_id)
    mail_account = db.get_json("mail_accounts", f"mail-{project_id}", project_id)
    return {
        "project": project["data"],
        "version": version,
        "watch_plan": watch["data"] if watch else None,
        "events": db.list_json("events", project_id),
        "source_snapshots": db.list_json("source_snapshots", project_id, 10),
        "runs": db.list_json("runs", project_id, 20),
        "actions": db.list_json("actions", project_id),
        "documents": db.list_json("documents", project_id),
        "notifications": db.list_json("notifications", project_id),
        "public_feeds": db.list_json("public_feeds", project_id),
        "mail_account": mail_account["data"] if mail_account else None,
        "site_prep_items": db.list_json("site_prep_items", project_id),
        "supplier_calendars": db.list_json("supplier_calendars", project_id),
        "decision_deadlines": [{"id": item["id"], **item["data"]} for item in db.list_json("actions", project_id) if item["data"].get("due_at")],
        "demo_events": version["data"].get("demo_events", []) if version else [],
    }


@app.post("/api/projects/{project_id}/imports", dependencies=[Depends(authorize)])
async def preview_import(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    content = await file.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(413, "file exceeds 8 MiB")
    try:
        from .importers import parse_upload

        parsed = parse_upload(file.filename or "upload", content)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    import_id = identifier()
    db.save_upload(import_id, content)
    parsed["_upload"] = {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
    db.put_json("imports", import_id, parsed, project_id=project_id, filename=file.filename or "upload", status="preview")
    current = db.current_version(project_id)
    if current:
        from .importers import diff_tasks

        normalized = normalize_import_snapshot(parsed, current["data"]["project"], ConfirmInput())
        return {"import_id": import_id, **parsed, "diff": diff_tasks(current["data"]["tasks"], normalized["tasks"]), "import_kind": "change"}
    return {"import_id": import_id, **parsed, "import_kind": "baseline"}


@app.get("/api/projects/{project_id}/imports/{import_id}/original", dependencies=[Depends(authorize)])
def download_original(project_id: str, import_id: str) -> FileResponse:
    db = store()
    project_or_404(db, project_id)
    record = db.get_json("imports", import_id, project_id)
    if not record:
        raise HTTPException(404, "import not found")
    path = db.upload_path(import_id)
    if not path.is_file():
        raise HTTPException(404, "original file not found")
    suffix = ".csv" if record["filename"].lower().endswith(".csv") else ".xlsx"
    return FileResponse(path, filename=f"replan-original-{import_id[:8]}{suffix}")


@app.post("/api/projects/{project_id}/imports/{import_id}/confirm", dependencies=[Depends(authorize)])
def confirm_import(project_id: str, import_id: str, value: ConfirmInput) -> dict[str, Any]:
    db = store()
    current_project = project_or_404(db, project_id)
    current = db.current_version(project_id)
    import_record = db.get_json("imports", import_id, project_id)
    if not import_record:
        raise HTTPException(404, "import preview not found")
    parsed = import_record["data"]
    snapshot = normalize_import_snapshot(parsed, current_project["data"], value)
    tasks = snapshot["tasks"]
    if not tasks:
        raise HTTPException(422, "no tasks")
    from .scheduling import validate_tasks

    errors = validate_tasks(tasks)
    if errors:
        raise HTTPException(422, {"task_errors": errors})
    if current:
        from .importers import diff_tasks

        difference = diff_tasks(current["data"]["tasks"], tasks)
        if difference["added"] or difference["removed"]:
            raise HTTPException(409, {"reason": "task IDs changed; map them before import", "diff": difference})
        if not difference["changed"]:
            return {"unchanged": True, "diff": difference}
        patch: dict[str, Any] = {"estimated_finish": {}, "not_before": {}}
        for change in difference["changed"]:
            after = change["after"]
            if "baseline_finish" in change["changes"]:
                patch["estimated_finish"][change["task_id"]] = after["baseline_finish"]
            if "baseline_start" in change["changes"]:
                patch["not_before"][change["task_id"]] = after["baseline_start"]
        patch = {key: item for key, item in patch.items() if item}
        event = {
            "event_id": f"IMPORT-{import_id[:8]}", "project_id": project_id,
            "channel": "revised_excel", "source_label": import_record["filename"],
            "content": f"수정 일정표: {len(difference['changed'])}개 작업 변경",
            "published_at": None, "received_at": utcnow(),
            "mode": current["data"]["project"].get("mode", "LIVE"),
            "data_origin": current["data"].get("data_origin", "USER"),
            "related_task_ids": [item["task_id"] for item in difference["changed"]],
            "classification_status": "PATCH_CONFIRMED", "patch": patch,
            "diff": difference, "base_version_id": current["id"],
        }
        fingerprint = digest({"import_id": import_id, "version_id": current["id"], "diff": difference})
        event_id = identifier()
        event["id"] = event_id
        db.put_json("events", event_id, event, project_id=project_id, fingerprint=fingerprint)
        return {"event_id": event_id, "event": event, "diff": difference, "import_kind": "change"}
    profile = {**snapshot["project"], "project_id": project_id}
    snapshot = {**snapshot, "project": profile, "import_id": import_id}
    version_id = identifier()
    db.put_json("versions", version_id, snapshot, project_id=project_id, parent_id=None, status="baseline", content_hash=digest(snapshot))
    db.put_json("projects", project_id, profile)
    suggestion = suggest_watch_plan(profile, tasks)
    db.put_json("watch_plans", project_id, suggestion)
    return {"version_id": version_id, "version_hash": digest(snapshot), "task_count": len(tasks), "watch_plan_suggestion": suggestion}


@app.put("/api/projects/{project_id}/watch-plan", dependencies=[Depends(authorize)])
def save_watch_plan(project_id: str, value: WatchPlanInput) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    data = value.model_dump(mode="json")
    approved_hosts = {host.strip().lower() for host in os.environ.get("REPLAN_ALLOWED_SOURCE_HOSTS", "environment.ec.europa.eu").split(",") if host.strip()}
    for url in data["source_allowlist"]:
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.hostname not in approved_hosts or parsed_url.username or parsed_url.password:
            raise HTTPException(422, "registered source host is not server-approved")
    if set(data["weather_limits"]) - {"max_wind_speed_kmh", "max_precipitation_mm"} or any(
        value < 0 for value in data["weather_limits"].values()
    ):
        raise HTTPException(422, "invalid weather limits")
    db.put_json("watch_plans", project_id, data)
    return {"project_id": project_id, "watch_plan": data}


@app.post("/api/projects/{project_id}/scan", status_code=202, dependencies=[Depends(authorize)])
def scan(project_id: str, idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    watch = db.get_json("watch_plans", project_id)
    if not watch or not watch["data"].get("enabled"):
        raise HTTPException(409, "watch plan is disabled")
    run = db.create_run(project_id, "scan", None, None, idempotency_key, {"watch_plan": watch["data"]})
    return {"run_id": run["id"], "status": run["status"]}


@app.post("/api/projects/{project_id}/events", dependencies=[Depends(authorize)])
def create_event(project_id: str, value: EventInput) -> dict[str, Any]:
    db = store()
    project = project_or_404(db, project_id)
    version = db.current_version(project_id)
    if not version:
        raise HTTPException(409, "confirm a baseline first")
    raw = value.model_dump(exclude_none=True)
    if raw.get("mode") is None:
        raw["mode"] = project["data"].get("mode", "LIVE")
    if raw["mode"] not in {"LIVE", "REPLAY"}:
        raise HTTPException(422, "invalid mode")
    if raw["mode"] == "REPLAY" and not raw.get("simulation_as_of"):
        raw["simulation_as_of"] = raw.get("published_at") or utcnow()
    raw.setdefault("received_at", utcnow())
    event = normalize_event(raw, project["data"], version["data"]["tasks"])
    fingerprint = digest({"source": event.get("source_label"), "external_id": event.get("event_id"), "content": event["content"], "published_at": event.get("published_at")})
    with db.connection() as conn:
        prior = conn.execute("SELECT * FROM events WHERE project_id=? AND fingerprint=?", (project_id, fingerprint)).fetchone()
    if prior:
        return {"event_id": prior["id"], "event": json.loads(prior["data"]), "duplicate": True}
    event_id = identifier()
    event["id"] = event_id
    db.put_json("events", event_id, event, project_id=project_id, fingerprint=fingerprint)
    notify_project(db, project_id, "event_received", "새 변경 이벤트", f"{event.get('source_label', '입력')} 이벤트가 등록되었습니다.", data={"event_id": event_id})
    return {"event_id": event_id, "event": event, "duplicate": False}


@app.patch("/api/projects/{project_id}/events/{event_id}/review", dependencies=[Depends(authorize)])
def review_event(project_id: str, event_id: str, value: EventReviewInput) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    record = db.get_json("events", event_id, project_id)
    if not record:
        raise HTTPException(404, "event not found")
    event = dict(record["data"])
    if value.patch is not None:
        event["patch"] = value.patch
        event["classification_status"] = "PATCH_CONFIRMED" if value.confirmed else "PATCH_REJECTED"
    if value.related_task_ids is not None:
        event["related_task_ids"] = value.related_task_ids
    event["review_status"] = "CONFIRMED" if value.confirmed else "REJECTED"
    event["reviewed_at"] = utcnow()
    db.put_json(
        "events", event_id, event, project_id=project_id,
        fingerprint=record.get("fingerprint"), created_at=record.get("created_at"),
    )
    notify_project(
        db,
        project_id,
        "event_reviewed",
        "변경 해석 확인됨" if value.confirmed else "변경 해석 보류됨",
        "변경 사실을 확인했습니다. 영향 분석을 진행할 수 있습니다." if value.confirmed else "변경 해석을 확인하지 않아 영향 분석을 진행하지 않습니다.",
        data={"event_id": event_id, "review_status": event["review_status"]},
    )
    return {"event_id": event_id, "event": event}


@app.post("/api/projects/{project_id}/analyses", status_code=202, dependencies=[Depends(authorize)])
def create_analysis(project_id: str, value: AnalysisInput, idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    event = db.get_json("events", value.event_id, project_id)
    if not event:
        raise HTTPException(404, "event not found")
    event_data = event["data"]
    if event_data.get("patch") and event_data.get("review_status") != "CONFIRMED":
        raise HTTPException(409, "review the proposed change before analysis")
    version = db.get_json("versions", value.version_id, project_id) if value.version_id else db.current_version(project_id)
    if not version:
        raise HTTPException(409, "schedule version not found")
    key = idempotency_key or digest({"event_id": value.event_id, "version_id": version["id"], "budget": value.budget_krw})
    run = db.create_run(
        project_id,
        "analysis",
        value.event_id,
        version["id"],
        key,
        {
            "budget_krw": value.budget_krw,
            "project_context_snapshot": db.project_context_snapshot(project_id),
        },
    )
    return {"run_id": run["id"], "status": run["status"]}


@app.get("/api/runs/{run_id}", dependencies=[Depends(authorize)])
def get_run(run_id: str) -> dict[str, Any]:
    db = store()
    run = db.get_json("runs", run_id)
    if not run:
        raise HTTPException(404, "run not found")
    project_or_404(db, run["project_id"])
    scenarios = [item for item in db.list_json("scenarios", run["project_id"]) if item["run_id"] == run_id]
    return {"run": run, "scenarios": scenarios}


@app.post("/api/runs/{run_id}/replan", status_code=202, dependencies=[Depends(authorize)])
def replan(run_id: str, value: ReplanInput) -> dict[str, Any]:
    db = store()
    prior = db.get_json("runs", run_id)
    if not prior or prior["kind"] != "analysis":
        raise HTTPException(404, "analysis run not found")
    project_or_404(db, prior["project_id"])
    key = digest({"prior": run_id, "budget": value.budget_krw, "unavailable": sorted(value.unavailable_option_ids)})
    run_data = value.model_dump()
    run_data["project_context_snapshot"] = db.project_context_snapshot(prior["project_id"])
    run = db.create_run(prior["project_id"], "analysis", prior["event_id"], prior["version_id"], key, run_data)
    return {"run_id": run["id"], "status": run["status"]}


@app.get("/api/projects/{project_id}/scenarios", dependencies=[Depends(authorize)])
def list_scenarios(project_id: str) -> dict[str, Any]:
    db = store()
    project_or_404(db, project_id)
    return {"scenarios": db.list_json("scenarios", project_id)}


@app.post("/api/scenarios/{scenario_id}/prepare", dependencies=[Depends(authorize)])
def prepare_scenario(scenario_id: str) -> dict[str, Any]:
    db = store()
    scenario = scenario_or_404(db, scenario_id)
    data = scenario["data"]
    existing = [item for item in db.list_json("actions", scenario["project_id"]) if item["scenario_id"] == scenario_id]
    if existing:
        return {"actions": existing, "duplicate": True}
    actions = []
    event_id = data.get("event_id")
    project = db.get_json("projects", scenario["project_id"])
    version = db.get_json("versions", scenario["version_id"], scenario["project_id"])
    due_at = decision_deadline(project["data"] if project else {}, data, data.get("option_ids", []), (version or {}).get("data", {}).get("options", []))
    for condition in data.get("required_confirmations", []):
        action = {"owner": "프로젝트 운영팀", "state": "OPEN", "request": f"{condition} 확인 및 수락", "condition": condition, "due_at": due_at, "scenario_id": scenario_id, "event_id": event_id}
        action_id = identifier()
        db.put_json("actions", action_id, action, project_id=scenario["project_id"], event_id=event_id, scenario_id=scenario_id)
        actions.append({"id": action_id, "data": action})
    if not actions:
        action = {"owner": "프로젝트 운영팀", "state": "OPEN", "request": "대응안 검토 및 관계자 협의", "due_at": due_at, "scenario_id": scenario_id, "event_id": event_id}
        action_id = identifier()
        db.put_json("actions", action_id, action, project_id=scenario["project_id"], event_id=event_id, scenario_id=scenario_id)
        actions.append({"id": action_id, "data": action})
    return {"actions": actions, "duplicate": False}


@app.post("/api/scenarios/{scenario_id}/approve", dependencies=[Depends(authorize)])
def approve_scenario(scenario_id: str, value: ApprovalInput) -> dict[str, Any]:
    db = store()
    scenario = scenario_or_404(db, scenario_id)
    current = db.current_version(scenario["project_id"])
    if not current or current["id"] != scenario["version_id"]:
        raise HTTPException(409, "schedule changed; replan required")
    if value.decision != "APPROVED":
        raise HTTPException(422, "only APPROVED is supported by this endpoint")
    data = scenario["data"]
    if not data.get("budget_met", False) or data.get("violations"):
        raise HTTPException(409, "scenario violates hard constraints")
    required = set(data.get("required_confirmations", []))
    if not required.issubset(value.confirmed_conditions):
        raise HTTPException(409, {"unconfirmed_conditions": sorted(required - set(value.confirmed_conditions))})
    actions = [item for item in db.list_json("actions", scenario["project_id"]) if item["scenario_id"] == scenario_id]
    if not actions:
        raise HTTPException(409, "prepare actions before approval")
    accepted = {item["data"].get("condition") for item in actions if item["data"].get("state") in {"ACCEPTED", "DONE"}}
    if not required.issubset(accepted):
        raise HTTPException(409, {"unaccepted_conditions": sorted(required - accepted)})
    approval_id = identifier()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?,?)",
            (approval_id, scenario["project_id"], scenario_id, current["content_hash"], value.actor, value.decision, utcnow()),
        )
    return {"approval_id": approval_id, "scenario_id": scenario_id, "confirmed_conditions": value.confirmed_conditions}


@app.post("/api/scenarios/{scenario_id}/commit", dependencies=[Depends(authorize)])
def commit_scenario(scenario_id: str) -> dict[str, Any]:
    db = store()
    scenario = scenario_or_404(db, scenario_id)
    result = scenario["data"]
    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM versions WHERE parent_id=? AND json_extract(data,'$.scenario_id')=?",
            (scenario["version_id"], scenario_id),
        ).fetchone()
        if existing is not None:
            return {"version_id": existing["id"], "version_hash": existing["content_hash"], "duplicate": True}
        current = conn.execute(
            "SELECT * FROM versions WHERE project_id=? AND status IN ('baseline','committed') ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (scenario["project_id"],),
        ).fetchone()
        if current is None or current["id"] != scenario["version_id"]:
            raise HTTPException(409, "schedule changed; replan required")
        approval = conn.execute(
            "SELECT * FROM approvals WHERE scenario_id=? AND version_hash=? AND decision='APPROVED' ORDER BY created_at DESC LIMIT 1",
            (scenario_id, current["content_hash"]),
        ).fetchone()
        if approval is None:
            raise HTTPException(409, "approval required")
        actions = conn.execute("SELECT data FROM actions WHERE scenario_id=?", (scenario_id,)).fetchall()
        if not actions:
            raise HTTPException(409, "prepare actions before commit")
        required = set(result.get("required_confirmations", []))
        accepted = {
            action.get("condition") for action in (json.loads(row["data"]) for row in actions)
            if action.get("state") in {"ACCEPTED", "DONE"}
        }
        if not required.issubset(accepted):
            raise HTTPException(409, "required conditions are no longer accepted")
        snapshot = json.loads(current["data"])
        updates = {str(task.get("task_id")): task for task in result.get("schedule", [])}
        new_tasks = []
        for task in snapshot["tasks"]:
            task = dict(task)
            update = updates.get(str(task.get("task_id")))
            if update:
                task["baseline_start"] = update["planned_start"]
                task["baseline_finish"] = update["planned_finish"]
            new_tasks.append(task)
        snapshot = {**snapshot, "tasks": new_tasks, "scenario_id": scenario_id}
        version_id = identifier()
        content_hash = digest(snapshot)
        conn.execute(
            "INSERT INTO versions VALUES (?,?,?,?,?,?,?)",
            (version_id, scenario["project_id"], current["id"], "committed", content_hash, json.dumps(snapshot, ensure_ascii=False, default=str), utcnow()),
        )
    return {"version_id": version_id, "version_hash": content_hash, "duplicate": False}


@app.patch("/api/actions/{action_id}", dependencies=[Depends(authorize)])
def update_action(action_id: str, value: ActionUpdate) -> dict[str, Any]:
    db = store()
    action = db.get_json("actions", action_id)
    if not action:
        raise HTTPException(404, "action not found")
    project_or_404(db, action["project_id"])
    if value.state not in {"OPEN", "ACCEPTED", "REJECTED", "DONE"}:
        raise HTTPException(422, "invalid action state")
    data = {**action["data"], "state": value.state, "note": value.note}
    db.put_json("actions", action_id, data, project_id=action["project_id"], event_id=action["event_id"], scenario_id=action["scenario_id"], created_at=action["created_at"])
    return {"action_id": action_id, "action": data}


def safe_excel_text(value: Any) -> Any:
    if isinstance(value, str) and value and value[0] in "=+-@":
        return "'" + value
    return value


@app.get("/api/projects/{project_id}/export", dependencies=[Depends(authorize)])
def export_schedule(project_id: str, version_id: str | None = None) -> StreamingResponse:
    db = store()
    project_or_404(db, project_id)
    version = db.get_json("versions", version_id, project_id) if version_id else db.current_version(project_id)
    if not version:
        raise HTTPException(404, "schedule version not found")
    snapshot = version["data"]
    wb = Workbook()
    ws = wb.active
    ws.title = "변경 일정"
    ws.append(["버전 ID", version["id"], "버전 해시", version["content_hash"], "모드", snapshot["project"].get("mode"), "데이터 성격", snapshot.get("data_origin")])
    ws.append(["작업 ID", "작업명", "기준 시작", "기준 종료", "변경 시작", "변경 종료", "종료 차이(일)", "담당자", "상태", "사유"])
    parent = db.get_json("versions", version["parent_id"], project_id) if version.get("parent_id") else None
    baseline = parent
    while baseline and baseline.get("parent_id"):
        baseline = db.get_json("versions", baseline["parent_id"], project_id)
    old_tasks = {str(task.get("task_id")): task for task in baseline["data"]["tasks"]} if baseline else {}
    for task in snapshot["tasks"]:
        prior = old_tasks.get(str(task.get("task_id")), task)
        old_finish = prior.get("baseline_finish")
        new_finish = task.get("baseline_finish")
        difference = (date.fromisoformat(str(new_finish)[:10]) - date.fromisoformat(str(old_finish)[:10])).days if old_finish and new_finish else None
        ws.append([
            safe_excel_text(task.get("task_id")),
            safe_excel_text(task.get("name")),
            prior.get("baseline_start"), prior.get("baseline_finish"),
            task.get("baseline_start"), task.get("baseline_finish"),
            difference,
            safe_excel_text(task.get("owner")), safe_excel_text(task.get("status")),
            "승인된 대응안" if parent and difference else "",
        ])
    payload = io.BytesIO()
    wb.save(payload)
    payload.seek(0)
    return StreamingResponse(payload, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="replan-{project_id}-{version["id"][:8]}.xlsx"'})


@app.get("/api/usage", dependencies=[Depends(authorize)])
def usage() -> dict[str, Any]:
    db = store()
    with db.connection() as conn:
        rows = conn.execute("SELECT model, input_tokens, output_tokens, cost_usd, cost_status FROM usage_ledger ORDER BY created_at DESC").fetchall()
    items = [dict(row) for row in rows]
    known_cost = sum(item["cost_usd"] or 0 for item in items if item["cost_status"] == "KNOWN")
    return {
        "records": items,
        "known_cost_usd": known_cost,
        "cost_unknown": any(item["cost_status"] != "KNOWN" for item in items),
        "paid_calls_enabled": os.environ.get("REPLAN_PAID_CALLS_ENABLED", "false").lower() == "true",
        "daily_paid_run_limit": int(os.environ.get("REPLAN_MAX_PAID_RUNS_PER_DAY", "20")),
    }

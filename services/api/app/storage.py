"""Small transactional store for the single-project MVP.

The API and worker use the same on-disk SQLite database. Every committed schedule
is an immutable snapshot; proposal and source records never overwrite it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier() -> str:
    return uuid.uuid4().hex


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.environ.get("REPLAN_DATA_DIR", ".data")).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db_path = self.root / "replan.sqlite3"
        self._initialize()

    def save_upload(self, upload_id: str, content: bytes) -> Path:
        """Preserve uploaded bytes under a server-generated name, never a user path."""
        if len(upload_id) != 32 or any(character not in "0123456789abcdef" for character in upload_id):
            raise ValueError("invalid upload id")
        directory = self.root / "uploads"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / f"{upload_id}.bin"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(content)
        return path

    def upload_path(self, upload_id: str) -> Path:
        if len(upload_id) != 32 or any(character not in "0123456789abcdef" for character in upload_id):
            raise ValueError("invalid upload id")
        return self.root / "uploads" / f"{upload_id}.bin"

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def _initialize(self) -> None:
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL,
                    data TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT,
                    status TEXT NOT NULL, content_hash TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS versions_by_project ON versions(project_id, created_at);
                CREATE TABLE IF NOT EXISTS watch_plans (
                    project_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_snapshots (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    body_hash TEXT, status TEXT NOT NULL, data TEXT NOT NULL, fetched_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS source_by_project ON source_snapshots(project_id, source_id, fetched_at);
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, event_id TEXT,
                    version_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    idempotency_key TEXT,
                    UNIQUE(project_id, kind, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS scenarios (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    version_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, event_id TEXT,
                    scenario_id TEXT, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
                    version_hash TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_ledger (
                    id TEXT PRIMARY KEY, run_id TEXT, model TEXT, input_tokens INTEGER,
                    output_tokens INTEGER, cost_usd REAL, cost_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS usage_one_attempt_per_run ON usage_ledger(run_id) WHERE run_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mail_accounts (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS public_feeds (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS supplier_calendars (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_channels (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS site_prep_items (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )

    def put_json(self, table: str, record_id: str, data: dict[str, Any], **fields: Any) -> None:
        allowed = {
            "projects": ("id", "data", "created_at"),
            "imports": ("id", "project_id", "filename", "data", "status", "created_at"),
            "versions": ("id", "project_id", "parent_id", "status", "content_hash", "data", "created_at"),
            "watch_plans": ("project_id", "data", "updated_at"),
            "source_snapshots": ("id", "project_id", "source_id", "body_hash", "status", "data", "fetched_at"),
            "events": ("id", "project_id", "fingerprint", "data", "created_at"),
            "scenarios": ("id", "project_id", "run_id", "version_id", "data", "created_at"),
            "actions": ("id", "project_id", "event_id", "scenario_id", "data", "created_at"),
            "documents": ("id", "project_id", "data", "created_at"),
            "mail_accounts": ("id", "project_id", "data", "created_at"),
            "public_feeds": ("id", "project_id", "data", "created_at"),
            "supplier_calendars": ("id", "project_id", "data", "created_at"),
            "notification_channels": ("id", "project_id", "data", "created_at"),
            "notifications": ("id", "project_id", "data", "created_at"),
            "site_prep_items": ("id", "project_id", "data", "created_at"),
        }
        if table not in allowed:
            raise ValueError("unsupported table")
        key = "project_id" if table == "watch_plans" else "id"
        values = {key: record_id, "data": encoded(data), **fields}
        columns = allowed[table]
        timestamp = "updated_at" if table == "watch_plans" else "fetched_at" if table == "source_snapshots" else "created_at"
        values.setdefault(timestamp, utcnow())
        if set(values) != set(columns):
            raise ValueError(f"invalid fields for {table}: {set(values) ^ set(columns)}")
        placeholders = ",".join("?" for _ in columns)
        with self.transaction() as db:
            db.execute(
                f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                [values[column] for column in columns],
            )

    def get_json(self, table: str, record_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        if table not in {"projects", "imports", "versions", "watch_plans", "source_snapshots", "events", "runs", "scenarios", "actions", "documents", "mail_accounts", "public_feeds", "supplier_calendars", "notification_channels", "notifications", "site_prep_items"}:
            raise ValueError("unsupported table")
        key = "project_id" if table == "watch_plans" else "id"
        query = f"SELECT * FROM {table} WHERE {key}=?"
        params: list[Any] = [record_id]
        if project_id and table != "projects":
            query += " AND project_id=?"
            params.append(project_id)
        with self.connection() as db:
            row = db.execute(query, params).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result

    def list_json(self, table: str, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if table not in {"versions", "source_snapshots", "events", "runs", "scenarios", "actions", "documents", "mail_accounts", "public_feeds", "supplier_calendars", "notification_channels", "notifications", "site_prep_items"}:
            raise ValueError("unsupported table")
        order_column = "updated_at" if table == "runs" else "created_at"
        if table == "source_snapshots":
            order_column = "fetched_at"
        with self.connection() as db:
            rows = db.execute(
                f"SELECT * FROM {table} WHERE project_id=? ORDER BY {order_column} DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"])
            result.append(item)
        return result

    def find_document_by_hash(self, project_id: str, content_hash: str) -> dict[str, Any] | None:
        """Return the newest document with the same content in a project."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM documents WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        for row in rows:
            data = json.loads(row["data"])
            if data.get("sha256") == content_hash:
                item = dict(row)
                item["data"] = data
                return item
        return None

    def find_event_by_fingerprint(self, project_id: str, fingerprint: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM events WHERE project_id=? AND fingerprint=?",
                (project_id, fingerprint),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["data"] = json.loads(item["data"])
        return item

    def current_version(self, project_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM versions WHERE project_id=? AND status IN ('baseline','committed') ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["data"] = json.loads(item["data"])
        return item

    def project_context_snapshot(self, project_id: str) -> dict[str, Any]:
        """Capture mutable project inputs before an asynchronous run is queued."""
        project = self.get_json("projects", project_id)
        profile = dict(project["data"] if project else {})
        calendars = [item["data"] for item in self.list_json("supplier_calendars", project_id)]
        watch = self.get_json("watch_plans", project_id)
        context = {"project": profile, "supplier_calendars": calendars,
                   "watch_plan": watch["data"] if watch else None}
        return {
            **context,
            "content_hash": digest(context),
            "captured_at": utcnow(),
        }

    def create_run(self, project_id: str, kind: str, event_id: str | None, version_id: str | None, idempotency_key: str | None, data: dict[str, Any]) -> dict[str, Any]:
        run_id = identifier()
        now = utcnow()
        with self.transaction() as db:
            if idempotency_key:
                row = db.execute(
                    "SELECT * FROM runs WHERE project_id=? AND kind=? AND idempotency_key=?",
                    (project_id, kind, idempotency_key),
                ).fetchone()
                if row is not None:
                    item = dict(row)
                    item["data"] = json.loads(item["data"])
                    return item
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, event_id, version_id, kind, "queued", encoded(data), now, now, idempotency_key),
            )
        return self.get_json("runs", run_id, project_id)  # type: ignore[return-value]

    def update_run(self, run_id: str, status: str, data: dict[str, Any]) -> None:
        with self.transaction() as db:
            prior = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            prior_data = json.loads(prior["data"]) if prior else {}
            db.execute(
                "UPDATE runs SET status=?, data=?, updated_at=? WHERE id=?",
                (status, encoded({**prior_data, **data}), utcnow(), run_id),
            )

    def claim_next_run(self) -> dict[str, Any] | None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM runs AS candidate WHERE candidate.status='queued' "
                "AND NOT EXISTS (SELECT 1 FROM runs AS active WHERE active.project_id=candidate.project_id AND active.status='running') "
                "ORDER BY candidate.created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE runs SET status='running', updated_at=? WHERE id=? AND status='queued'", (utcnow(), row["id"]))
            item = dict(row)
            item["status"] = "running"
            item["data"] = json.loads(item["data"])
            return item

    def recover_interrupted_runs(self) -> None:
        with self.transaction() as db:
            db.execute("UPDATE runs SET status='queued', updated_at=? WHERE status='running'", (utcnow(),))

"""Durable run metadata; result series stay in the existing JSON result files."""

import json
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time

TERMINAL = {"done", "error", "cancelled", "interrupted"}


class RunStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, project TEXT NOT NULL, status TEXT NOT NULL, owner INTEGER NOT NULL, updated REAL NOT NULL, metadata TEXT NOT NULL)")
            rows = db.execute("SELECT id,owner,metadata FROM runs WHERE status IN ('queued','running','cancelling')").fetchall()
            for rid, owner, metadata in rows:
                try:
                    os.kill(owner, 0)
                except ProcessLookupError:
                    data = json.loads(metadata)
                    data.update(status="interrupted", error="Previous worker process exited before completion")
                    db.execute("UPDATE runs SET status=?,metadata=?,updated=? WHERE id=?",
                               ("interrupted", json.dumps(data), time.time(), rid))
                except PermissionError:
                    pass  # inability to inspect a live process is not proof it exited

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, data):
        keys = ("run_id", "project", "scenario_name", "engine", "status", "created", "duration", "progress", "error", "label", "study_id")
        metadata = {k: data[k] for k in keys if k in data}
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated=excluded.updated,metadata=excluded.metadata",
                       (data["run_id"], data["project"], data["status"], os.getpid(), time.time(), json.dumps(metadata)))

    def get(self, project, run_id):
        with self.connect() as db:
            row = db.execute("SELECT metadata FROM runs WHERE project=? AND id=?", (project, run_id)).fetchone()
        if not row:
            raise FileNotFoundError(run_id)
        return json.loads(row[0])

    def list(self, project):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT metadata FROM runs WHERE project=? ORDER BY updated DESC", (project,))]

    def delete(self, project, run_id):
        with self.connect() as db:
            db.execute("DELETE FROM runs WHERE project=? AND id=?", (project, run_id))

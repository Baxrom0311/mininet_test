"""Durable, indexed run metadata; result series stay in separate JSON files."""

from contextlib import closing, contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

TERMINAL = {"done", "error", "cancelled", "interrupted"}
PENDING = {"queued", "running", "cancelling"}
METADATA_KEYS = ("run_id", "project", "scenario_name", "engine", "engine_label", "status", "created", "duration",
                 "wall_s", "progress", "error", "label", "study_id", "study_param", "study_value", "study_index",
                 "study_param2", "study_value2", "study_index2", "experiment_id", "replication_index",
                 "replication_count", "seed", "rng_run", "campaign_id", "campaign_variant")


class RunStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            columns = db.execute("PRAGMA table_info(runs)").fetchall()
            if not columns:
                self._create(db, "runs")
            elif [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]] != ["project", "id"]:
                self._backup_legacy(db)
                self._migrate(db)
            for field in ("created", "status", "engine", "study_id", "experiment_id"):
                parts = "created DESC,id DESC" if field == "created" else f"{field},created DESC,id DESC"
                db.execute(f"CREATE INDEX IF NOT EXISTS runs_project_{field} ON runs(project,{parts})")
            rows = db.execute("SELECT id,project,owner,metadata FROM runs WHERE status IN ('queued','running','cancelling')").fetchall()
            for rid, project, owner, metadata in rows:
                try:
                    os.kill(owner, 0)
                except ProcessLookupError:
                    data = json.loads(metadata)
                    data.update(status="interrupted", error="Previous worker process exited before completion")
                    db.execute("UPDATE runs SET status=?,metadata=?,updated=? WHERE project=? AND id=?",
                               ("interrupted", json.dumps(data), time.time(), project, rid))
                except PermissionError:
                    pass  # Inability to inspect a live process is not proof it exited.

    @staticmethod
    def _create(db, name):
        # Table names are fixed internal constants, never user input.
        db.execute(f"CREATE TABLE {name} (id TEXT NOT NULL,project TEXT NOT NULL,status TEXT NOT NULL,"
                   "owner INTEGER NOT NULL,updated REAL NOT NULL,metadata TEXT NOT NULL,created REAL NOT NULL,"
                   "engine TEXT,study_id TEXT,experiment_id TEXT,result_mtime_ns INTEGER,result_size INTEGER,"
                   "PRIMARY KEY(project,id))")

    def _migrate(self, db):
        # DDL and data copy must roll back together if any legacy row cannot be read.
        db.execute("BEGIN IMMEDIATE")
        self._create(db, "runs_migrating")
        for rid, project, status, owner, updated, raw in db.execute("SELECT id,project,status,owner,updated,metadata FROM runs").fetchall():
            data = {**json.loads(raw), "run_id": rid, "project": project, "status": status}
            self._write(db, data, table="runs_migrating", owner=owner, updated=updated)
        db.execute("DROP TABLE runs")
        db.execute("ALTER TABLE runs_migrating RENAME TO runs")

    def _backup_legacy(self, db):
        backup = Path(self.path + ".pre-index.bak")
        if backup.exists():
            return
        fd, temporary = tempfile.mkstemp(prefix=backup.name + "-", dir=backup.parent)
        os.close(fd)
        try:
            # SQLite backup includes committed WAL content; copying only the main file does not.
            with closing(sqlite3.connect(temporary)) as target:
                db.backup(target)
            os.replace(temporary, backup)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _write(db, data, signature=None, table="runs", owner=None, updated=None):
        metadata = {k: data[k] for k in METADATA_KEYS if k in data}
        created = data.get("created") or 0
        created = float(created) if isinstance(created, (int, float)) and math.isfinite(created) else 0
        metadata["created"] = created
        mtime, size = signature if signature is not None else (None, None)
        db.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project,id) DO UPDATE SET "
                   "status=excluded.status,owner=excluded.owner,updated=excluded.updated,metadata=excluded.metadata,"
                   "created=excluded.created,engine=excluded.engine,study_id=excluded.study_id,experiment_id=excluded.experiment_id,"
                   f"result_mtime_ns=COALESCE(excluded.result_mtime_ns,{table}.result_mtime_ns),"
                   f"result_size=COALESCE(excluded.result_size,{table}.result_size)",
                   (data["run_id"], data["project"], data["status"], os.getpid() if owner is None else owner,
                    time.time() if updated is None else updated, json.dumps(metadata, allow_nan=False), created,
                    data.get("engine"), data.get("study_id"), data.get("experiment_id"), mtime, size))

    def save(self, data, signature=None):
        with self.connect() as db:
            self._write(db, data, signature)

    def get(self, project, run_id):
        with self.connect() as db:
            row = db.execute("SELECT metadata FROM runs WHERE project=? AND id=?", (project, run_id)).fetchone()
        if not row:
            raise FileNotFoundError(run_id)
        return json.loads(row[0])

    def get_many(self, project, ids):
        if not ids:
            return {}
        if len(ids) > 256:
            raise ValueError("at most 256 run identities")
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as db:
            return {rid: json.loads(raw) for rid, raw in db.execute(
                f"SELECT id,metadata FROM runs WHERE project=? AND id IN ({placeholders})", [project, *ids])}

    @staticmethod
    def _where(project, filters):
        clauses, params = ["project=?"], [project]
        for field, value in filters.items():
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"invalid {field} filter")
            if field == "status":
                valid = value in TERMINAL | PENDING | {"unknown"}
            elif field == "engine":
                valid = value in ("analytic", "ns3", "mininet")
            else:
                valid = isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,60}", value)
            if not valid:
                raise ValueError(f"invalid {field} filter")
            clauses.append(f"{field}=?")
            params.append(value)
        return " AND ".join(clauses), params

    def page(self, project, limit=None, offset=0, *, status=None, engine=None, study_id=None, experiment_id=None):
        if (limit is not None and (type(limit) is not int or not 1 <= limit <= 200)
                or type(offset) is not int or offset < 0 or (limit is None and offset)):
            raise ValueError("limit: 1..200; offset: nonnegative integer")
        where, params = self._where(project, {"status": status, "engine": engine, "study_id": study_id,
                                              "experiment_id": experiment_id})
        with self.connect() as db:
            db.execute("BEGIN")  # Count and page share a read snapshot during concurrent writes.
            total = db.execute(f"SELECT COUNT(*) FROM runs WHERE {where}", params).fetchone()[0]
            sql = f"SELECT metadata FROM runs WHERE {where} ORDER BY created DESC,id DESC"
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params += [limit, offset]
            rows = [json.loads(raw) for raw, in db.execute(sql, params)]
        return {"runs": rows, "total": total, "limit": limit, "offset": offset}

    def list(self, project, **filters):
        return self.page(project, **filters)["runs"]

    def signatures(self, project):
        with self.connect() as db:
            return {rid: (mtime, size) for rid, mtime, size in db.execute(
                "SELECT id,result_mtime_ns,result_size FROM runs WHERE project=? AND result_mtime_ns IS NOT NULL", (project,))}

    def delete(self, project, run_id):
        with self.connect() as db:
            db.execute("DELETE FROM runs WHERE project=? AND id=?", (project, run_id))

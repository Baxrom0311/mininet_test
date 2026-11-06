"""Campaign plans share the run registry's SQLite transaction and project scope."""

import json
import re


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", value):
        raise ValueError("invalid project/campaign identity")
    return value


class CampaignStore:
    def __init__(self, runs):
        self.runs = runs
        with runs.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS campaigns (project TEXT NOT NULL,id TEXT NOT NULL,"
                       "created REAL NOT NULL,plan TEXT NOT NULL,summary TEXT,PRIMARY KEY(project,id))")
            if "summary" not in {c[1] for c in db.execute("PRAGMA table_info(campaigns)")}:
                db.execute("ALTER TABLE campaigns ADD COLUMN summary TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS campaigns_project_created ON campaigns(project,created DESC,id DESC)")

    @staticmethod
    def put(db, plan):
        summary = {k: v for k, v in plan.items() if k not in ("project_snapshot", "runtime")}
        db.execute("INSERT INTO campaigns(project,id,created,plan,summary) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(project,id) DO UPDATE SET plan=excluded.plan,summary=excluded.summary",
                   (plan["project"], plan["campaign_id"], plan["created"], json.dumps(plan, allow_nan=False),
                    json.dumps(summary, allow_nan=False)))

    def get(self, project, cid, *, full=True):
        with self.runs.connect() as db:
            column = "plan" if full else "COALESCE(summary,plan)"
            row = db.execute(f"SELECT {column} FROM campaigns WHERE project=? AND id=?", (safe_id(project), safe_id(cid))).fetchone()
        if not row:
            raise FileNotFoundError(cid)
        return json.loads(row[0])

    def list(self, project, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("limit: 1..200; offset: nonnegative integer")
        with self.runs.connect() as db:
            db.execute("BEGIN")
            total = db.execute("SELECT COUNT(*) FROM campaigns WHERE project=?", (safe_id(project),)).fetchone()[0]
            plans = [json.loads(p) for p, in db.execute("SELECT COALESCE(summary,plan) FROM campaigns WHERE project=? ORDER BY created DESC,id DESC LIMIT ? OFFSET ?",
                                                       (project, limit, offset))]
        return {"campaigns": [{k: v for k, v in p.items() if k not in ("project_snapshot", "runtime", "owner_instance")} for p in plans],
                "total": total, "limit": limit, "offset": offset}

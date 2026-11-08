"""Reconcile JSON result files without rereading unchanged series on every poll."""

import json
import os
from pathlib import Path
import re
import threading

from lab.result_json import load_result


class ResultIndex:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        self.lock = threading.RLock()

    def record(self, project, result):
        stat = (self.root / project / (result["run_id"] + ".json")).stat()
        self.store.save({**result, "project": project}, signature=(stat.st_mtime_ns, stat.st_size))

    def reconcile(self, project, force=False):
        if not isinstance(project, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", project):
            raise ValueError("invalid project name")
        directory = self.root / project
        directory.mkdir(parents=True, exist_ok=True)
        report = {"scanned": 0, "updated": 0, "removed": 0, "invalid": 0}
        with self.lock:
            indexed, seen = self.store.signatures(project), set()
            rejected = self.store.invalid_signatures(project)
            with os.scandir(directory) as entries:
                for entry in entries:
                    rid = entry.name[:-5]
                    if (not entry.name.endswith(".json") or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", rid)
                            or not entry.is_file(follow_symlinks=False)):
                        continue
                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    seen.add(rid)
                    report["scanned"] += 1
                    signature = (stat.st_mtime_ns, stat.st_size)
                    if not force and indexed.get(rid) == signature:
                        continue
                    if not force and rejected.get(rid) == signature:
                        report["invalid"] += 1
                        continue
                    try:
                        with open(entry.path) as stream:
                            result = load_result(stream, rid)
                        if not isinstance(result, dict) or result.get("run_id") != rid:
                            raise ValueError("run_id must match its result filename")
                        for key in ("status", "engine", "study_id", "experiment_id"):
                            if result.get(key) is not None and not isinstance(result[key], str):
                                raise ValueError(f"invalid {key} metadata")
                    except (ValueError, TypeError) as error:
                        report["invalid"] += 1
                        self.store.reject_result(project, rid, signature, error)
                        continue
                    except OSError:
                        # Permission/read failures may recover without changing
                        # size/mtime. Do not cache them as deterministic corruption.
                        report["invalid"] += 1
                        continue
                    self.store.save({**result, "project": project, "status": result.get("status") or "unknown"},
                                    signature=signature)
                    report["updated"] += 1
            for rid in (indexed.keys() | rejected.keys()) - seen:
                self.store.delete(project, rid, preserve_pending=True)
                report["removed"] += 1
        return report

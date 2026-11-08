"""Explicit simulation inputs and a small, credential-free runtime manifest."""

import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def comparison_inputs(project, scenario, engine):
    """Replications may differ only in the explicit random seeds/substreams."""
    p, sc = copy.deepcopy(project), copy.deepcopy(scenario)
    p.pop("scenarios", None)
    sc.pop("seed", None)
    sc.pop("rng_run", None)
    if isinstance(sc.get("failures"), dict):
        sc["failures"].pop("seed", None)
    return {"project": p, "scenario": sc, "engine": engine}


def comparison_key(project, scenario, engine):
    return digest(comparison_inputs(project, scenario, engine))


def _git(*args):
    try:
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=2, check=True)
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _source_digest():
    # Include untracked Python modules too; git diff alone would miss new files.
    h = hashlib.sha256()
    paths = []
    for directory, dirs, files in os.walk(ROOT / "lab"):
        dirs[:] = [d for d in dirs if d not in {"node_modules", "__pycache__", "results", "web-react", ".venv"}]
        paths.extend(Path(directory, name) for name in files if name.endswith(".py"))
    for path in sorted(paths):
        h.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def capture(project, scenario_name, scenario, effective_scenario, engine):
    p = copy.deepcopy(project)
    p.setdefault("scenarios", {})[scenario_name] = copy.deepcopy(scenario)
    versions = {}
    for name in ("ns3", "cppyy", "fastapi", "numpy"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    revision, diff = _git("rev-parse", "HEAD"), _git("diff", "HEAD", "--", ".")
    # This engine switch affects results. Capture it explicitly, never dump env.
    switches = {"NETLAB_NS3_FAST": os.environ.get("NETLAB_NS3_FAST") == "1"} if engine == "ns3" else {}
    return {"schema": 1, "project": p, "scenario_name": scenario_name,
            "project_sha256": digest(p), "effective_scenario": copy.deepcopy(effective_scenario),
            "effective_scenario_sha256": digest(effective_scenario),
            "comparison_key": comparison_key(p, scenario, engine),
            "runtime": {"python": platform.python_version(), "system": platform.system(),
                        "machine": platform.machine(), "packages": versions, "switches": switches,
                        "lab_python_sha256": _source_digest(),
                        "git_commit": revision.decode().strip() if revision else None,
                        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest() if diff is not None else None,
                        "tracked_dirty": bool(diff) if diff is not None else None}}


def verify(record):
    """Validate exported input hashes before treating a run as reproducible."""
    if (not isinstance(record, dict) or type(record.get("schema")) is not int or record["schema"] != 1
            or not isinstance(record.get("project"), dict) or not isinstance(record.get("effective_scenario"), dict)):
        return False
    try:
        return (record.get("project_sha256") == digest(record["project"])
                and record.get("effective_scenario_sha256") == digest(record["effective_scenario"]))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False

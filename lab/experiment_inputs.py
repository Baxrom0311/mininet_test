"""Validate archived experiment inputs before grouping or computing statistics."""

import re

from lab.analysis_windows import finite
from lab.reproducibility import digest, verify
from lab.sampling import sample_interval


def _identity(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,60}", value)


def validate_cohort(runs):
    if not isinstance(runs, (list, tuple)) or not 1 <= len(runs) <= 25:
        raise ValueError("experiment: 1..25 run objects required")
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("experiment run must be an object")
        if not _identity(run.get("run_id")) or not _identity(run.get("experiment_id")):
            raise ValueError("invalid run/experiment identity")
        count, index = run.get("replication_count"), run.get("replication_index")
        if type(count) is not int or not 2 <= count <= 25:
            raise ValueError("invalid replication count")
        if type(index) is not int or not 0 <= index < count:
            raise ValueError("invalid replication index")
        if any(type(run.get(field)) is not int or not 1 <= run[field] <= 2147483647
               for field in ("seed", "rng_run")):
            raise ValueError("replication seed/rng_run: integer 1..2147483647")
        if run.get("engine") not in ("analytic", "ns3"):
            raise ValueError("experiment requires analytic/ns3 engine")
        if run.get("status") not in ("queued", "running", "cancelling", "done", "error", "cancelled", "interrupted", "unknown"):
            raise ValueError("invalid replication status")
        if "scenario" in run and not isinstance(run["scenario"], dict):
            raise ValueError("scenario metadata must be an object")
    if len({run["run_id"] for run in runs}) != len(runs):
        raise ValueError("replications must not reuse run identities")
    if len({run["replication_count"] for run in runs}) != 1 or len({run["experiment_id"] for run in runs}) != 1:
        raise ValueError("one experiment is required")
    expected = runs[0]["replication_count"]
    if len(runs) > expected:
        raise ValueError("invalid replication count")
    if (len({(run["seed"], run["rng_run"]) for run in runs}) != len(runs)
            or len({run["replication_index"] for run in runs}) != len(runs)):
        raise ValueError("replication seeds and indices must be distinct")
    return expected, runs[0]["experiment_id"]


def completed_inputs(run):
    """Return verified original inputs; absent legacy output metadata is allowed."""
    record = run.get("reproducibility")
    if not verify(record):
        raise ValueError("completed run lacks verified reproducibility inputs")
    project, name = record["project"], record.get("scenario_name")
    scenarios = project.get("scenarios")
    if not isinstance(name, str) or not isinstance(scenarios, dict) or not isinstance(scenarios.get(name), dict):
        raise ValueError("recorded scenario must be an object")
    scenario = scenarios[name]
    if not finite(scenario.get("duration")) or scenario["duration"] <= 0:
        raise ValueError("invalid recorded duration")
    if scenario.get("failures") is not None and not isinstance(scenario["failures"], dict):
        raise ValueError("recorded failures must be an object")
    runtime = record.get("runtime")
    if not isinstance(runtime, dict) or not isinstance(runtime.get("lab_python_sha256"), str) or not runtime["lab_python_sha256"]:
        raise ValueError("experiment requires a recorded runtime/source identity")
    if "scenario" in run and digest(run["scenario"]) != digest(scenario):
        raise ValueError("scenario metadata does not match recorded inputs")
    if "duration" in run and (not finite(run["duration"]) or run["duration"] != scenario["duration"]):
        raise ValueError("duration does not match recorded inputs")
    step = sample_interval({**run, "scenario": scenario, "duration": scenario["duration"]})
    if run["engine"] == "analytic" and "sample_s" in run and (
            not finite(scenario.get("sample", 1)) or step != scenario.get("sample", 1)):
        raise ValueError("analytic sample_s does not match recorded inputs")
    flows = run.get("flows", {})
    if not isinstance(flows, dict):
        raise ValueError("flows must be an object")
    for fid, flow in flows.items():
        if not isinstance(fid, str) or not fid:
            raise ValueError("flow identity must be a nonempty string")
        if not isinstance(flow, dict):
            raise ValueError("flow must be an object")
        if not isinstance(flow.get("summary", {}), dict):
            raise ValueError("flow summary must be an object")
        if not isinstance(flow.get("series", []), list):
            raise ValueError("flow series must be an array")
        if any(not isinstance(point, dict) for point in flow.get("series", [])):
            raise ValueError("flow series must contain sample objects")
    return record, scenario

"""Read a study's durable rows without hiding unsuccessful or missing variants."""

from collections import Counter
import math
import re

from lab.engines.run_store import PENDING, TERMINAL


def describe(records, *, project=None, study_id=None, active=None, max_runs=49):
    """Summarize observed rows; legacy rows cannot establish a complete plan."""
    records = list(records)
    active = active or {}
    if not records:
        raise FileNotFoundError(study_id or "study")
    study_id = study_id or records[0].get("study_id")
    project = project or records[0].get("project")
    totals = [record.get("study_total") for record in records]
    has_plan = any("study_total" in record or "study_position" in record for record in records)
    valid_totals = {value for value in totals if type(value) is int and 1 <= value <= max_runs}
    planned = next(iter(valid_totals)) if len(valid_totals) == 1 else None
    planning = "known" if planned is not None and all(value == planned and type(value) is int for value in totals) else "invalid" if has_plan else "legacy"
    expected = [f"{study_id}-v{i + 1:02d}" for i in range(planned)] if planning == "known" else []
    invalid = []
    rows = []
    for record in records:
        rid = record.get("run_id")
        position = record.get("study_position")
        if (record.get("study_id") != study_id or planning == "invalid" or planning == "known" and (
                type(position) is not int or not 0 <= position < planned or rid != expected[position])):
            invalid.append(rid)
        state = record.get("status") if isinstance(record.get("status"), str) else "unknown"
        state = state or "unknown"
        progress = 1. if state in TERMINAL else active.get(rid, {}).get("progress", record.get("progress", 0))
        if type(progress) not in (int, float) or not math.isfinite(progress):
            progress = 0.
        rows.append({key: record.get(key) for key in (
            "run_id", "study_position", "study_index", "study_index2", "study_value", "study_value2", "label", "error")}
                    | {"status": state, "progress": min(1., max(0., progress))})
    rows.sort(key=lambda row: (row["study_position"] if type(row["study_position"]) is int else max_runs,
                               str(row["run_id"])))
    observed = {row["run_id"] for row in rows}
    invalid.extend(rid for rid, count in Counter(row["run_id"] for row in rows).items() if count > 1 and rid not in invalid)
    missing = [rid for rid in expected if rid not in observed]
    states = [row["status"] for row in rows]
    observed_settled = all(state in TERMINAL for state in states)
    complete_plan = planning == "known" and not missing and not invalid
    if missing or invalid or any(state not in PENDING | TERMINAL for state in states):
        state = "incomplete"
    elif any(state in PENDING for state in states):
        state = "queued" if all(state == "queued" for state in states) else "running"
    elif "error" in states:
        state = "failed"
    elif "interrupted" in states:
        state = "interrupted"
    elif "cancelled" in states:
        state = "cancelled"
    else:
        state = "done" if complete_plan else "incomplete"
    return {"study_id": study_id, "project": project, "planned_runs": planned if planning == "known" else None,
            "observed_runs": len(rows), "planning": planning, "plan_known": planning == "known",
            "status": state, "complete": complete_plan and state == "done",
            "settled": complete_plan and observed_settled, "observed_settled": observed_settled,
            "progress": min(1., sum(row["progress"] for row in rows) / planned) if planning == "known" else None,
            "statuses": dict(Counter(states)), "missing_run_ids": missing, "invalid_run_ids": invalid, "runs": rows}


def status(queue, project, study_id):
    if not isinstance(study_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", study_id):
        raise ValueError("invalid study id")
    with queue.queue_lock:
        queue.result_index.reconcile(project)
        records = queue.store.list(project, study_id=study_id)
        with queue._lock:
            active = {rid: dict(row) for rid, row in queue.active.items() if row.get("project") == project}
        result = describe(records, project=project, study_id=study_id, active=active,
                          max_runs=queue.MAX_STUDY_RUNS)
        notice = getattr(queue, "_study_notices", {}).get(study_id)
        futures = getattr(queue, "futures", {})
        owned_ids = set(notice["run_ids"]) if notice and notice.get("project") == project else set()
        orphaned = [row["run_id"] for row in result["runs"] if row["run_id"] in owned_ids and row["status"] in PENDING
                    and (row["run_id"] not in futures or futures[row["run_id"]].done())]
        result["orphaned_run_ids"] = orphaned
        if orphaned:
            live = any(row["status"] in PENDING and row["run_id"] not in orphaned for row in result["runs"])
            result.update(status="incomplete" if live else "interrupted", complete=False, settled=False)
        return result


def cancel(queue, project, study_id):
    before = status(queue, project, study_id)
    requested, rejected = [], []
    for row in before["runs"]:
        if row["status"] in TERMINAL:
            continue
        try:
            queue.cancel(project, row["run_id"])
            requested.append(row["run_id"])
        except Exception as error:
            rejected.append({"run_id": row["run_id"], "error": str(error)})
    return {**status(queue, project, study_id), "cancel_requested": len(requested),
            "cancel_requested_run_ids": requested, "not_cancelled": rejected}

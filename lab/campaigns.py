"""Bounded baseline/variant campaigns using the existing shared run executor."""

import copy
import time

from lab.analysis_windows import finite, specification
from lab.engines.campaign_store import CampaignStore, safe_id
from lab.engines.manager import new_run_id
from lab.engines.run_store import PENDING, TERMINAL
from lab.engines.run_submission import PreparedRun, submit
from lab.experiments import summarize, variants
from lab.experiment_comparison import compare, design_diff
from lab.reproducibility import capture, comparison_inputs

MAX_CAMPAIGN_RUNS = 100


def prepare_experiment(queue, project, name, engine, repetitions, seed, extra=None):
    if engine not in ("analytic", "ns3"):
        raise ValueError("independent replications require analytic or ns3")
    project, _, _ = queue._prepare(project, name, engine)
    projects = variants(project, name, repetitions, seed)
    eid, jobs = new_run_id(), []
    for index, p in enumerate(projects):
        p, sc, pname = queue._prepare(p, name, engine)
        metadata = {**(extra or {}), "experiment_id": eid, "replication_index": index,
                    "replication_count": repetitions, "seed": seed, "rng_run": index + 1}
        jobs.append(PreparedRun(new_run_id(), pname, p, name, sc, engine, metadata))
    return {"experiment_id": eid, "run_ids": [j.run_id for j in jobs]}, jobs


class CampaignManager:
    def __init__(self, queue):
        self.queue = queue
        self.instance_id = new_run_id()
        self.store = CampaignStore(queue.store)

    def prepare(self, body):
        param, values = body.get("param"), body.get("values")
        if not isinstance(param, dict) or not isinstance(values, list) or not 1 <= len(values) <= 8:
            raise ValueError("param: object; values: 1..8 scalar variant values")
        if any(not (isinstance(v, str) or finite(v)) for v in values):
            raise ValueError("variant values must be strings or finite numbers")
        if param.get("kind") == "failure" and param.get("field") == "seed":
            raise ValueError("campaign seed controls paired failure seeds; choose a timing parameter")
        repetitions, seed = body.get("repetitions", 3), body.get("seed", 1)
        if type(repetitions) is not int or not 2 <= repetitions <= 25 or (len(values) + 1) * repetitions > MAX_CAMPAIGN_RUNS:
            raise ValueError("repetitions: 2..25; campaign total <= 100 runs")
        name, engine = body.get("scenario"), body.get("engine", "analytic")
        if engine not in ("analytic", "ns3"):
            raise ValueError("campaigns require analytic or ns3")
        project, sc, pname = self.queue._prepare(body.get("project") or {}, name, engine)
        window = None
        if body.get("start_s") is not None or body.get("end_s") is not None:
            window = specification(sc["duration"], body.get("start_s"), body.get("end_s"))
        cid, conditions, jobs = new_run_id(), [], []
        baseline_inputs = comparison_inputs(project, sc, engine)
        for index, value in enumerate([None, *values]):
            key = "baseline" if index == 0 else f"v{index:02d}"
            p = copy.deepcopy(project)
            if index:
                try:
                    self.queue._apply_param(p, p["scenarios"][name], param, value, engine)
                except (TypeError, KeyError, OverflowError) as exc:
                    raise ValueError(f"invalid campaign parameter: {exc}") from exc
            condition, prepared = prepare_experiment(self.queue, p, name, engine, repetitions, seed,
                {"campaign_id": cid, "campaign_variant": key})
            condition.update(key=key, value=value,
                label="Baseline (original)" if index == 0 else f"{self.queue._param_label(param)}={value}")
            condition["design_diff"] = design_diff(baseline_inputs, comparison_inputs(p, p["scenarios"][name], engine))
            for job in prepared:
                job.extra["label"] = condition["label"][:80]
            conditions.append(condition)
            jobs.extend(prepared)
        runtime = capture(project, name, sc, None, engine)["runtime"]
        plan = {"schema": 1, "campaign_id": cid, "project": pname, "created": time.time(), "scenario": name,
                "engine": engine, "param": copy.deepcopy(param), "values": list(values), "repetitions": repetitions,
                "seed": seed, "run_count": len(jobs), "window": window, "conditions": conditions,
                "project_snapshot": project, "runtime": runtime, "owner_instance": self.instance_id}
        return plan, jobs

    @staticmethod
    def public_plan(plan):
        return {k: v for k, v in plan.items() if k not in ("project_snapshot", "runtime", "owner_instance")}

    def preview(self, body):
        plan, _ = self.prepare(body)
        return {**self.public_plan(plan), "submission": "preview", "scheduled": False}

    def start(self, body):
        plan, jobs = self.prepare(body)
        submit(self.queue, jobs, campaign=plan)
        return self.public_plan(plan)

    def _read(self, project, cid, *, full=False):
        plan = self.store.get(project, cid, full=full)
        if plan.get("schema") != 1 or not 2 <= len(plan["conditions"]) <= 9 or type(plan["repetitions"]) is not int or not 2 <= plan["repetitions"] <= 25:
            raise ValueError("invalid campaign plan")
        for index, c in enumerate(plan["conditions"]):
            if c["key"] != ("baseline" if index == 0 else f"v{index:02d}") or len(c["run_ids"]) != plan["repetitions"]:
                raise ValueError("invalid campaign condition")
            safe_id(c["experiment_id"])
        ids = [rid for c in plan["conditions"] for rid in c["run_ids"]]
        if (plan["project"] != project or plan["campaign_id"] != cid or len(ids) != plan["run_count"]
                or len(set(ids)) != len(ids) or not 1 <= len(ids) <= MAX_CAMPAIGN_RUNS):
            raise ValueError("invalid campaign plan")
        for rid in ids:
            safe_id(rid)
        return plan, ids

    def _snapshot(self, project, cid):
        plan, ids = self._read(project, cid)
        self.queue.result_index.reconcile(project)
        with self.queue.queue_lock:
            data = self.queue.store.get_many(project, ids)
            with self.queue._lock:
                active = {rid: dict(a) for rid, a in self.queue.active.items() if rid in data}
            known, rows, conditions = {}, [], []
            for c in plan["conditions"]:
                counts, missing, invalid = {}, [], []
                for index, rid in enumerate(c["run_ids"]):
                    record = data.get(rid)
                    matches = record and all(record.get(k) == v for k, v in (
                        ("campaign_id", cid), ("campaign_variant", c["key"]), ("experiment_id", c["experiment_id"])))
                    state = record["status"] if matches else "invalid" if record else "missing"
                    if matches:
                        known[rid] = record
                    else:
                        (invalid if record else missing).append(rid)
                    counts[state] = counts.get(state, 0) + 1
                    progress = 1.0 if state in TERMINAL else active.get(rid, {}).get("progress", 0)
                    rows.append({"run_id": rid, "variant": c["key"], "experiment_id": c["experiment_id"],
                                 "seed": plan["seed"], "rng_run": index + 1, "status": state, "progress": progress,
                                 "error": record.get("error") if matches else None})
                conditions.append({**c, "statuses": counts, "missing_run_ids": missing, "invalid_run_ids": invalid})
            states = [r["status"] for r in rows]
            pending = [r["run_id"] for r in rows if r["status"] in PENDING]
            live = {rid for rid in pending if rid in self.queue.futures and not self.queue.futures[rid].done()}
            orphaned = [rid for rid in pending if rid not in live] if plan.get("owner_instance") == self.instance_id else []
            if plan.get("submission") == "error":
                state = "submission_error"
            elif (plan.get("submission") == "admitted" and plan.get("owner_instance") == self.instance_id
                  and not any(rid in self.queue.futures for rid in ids)):
                state = "interrupted"
            elif orphaned and not live:
                state = "interrupted"
            elif any(s in PENDING for s in states):
                state = "queued" if all(s == "queued" for s in states) else "running"
            elif not all(s in TERMINAL for s in states):
                state = "incomplete"
            elif all(s == "done" for s in states):
                state = "done"
            else:
                state = "failed" if "error" in states else "interrupted" if "interrupted" in states else "cancelled"
            result = {**self.public_plan(plan), "conditions": conditions, "status": state,
                      "complete": state == "done" and plan.get("submission") == "submitted",
                      "progress": sum(r["progress"] for r in rows) / len(rows), "runs": rows, "orphaned_run_ids": orphaned}
        return plan, result, known

    def status(self, project, cid):
        return self._snapshot(project, cid)[1]

    def cancel(self, project, cid):
        _, _, known = self._snapshot(project, cid)
        rejected, requested = [], 0
        for rid, record in known.items():
            if record["status"] in TERMINAL:
                continue
            try:
                self.queue.cancel(project, rid)
                requested += 1
            except (ValueError, FileNotFoundError) as exc:
                rejected.append({"run_id": rid, "error": str(exc)})
        return {**self.status(project, cid), "cancel_requested": requested, "not_cancelled": rejected}

    def analysis(self, project, cid):
        _, status, known = self._snapshot(project, cid)
        plan, _ = self._read(project, cid, full=True)
        cohorts, experiments, errors = {}, [], {}
        options = {k: plan["window"][k] for k in ("start_s", "end_s")} if plan["window"] else {}
        for c in plan["conditions"]:
            records = []
            try:
                for rid in c["run_ids"]:
                    if rid in known:
                        record = self.queue.get(project, rid)
                        if any(record.get(k) != v for k, v in (("campaign_id", cid), ("campaign_variant", c["key"]),
                                                              ("experiment_id", c["experiment_id"]))):
                            raise ValueError("campaign membership changed while reading result")
                        records.append(record)
                summary = summarize(records, **options)
            except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
                summary, errors[c["key"]] = None, f"{type(exc).__name__}: {exc}"
            cohorts[c["key"]] = records
            experiments.append({"variant": c["key"], "summary": summary, "error": errors.get(c["key"])})
        comparisons = []
        for c in plan["conditions"][1:]:
            result, error = None, errors.get("baseline") or errors.get(c["key"])
            if not error:
                try:
                    result = compare(cohorts["baseline"], cohorts[c["key"]], **options)
                except (ValueError, KeyError, TypeError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
            comparisons.append({"variant": c["key"], "result": result, "error": error})
        report = {"schema": 1, "campaign": status, "experiments": experiments, "comparisons": comparisons,
                  "analysis_complete": status["complete"] and not errors and all(c["result"] is not None for c in comparisons),
                  "notes": ["Intervals are per comparison/metric; no multiple-comparison adjustment or automatic winner is applied.",
                            "Missing, failed or incompatible records remain visible and cannot produce a final interval.",
                            "analysis_complete means cohort comparisons were computed; each metric still needs valid coverage for its interval.",
                            "Runtime/source identities are checked by the paired comparison; code is not pinned or replayed on restart."]}
        return plan, report, cohorts

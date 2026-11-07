"""Prepare and validate every parametric-study variant before execution starts."""

import copy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math

from lab.engines.manager import MAX_STUDY_VALUES, STUDY_APP_FIELDS, STUDY_LINK_FIELDS, RunManager, new_run_id
from lab.engines.run_submission import PreparedRun


_FIELDS = {"link": STUDY_LINK_FIELDS, "app": STUDY_APP_FIELDS,
           "ap": ("tx_power", "channel"), "router": ("rip_update", "rip_timeout"),
           "failure": ("mtbf_s", "mttr_s", "seed")}


@dataclass
class StudyPlan:
    study_id: str
    jobs: list[PreparedRun]

    def response(self):
        return {"study_id": self.study_id, "runs": len(self.jobs), "run_ids": [job.run_id for job in self.jobs]}


def _parameter(param, name):
    if not isinstance(param, dict):
        raise ValueError(f"{name}: object required")
    param = copy.deepcopy(param)
    kind, field = param.get("kind"), param.get("field")
    if not isinstance(kind, str) or kind not in _FIELDS or field not in _FIELDS[kind]:
        raise ValueError(f"{name}: unknown kind or field")
    allowed = {"kind", "field", "label", kind} if kind != "failure" else {"kind", "field", "label", "group"}
    if kind == "router":
        allowed.add("all")
    if set(param) - allowed:
        raise ValueError(f"{name}: unknown parameter fields")
    if kind != "failure" and (not isinstance(param.get(kind), str) or not param[kind].strip()):
        raise ValueError(f"{name}.{kind}: target name required")
    if "all" in param and type(param["all"]) is not bool:
        raise ValueError(f"{name}.all: boolean required")
    if "group" in param and param["group"] not in ("links", "devices"):
        raise ValueError(f"{name}.group: links | devices")
    if "label" in param and not isinstance(param["label"], str):
        raise ValueError(f"{name}.label: string required")
    return param


def _axis(values, name):
    if not isinstance(values, list) or not 2 <= len(values) <= MAX_STUDY_VALUES:
        raise ValueError(f"{name}: array of 2..{MAX_STUDY_VALUES} scalar values required")
    for value in values:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError(f"{name}: empty scalar value")
            try:
                number = float(value)
            except ValueError:
                continue  # Unit-bearing values, codecs and qdisc names are valid scalar strings.
        elif type(value) in (int, float):
            number = value
        else:
            raise ValueError(f"{name}: strings or finite numbers required; booleans are not scalar values")
        try:
            finite = math.isfinite(number)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"{name}: finite scalar values required")
    return list(values)


def _value(param, value):
    if param["kind"] == "app" and param["field"] == "size":
        try:
            number = Decimal(str(value))
            if number <= 0 or number != number.to_integral_value():
                raise ValueError("study app size: positive integer required")
            return int(number)
        except InvalidOperation as error:
            raise ValueError("study app size: positive integer required") from error
    return value


def prepare_study(manager, project, scenario_name, engine, param, values, param2=None, values2=None):
    """Return complete, isolated jobs or fail without launching/admitting any of them."""
    param, values = _parameter(param, "param"), _axis(values, "values")
    two_d = param2 is not None
    if two_d:
        param2, values2 = _parameter(param2, "param2"), _axis(values2, "values2")
        if manager._param_label(param) == manager._param_label(param2):
            raise ValueError("ikki parametr bir xil bo'lmasin")
    else:
        if values2 is not None and values2 != []:
            raise ValueError("values2 requires param2")
        values2 = [None]
    total = len(values) * len(values2)
    if total > manager.MAX_STUDY_RUNS:
        raise ValueError(f"kombinatsiyalar soni {total} > {manager.MAX_STUDY_RUNS}")
    project, _, _ = manager._prepare(project, scenario_name, engine)
    parallelism = getattr(manager, "study_parallelism", RunManager.study_parallelism)(engine)
    if type(parallelism) is not int or not 1 <= parallelism <= 64:
        raise ValueError("study parallelism: integer 1..64 required")
    study_id, jobs = new_run_id(), []
    pdesc = {**param, "label": manager._param_label(param)}
    pdesc2 = {**param2, "label": manager._param_label(param2)} if two_d else None
    for j, value2 in enumerate(values2):
        for i, value in enumerate(values):
            p2 = copy.deepcopy(project)
            sc2 = p2["scenarios"][scenario_name]
            try:
                manager._apply_param(p2, sc2, param, _value(param, value), engine)
                if two_d:
                    manager._apply_param(p2, sc2, param2, _value(param2, value2), engine)
                # Reattach explicitly: preparation and provenance must see the same modified scenario.
                p2["scenarios"][scenario_name] = sc2
                p2, sc2, pname = manager._prepare(p2, scenario_name, engine)
            except (TypeError, KeyError, AttributeError, OverflowError) as error:
                raise ValueError(f"invalid study variant {len(jobs) + 1}: {error}") from error
            label = f"{pdesc['label']}={value}" + (f", {pdesc2['label']}={value2}" if two_d else "")
            extra = {"study_id": study_id, "study_param": copy.deepcopy(pdesc), "study_value": value,
                     "study_index": i, "study_position": len(jobs), "study_total": total, "label": label}
            if two_d:
                extra.update(study_param2=copy.deepcopy(pdesc2), study_value2=value2, study_index2=j)
            job = PreparedRun(f"{study_id}-v{len(jobs) + 1:02d}", pname, p2, scenario_name, sc2, engine, extra)
            job.resource_group, job.resource_parallelism = study_id, parallelism
            jobs.append(job)
    return StudyPlan(study_id, jobs)

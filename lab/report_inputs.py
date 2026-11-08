"""Validate the containers and numeric fields consumed by result reports."""

from lab.analysis_windows import finite


def _object(value, path):
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path}: object required")
    return value


def _array(value, path):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{path}: array required")
    return value


def _numbers(value, fields, path):
    for field in fields:
        if value.get(field) is not None and not finite(value[field]):
            raise ValueError(f"{path}.{field}: finite number or null required")


def _points(value, fields, path):
    for index, point in enumerate(_array(value, path)):
        if not isinstance(point, dict):
            raise ValueError(f"{path}[{index}]: sample object required")
        _numbers(point, fields, f"{path}[{index}]")


def _strings(value, path):
    if any(not isinstance(item, str) for item in _array(value, path)):
        raise ValueError(f"{path}: array of strings required")


def validate_run(run, path="result"):
    if not isinstance(run, dict):
        raise ValueError(f"{path}: result object required")
    for field in ("run_id", "status", "engine"):
        if run.get(field) is not None and not isinstance(run[field], str):
            raise ValueError(f"{path}.{field}: string required")
    for field in ("topology", "scenario"):
        _object(run.get(field), f"{path}.{field}")
    _numbers(run, ("duration", "wall_s"), path)
    _strings(run.get("notes"), f"{path}.notes")
    for fid, flow in _object(run.get("flows"), f"{path}.flows").items():
        loc = f"{path}.flows.{fid}"
        if not isinstance(flow, dict):
            raise ValueError(f"{loc}: flow object required")
        summary = _object(flow.get("summary"), loc+".summary")
        _numbers(summary, ("avg_thr_bps", "avg_delay_ms", "avg_jitter_ms", "loss_pct", "avg_rtt_ms"), loc+".summary")
        _points(flow.get("series"), ("t", "thr_bps", "delay_ms", "jitter_ms", "loss_pct", "rtt_ms", "mos"), loc+".series")
    for section, fields in (("links", ("t", "ab_bps", "ba_bps", "util", "queue_ab", "queue_ba", "drops_ab", "drops_ba")),
                            ("nodes", ("t", "routes", "fwd_pps", "rx_pps", "tx_pps", "noroute", "discards"))):
        for name, points in _object(run.get(section), f"{path}.{section}").items():
            _points(points, fields, f"{path}.{section}.{name}")
    for aid, app in _object(run.get("apps"), f"{path}.apps").items():
        if not isinstance(app, dict):
            raise ValueError(f"{path}.apps.{aid}: app object required")
        _numbers(app, ("avg_mos", "avg_page_s", "transfer_s", "reached", "avg_loss_pct"), f"{path}.apps.{aid}")
    availability = _object(run.get("availability"), f"{path}.availability")
    _object(availability.get("theoretical"), f"{path}.availability.theoretical")
    for fid, item in _object(availability.get("flows"), f"{path}.availability.flows").items():
        loc = f"{path}.availability.flows.{fid}"
        if not isinstance(item, dict):
            raise ValueError(f"{loc}: availability object required")
        _numbers(item, ("availability_pct", "downtime_s"), loc)
        _array(item.get("outages"), loc+".outages")
    for section, fields, lists in (("convergence", ("t", "converged_s"), ("devices",)),
                                   ("route_events", ("t",), ("added", "removed")), ("events", ("t",), ())):
        for index, item in enumerate(_array(run.get(section), f"{path}.{section}")):
            loc = f"{path}.{section}[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{loc}: event object required")
            _numbers(item, fields, loc)
            for field in lists:
                _strings(item.get(field), loc+"."+field)
    return run


def report_runs(value):
    runs = [value] if isinstance(value, dict) else value
    if not isinstance(runs, (list, tuple)):
        raise ValueError("report: result object or array required")
    for index, run in enumerate(runs):
        validate_run(run, f"results[{index}]")
    return runs

"""Strict result JSON parsing shared by history, retrieval and offline analysis."""

import json
import math


def _constant(value):
    raise ValueError(f"nonfinite result constant: {value}")


def _float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("nonfinite result number")
    return parsed


def load_json(stream):
    return json.load(stream, parse_constant=_constant, parse_float=_float)


def load_result(stream, expected_id=None):
    result = load_json(stream)
    if not isinstance(result, dict):
        raise ValueError("result must be an object")
    if expected_id is not None and result.get("run_id") != expected_id:
        raise ValueError("run_id must match its result filename")
    return result

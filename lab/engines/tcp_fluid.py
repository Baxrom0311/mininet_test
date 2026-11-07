"""Weighted max-min rates and bounded-transfer integration for the fluid engine.

Each filling pass freezes at least one flow. Each in-bin event finishes at least
one bounded transfer or consumes the remaining time. Neither loop truncates work
at a fixed number of flows/events.
"""

from collections import defaultdict
import math


def _weights(flows, capacity):
    weights = []
    for flow in flows:
        speed, limit = flow["spd"], flow["cap"]
        if not math.isfinite(speed) or speed <= 0 or math.isnan(limit) or limit < 0:
            raise ValueError("TCP speed must be positive finite; rate cap must be nonnegative")
        combined = defaultdict(list)
        for key, weight in flow["res"]:
            if not math.isfinite(weight) or weight <= 0:
                raise ValueError("TCP resource weights must be positive finite")
            value = capacity[key]
            if not math.isfinite(value) or value < 0:
                raise ValueError("TCP resource capacity must be nonnegative finite")
            combined[key].append(weight)
        weights.append({key: math.fsum(values) for key, values in combined.items()})
    return weights


def _fill(flows, weights, capacity):
    rates = [0.] * len(flows)
    left = dict(capacity)
    active = [i for i, flow in enumerate(flows) if weights[i] and flow["cap"] > 0]
    while active:
        demands = defaultdict(list)
        for i in active:
            for key, weight in weights[i].items():
                demands[key].append(weight * flows[i]["spd"])
        slopes = {key: math.fsum(values) for key, values in demands.items()}
        resource_limits = {key: max(0., left[key]) / slope for key, slope in slopes.items()}
        flow_limits = {i: max(0., flows[i]["cap"] - rates[i]) / flows[i]["spd"] for i in active}
        increment = min(*resource_limits.values(), *flow_limits.values())
        if not math.isfinite(increment):
            raise ArithmeticError("TCP allocation has no finite limiting constraint")
        # Identify limiting constraints before subtracting floats, guaranteeing progress.
        saturated = {key for key, value in resource_limits.items() if value == increment}
        capped = {i for i, value in flow_limits.items() if value == increment}
        deltas = defaultdict(list)
        for i in active:
            previous = rates[i]
            rates[i] = min(flows[i]["cap"], previous + increment * flows[i]["spd"])
            for key, weight in weights[i].items():
                deltas[key].append((rates[i] - previous) * weight)
        for key, values in deltas.items():
            left[key] = max(0., left[key] - math.fsum(values))
        active = [i for i in active if i not in capped and not saturated.intersection(weights[i])]
    for flow, rate in zip(flows, rates):
        flow["rate"] = rate


def allocate(flows, capacity):
    """Set flow rates using speed-weighted fairness and weighted resource costs."""
    _fill(flows, _weights(flows, capacity), capacity)


def advance(flows, capacity, remaining, setup_left, done_at, start, duration):
    """Set bin-average reserved/payload rates; update bytes, setup and completion times.

    Setup still reserves its allocated share, matching the analytical engine's
    existing handshake/slow-start approximation. Payload can never exceed the
    remaining file. Resource capacity is the budget left after inelastic traffic.
    """
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(start):
        raise ValueError("TCP integration needs finite time and positive duration")
    weights = _weights(flows, capacity)
    live = [i for i, flow in enumerate(flows) if weights[i]]
    reserved, delivered = [0.] * len(flows), [0.] * len(flows)
    for flow in flows:
        aid = flow["app"]["id"]
        if aid in remaining:
            size, delay = remaining[aid], setup_left.get(aid, 0.)
            if not math.isfinite(size) or size < 0 or not math.isfinite(delay) or delay < 0:
                raise ValueError("TCP remaining bytes and setup must be nonnegative finite")
    elapsed = 0.
    while live and elapsed < duration:
        current = [flows[i] for i in live]
        _fill(current, [weights[i] for i in live], capacity)
        horizon = duration - elapsed
        completion = {}
        for i in live:
            flow, aid = flows[i], flows[i]["app"]["id"]
            if aid in remaining:
                if remaining[aid] == 0:
                    completion[i] = 0.
                elif flow["rate"] > 0:
                    completion[i] = setup_left.get(aid, 0.) + remaining[aid] * 8 / flow["rate"]
        step = min([horizon, *completion.values()])
        finished = set()
        for i in live:
            flow, aid = flows[i], flows[i]["app"]["id"]
            reserved[i] += flow["rate"] * step
            if aid not in remaining:
                continue
            delay = setup_left.get(aid, 0.)
            wait = min(delay, step)
            setup_left[aid] = max(0., delay - step)
            completes = i in completion and completion[i] <= step
            # Use the limiting event's exact remaining bytes, avoiding subtraction
            # residue without advancing other flows by an artificial minimum step.
            bits = remaining[aid] * 8 if completes else min(remaining[aid] * 8, flow["rate"] * (step - wait))
            delivered[i] += bits
            remaining[aid] = max(0., remaining[aid] - bits / 8)
            if completes or remaining[aid] == 0:
                remaining[aid] = 0.
                setup_left[aid] = 0.
                done_at[aid] = start + elapsed + step
                finished.add(i)
        if step >= horizon:
            elapsed = duration
        else:
            # At least one limiting transfer must finish when time remains.
            if not finished:
                raise ArithmeticError("TCP completion event made no progress")
            elapsed += step
        live = [i for i in live if i not in finished]
    for i, flow in enumerate(flows):
        flow["rate"] = reserved[i] / duration
        if flow["app"]["id"] in remaining:
            flow["thr_fixed"] = delivered[i] / duration

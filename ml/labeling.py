"""Attack-window labels shared with small data-integrity regression tests."""

import json


def label_events(events, anomalies):
    out = events.copy()
    out["label"] = "normal"
    timestamps = out["ts"].astype(float)
    for _, event in anomalies.iterrows():
        targets = event.get("target_ips")
        if isinstance(targets, str):
            targets = json.loads(targets)
        if not isinstance(targets, list) or not targets:
            targets = [event["target_ip"]]  # legacy datasets
        attacker = event["attacker_ip"]
        pair = ((out["ip_src"] == attacker) & out["ip_dst"].isin(targets)
                | (out["ip_dst"] == attacker) & out["ip_src"].isin(targets))
        start = float(event["ts"])
        mask = pair & timestamps.between(start, start + max(float(event["duration_sec"]), 0))
        for column in ("routing", "run_id"):
            if column in out and column in anomalies:
                mask &= out[column] == event[column]
        out.loc[mask, "label"] = event["type"]
    return out

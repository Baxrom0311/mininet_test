"""Xom JSONL loglarni CSV/parquet dataset fayllariga aylantiradi."""

import json
import os

from config import DATA_DIR
from impairments import IMPAIRMENT_EVENTS
from traffic_gen import TRAFFIC_MIX


def build_dataset(topo, routing_mode, seed=None):
    import pandas as pd
    print("\n[Dataset] Ma'lumotlar qayta ishlanmoqda...")
    os.makedirs(f"{DATA_DIR}/datasets", exist_ok=True)

    datasets = {}
    for name, filename in [
        ("transport_events", "stats/transport_events.jsonl"),
        ("flow_stats", "stats/flow_stats.jsonl"),
        ("port_stats", "stats/port_stats.jsonl"),
        ("rtt", "stats/rtt_measurements.jsonl"),
        ("traffic_log", "stats/traffic_log.jsonl"),
        ("impairments", "stats/impairment_log.jsonl"),
        ("path_traces", "stats/path_traces.jsonl"),
        ("dns_queries", "stats/dns_queries.jsonl"),
        ("http_transactions", "stats/http_transactions.jsonl"),
        ("anomaly_events", "stats/anomaly_events.jsonl"),
        ("connection_states", "stats/connection_states.jsonl"),
        ("nat_translations", "stats/nat_translations.jsonl"),
    ]:
        path = f"{DATA_DIR}/{filename}"
        if not os.path.exists(path):
            continue
        rows = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try: rows.append(json.loads(line))
                    except json.JSONDecodeError: pass
        if rows:
            datasets[name] = pd.DataFrame(rows)

    # Path traces -> hop details
    if "path_traces" in datasets:
        pt = datasets["path_traces"]
        for col in ["path_switches", "as_path", "hops"]:
            if col in pt.columns:
                pt[col] = pt[col].apply(lambda x: json.dumps(x) if isinstance(x, list) else x)
        datasets["path_traces"] = pt

        hop_rows = []
        path_file = f"{DATA_DIR}/stats/path_traces.jsonl"
        if os.path.exists(path_file):
            with open(path_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try: rec = json.loads(line)
                    except json.JSONDecodeError: continue
                    for hop in rec.get("hops", []):
                        hop_rows.append({
                            "ts": rec["ts"], "src": rec["src"], "dst": rec["dst"],
                            "routing": rec.get("routing", ""),
                            "num_switch_hops": rec.get("num_switch_hops", 0),
                            "theoretical_rtt_ms": rec.get("theoretical_rtt_ms", 0),
                            "bottleneck_bw_mbps": rec.get("bottleneck_bw_mbps", 0),
                            "real_rtt_ms": rec.get("real_rtt_ms"),
                            "real_loss_pct": rec.get("real_loss_pct"),
                            "hop_num": hop.get("hop", 0),
                            "hop_from": hop.get("from", ""), "hop_to": hop.get("to", ""),
                            "hop_type": hop.get("type", ""),
                            "hop_as": hop.get("as", 0), "hop_role": hop.get("role", ""),
                            "hop_bw_mbps": hop.get("bw_mbps", 0),
                            "hop_delay_ms": hop.get("delay_ms", 0),
                            "hop_loss_pct": hop.get("loss_pct", 0),
                            "hop_jitter_ms": hop.get("jitter_ms", 0),
                            "hop_queue_size": hop.get("queue_size", 0),
                        })
            if hop_rows:
                datasets["hop_details"] = pd.DataFrame(hop_rows)

    # Port stats enrichment
    if "port_stats" in datasets:
        df = datasets["port_stats"]
        if "rx_bytes" in df.columns:
            df["total_bytes"] = df["rx_bytes"] + df["tx_bytes"]
            df["total_packets"] = df["rx_packets"] + df["tx_packets"]
            df["total_dropped"] = df["rx_dropped"] + df["tx_dropped"]
            df = df.sort_values(["dpid", "port", "ts"])
            ts_diff = df.groupby(["dpid", "port"])["ts"].diff().clip(lower=0.1)
            df["bytes_per_sec"] = df.groupby(["dpid", "port"])["total_bytes"].diff() / ts_diff
            df["packets_per_sec"] = df.groupby(["dpid", "port"])["total_packets"].diff() / ts_diff
            datasets["port_stats"] = df

    # Save
    for name, df in datasets.items():
        df.to_csv(f"{DATA_DIR}/datasets/{name}.csv", index=False)
        try: df.to_parquet(f"{DATA_DIR}/datasets/{name}.parquet", index=False)
        except Exception: pass

    # Metadata
    topo_json = {k: v for k, v in topo.items() if k != "links"}
    topo_json["links"] = {f"{k[0]}-{k[1]}": v for k, v in topo["links"].items()}
    with open(f"{DATA_DIR}/datasets/metadata.json", "w") as f:
        json.dump({"topology": topo_json, "routing": routing_mode,
                    "seed": seed,
                    "traffic_mix": [{"name": m[0], "weight": m[1], "proto": m[2]} for m in TRAFFIC_MIX],
                    "impairments": IMPAIRMENT_EVENTS}, f, indent=2, default=str)

    # Summary
    print(f"\n{'='*55}\n  DATASET XULOSA\n{'='*55}")
    total = 0
    for name, df in datasets.items():
        print(f"  {name:25s} {len(df):>8,} rows x {len(df.columns):>2} cols")
        total += len(df)
    print(f"  {'─'*45}\n  {'JAMI':25s} {total:>8,} rows")
    total_size = sum(os.path.getsize(f"{DATA_DIR}/datasets/{f}") for f in os.listdir(f"{DATA_DIR}/datasets"))
    print(f"  Hajm: {total_size/1024:.1f} KB\n{'='*55}")
    return datasets

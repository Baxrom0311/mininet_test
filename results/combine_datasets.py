#!/usr/bin/env python3
"""Combine per-routing-mode dataset CSVs into single files with a `routing` column."""

import glob
import os

import pandas as pd

RESULTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODES = ["l2_learn", "rip", "ospf", "isis", "eigrp", "bgp", "ecmp", "spf", "policy", "static", "hybrid"]
OUT_DIR = os.path.join(RESULTS_DIR, "combined")

os.makedirs(OUT_DIR, exist_ok=True)

file_types = sorted(
    os.path.basename(p)
    for p in glob.glob(os.path.join(RESULTS_DIR, f"five_as_{MODES[0]}", "datasets", "*.csv"))
)

print(f"Fayl turlari: {file_types}\n")

for fname in file_types:
    frames = []
    for mode in MODES:
        path = os.path.join(RESULTS_DIR, f"five_as_{mode}", "datasets", fname)
        if not os.path.exists(path):
            print(f"  [!] Yo'q: {path}")
            continue
        df = pd.read_csv(path)
        if "routing" not in df.columns:
            df.insert(0, "routing", mode)
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    out_path = os.path.join(OUT_DIR, fname)
    combined.to_csv(out_path, index=False)
    print(f"{fname:<28} {len(combined):>8,} qator  ->  combined/{fname}")

print(f"\nJami: {OUT_DIR}")

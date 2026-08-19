#!/usr/bin/env python3
"""Anomaliya (hujum) aniqlash: transport_events.csv dagi paketlarni
anomaly_events.csv dagi vaqt oynasi + IP moslik orqali label qilib,
PyTorch MLP klassifikator o'qitadi (GPU bo'lsa GPU'da)."""

import os
import random
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

# Reproducibility: seed every source of randomness before any split/init happens.
SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
torch.cuda.manual_seed_all(SEED)

# Defaults are repo-relative (resolved from this file's own location, not cwd)
# so the script works whether it's invoked from repo root, ml/, or elsewhere.
# The remote GPU server sets DATA_DIR/OUT_DIR explicitly, so this override
# path is unaffected.
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
DATA_DIR = os.environ.get("DATA_DIR", str(_REPO_ROOT / "results" / "combined"))
OUT_DIR = os.environ.get("OUT_DIR", str(_SCRIPT_DIR / "out"))
os.makedirs(OUT_DIR, exist_ok=True)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}")

# ─── Ma'lumotlarni yuklash ────────────────────────────────
print("Yuklanmoqda...")
events = pd.read_csv(os.path.join(DATA_DIR, "transport_events.csv"))
anomalies = pd.read_csv(os.path.join(DATA_DIR, "anomaly_events.csv"))
print(f"transport_events: {len(events):,} qator, anomaly_events: {len(anomalies):,} qator")

# ─── Label qilish: vaqt oynasi + IP moslik ────────────────
events["label"] = "normal"
events["ts"] = events["ts"].astype(float)
anomalies["ts"] = anomalies["ts"].astype(float)

for routing, grp in anomalies.groupby("routing"):
    mask_routing = events["routing"] == routing
    for _, ev in grp.iterrows():
        start, end = ev["ts"], ev["ts"] + max(float(ev["duration_sec"]), 1.0)
        # Require the packet's {ip_src, ip_dst} pair to touch BOTH the
        # attacker AND the target (not just either one) -- a row where only
        # one endpoint matches is bystander traffic from/to that host that
        # has nothing to do with this particular attacker->target flow.
        touches_attacker = (events["ip_src"] == ev["attacker_ip"]) | (events["ip_dst"] == ev["attacker_ip"])
        touches_target = (events["ip_src"] == ev["target_ip"]) | (events["ip_dst"] == ev["target_ip"])
        ip_match = touches_attacker & touches_target
        time_match = events["ts"].between(start, end)
        events.loc[mask_routing & ip_match & time_match, "label"] = ev["type"]

print("\nLabel taqsimoti:")
print(events["label"].value_counts())

# ─── Xususiyatlar (features) ──────────────────────────────
df = events.copy()
df["src_port"] = df["tcp_src"].fillna(df["udp_src"]).fillna(0)
df["dst_port"] = df["tcp_dst"].fillna(df["udp_dst"]).fillna(0)
df["ip_proto"] = df["ip_proto"].fillna(0)
df["ip_len"] = df["ip_len"].fillna(0)
df["ip_ttl"] = df["ip_ttl"].fillna(0)
df["tcp_flags"] = df["tcp_flags"].fillna(0)
df["is_icmp"] = df["icmp_type"].notna().astype(int)
df["is_arp"] = df["arp_op"].notna().astype(int)
df["is_syn"] = ((df["tcp_flags"].fillna(0).astype(int) & 0x02) != 0).astype(int)


def _sliding_window_features(sub, window=1.0):
    """(routing, ip_src) guruhi ichida 1s sirg'anuvchi oyna: paket tezligi,
    nechta xil dst/port bilan aloqa, SYN nisbati, bayt tezligi."""
    ts = sub["ts"].to_numpy()
    dst = sub["ip_dst"].fillna("").to_numpy()
    dport = sub["dst_port"].to_numpy()
    is_syn = sub["is_syn"].to_numpy()
    ip_len = sub["ip_len"].to_numpy()
    n = len(ts)
    pkt_rate = np.zeros(n, dtype=np.float32)
    uniq_dst = np.zeros(n, dtype=np.float32)
    uniq_dport = np.zeros(n, dtype=np.float32)
    syn_ratio = np.zeros(n, dtype=np.float32)
    byte_rate = np.zeros(n, dtype=np.float32)

    left = 0
    dst_counter, dport_counter = {}, {}
    syn_count, byte_sum = 0, 0.0
    for right in range(n):
        d = dst[right]
        dst_counter[d] = dst_counter.get(d, 0) + 1
        p = dport[right]
        dport_counter[p] = dport_counter.get(p, 0) + 1
        syn_count += int(is_syn[right])
        byte_sum += float(ip_len[right])
        while ts[right] - ts[left] > window:
            dl = dst[left]
            dst_counter[dl] -= 1
            if dst_counter[dl] == 0:
                del dst_counter[dl]
            pl = dport[left]
            dport_counter[pl] -= 1
            if dport_counter[pl] == 0:
                del dport_counter[pl]
            syn_count -= int(is_syn[left])
            byte_sum -= float(ip_len[left])
            left += 1
        wsize = right - left + 1
        pkt_rate[right] = wsize
        uniq_dst[right] = len(dst_counter)
        uniq_dport[right] = len(dport_counter)
        syn_ratio[right] = syn_count / wsize
        byte_rate[right] = byte_sum

    out = sub.copy()
    out["pkt_rate_1s"] = pkt_rate
    out["uniq_dst_1s"] = uniq_dst
    out["uniq_dport_1s"] = uniq_dport
    out["syn_ratio_1s"] = syn_ratio
    out["byte_rate_1s"] = byte_rate
    return out


print("\nVaqt-oynali (rate-based) xususiyatlar hisoblanmoqda...")
df = df.sort_values(["routing", "ip_src", "ts"])
parts = []
for (routing, ip_src), sub in df.groupby(["routing", "ip_src"], sort=False, dropna=False):
    if pd.isna(ip_src):
        parts.append(sub.assign(pkt_rate_1s=0, uniq_dst_1s=0, uniq_dport_1s=0, syn_ratio_1s=0, byte_rate_1s=0))
        continue
    parts.append(_sliding_window_features(sub))
df = pd.concat(parts, ignore_index=True)
print("Xususiyatlar tayyor.")

# ─── port_stats.csv bilan bog'lash ────────────────────────
# transport_events.dpid/in_port va port_stats.dpid/port bir xil switch-port
# maydonini bildiradi, shuning uchun (routing, dpid, port) bo'yicha eng yaqin
# vaqtli (nearest-ts) qo'shish real bytes/packets-per-sec va drop signalini
# beradi -- dataset_builder.py buni allaqachon hisoblab qo'ygan, lekin
# hech qaysi train skripti ulardan foydalanmagan edi.
print("\nport_stats.csv bilan bog'lanmoqda (dpid+port bo'yicha eng yaqin vaqt)...")
port_stats = pd.read_csv(os.path.join(DATA_DIR, "port_stats.csv"))
port_stats["ts"] = port_stats["ts"].astype(float)
port_rate_cols = ["bytes_per_sec", "packets_per_sec", "total_dropped"]
ps = (
    port_stats[["routing", "ts", "dpid", "port"] + port_rate_cols]
    .rename(columns={"port": "in_port"})
    .sort_values("ts")
)
df = df.sort_values("ts").reset_index(drop=True)
df = pd.merge_asof(
    df, ps,
    on="ts", by=["routing", "dpid", "in_port"],
    direction="nearest",
)
# NaN here means no port-stat poll was close in time for that dpid/port (e.g.
# the very first poll of the run, before any rate delta can be computed) --
# genuinely "no rate info available yet", not a join failure, so 0 is the
# correct neutral fill rather than imputed noise.
for c in port_rate_cols:
    df[c] = df[c].fillna(0)

feature_cols = [
    "ip_proto", "ip_len", "ip_ttl", "tcp_flags",
    "src_port", "dst_port", "is_icmp", "is_arp",
    "pkt_rate_1s", "uniq_dst_1s", "uniq_dport_1s", "syn_ratio_1s", "byte_rate_1s",
] + port_rate_cols
X = df[feature_cols].to_numpy(dtype=np.float32)
y_binary = (df["label"] != "normal").astype(int).to_numpy()

# Group key for the split: anomaly_events.csv has no per-attack event id, but
# rows from the same (routing, ip_src) host share the sliding-window features
# above (pkt_rate_1s etc. are computed per that exact group) -- letting rows
# from the same host land in both train and test would leak that host's
# traffic pattern rather than testing generalization to unseen hosts.
# fillna() BEFORE astype(str) is required: ip_src is missing for ~74% of rows
# (ARP/L2-only packets with no IP layer) and on pandas's "str" extension
# dtype, astype(str) is a no-op that leaves those as real NaN rather than the
# text "nan" -- string-concatenating a NaN produces NaN, so without the
# explicit fillna the resulting groups array is a NaN/str mix that crashes
# GroupShuffleSplit's internal np.unique(groups) call.
ip_src_key = df["ip_src"].fillna("__no_ip__").astype(str)
routing_key = df["routing"].fillna("__unknown_routing__").astype(str)
groups = (routing_key + "|" + ip_src_key).to_numpy()

print(f"\nBinary label taqsimoti: normal={sum(y_binary == 0):,}, anomaliya={sum(y_binary == 1):,}")

# ─── Train/test split (group-aware) + scaling ─────────────
gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=SEED)
train_idx, test_idx = next(gss.split(X, y_binary, groups=groups))
X_train, X_test = X[train_idx], X[test_idx]
y_train, y_test = y_binary[train_idx], y_binary[test_idx]

scaler = StandardScaler()
X_train = scaler.fit_transform(X_train)
X_test = scaler.transform(X_test)
joblib.dump(scaler, os.path.join(OUT_DIR, "scaler.joblib"))
joblib.dump(feature_cols, os.path.join(OUT_DIR, "feature_cols.joblib"))

X_train_t = torch.tensor(X_train, dtype=torch.float32, device=device)
y_train_t = torch.tensor(y_train, dtype=torch.float32, device=device).unsqueeze(1)
X_test_t = torch.tensor(X_test, dtype=torch.float32, device=device)
y_test_t = torch.tensor(y_test, dtype=torch.float32, device=device).unsqueeze(1)


class MLP(nn.Module):
    def __init__(self, in_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 64), nn.ReLU(),
            nn.Linear(64, 32), nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x):
        return self.net(x)


model = MLP(X_train.shape[1]).to(device)

# Class imbalance uchun pos_weight — sqrt bilan yumshatilgan
# (to'liq nisbat juda ko'p false-positive berardi)
n_pos = max(int(y_train.sum()), 1)
n_neg = len(y_train) - n_pos
pos_weight = torch.tensor([(n_neg / n_pos) ** 0.5], device=device)
criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

print("\nO'qitish boshlandi...")
EPOCHS = 30
BATCH = 4096
n = X_train_t.shape[0]
for epoch in range(EPOCHS):
    model.train()
    perm = torch.randperm(n, device=device)
    total_loss = 0.0
    for i in range(0, n, BATCH):
        idx = perm[i:i + BATCH]
        xb, yb = X_train_t[idx], y_train_t[idx]
        optimizer.zero_grad()
        out = model(xb)
        loss = criterion(out, yb)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * len(idx)
    if (epoch + 1) % 5 == 0 or epoch == 0:
        print(f"  epoch {epoch + 1:>3}/{EPOCHS}  loss={total_loss / n:.5f}")

# ─── Baholash ──────────────────────────────────────────────
model.eval()
with torch.no_grad():
    logits = model(X_test_t)
    preds = (torch.sigmoid(logits) > 0.5).int().cpu().numpy().ravel()

print("\n=== Natija (binary: normal vs anomaliya) ===")
print(classification_report(y_test, preds, target_names=["normal", "anomaliya"]))
print("Confusion matrix:")
print(confusion_matrix(y_test, preds))

torch.save(model.state_dict(), os.path.join(OUT_DIR, "anomaly_model.pt"))
print(f"\nModel saqlandi: {os.path.join(OUT_DIR, 'anomaly_model.pt')}")
print(f"Scaler saqlandi: {os.path.join(OUT_DIR, 'scaler.joblib')}")

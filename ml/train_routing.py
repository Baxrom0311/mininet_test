#!/usr/bin/env python3
"""Routing algoritmini bashorat qilish: path_traces.csv dagi
RTT/loss/hop xususiyatlaridan qaysi routing algoritm ishlatilganini
(11 sinf) aniqlaydigan PyTorch MLP klassifikator."""

import ast
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
from sklearn.preprocessing import LabelEncoder, StandardScaler

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

print("Yuklanmoqda...")
df = pd.read_csv(os.path.join(DATA_DIR, "path_traces.csv"))
print(f"path_traces: {len(df):,} qator")
print("\nRouting taqsimoti:")
print(df["routing"].value_counts())

def _parse_as_path_len(v):
    try:
        return len(ast.literal_eval(v)) if isinstance(v, str) else len(v)
    except (ValueError, SyntaxError, TypeError):
        return np.nan


df["as_path_len"] = df["as_path"].apply(_parse_as_path_len)
df["same_as"] = (df["src_as"] == df["dst_as"]).astype(float)

# ─── hop_details.csv bilan bog'lash ───────────────────────
# Har bir path_traces qatori (ts, src, dst, routing) bo'yicha bir nechta
# hop_details qatoriga (hop_num=0..N) mos keladi -- ular xuddi shu trace
# hodisasidan yozilgani uchun ts aniq mos keladi (asof/tolerance kerak emas).
# Bu yerda hop-darajasidagi haqiqiy signal (jitter, queue) ni yo'l darajasiga
# aggregatsiya qilib qo'shamiz -- path_traces o'zida jitter/queue umuman yo'q.
print("\nhop_details.csv bilan bog'lanmoqda (ts+src+dst+routing bo'yicha aggregatsiya)...")
hop_details = pd.read_csv(os.path.join(DATA_DIR, "hop_details.csv"))
hop_agg_cols = ["hop_bw_mbps", "hop_delay_ms", "hop_loss_pct", "hop_jitter_ms", "hop_queue_size"]
hop_agg = (
    hop_details.groupby(["ts", "src", "dst", "routing"])[hop_agg_cols]
    .mean()
    .rename(columns={c: f"{c}_mean" for c in hop_agg_cols})
    .reset_index()
)
hop_agg["hop_queue_size_max"] = (
    hop_details.groupby(["ts", "src", "dst", "routing"])["hop_queue_size"].max().to_numpy()
)
df = df.merge(hop_agg, on=["ts", "src", "dst", "routing"], how="left")
hop_feature_cols = [f"{c}_mean" for c in hop_agg_cols] + ["hop_queue_size_max"]

feature_cols = [
    "num_switch_hops", "num_total_hops",
    "theoretical_delay_ms", "theoretical_rtt_ms", "theoretical_loss_pct",
    "bottleneck_bw_mbps",
    "real_rtt_ms", "real_rtt_min", "real_rtt_max", "real_loss_pct",
    "as_path_len", "same_as", "src_as", "dst_as",
] + hop_feature_cols
df = df.dropna(subset=feature_cols + ["routing"])
X = df[feature_cols].to_numpy(dtype=np.float32)

le = LabelEncoder()
y = le.fit_transform(df["routing"])
n_classes = len(le.classes_)
print(f"\nSinflar ({n_classes}): {list(le.classes_)}")

# Group key for the split: PathTracer polls every ~15s, so the same (src,dst)
# host pair is re-traced many times -- a plain row-level split would let
# near-duplicate traces of the same pair leak into both train and test,
# inflating accuracy via memorization of that pair's fingerprint rather than
# genuine generalization to unseen host pairs.
groups = (df["src"].astype(str) + "|" + df["dst"].astype(str)).to_numpy()

gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=SEED)
train_idx, test_idx = next(gss.split(X, y, groups=groups))
X_train, X_test = X[train_idx], X[test_idx]
y_train, y_test = y[train_idx], y[test_idx]

scaler = StandardScaler()
X_train = scaler.fit_transform(X_train)
X_test = scaler.transform(X_test)
joblib.dump(scaler, os.path.join(OUT_DIR, "scaler.joblib"))
joblib.dump(le, os.path.join(OUT_DIR, "label_encoder.joblib"))
joblib.dump(feature_cols, os.path.join(OUT_DIR, "feature_cols.joblib"))

X_train_t = torch.tensor(X_train, dtype=torch.float32, device=device)
y_train_t = torch.tensor(y_train, dtype=torch.long, device=device)
X_test_t = torch.tensor(X_test, dtype=torch.float32, device=device)
y_test_t = torch.tensor(y_test, dtype=torch.long, device=device)


class MLP(nn.Module):
    def __init__(self, in_dim, n_classes):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 128), nn.ReLU(),
            nn.Linear(128, 64), nn.ReLU(),
            nn.Linear(64, n_classes),
        )

    def forward(self, x):
        return self.net(x)


model = MLP(X_train.shape[1], n_classes).to(device)
criterion = nn.CrossEntropyLoss()
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

print("\nO'qitish boshlandi...")
EPOCHS = 60
BATCH = 512
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
    if (epoch + 1) % 10 == 0 or epoch == 0:
        print(f"  epoch {epoch + 1:>3}/{EPOCHS}  loss={total_loss / n:.5f}")

model.eval()
with torch.no_grad():
    logits = model(X_test_t)
    preds = torch.argmax(logits, dim=1).cpu().numpy()

print("\n=== Natija (11-sinfli routing bashorati) ===")
print(classification_report(y_test, preds, target_names=le.classes_))
print("Confusion matrix:")
print(confusion_matrix(y_test, preds))

torch.save(model.state_dict(), os.path.join(OUT_DIR, "routing_model.pt"))
print(f"\nModel saqlandi: {os.path.join(OUT_DIR, 'routing_model.pt')}")
print(f"Scaler saqlandi: {os.path.join(OUT_DIR, 'scaler.joblib')}")
print(f"Label encoder saqlandi: {os.path.join(OUT_DIR, 'label_encoder.joblib')}")

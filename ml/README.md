# ML — anomaly detection & routing prediction

Two standalone PyTorch training scripts that consume the CSV datasets produced by
`dataset_builder.py` (see the repo root `README.md` / `docs/CLAUDE.md` for how those datasets
are generated). Both are plain scripts, not a package — run them directly with `python3`.

## Scripts

### `train_anomaly.py`

Binary classifier (normal vs. anomalous traffic) trained on `transport_events.csv`, labeled by
joining against `anomaly_events.csv` on time-window + IP match (attacker/target IP active
during the attack's `[ts, ts + duration]` window). Features are per-packet fields (proto, IP
length/TTL, TCP flags, ports) plus 1-second sliding-window rate features computed per
`(routing, ip_src)` group (packet rate, unique destinations/ports, SYN ratio, byte rate) — this
is what lets it catch scan/flood-style attacks rather than just single-packet signatures. A
3-layer MLP (`64 → 32 → 1`), trained with `BCEWithLogitsLoss` and a sqrt-dampened `pos_weight`
to counter class imbalance (attack traffic is a small fraction of all packets). Saves
`anomaly_model.pt` to `OUT_DIR`.

### `train_routing.py`

11-class classifier that predicts which routing mode (`l2_learn`/`rip`/`ospf`/`isis`/`eigrp`/
`bgp`/`ecmp`/`spf`/`policy`/`static`/`hybrid`) produced a given path trace, trained on
`path_traces.csv`. Features: hop counts, theoretical delay/RTT/loss, bottleneck bandwidth,
measured RTT (mean/min/max), measured loss, AS-path length, same-AS flag, src/dst AS. A 3-layer
MLP (`128 → 64 → n_classes`), trained with `CrossEntropyLoss`. Saves `routing_model.pt` to
`OUT_DIR`.

## Input data location

Both scripts default to repo-relative paths, resolved from the script's own location so they
work regardless of current working directory:

```
DATA_DIR = <repo_root>/results/combined
OUT_DIR  = <repo_root>/ml/out
```

Override both via environment variables when running against a different layout (e.g. the
remote GPU server's own copy of the combined dataset):

```bash
DATA_DIR=/root/ml/combined OUT_DIR=/root/ml/out python3 ml/train_anomaly.py
DATA_DIR=/root/ml/combined OUT_DIR=/root/ml/out python3 ml/train_routing.py
```

`train_anomaly.py` needs `transport_events.csv` + `anomaly_events.csv` **and** `port_stats.csv`
in `DATA_DIR` (the latter is joined in as real congestion features — `bytes_per_sec`,
`packets_per_sec`, `total_dropped` — by nearest-timestamp per `dpid`/`port`; missing without it).
`train_routing.py` needs `path_traces.csv` **and** `hop_details.csv` (joined in as per-hop
`bw/delay/loss/jitter/queue_size` aggregates). `results/combined/` already has all four as of
the last data collection in this repo.

## Outputs

Both scripts now write, into `OUT_DIR`:

- `anomaly_model.pt` / `routing_model.pt` — the trained model's `state_dict()`
- `scaler.joblib` — the fitted `StandardScaler`, needed to preprocess new data identically
- `feature_cols.joblib` — the exact ordered feature-column list the model expects
- `label_encoder.joblib` (`train_routing.py` only) — the fitted routing-mode `LabelEncoder`

Load them together at inference time rather than re-fitting a fresh scaler/encoder, or
predictions won't be comparable to training.

## Dependencies

`torch`, `pandas`, `numpy`, `scikit-learn` (`sklearn.model_selection`, `sklearn.preprocessing`,
`sklearn.metrics`), `joblib`. Uses CUDA automatically if available
(`torch.cuda.is_available()`), otherwise CPU. Pinned in `ml/requirements.txt` (not part of
`docker/requirements.txt`, which is scoped to the simulator, not model training):

```bash
pip install -r ml/requirements.txt
```

## Reproducibility

Both scripts seed `random`, `numpy`, and `torch` (including `torch.cuda.manual_seed_all`) with
a fixed seed near the top, and use `GroupShuffleSplit` (grouped by `(src, dst)` for
`train_routing.py`, by `(routing, ip_src)` for `train_anomaly.py`) instead of a plain row-level
split — the same host pair / flow group never appears in both train and test, avoiding the
memorization leakage a naive row-level split would allow given how repeatedly each pair/flow is
sampled in the raw data.

## Known accuracy — honest status, not verified this session

Neither script has been re-run since the fixes described above landed (no GPU/training
environment was exercised in this pass — this is a code + documentation change, not a
benchmark run). Going by the project's prior measured characterization, plus what changed:

- **Anomaly detection** (`train_anomaly.py`): the last measured run had "reasonable" overall
  accuracy but weak precision/recall on the anomaly class specifically (better at not-crying-
  wolf on normal traffic than at reliably catching attacks). This session's label-matching fix
  (labels now require the packet to touch **both** the attacker and target IP, not just one —
  an 82% drop in the number of rows labeled "attack" on the current dataset, since most of the
  old matches were bystander traffic) plus the port_stats.csv congestion features and the
  group-aware split are all aimed at this exact problem, but **none of this has been re-run
  through `classification_report` yet** — re-run the script and read its actual output before
  citing any number.
- **Routing-mode classification** (`train_routing.py`): the last measured run was close-to-
  random (11-class accuracy near the ~9% random-guess floor). Two independent fixes landed
  this session that directly target the root cause (two of the 11 modes, `static` and
  `l2_learn`, were literally computing identical paths; and most path-cost functions ranked
  paths identically across most host pairs, collapsing signal): `static` now uses a distinct
  bandwidth-weighted policy, and `five_as` gained an additional link that breaks the
  bandwidth/delay correlation for a meaningful fraction of host pairs (measured 68→75% of
  host pairs now show ≥2 distinct path signatures across the 11 modes, up from an audited 45%
  baseline). This should raise the classifier's ceiling, but **it has not been re-trained or
  re-measured** — a fresh `results/combined/` (re-run the data collection campaign against the
  updated `routing.py`/`topologies.py`) plus a fresh training run is required before trusting
  any new accuracy figure.

Treat both bullet points as "what changed and why we expect it to help," not results — run the
scripts yourself against freshly-collected data and read `classification_report`/
`confusion_matrix` output for actual numbers before citing any accuracy figure.

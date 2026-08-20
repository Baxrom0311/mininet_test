#!/usr/bin/env python3
"""Per-run dataset CSV'larni fayl turi bo'yicha bitta faylga birlashtirish.

Har bir run natijasi `results/<topo>_<mode>[_<suffix>]/datasets/*.csv` ko'rinishida bo'ladi,
masalan:

    results/five_as_ospf/datasets/            (topologiya + rejim)
    results/three_as_ospf_seed3/datasets/     (topologiya + rejim + seed — run_campaign.sh)

Skript shu papkalarni topib, bir xil nomli CSV'larni (transport_events.csv, path_traces.csv, ...)
birlashtiradi va har qatorga provenance ustunlarini qo'shadi, shunda birlashtirilgan qatorni
qaysi run yaratganini kuzatish mumkin:

    routing     - routing rejimi (agar CSV'da allaqachon bo'lsa, tegilmaydi)
    topology    - qaysi topologiya
    seed        - dir nomida `_seed<N>` bo'lsa, aks holda bo'sh
    run_id      - manba run papkasining bazaviy nomi (masalan `three_as_ospf_seed3`)
    source_dir  - results/ ga nisbatan manba yo'li (masalan `three_as_ospf_seed3/datasets`)

Foydalanish:
    python3 results/combine_datasets.py                      # barcha topologiya x rejim
    python3 results/combine_datasets.py --topology five_as   # faqat five_as* run'lar
    python3 results/combine_datasets.py --pattern 'five_as_*' # glob bilan run papkalari
"""

import argparse
import glob
import os
import re

import pandas as pd

RESULTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODES = ["l2_learn", "rip", "ospf", "isis", "eigrp", "bgp", "ecmp", "spf", "policy", "static", "hybrid"]
OUT_DIR = os.path.join(RESULTS_DIR, "combined")

# <topo>_<mode>[_<suffix>] — rejimlar uzunlik bo'yicha kamayish tartibida ("l2_learn"
# "learn"'dan, "ospf" "spf"'dan oldin sinalsin, aks holda noto'g'ri bo'linadi).
_MODE_ALT = "|".join(re.escape(m) for m in sorted(MODES, key=len, reverse=True))
_DIR_RE = re.compile(rf"^(?P<topo>.+)_(?P<mode>{_MODE_ALT})(?:_(?P<suffix>.+))?$")
_SEED_RE = re.compile(r"seed(\d+)")


def discover_runs(pattern=None, topology=None):
    """results/ ichidagi run papkalarini topib, provenance metama'lumoti bilan qaytaradi.

    Return: [{"dir": abs_path, "topo": str, "mode": str, "seed": str|"",
              "run_id": str, "source_dir": rel_path}], mode+run_id bo'yicha saralangan.
    """
    if pattern:
        candidates = glob.glob(os.path.join(RESULTS_DIR, pattern))
    else:
        candidates = glob.glob(os.path.join(RESULTS_DIR, "*"))

    runs = []
    for path in candidates:
        ds_dir = os.path.join(path, "datasets")
        if not os.path.isdir(ds_dir):
            continue
        base = os.path.basename(path.rstrip("/"))
        m = _DIR_RE.match(base)
        if not m:
            continue
        mode = m.group("mode")
        topo = m.group("topo")
        if topology and topo != topology:
            continue
        suffix = m.group("suffix") or ""
        seed_m = _SEED_RE.search(suffix)
        runs.append({
            "dir": ds_dir,
            "topo": topo,
            "mode": mode,
            "seed": seed_m.group(1) if seed_m else "",
            "run_id": base,
            "source_dir": os.path.relpath(ds_dir, RESULTS_DIR),
        })

    runs.sort(key=lambda r: (MODES.index(r["mode"]) if r["mode"] in MODES else 99, r["run_id"]))
    return runs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topology", help="Faqat shu topologiyaning run'larini birlashtirish (masalan five_as)")
    parser.add_argument("--pattern", help="results/ ga nisbatan run papkalari uchun glob (masalan 'five_as_*')")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    runs = discover_runs(pattern=args.pattern, topology=args.topology)
    if not runs:
        print("Hech qanday run papkasi topilmadi "
              "(results/<topo>_<mode>[_seed<N>]/datasets/ kutilgan edi).")
        return

    print(f"{len(runs)} ta run topildi:")
    for r in runs:
        print(f"  {r['run_id']:<28} -> {r['source_dir']}")
    print()

    # Barcha run'lar bo'ylab uchraydigan CSV fayl turlari
    file_types = sorted({
        os.path.basename(p)
        for r in runs
        for p in glob.glob(os.path.join(r["dir"], "*.csv"))
    })

    print(f"Fayl turlari: {file_types}\n")

    for fname in file_types:
        frames = []
        for r in runs:
            path = os.path.join(r["dir"], fname)
            if not os.path.exists(path):
                continue
            df = pd.read_csv(path)
            # Provenance ustunlari. "routing" allaqachon bo'lsa tegilmaydi (mavjud guard).
            if "routing" not in df.columns:
                df.insert(0, "routing", r["mode"])
            df["topology"] = r["topo"]
            df["seed"] = r["seed"]
            df["run_id"] = r["run_id"]
            df["source_dir"] = r["source_dir"]
            frames.append(df)

        if not frames:
            continue

        combined = pd.concat(frames, ignore_index=True)
        out_path = os.path.join(OUT_DIR, fname)
        combined.to_csv(out_path, index=False)
        print(f"{fname:<28} {len(combined):>8,} qator  ->  combined/{fname}")

    print(f"\nJami: {OUT_DIR}")


if __name__ == "__main__":
    main()

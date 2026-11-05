"""ns-3 dvigateli katta generatsiya qilingan topologiyada: tezlik chegarasi va natija mantiqiyligi.

    python3 -m lab.tests.smoke_ns3_perf            # tezkor rejim (ns3_fast) — ring 20 router
    NETLAB_NS3_PERF_EXACT=1 python3 -m lab.tests.smoke_ns3_perf   # + aniq rejim (uzoq, ~9 daqiqa)

Chegara = o'lchangan vaqt x2 (4 yadroli konteyner, boshqa yuklamasiz).
"""

import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lab.topogen import generate  # noqa: E402

FAILS = []
# o'lchangan (ring routers=20 hosts=5 flows=40, 30 s, bitta link_down): tezkor 107 s, aniq 567 s (asos) — chegara x2
BOUND_FAST = float(os.environ.get("NETLAB_NS3_PERF_BOUND_FAST", "220"))
BOUND_EXACT = float(os.environ.get("NETLAB_NS3_PERF_BOUND_EXACT", "1150"))


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def run(sc_extra, bound, label):
    p = generate("ring", routers=20, hosts=5, flows=40)
    sc = dict(p["scenarios"]["asosiy"], **sc_extra)
    sc["events"] = [{"t": 10, "action": "link_down", "link": "l1"}]
    with tempfile.TemporaryDirectory() as td:
        inp, out = os.path.join(td, "in.json"), os.path.join(td, "out.json")
        json.dump({"project": p, "scenario": sc}, open(inp, "w"))
        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", "lab.engines.ns3_runner", inp, out], cwd=ROOT,
                           capture_output=True, text=True, timeout=bound * 1.5)
        dt = time.time() - t0
        if r.returncode != 0:
            check(False, f"{label}: ns3_runner xato: {r.stderr[-300:]}")
            return
        res = json.load(open(out))
    fl = res["flows"]
    got = [k for k, f in fl.items() if (f["summary"].get("avg_thr_bps") or 0) > 0]
    check(dt < bound, f"{label}: {len(p['devices'])} qurilma, {len(fl)} oqim, 30 s -> {dt:.1f} s (chegara {bound:.0f} s)")
    check(len(got) == len(fl), f"{label}: barcha oqimlar yetkazildi ({len(got)}/{len(fl)})")
    udp = [f["summary"] for f in fl.values() if f["type"] == "udp"]
    check(all(abs(s["avg_thr_bps"] - 5e6) / 5e6 < 0.1 for s in udp), f"{label}: UDP 5 Mbit/s oqimlar ~5 Mbit/s")
    check(res.get("nodes") and res.get("convergence") is not None, f"{label}: qurilma statistikasi va konvergensiya bor")
    if sc_extra.get("ns3_fast"):
        check(res.get("ns3_fast") is True and any("ns3_fast" in n for n in res.get("notes", [])),
              f"{label}: natijada tezkor rejim belgisi va izohi")


if "ns3" not in sys.modules:
    try:
        __import__("ns")
    except ImportError:
        print("  --   ns-3 o'rnatilmagan (pip install ns3) — o'tkazib yuborildi")
        sys.exit(0)

run({"ns3_fast": True}, BOUND_FAST, "ns3 tezkor (MTU 9000)")
if os.environ.get("NETLAB_NS3_PERF_EXACT") == "1":
    run({}, BOUND_EXACT, "ns3 aniq")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

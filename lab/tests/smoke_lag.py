"""Smoke test: link aggregation (bond/LACP) — demo driver, model, analitik (+ ns-3).

    python3 -m lab.tests.smoke_lag          # NETLAB_SKIP_NS3=1 — ns-3'siz
"""

import copy
import ipaddress
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from lab.drivers.demo_driver import Net  # noqa: E402
from lab.server import create_app  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def ping(c, dev, target, count=2):
    with c.websocket_connect(f"/ws/term/{dev}") as ws:
        ws.send_text(json.dumps({"t": "in", "d": f"ping {target} -c {count}\r"}))
        out, t0 = b"", time.time()
        while time.time() - t0 < 8:
            out += ws.receive_bytes()
            if b"packet loss" in out and out.rstrip().endswith(b"#"):
                break
        return out.decode(errors="replace")


def run(c, p, sc_name, eng):
    rid = c.post("/api/runs", json={"project": p, "scenario": sc_name, "engine": eng}).json()["run_id"]
    t0 = time.time()
    while time.time() - t0 < 300 and any(r.get("status") == "running" for r in c.get(f"/api/runs/{p['name']}").json()["runs"]):
        time.sleep(0.3)
    r = c.get(f"/api/runs/{p['name']}/{rid}").json()
    c.delete(f"/api/runs/{p['name']}/{rid}")
    return r


with TestClient(create_app("demo")) as c:
    p = c.get("/api/projects/lag_lab?example=true").json()
    check(c.post("/api/validate", json=p).json()["errors"] == [], "lag_lab: validatsiya")
    # ---- validatsiya xatolari ----
    bad = copy.deepcopy(p)
    bad["devices"]["s2"]["config"]["bonds"] = {}
    check(any("mos bond yo'q" in e for e in c.post("/api/validate", json=bad).json()["errors"]), "peer'da bond yo'q -> xato")
    bad = copy.deepcopy(p)
    for sw in ("s1", "s2"):
        bad["devices"][sw]["config"]["bonds"]["bond1"]["lacp"] = "passive"
    check(any("passive" in e for e in c.post("/api/validate", json=bad).json()["errors"]), "ikkala tomon passive -> xato")
    bad = copy.deepcopy(p)
    bad["devices"]["s1"]["config"]["bonds"]["bond1"]["ports"] = [1, 3]
    check(any("bitta qo'shni switch" in e or "mos bond" in e for e in c.post("/api/validate", json=bad).json()["errors"]),
          "a'zolar turli qurilmalarga -> xato")
    bad = copy.deepcopy(p)
    bad["devices"]["h1"]["config"]["bonds"] = {"b": {"ports": [1, 2]}}
    check(any("faqat switch" in e for e in c.post("/api/validate", json=bad).json()["errors"]), "host bond -> xato")
    bad = copy.deepcopy(p)
    bad["devices"]["s1"]["config"]["bonds"]["bond1"]["mode"] = "balance-xor"
    check(any("mode" in e for e in c.post("/api/validate", json=bad).json()["errors"]), "noma'lum bond mode -> xato")

    # ---- STP / halqa: bond — bitta qirra ----
    n = Net(p)
    st = n.stp()
    check(not st["blocked"] and not st["storm"], "bond: parallel a'zolar halqa emas (storm yo'q, blocking yo'q)")
    nob = copy.deepcopy(p)
    for sw in ("s1", "s2"):
        nob["devices"][sw]["config"].pop("bonds")
    st2 = Net(nob).stp()
    check(len(st2["blocked"]) == 1 and not st2["storm"], f"bond'siz, STP yoqiq: bitta a'zo bloklanadi {sorted(st2['blocked'])}")
    for sw in ("s1", "s2"):
        nob["devices"][sw]["config"]["stp"] = False
    st3 = Net(nob).stp()
    check(st3["storm"] == {"s1", "s2"}, "bond'siz, STP o'chiq: broadcast storm")
    # ---- hash: oqimlar a'zolarga ----
    picks = {}
    for key in (("h1", "h3", "f1"), ("h2", "h4", "f2")):
        n2 = Net(p)
        n2.flow_key = key
        ok, _w, _h, path, _d = n2.forward(key[0], ipaddress.ip_address({"h3": "10.0.0.3", "h4": "10.0.0.4"}[key[1]]))
        picks[key[2]] = [lid for lid, _ in path if lid in ("l5", "l6")]
    check(picks["f1"] != picks["f2"] and all(len(v) == 1 for v in picks.values()),
          f"balance-tcp hash: f1 {picks['f1']}, f2 {picks['f2']}")
    ab = copy.deepcopy(p)
    for sw in ("s1", "s2"):
        ab["devices"][sw]["config"]["bonds"]["bond1"]["mode"] = "active-backup"
    for key in (("h1", "h3", "f1"), ("h2", "h4", "f2")):
        n3 = Net(ab)
        n3.flow_key = key
        path = n3.forward(key[0], ipaddress.ip_address({"h3": "10.0.0.3", "h4": "10.0.0.4"}[key[1]]))[3]
        check([lid for lid, _ in path if lid in ("l5", "l6")] == ["l5"], f"active-backup: {key[2]} faqat birlamchi a'zo (l5)")

    # ---- demo: ping, inspect, a'zo uzilishi ----
    c.post("/api/net/start", json={"project": p})
    check("0% packet loss" in ping(c, "h1", "10.0.0.3"), "demo: h1 -> h3 bond orqali")
    secs = c.get("/api/net/inspect/s1").json()["sections"]
    lag = next((s for s in secs if s["title"].startswith("Link aggregation")), None)
    check(lag is not None and len(lag["rows"]) == 2, f"inspect s1: bond a'zolari {lag and lag['rows']}")
    c.patch("/api/net/link/l5", json={"up": False})
    check("0% packet loss" in ping(c, "h1", "10.0.0.3"), "demo: l5 uzilganda ham ping (l6 orqali)")
    c.patch("/api/net/link/l6", json={"up": False})
    check("100% packet loss" in ping(c, "h1", "10.0.0.3", 1), "demo: ikkala a'zo uzilsa — aloqa yo'q")
    c.post("/api/net/stop")

    # ---- analitik / ns-3 ----
    want = ["analytic"] + (["ns3"] if os.environ.get("NETLAB_SKIP_NS3") != "1" and
                           any(e["id"] == "ns3" and e["available"] for e in c.get("/api/engines").json()["engines"]) else [])
    for eng in want:
        r = run(c, p, "lag", eng)
        check(r["status"] == "done", f"{eng}: lag ssenariysi ({r.get('error')})")
        if r["status"] != "done":
            continue
        f = r["flows"]
        before = [sum(next((x["thr_bps"] for x in f[k]["series"] if x["t"] == t), 0) for k in ("f1", "f2")) for t in range(3, 10)]
        after = [sum(next((x["thr_bps"] for x in f[k]["series"] if x["t"] == t), 0) for k in ("f1", "f2")) for t in range(15, 19)]
        print(f"    {eng}: jami oldin {[round(x / 1e6) for x in before]}, keyin {[round(x / 1e6) for x in after]} Mbit/s")
        check(min(before) > 150e6, f"{eng}: ikki oqim ~2x100 Mbit/s (aggregatsiya)")
        check(80e6 < min(after) and max(after) < 120e6, f"{eng}: l5 uzilgach jami ~100 Mbit/s (l6)")
        if eng == "analytic":
            l5 = [x["ab_bps"] for x in r["links"]["l5"] if 3 <= x["t"] <= 9]
            l6 = [x["ab_bps"] for x in r["links"]["l6"] if 3 <= x["t"] <= 9]
            check(min(l5) > 90e6 and min(l6) > 90e6, "analytic: har a'zo kabelda alohida statistika (~100 Mbit/s)")
            sc2 = copy.deepcopy(p)
            sc2["scenarios"]["lag"]["bond_failover_s"] = 3
            r2 = run(c, sc2, "lag", "analytic")
            gap = [t for t in range(10, 16) if any(next((x["thr_bps"] for x in r2["flows"][k]["series"] if x["t"] == t), 1) == 0
                                                   for k in ("f1", "f2"))]
            check(len(gap) == 3, f"analytic: bond_failover_s=3 -> l5 dagi oqim 3 s yo'qotiladi {gap}")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

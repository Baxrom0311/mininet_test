"""VRRP smoke testi (demo driver + analitik [+ ns-3]) — root/Mininet shart emas.

    python3 -m lab.tests.smoke_vrrp          # NETLAB_SKIP_NS3=1 — ns-3'siz
"""

import copy
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from lab import vrrp  # noqa: E402
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


def wait_run(c, proj, rid, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not any(r.get("status") == "running" for r in c.get(f"/api/runs/{proj}").json()["runs"]):
            break
        time.sleep(0.3)
    return c.get(f"/api/runs/{proj}/{rid}").json()


def vrrp_rows(c, r):
    secs = {s["title"]: s for s in c.get(f"/api/net/inspect/{r}").json()["sections"]}
    return (secs.get("VRRP (show vrrp)") or {}).get("rows") or []


with TestClient(create_app("demo")) as c:
    p = c.get("/api/projects/vrrp_lab?example=true").json()
    check(c.post("/api/validate", json=p).json()["errors"] == [], "vrrp_lab: validatsiya")
    bad = copy.deepcopy(p)
    bad["devices"]["r1"]["config"]["vrrp"][0]["vrid"] = 300
    bad["devices"]["r2"]["config"]["vrrp"][0]["vip"] = "10.9.9.9/24"
    bad["devices"]["r2"]["config"]["vrrp"].append({"iface": "r2-eth1", "vrid": 2, "vip": "10.0.1.253/24", "priority": 255})
    errs = c.post("/api/validate", json=bad).json()["errors"]
    check(sum("VRRP" in e for e in errs) >= 3, f"vrrp: noto'g'ri vrid / VIP subnet / priority rad etiladi ({len(errs)})")

    # timeline (model): MDI = 3*1 + (256-100)/256 = 3.609 s; preempt darhol
    tl = vrrp.timeline(p, p["scenarios"]["zaxira"])
    check([(round(t, 3), m) for t, _k, m in tl] == [(0.0, "r1"), (10.0, None), (13.609, "r2"), (20.0, "r1")],
          f"vrrp: vaqt jadvali {[(round(t, 3), m) for t, _k, m in tl]}")

    c.post("/api/net/start", json={"project": p})
    check("0% packet loss" in ping(c, "pc1", "10.0.3.10"), "vrrp: pc1 -> srv1 (gateway = VIP 10.0.1.254)")
    check("0% packet loss" in ping(c, "pc1", "10.0.1.254"), "vrrp: pc1 -> VIP ping (master javob beradi)")
    r1, r2 = vrrp_rows(c, "r1"), vrrp_rows(c, "r2")
    check(r1 and r1[0][4] == "Master" and r2 and r2[0][4] == "Backup", f"vrrp: r1 {r1[:1]} / r2 {r2[:1]}")
    c.patch("/api/net/link/l3", json={"up": False})
    r1, r2 = vrrp_rows(c, "r1"), vrrp_rows(c, "r2")
    check(r2 and r2[0][4] == "Master" and r1 and r1[0][4] == "Initialize", f"vrrp: l3 uzilgach r2 Master ({r2[:1]})")
    check("0% packet loss" in ping(c, "pc1", "10.0.3.10"), "vrrp: failover dan keyin pc1 -> srv1 (r2 orqali)")
    c.patch("/api/net/link/l3", json={"up": True})
    check(vrrp_rows(c, "r1")[0][4] == "Master", "vrrp: preempt — r1 yana Master")
    c.post("/api/net/stop")

    engines = {e["id"]: e["available"] for e in c.get("/api/engines").json()["engines"]}
    want = ["analytic"] + (["ns3"] if engines.get("ns3") and os.environ.get("NETLAB_SKIP_NS3") != "1" else [])
    for eng in want:
        rid = c.post("/api/runs", json={"project": p, "scenario": "zaxira", "engine": eng}).json()["run_id"]
        r = wait_run(c, "vrrp_lab", rid)
        check(r["status"] == "done", f"{eng}: ishga tushirish ({r.get('error')})")
        v = {int(x["t"]): x["thr_bps"] for x in r["flows"]["video"]["series"]}
        gap = [t for t in range(2, 29) if v.get(t, 0) < 1e5]
        check(gap and gap[0] == 11 and 3 <= len(gap) <= 5 and all(v.get(t, 0) > 3e6 for t in range(16, 29)),
              f"{eng}: failover uzilishi {gap} (~MDI 3.6 s), preempt'da uzilish yo'q")
        if eng == "analytic":
            cv = {x["action"]: x["converged_s"] for x in r.get("convergence") or []}
            check(cv.get("link_down") == 3.609 and cv.get("link_up") == 0.0,
                  f"analytic: VRRP konvergensiya {cv}")
            ev = [e for e in r.get("route_events") or [] if any("VRRP" in x for x in e["added"] + e["removed"])]
            check(len(ev) == 4, f"analytic: route_events'da VRRP master o'zgarishlari ({len(ev)})")
        c.delete(f"/api/runs/vrrp_lab/{rid}")
    # vrrp_convergence_s qo'lda
    p2 = copy.deepcopy(p)
    p2["scenarios"]["zaxira"]["vrrp_convergence_s"] = 1
    rid = c.post("/api/runs", json={"project": p2, "scenario": "zaxira", "engine": "analytic"}).json()["run_id"]
    r = wait_run(c, "vrrp_lab", rid)
    v = {int(x["t"]): x["thr_bps"] for x in r["flows"]["video"]["series"]}
    check([t for t in range(2, 29) if v.get(t, 0) < 1e5] == [11], "analytic: vrrp_convergence_s=1 -> 1 s uzilish")
    c.delete(f"/api/runs/vrrp_lab/{rid}")
    if "ns3" not in want:
        print("  --   ns-3 o'tkazib yuborildi")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

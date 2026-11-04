"""BGP smoke testi (demo driver + analitik [+ ns-3]) — root/Mininet shart emas.

    python3 -m lab.tests.smoke_bgp          # NETLAB_SKIP_NS3=1 — ns-3'siz
"""

import copy
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

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


with TestClient(create_app("demo")) as c:
    p = c.get("/api/projects/bgp_lab?example=true").json()
    check(c.post("/api/validate", json=p).json()["errors"] == [], "bgp_lab: validatsiya")
    bad = copy.deepcopy(p)
    bad["devices"]["r3"]["config"]["bgp"]["asn"] = 0
    bad["devices"]["r2"]["config"]["bgp"]["neighbors"][1]["ip"] = "x"
    errs = c.post("/api/validate", json=bad).json()["errors"]
    check(sum("BGP" in e for e in errs) >= 2, f"bgp: noto'g'ri asn / qo'shni rad etiladi ({len(errs)})")
    c.post("/api/net/start", json={"project": p})
    check("0% packet loss" in ping(c, "pc1", "10.2.0.10"), "bgp: pc1 -> srv1 (AS65001 -> AS65002)")
    secs = {s["title"]: s for s in c.get("/api/net/inspect/r2").json()["sections"]}
    summ = secs.get("FRR: show bgp summary", {}).get("rows") or []
    check(len(summ) == 2 and all(r[3] == "Established" for r in summ), f"bgp: r2 sessiyalari {summ}")
    r1b = {s["title"]: s for s in c.get("/api/net/inspect/r1").json()["sections"]}.get("FRR: show ip bgp", {}).get("rows") or []
    check(any(r[0] == "10.2.0.0/24" and r[1] == "10.12.0.2" and "BGP i" in r[3] for r in r1b),
          f"bgp: r1 10.2.0.0/24 iBGP (next-hop-self) {r1b}")
    r3b = {s["title"]: s for s in c.get("/api/net/inspect/r3").json()["sections"]}.get("FRR: show ip bgp", {}).get("rows") or []
    check(any(r[0] == "10.1.0.0/24" and "65001" in r[3] for r in r3b), "bgp: r3 10.1.0.0/24 AS_PATH 65001")
    c.patch("/api/net/link/l4", json={"up": False})
    check("100% packet loss" in ping(c, "pc1", "10.2.0.10", 1), "bgp: eBGP kabeli uzilganda marshrut yo'q")
    secs = {s["title"]: s for s in c.get("/api/net/inspect/r2").json()["sections"]}
    check(any(r[0] == "203.0.113.2" and r[3] != "Established" for r in secs["FRR: show bgp summary"]["rows"]),
          "bgp: eBGP sessiya tushdi")
    c.patch("/api/net/link/l4", json={"up": True})
    check("0% packet loss" in ping(c, "pc1", "10.2.0.10"), "bgp: tiklangach qayta ishlaydi")
    c.post("/api/net/stop")

    # AS loop: r3 da asn=65001 (bir xil AS — sessiya iBGP bo'lishi kerak, lekin r2 uni eBGP deb kutadi)
    loop = copy.deepcopy(p)
    loop["devices"]["r3"]["config"]["bgp"]["asn"] = 65009
    c.post("/api/net/start", json={"project": loop})
    check("100% packet loss" in ping(c, "pc1", "10.2.0.10", 1), "bgp: remote_as mos kelmasa sessiya yo'q")
    c.post("/api/net/stop")

    engines = {e["id"]: e["available"] for e in c.get("/api/engines").json()["engines"]}
    want = ["analytic"] + (["ns3"] if engines.get("ns3") and os.environ.get("NETLAB_SKIP_NS3") != "1" else [])
    for eng in want:
        rid = c.post("/api/runs", json={"project": p, "scenario": "ebgp_uzilish", "engine": eng}).json()["run_id"]
        r = wait_run(c, "bgp_lab", rid)
        check(r["status"] == "done", f"{eng}: ishga tushirish ({r.get('error')})")
        v = {int(x["t"]): x["thr_bps"] for x in r["flows"]["video"]["series"]}
        gap = [t for t in range(11, 39) if v.get(t, 0) < 1e5]
        check(v.get(5, 0) > 3e6 and v.get(38, 0) > 3e6 and 8 <= len(gap) <= 14,
              f"{eng}: eBGP uzilishi — video {len(gap)} s to'xtaydi, keyin qaytadi")
        cv = {x["action"]: x["converged_s"] for x in r.get("convergence") or []}
        print(f"    {eng}: convergence {cv}")
        if eng == "analytic":
            check(cv.get("link_up") == 2.0, f"analytic: BGP tiklanish konvergensiyasi {cv.get('link_up')} s (bgp_convergence_s=2)")
        c.delete(f"/api/runs/bgp_lab/{rid}")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

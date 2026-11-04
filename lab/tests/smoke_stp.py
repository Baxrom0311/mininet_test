"""STP smoke testi (demo driver + analitik [+ ns-3]) — root/Mininet shart emas.

    python3 -m lab.tests.smoke_stp            (NETLAB_SKIP_NS3=1 — ns-3'siz)
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


def run(c, proj, sc_name, eng):
    rid = c.post("/api/runs", json={"project": proj, "scenario": sc_name, "engine": eng}).json()["run_id"]
    t0 = time.time()
    while time.time() - t0 < 600 and any(r.get("status") == "running"
                                         for r in c.get(f"/api/runs/{proj['name']}").json()["runs"]):
        time.sleep(0.3)
    r = c.get(f"/api/runs/{proj['name']}/{rid}").json()
    c.delete(f"/api/runs/{proj['name']}/{rid}")
    return r


def gap(r):
    return [int(p["t"]) for p in r["flows"]["video"]["series"] if p["thr_bps"] < 1e5 and 2 <= p["t"] <= 88]


with TestClient(create_app("demo")) as c:
    p = c.get("/api/projects/stp_lab?example=true").json()
    check(c.post("/api/validate", json=p).json()["errors"] == [], "stp_lab: validatsiya")
    bad = copy.deepcopy(p)
    bad["devices"]["s1"]["config"]["stp_priority"] = 5000
    bad["devices"]["s2"]["config"]["stp_mode"] = "mstp"
    errs = c.post("/api/validate", json=bad).json()["errors"]
    check(len(errs) == 2 and any("stp_priority" in e for e in errs), f"stp: noto'g'ri priority/mode rad etiladi {errs}")

    c.post("/api/net/start", json={"project": p})
    secs = {s["title"]: s for s in c.get("/api/net/inspect/s3").json()["sections"]}
    stp = secs.get("STP (spanning tree)")
    roles = {r[0]: (r[1], r[2]) for r in (stp or {}).get("rows", [])}
    check(roles.get(2) == ("alternate", "blocking") and roles.get(1) == ("root", "forwarding"),
          f"s3: port2 alternate/blocking, port1 root ({roles})")
    s1 = {s["title"]: s for s in c.get("/api/net/inspect/s1").json()["sections"]}.get("STP (spanning tree)")
    check(s1 and "BU ROOT" in s1.get("hint", ""), "s1 — root bridge (priority 4096)")
    check("0% packet loss" in ping(c, "h1", "10.0.0.2"), "stp: h1 -> h2 ping (daraxt bo'yicha)")
    c.patch("/api/net/link/l2", json={"up": False})
    check("0% packet loss" in ping(c, "h1", "10.0.0.2"), "demo: l2 uzildi -> s2-s3 orqali (demo'da konvergensiya darhol)")
    c.post("/api/net/stop")

    storm = copy.deepcopy(p)
    for n in ("s1", "s2", "s3"):
        storm["devices"][n]["config"]["stp"] = False
    c.post("/api/net/start", json={"project": storm})
    out = ping(c, "h1", "10.0.0.2", 1)
    check("100% packet loss" in out and "storm" in out, "STP'siz halqa: broadcast storm — ping o'tmaydi, sabab ko'rsatiladi")
    c.post("/api/net/stop")

    # analitik: 802.1D (30 s) va RSTP (~1 s) konvergensiyasi
    r = run(c, p, "uzilish", "analytic")
    g = gap(r)
    cv = r.get("convergence") or []
    check(g and g[0] == 11 and g[-1] == 80 and len(g) == 60 and cv and cv[0]["converged_s"] == 30,
          f"analytic STP: uzilish {g[:1]}..{g[-1:]} ({len(g)} s), konvergensiya {[x['converged_s'] for x in cv]}")
    rp = copy.deepcopy(p)
    for n in ("s1", "s2", "s3"):
        rp["devices"][n]["config"]["stp_mode"] = "rstp"
    r = run(c, rp, "uzilish", "analytic")
    g = gap(r)
    check(g == [11, 51] and r["convergence"][0]["converged_s"] == 1, f"analytic RSTP: uzilish {g}")
    ov = copy.deepcopy(p)
    ov["scenarios"]["uzilish"]["stp_convergence_s"] = 5
    g = gap(run(c, ov, "uzilish", "analytic"))
    check(len(g) == 10, f"analytic: stp_convergence_s=5 -> {len(g)} s uzilish (2 hodisa)")
    r = run(c, storm, "uzilish", "analytic")
    vs = {int(x["t"]): x["thr_bps"] for x in r["flows"]["video"]["series"]}
    check(all(vs.get(t, 0) == 0 for t in range(2, 11)) and all(vs.get(t, 0) > 4e6 for t in range(12, 50))
          and all(vs.get(t, 0) == 0 for t in range(52, 88)),
          "analytic: STP'siz — halqa bor paytda storm (0), l2 uzilganda (halqa yo'q) o'tadi")

    eng = c.get("/api/engines").json()
    has_ns3 = any(e["id"] == "ns3" and e["available"] for e in (eng.get("engines") if isinstance(eng, dict) else eng))
    if has_ns3 and os.environ.get("NETLAB_SKIP_NS3") != "1":
        r = run(c, p, "uzilish", "ns3")
        g = [t for t in gap(r) if t > 2]
        check(r["status"] == "done" and g and g[0] == 11 and 40 <= g[-1] <= 81,
              f"ns3 STP: uzilish {g[:1]}..{g[-1:]} ({len(g)} s), konvergensiya {[x['converged_s'] for x in r.get('convergence') or []]}")
        r = run(c, storm, "uzilish", "ns3")
        check(r["status"] == "error" and "storm" in (r.get("error") or ""), "ns3: STP'siz halqa — tushunarli xato")
    else:
        print("  --   ns-3 o'tkazib yuborildi")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

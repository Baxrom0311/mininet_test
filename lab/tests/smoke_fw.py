"""Firewall/ACL smoke testi (demo driver + analitik/ns-3 dvigatellari, Mininet shart emas).

    python3 -m lab.tests.smoke_fw            # NETLAB_SKIP_NS3=1 — ns-3 qismini o'tkazib yuborish
"""

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
        while time.time() - t0 < 12:
            out += ws.receive_bytes()
            if b"packet loss" in out and out.rstrip().endswith(b"#"):
                break
        return out.decode(errors="replace")


def run(c, proj, scen, eng):
    rid = c.post("/api/runs", json={"project": proj, "scenario": scen, "engine": eng}).json()["run_id"]
    t0 = time.time()
    while time.time() - t0 < 300:
        if not any(r.get("status") == "running" for r in c.get(f"/api/runs/{proj['name']}").json()["runs"]):
            break
        time.sleep(0.3)
    r = c.get(f"/api/runs/{proj['name']}/{rid}").json()
    c.delete(f"/api/runs/{proj['name']}/{rid}")
    return r


with TestClient(create_app("demo")) as c:
    p = c.get("/api/projects/firewall_lab?example=true").json()
    check(c.post("/api/validate", json=p).json()["errors"] == [], "firewall_lab: validatsiya")
    for bad_rule, what in (({"chain": "forward", "action": "allow"}, "noto'g'ri action"),
                           ({"chain": "forward", "action": "accept", "proto": "icmp", "dport": 80}, "icmp + dport"),
                           ({"chain": "forward", "action": "accept", "src": "10.0.0.0/33"}, "noto'g'ri subnet"),
                           ({"chain": "forward", "action": "accept", "in": "r1-eth9"}, "yo'q interfeys")):
        bad = json.loads(json.dumps(p))
        bad["devices"]["r1"]["config"]["firewall"]["rules"].append(bad_rule)
        errs = c.post("/api/validate", json=bad).json()["errors"]
        check(any("firewall" in e for e in errs), f"validatsiya: {what} rad etiladi")

    c.post("/api/net/start", json={"project": p})
    out = ping(c, "pc1", "203.0.113.50")
    check("0% packet loss" in out, "pc1 -> inet ping o'tadi (LAN chiqadi, javob established)")
    out = ping(c, "inet", "10.0.1.10", 1)
    check("100% packet loss" in out and "Prohibited" in out, "inet -> pc1 ping: reject (Destination Host Prohibited)")
    out = ping(c, "inet", "10.0.2.10", 1)
    check("100% packet loss" in out and "timeout" in out.lower(), "inet -> srv1 ping: drop (timeout)")
    secs = c.get("/api/net/inspect/r1").json()["sections"]
    fw = next((s for s in secs if s["title"].startswith("Firewall")), None)
    check(fw is not None and len(fw["rows"]) >= 4, "inspect r1: Firewall (ACL) jadvali")
    c.post("/api/net/stop")

    # established qoidasisiz — javoblar ham tashlanadi (stateful semantika)
    p2 = json.loads(json.dumps(p))
    p2["devices"]["r1"]["config"]["firewall"]["rules"].pop(0)
    c.post("/api/net/start", json={"project": p2})
    out = ping(c, "pc1", "203.0.113.50", 1)
    check("100% packet loss" in out, "established qoidasi olib tashlansa javob qaytmaydi")
    c.post("/api/net/stop")

    engines = {e["id"]: e["available"] for e in c.get("/api/engines").json()["engines"]}
    want = ["analytic"] + (["ns3"] if engines.get("ns3") and os.environ.get("NETLAB_SKIP_NS3") != "1" else [])
    for eng in want:
        r = run(c, p, "trafik", eng)
        check(r["status"] == "done", f"{eng}: ishga tushirish tugadi ({r.get('error')})")
        s = {k: f["summary"] for k, f in r.get("flows", {}).items()}
        ok = (s["web_in"]["avg_thr_bps"] > 20e6 and s["lan_out"]["avg_thr_bps"] > 4e6
              and s["lan_web"]["avg_thr_bps"] > 20e6 and (s["ping_out"]["loss_pct"] or 0) < 5)
        check(ok, f"{eng}: ruxsat etilgan oqimlar o'tadi (web_in {s['web_in']['avg_thr_bps'] / 1e6:.0f}, "
                  f"lan_out {s['lan_out']['avg_thr_bps'] / 1e6:.1f}, lan_web {s['lan_web']['avg_thr_bps'] / 1e6:.0f} Mbit/s)")
        check(s["ssh_in"]["avg_thr_bps"] == 0 and (s["ping_in"]["loss_pct"] or 0) >= 99,
              f"{eng}: bloklangan oqimlar (ssh_in, ping_in) o'tmaydi")
    if "ns3" not in want:
        print("  --   ns-3 o'tkazib yuborildi")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

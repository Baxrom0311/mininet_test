#!/usr/bin/env python3
"""NetLab smoke test (Mininet'siz, demo driver): barcha namunalar validatsiyadan
o'tadi, ishga tushadi, terminal orqali ping ishlaydi, kabel uzilsa ping
to'xtaydi, token himoyasi ishlaydi. CI va lokal tekshiruv uchun:

    python3 lab/tests/smoke_demo.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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


CASES = [("ospf_lab", "pc1", "10.0.2.11", "l4"),
         ("p4_l2_lab", "h1", "10.0.0.3", "l3"),
         ("sdn_router_lab", "pc1", "10.0.2.10", "l4")]

with TestClient(create_app("demo")) as c:
    check(c.get("/api/info").json()["driver"] == "demo", "info: demo driver")
    tpl = c.get("/api/templates").json()
    check({"l2_switch", "ipv4_router"} <= set(tpl["p4"]) and {"l2_learning", "l3_router"} <= set(tpl["sdn"]), "shablonlar")
    for name, dev, target, cut in CASES:
        p = c.get(f"/api/projects/{name}?example=true").json()
        check(c.post("/api/validate", json=p).json()["errors"] == [], f"{name}: validatsiya")
        check(c.post("/api/net/start", json={"project": p}).json()["running"], f"{name}: start")
        check("0% packet loss" in ping(c, dev, target), f"{name}: {dev} -> {target} ping")
        c.patch(f"/api/net/link/{cut}", json={"up": False})
        check("100% packet loss" in ping(c, dev, target, 1), f"{name}: {cut} uzilganda ping o'tmaydi")
        secs = c.get(f"/api/net/inspect/{dev}").json()["sections"]
        check(any(s["title"].startswith("Routing") for s in secs), f"{name}: inspect")
        c.post("/api/net/stop")
    r = c.post("/api/p4/compile", json={"source": tpl["p4"]["ipv4_router"]}).json()
    tables = [t["name"] for pl in r["pipeline"]["pipelines"] for t in pl["tables"]]
    check("ipv4_lpm" in tables, "P4 pipeline tahlili (ipv4_lpm)")
    bad = {"version": 1, "name": "x", "devices": {"Bad-Name": {"id": 1, "type": "host", "config": {}}},
           "links": {}, "p4_programs": {}, "controllers": {}}
    check(len(c.post("/api/validate", json=bad).json()["errors"]) > 0, "noto'g'ri nom rad etiladi")

with TestClient(create_app("demo", token="t0k")) as c:
    check(c.get("/api/info").status_code == 401, "token: tokensiz 401")
    check(c.get("/api/info?token=t0k").status_code == 200, "token: to'g'ri token 200")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

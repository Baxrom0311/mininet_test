"""Katta topologiyalar: generator (lab.topogen), validatsiya, demo ping, analitik va survivability tezligi.

    python3 -m lab.tests.smoke_perf
"""

import ipaddress
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from lab.drivers.demo_driver import Net  # noqa: E402
from lab.engines import analytic  # noqa: E402
from lab.model import validate_project, validate_scenario  # noqa: E402
from lab.server import create_app  # noqa: E402
from lab.topogen import generate  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


for kind, kw in (("ring", {"routers": 12, "hosts": 3, "flows": 10}), ("star", {"branches": 6, "hosts": 2, "flows": 8}),
                 ("tree", {"depth": 4, "hosts": 2, "flows": 10}),
                 ("campus", {"core": 2, "dist": 4, "access": 8, "hosts": 4, "flows": 20})):
    p = generate(kind, **kw)
    errs = validate_project(p) + validate_scenario(p, p["scenarios"]["asosiy"])
    check(not errs, f"{kind}: {len(p['devices'])} qurilma, validatsiya {errs[:2]}")
    n = Net(p)
    h = [d for d, x in p["devices"].items() if x["type"] == "host"]
    dst = ipaddress.ip_interface(next(iter(p["devices"][h[-1]]["config"]["interfaces"].values()))["ip"]).ip
    check(n.forward(h[0], dst)[0], f"{kind}: {h[0]} -> {h[-1]} yo'l bor (OSPF modeli)")

# katta: 40 router halqa, 200 host, 100 oqim, 30 s
big = generate("ring", routers=40, hosts=5, flows=100)
sc = big["scenarios"]["asosiy"]
sc["events"] = [{"t": 10, "action": "link_down", "link": "l1"}, {"t": 20, "action": "link_up", "link": "l1"}]
t0 = time.time()
r = analytic.run(big, sc)
dt = time.time() - t0
ok_flows = sum(1 for f in r["flows"].values() if f["summary"]["avg_thr_bps"] > 0)
check(dt < 15 and ok_flows == len(sc["apps"]),
      f"analitik: {len(big['devices'])} qurilma, {len(sc['apps'])} oqim, 30 s -> {dt:.2f} s (chegara 15 s)")

camp = generate("campus", core=2, dist=6, access=12, hosts=5, flows=40)
from lab.survivability import analyze  # noqa: E402
t0 = time.time()
rep = analyze(camp, camp["scenarios"]["asosiy"], "both")
dt = time.time() - t0
check(dt < 30 and rep.get("cases"), f"survivability: kampus {len(camp['devices'])} qurilma -> {dt:.2f} s")

with TestClient(create_app("demo")) as c:
    rj = c.post("/api/topogen", json={"kind": "campus", "core": 2, "dist": 2, "access": 4, "hosts": 3, "flows": 6})
    check(rj.status_code == 200 and len(rj.json()["devices"]) == 2 + 2 + 4 + 12, f"API /api/topogen: {rj.status_code}")
    check(c.post("/api/topogen", json={"kind": "nope"}).status_code == 400, "API: noma'lum tur -> 400")
    p = rj.json()
    check(c.post("/api/net/start", json={"project": p}).json().get("running"), "demo: generatsiya qilingan tarmoq ishga tushdi")
    c.post("/api/net/stop")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

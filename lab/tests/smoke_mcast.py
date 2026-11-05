"""Multicast (IGMP + PIM-SM) smoke testi — demo driver + analitik [+ ns-3]; root shart emas.

    python3 -m lab.tests.smoke_mcast          # NETLAB_SKIP_NS3=1 — ns-3'siz
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


def wait_run(c, pname, rid, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not any(r.get("status") == "running" for r in c.get(f"/api/runs/{pname}").json()["runs"]):
            break
        time.sleep(0.3)
    return c.get(f"/api/runs/{pname}/{rid}").json()


def run(c, proj, scname, eng):
    rid = c.post("/api/runs", json={"project": proj, "scenario": scname, "engine": eng}).json()["run_id"]
    r = wait_run(c, proj["name"], rid)
    c.delete(f"/api/runs/{proj['name']}/{rid}")
    return r


def avg(pts, key, t0, t1):
    xs = [p[key] for p in pts if t0 <= p["t"] <= t1 and p.get(key) is not None]
    return sum(xs) / len(xs) if xs else None


with TestClient(create_app("demo")) as c:
    p = c.get("/api/projects/mcast_lab?example=true").json()
    check(c.post("/api/validate", json=p).json()["errors"] == [], "mcast_lab: validatsiya")
    sc = p["scenarios"]["tv"]
    check(c.post("/api/scenario/validate", json={"project": p, "scenario": sc}).json()["errors"] == [],
          "mcast_lab: ssenariy validatsiya")
    bad = copy.deepcopy(sc)
    bad["apps"][0]["group"] = "224.0.0.5"
    bad["apps"][0]["dst"] = ["pc1", "nope"]
    errs = c.post("/api/scenario/validate", json={"project": p, "scenario": bad}).json()["errors"]
    check(len(errs) >= 2, f"noto'g'ri guruh/qabul qiluvchi rad etiladi ({len(errs)} xato)")
    badp = copy.deepcopy(p)
    badp["devices"]["r1"]["config"]["pim"]["rp"] = "x.y"
    check(any("PIM" in e for e in c.post("/api/validate", json=badp).json()["errors"]), "noto'g'ri PIM rp rad etiladi")

    engines = {e["id"]: e["available"] for e in c.get("/api/engines").json()["engines"]}
    want = ["analytic"] + (["ns3"] if engines.get("ns3") and os.environ.get("NETLAB_SKIP_NS3") != "1" else [])
    for eng in want:
        r = run(c, p, "tv", eng)
        check(r["status"] == "done", f"{eng}: ishga tushdi ({r.get('error')})")
        if r["status"] != "done":
            continue
        a = r["apps"]["tv"]
        check(a["reached"] == 2 and a["reached_list"] == ["pc1", "pc3"],
              f"{eng}: ikkala qabul qiluvchi oladi {a['reached_list']}, o'rtacha yo'qotish {a['avg_loss_pct']}%")
        L = r["links"]
        l1 = avg(L["l1"], "ab_bps", 3, 10)
        l2 = avg(L["l2"], "ab_bps", 3, 10)
        check(l1 and 3.5e6 < l1 < 4.6e6 and l2 and 3.5e6 < l2 < 4.6e6,
              f"{eng}: har kabelda BITTA nusxa: l1 {l1 and l1 / 1e6:.2f}, l2 {l2 and l2 / 1e6:.2f} Mbit/s (2 qabul qiluvchi)")
        if eng == "analytic":
            l7 = avg(L["l7"], "ab_bps", 3, 28) or 0
            check(l7 < 1e5, f"{eng}: IGMP snooping — a'zo bo'lmagan pc2 portiga (l7) oqim yo'q ({l7 / 1e6:.2f} Mbit/s)")
            l4 = avg(L["l4"], "ab_bps", 14, 20) or 0
            check(l4 > 3.5e6, f"{eng}: l3 uzilganda pc3 shoxchasi r2-r3 (l4) orqali ({l4 / 1e6:.2f} Mbit/s)")
        pc3 = r["flows"]["tv.pc3"]["series"]
        after = avg(pc3, "thr_bps", 24, 28) or 0
        check(after > 3.5e6, f"{eng}: oxirida pc3 ga oqim yetadi ({after / 1e6:.2f} Mbit/s)")
        if eng == "ns3":
            l4 = avg(L["l4"], "ab_bps", 14, 20) or 0
            check(l4 > 3.0e6, f"ns3: l3 uzilganda mroute qayta o'rnatildi — l4 orqali ({l4 / 1e6:.2f} Mbit/s)")

    # snooping o'chiq: pc2 portiga ham flood
    p2 = copy.deepcopy(p)
    p2["devices"]["s1"]["config"]["igmp_snooping"] = False
    r = run(c, p2, "tv", "analytic")
    l7 = avg(r["links"]["l7"], "ab_bps", 3, 28) or 0
    check(l7 > 3.5e6, f"analytic: snooping o'chiq — pc2 portiga flood ({l7 / 1e6:.2f} Mbit/s)")

    # yuklangan shoxcha: l3 (10 Mbit/s) ga 8 Mbit/s unicast -> faqat pc3 yo'qotadi
    p3 = copy.deepcopy(p)
    s3 = copy.deepcopy(sc)
    s3["events"] = []
    s3["apps"].append({"id": "bg", "type": "udp", "src": "srv1", "dst": "pc3", "rate": "8Mbps", "size": 1000,
                       "start": 1, "stop": 29})
    p3["scenarios"]["yuk"] = s3
    r = run(c, p3, "yuk", "analytic")
    per = r["apps"]["tv"]["per_receiver"]
    check((per["pc3"]["loss_pct"] or 0) > 5 and (per["pc1"]["loss_pct"] or 0) < 0.5,
          f"analytic: faqat yuklangan shoxcha yo'qotadi — pc3 {per['pc3']['loss_pct']}%, pc1 {per['pc1']['loss_pct']}%")

    # pimd yo'q router -> o'sha shoxchadagi qabul qiluvchi olmaydi
    p4 = copy.deepcopy(p)
    p4["devices"]["r3"]["config"]["daemons"] = ["ospfd"]
    p4["devices"]["r3"]["config"].pop("pim")
    p4["scenarios"]["tv"]["events"] = []
    r = run(c, p4, "tv", "analytic")
    a = r["apps"]["tv"]
    check(a["reached_list"] == ["pc1"], f"analytic: r3 da pimd yo'q — faqat pc1 oladi ({a['reached_list']})")

    # demo inspect: PIM bo'limi
    c.post("/api/net/start", json={"project": p})
    secs = c.get("/api/net/inspect/r1").json()["sections"]
    check(any("PIM" in s_["title"] for s_ in secs), f"inspect r1: PIM bo'limi ({[s_['title'] for s_ in secs][:6]})")
    c.post("/api/net/stop")
    if "ns3" not in want:
        print("  --   ns-3 o'tkazib yuborildi")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

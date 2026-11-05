"""Survivability (N-1 nosozlik ta'siri) smoke testi — analitik, Mininet/ns-3 shart emas.

    python3 -m lab.tests.smoke_survivability
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from lab.server import create_app  # noqa: E402
from lab.survivability import analyze  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def load(name):
    with open(os.path.join(ROOT, "lab", "examples", name + ".json")) as f:
        return json.load(f)


def case(rep, kind, target):
    return next((c for c in rep["cases"] if c["kind"] == kind and c["target"] == target), None)


# ---- ospf_lab: r1-r2 (l4) yagona kabel — SPOF ----
t0 = time.time()
p = load("ospf_lab")
rep = analyze(p, "asosiy", "both")
c = case(rep, "link", "l4")
check(c and {b["flow"] for b in c["broken"]} == {"video", "ftp", "ping"} and c["broken_demand_pct"] == 100,
      f"ospf_lab: l4 uzilsa barcha oqimlar uziladi ({c and [b['flow'] for b in c['broken']]})")
check(any(s_["target"] == "l4" for s_ in rep["spof"]) and any(s_["target"] == "r1" for s_ in rep["spof"]),
      f"ospf_lab: SPOF ro'yxatida l4 va r1 ({[s_['target'] for s_ in rep['spof']][:6]})")
check(rep["cases"][0]["score"] >= rep["cases"][-1]["score"], "reyting kamayish tartibida")
check(not case(rep, "device", "pc1"), "host/server standartda nishon emas")
# sig'im rejasi: 8 Mbit/s video 10 Mbit/s l4 da -> 80% chegarasidan oshadi
p2 = load("ospf_lab")
sc = dict(p2["scenarios"]["asosiy"])
sc["apps"] = [dict(sc["apps"][0], rate="9Mbps"), sc["apps"][2]]
r2 = analyze(p2, sc, "links")
up = [x for x in r2["upgrades"] if x["link"] == "l4" and x["dir"] == "ab"]
check(up and up[0]["need_mbps"] >= 11.25 and not any(x["link"] == "l1" for x in r2["upgrades"]),
      f"sig'im rejasi: l4 ab {up and up[0]['bw_mbps']} -> >= {up and up[0]['need_mbps']} Mbit/s (9 Mbit/s / 0.8)")

# ---- rip_ring: r1-r2 uzilsa r3 orqali — hech narsa uzilmaydi, yuk ko'chadi ----
p = load("rip_ring")
rep = analyze(p, "uzilish", "both")
c = case(rep, "link", "l2")
check(c and not c["broken"] and {"video", "ping"} <= set(c["rerouted"]),
      f"rip_ring: l2 -> uzilish yo'q, qayta yo'naltirildi {c and c['rerouted']}")
check(c and c["convergence"].get("RIP") == 15.0, f"rip_ring: RIP tiklanish ~ update/2 = {c and c['convergence']}")
plan = {(x["link"], x["dir"]): x for x in rep["capacity_plan"]}
check(plan.get(("l3", "ab"), {}).get("worst_case") == "link:l2" and plan[("l3", "ab")]["base_load_mbps"] == 0,
      f"rip_ring: l3 (zaxira) eng yomon holat l2 nosozligida ({plan.get(('l3', 'ab'))})")
check(not case(rep, "link", "l3")["broken"] and not case(rep, "device", "r3")["broken"], "rip_ring: zaxira yo'l nosozligi ta'sirsiz")

# VoIP: zaxira yo'l (l3) tor bo'lsa — l2 nosozligida MOS tushadi, ortiqcha yuk, sig'im rejasi
p = load("rip_ring")
p["links"]["l3"]["params"]["bw"] = 5
sc = {"duration": 10, "events": [], "apps": [
    {"id": "tel", "type": "voip", "src": "pc1", "dst": "srv1", "codec": "g711"},
    {"id": "big", "type": "udp", "src": "pc1", "dst": "srv1", "rate": "8Mbps", "size": 1000}]}
rep = analyze(p, sc, "links")
c = case(rep, "link", "l2")
check(c and c["mos"].get("tel", {}).get("base", 0) > 4 and c["mos"]["tel"]["fail"] < 2.5
      and any(o["link"] == "l3" for o in c["overloaded"]),
      f"VoIP: l2 nosozligida MOS {c and c['mos']}, ortiqcha yuk {c and c['overloaded']}")
check(any(x["link"] == "l3" and x["need_mbps"] >= 10 for x in rep["upgrades"]),
      f"sig'im rejasi: l3 >= 10 Mbit/s ({[(x['link'], x['need_mbps']) for x in rep['upgrades']]})")

# ---- lag_lab: bond a'zosi uzilsa uzilish yo'q, qolgan a'zo yuklanadi ----
p = load("lag_lab")
rep = analyze(p, "lag", "links")
c5, c6 = case(rep, "link", "l5"), case(rep, "link", "l6")
check(c5 and not c5["broken"] and not c6["broken"], "lag_lab: bitta a'zo uzilsa oqimlar uzilmaydi")
check(c5 and "LAG" in c5["convergence"], f"lag_lab: LAG failover {c5 and c5['convergence']}")
check(c5 and any(d["thr_change_pct"] <= -40 for d in c5["degraded"]),
      f"lag_lab: o'tkazuvchanlik yarmiga tushadi {c5 and c5['degraded']}")
plan = {(x["link"], x["dir"]): x for x in rep["capacity_plan"]}
check(plan[("l6", "ab")]["worst_tcp_mbps"] > plan[("l6", "ab")]["base_load_mbps"] + 150 or
      plan[("l6", "ab")]["worst_tcp_mbps"] >= 190,
      f"lag_lab: l5 uzilganda l6 ga ikkala oqim ({plan[('l6', 'ab')]['worst_tcp_mbps']} Mbit/s TCP)")

# ---- vrrp_lab: r1 nosozligi -> r2 orqali omon qoladi ----
p = load("vrrp_lab")
rep = analyze(p, "zaxira", "both")
c = case(rep, "device", "r1")
check(c and not c["broken"] and {"video", "ping"} <= set(c["rerouted"]),
      f"vrrp_lab: r1 nosozligi — oqimlar r2 orqali ({c and c['rerouted']})")
c3 = case(rep, "link", "l3")
check(c3 and not c3["broken"] and "VRRP" in c3["convergence"], f"vrrp_lab: r1 LAN kabeli -> VRRP {c3 and c3['convergence']}")
check(case(rep, "device", "r3")["broken"], "vrrp_lab: r3 (yadro) — SPOF")

# ---- firewall_lab: firewall bloklagan oqimlar nosozlik deb hisoblanmaydi ----
p = load("firewall_lab")
rep = analyze(p, "trafik", "both")
down = set(rep["baseline"]["down_flows"])
check({"ssh_in", "ping_in"} <= down, f"firewall_lab: asosiy holatda bloklangan {sorted(down)}")
check(all(b["flow"] not in down for c in rep["cases"] for b in c["broken"]),
      "firewall_lab: bloklangan oqimlar 'uzilgan' ro'yxatida yo'q")
check(rep["wall_s"] < 10, f"tezlik: {rep['wall_s']} s")
print(f"  ..  barcha tahlillar {time.time() - t0:.1f} s")

# ---- API ----
with TestClient(create_app("demo")) as cl:
    rp = cl.get("/api/projects/rip_ring?example=true").json()
    r = cl.post("/api/analysis/survivability", json={"project": rp, "scenario": "uzilish", "targets": "both"})
    js = r.json()
    check(r.status_code == 200 and js["cases"] and js["cases"][0]["score"] >= js["cases"][-1]["score"]
          and "capacity_plan" in js, f"API: {r.status_code}, {len(js.get('cases', []))} holat, reyting")
    r = cl.post("/api/analysis/survivability", json={"project": rp, "scenario": "yoq"})
    check(r.status_code == 400, f"API: noma'lum ssenariy -> {r.status_code}")
    r = cl.post("/api/analysis/survivability", json={"project": rp, "scenario": "uzilish", "targets": "xyz"})
    check(r.status_code == 400, f"API: noto'g'ri targets -> {r.status_code}")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

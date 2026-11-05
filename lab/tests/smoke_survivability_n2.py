"""Survivability N-2 (juft nosozliklar), SRLG va exclude_access — analitik model.

    NETLAB_SKIP_NS3=1 python3 -m lab.tests.smoke_survivability_n2
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from lab.model import validate_project  # noqa: E402
from lab.server import create_app  # noqa: E402
from lab.survivability import analyze  # noqa: E402
from lab.topogen import generate  # noqa: E402

FAILS = []
EX = os.path.join(ROOT, "lab", "examples")


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def load(name):
    with open(os.path.join(EX, name + ".json")) as f:
        return json.load(f)


# ---- rip_ring: kirish kabellarisiz minimal kesim 2 (r1 ikki tomondan uzilsa pc1 ajraladi) ----
rr = load("rip_ring")
r = analyze(rr, "uzilish", "links", order=2)
check(r["min_cut"] == {"video": 1, "ping": 1}, f"rip_ring (barcha kabellar): min kesim {r['min_cut']} (l1/l5 — kirish SPOF)")
check(r["n2"]["pairs_evaluated"] == 10 and not r["n2"]["truncated"], f"rip_ring: 10 juftlik ({r['n2']['pairs_evaluated']})")
r = analyze(rr, "uzilish", "links", order=2, exclude_access=True)
tg = sorted(c["target"] for c in r["cases"])
check(tg == ["l2", "l3", "l4"], f"exclude_access: host kabellari (l1, l5) nishonlardan chiqdi {tg}")
pairs = sorted(v["target"] for v in r["n2"]["vulnerabilities"])
check(pairs == ["l:l2 + l:l3", "l:l2 + l:l4"],
      f"rip_ring N-2 zaifliklari (faqat juft nosozlikda uziladi): {pairs}")
check(r["min_cut"] == {"video": 2, "ping": 2}, f"rip_ring: min kesim 2 {r['min_cut']}")
check(not r["spof"], "rip_ring (kirish kabellarisiz): N-1 da SPOF yo'q")
check(r["n2"]["cases"][0]["kind"] == "pair" and len(r["n2"]["cases"][0]["members"]) == 2,
      "N-2 holat: kind=pair, members 2 ta")

# ---- SRLG: l2+l3 bitta trassada -> juftlik bilan bir xil ta'sir ----
s = analyze(rr, "uzilish", "links", srlg={"trassa": ["l2", "l3"]}, exclude_access=True)
sc_ = next(c for c in s["cases"] if c["kind"] == "srlg")
pair = next(c for c in r["n2"]["cases"] if c["target"] == "l:l2 + l:l3")
check(sc_["target"] == "trassa" and sorted(b["flow"] for b in sc_["broken"]) == sorted(b["flow"] for b in pair["broken"])
      and sc_["down_links"] == ["l2", "l3"],
      f"SRLG 'trassa' = juftlik l2+l3: uzildi {[b['flow'] for b in sc_['broken']]}")
check(s["srlg_broken"] == {"video": ["trassa"], "ping": ["trassa"]} and s["min_cut"]["video"] == ">=2",
      f"SRLG: srlg_broken {s['srlg_broken']}, min kesim (N-1 bo'yicha) {s['min_cut']}")
rr2 = json.loads(json.dumps(rr))
rr2["srlg"] = {"trassa": ["l2", "l3"]}
s2 = analyze(rr2, "uzilish", "links", exclude_access=True)
check(any(c["kind"] == "srlg" and c["target"] == "trassa" for c in s2["cases"]), "SRLG loyihaning `srlg` maydonidan")
rr2["srlg"] = {"x": ["l2", "nope"]}
check(any("srlg" in e for e in validate_project(rr2)), "validatsiya: noma'lum kabelli SRLG rad etiladi")

# ---- ospf_lab: r1-r2 yagona kabel — min kesim 1 ----
ol = load("ospf_lab")
o = analyze(ol, "asosiy", "links", order=2, exclude_access=True)
check(all(v == 1 for v in o["min_cut"].values()) and any(x["target"] == "l4" for x in o["spof"]),
      f"ospf_lab: l4 SPOF, min kesim {o['min_cut']}")
check(not o["n2"]["vulnerabilities"], "ospf_lab: yangi N-2 zaiflik yo'q (hammasi allaqachon SPOF)")

# ---- reliability_lab: qurilmalar + kabellar, qurilma va o'z kabeli juftligi kesiladi ----
rl = load("reliability_lab")
q = analyze(rl, "qisqa", "both", order=2, exclude_access=True)
check(q["n2"]["pruned"] > 0 and q["min_cut"]["core"] == 1 and q["min_cut"]["edge"] == 1,
      f"reliability_lab: {q['n2']['pruned']} juftlik kesildi, min kesim {q['min_cut']}")
check("capacity_plan" in q["n2"] and isinstance(q["n2"]["upgrades"], list), "N-2 sig'im rejasi alohida maydonda")

# ---- cheklash (truncation) va determinizm ----
t = analyze(rr, "uzilish", "links", order=2, max_cases=4)
check(t["n2"]["truncated"] and t["n2"]["pairs_evaluated"] == 4 and t["n2"]["pairs_candidates"] == 10,
      f"max_cases=4: truncated={t['n2']['truncated']}, baholandi {t['n2']['pairs_evaluated']}/{t['n2']['pairs_candidates']}")
a1 = analyze(rr, "uzilish", "links", order=2)
a2 = analyze(rr, "uzilish", "links", order=2)
a1.pop("wall_s"), a2.pop("wall_s")
check(json.dumps(a1, sort_keys=True, default=str) == json.dumps(a2, sort_keys=True, default=str), "N-2: determinizm")

# ---- tezlik: generatsiya qilingan topologiyalar ----
for kind, kw, limit in (("campus", dict(core=2, dist=4, access=8, hosts=3), 60),
                        ("ring", dict(routers=12, hosts=2), 60)):
    g = generate(kind, **kw)
    t0 = time.time()
    rg = analyze(g, g["scenarios"]["asosiy"], "links", order=2)
    dt = time.time() - t0
    check(dt < limit and not rg["n2"]["truncated"],
          f"{kind}: {len(g['links'])} kabel, {rg['n2']['pairs_evaluated']} juftlik -> {dt:.1f} s (chegara {limit} s)")

# ---- API ----
with TestClient(create_app("demo")) as c:
    old = c.post("/api/analysis/survivability", json={"project": rr, "scenario": "uzilish", "targets": "links"})
    ref = analyze(rr, "uzilish", "links")
    check(old.status_code == 200 and sorted(old.json()) == sorted(ref) and "n2" not in old.json()
          and "min_cut" not in old.json(), f"API: eski so'rov — avvalgi kalitlar {sorted(old.json())}")
    new = c.post("/api/analysis/survivability", json={"project": rr, "scenario": "uzilish", "targets": "links",
                                                      "order": 2, "exclude_access": True,
                                                      "srlg": {"trassa": ["l2", "l3"]}, "max_cases": 100})
    j = new.json()
    check(new.status_code == 200 and j["n2"]["pairs_evaluated"] == 3 and j["min_cut"]["video"] == 2
          and any(x["kind"] == "srlg" for x in j["cases"]), f"API: order=2 + srlg + exclude_access ({new.status_code})")
    bad = c.post("/api/analysis/survivability", json={"project": rr, "scenario": "uzilish", "order": 3})
    check(bad.status_code == 400, f"API: order=3 -> {bad.status_code}")
    bad = c.post("/api/analysis/survivability", json={"project": rr, "scenario": "uzilish", "srlg": {"x": ["zz"]}})
    check(bad.status_code == 400, f"API: noto'g'ri srlg -> {bad.status_code}")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

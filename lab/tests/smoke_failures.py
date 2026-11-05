"""Nosozlik/tiklanish (MTBF/MTTR) smoke testi — demo driver + analitik [+ ns-3].

    python3 -m lab.tests.smoke_failures          # NETLAB_SKIP_NS3=1 — ns-3'siz
"""

import copy
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from lab import failures as F  # noqa: E402
from lab.engines import analytic  # noqa: E402
from lab.server import create_app  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def wait_run(c, pname, rid, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        runs = c.get(f"/api/runs/{pname}").json()["runs"]
        if not any(r.get("status") == "running" for r in runs):
            break
        time.sleep(0.3)
    return c.get(f"/api/runs/{pname}/{rid}").json()


def run_local(p, sc):
    ex = F.expand(p, sc)
    return F.postprocess(analytic.run(p, ex, lambda f: None, lambda m: None), sc, ex, p)


def line_project():
    """h1 - s1 - h2: l2 — zaxirasiz yagona kabel."""
    def host(n, i, ip):
        return {"id": i, "type": "host", "x": 0, "y": 0,
                "config": {"interfaces": {f"{n}-eth1": {"ip": ip}}, "gateway": "", "routes": []}}

    def link(a, pa, b, pb):
        return {"a": {"dev": a, "port": pa}, "b": {"dev": b, "port": pb},
                "params": {"bw": 100, "delay": "1ms", "jitter": "0ms", "loss": 0, "queue": 1000}, "up": True}
    return {"version": 1, "name": "fline", "devices": {
        "h1": host("h1", 1, "10.0.0.1/24"), "h2": host("h2", 2, "10.0.0.2/24"),
        "s1": {"id": 3, "type": "switch", "x": 0, "y": 0, "config": {"mode": "standalone", "stp": False}}},
        "links": {"l1": link("h1", 1, "s1", 1), "l2": link("s1", 2, "h2", 1)},
        "p4_programs": {}, "controllers": {}, "scenarios": {}}


def line_sc(seed=1, dist="exponential", mtbf=100, mttr=10, dur=3600):
    return {"duration": dur, "events": [],
            "apps": [{"id": "u", "type": "udp", "src": "h1", "dst": "h2", "rate": "1Mbps", "size": 1000,
                      "start": 0, "stop": dur}],
            "failures": {"seed": seed, "distribution": dist,
                         "links": {"mtbf_s": mtbf, "mttr_s": mttr, "targets": ["l2"]}}}


print("== nosozlik modeli")
lp = line_project()
e1, e2, e3 = (F.expand(lp, line_sc(s))["events"] for s in (1, 1, 2))
check(e1 == e2 and e1 != e3 and len(e1) > 20, f"determinizm: seed 1 == seed 1 ({len(e1)} hodisa), seed 2 farqli")
check(all(e["source"] == "failure" and e["cause"] == ["l2"] for e in e1)
      and [e["action"] for e in e1[:4]] == ["link_down", "link_up", "link_down", "link_up"],
      "hodisalar: link_down/link_up juftliklari, source=failure, cause=[l2]")

theo = 100 * 100 / 110          # 3600 s = 32 to'liq davr (110 s) + qisman
r = run_local(lp, line_sc(dist="fixed"))
a = r["availability"]
check(abs(a["flows"]["u"]["availability_pct"] - theo) < 1.5 and abs(a["theoretical"]["l2"] - theo) < 0.01,
      f"fixed MTBF 100/MTTR 10, 3600 s: mavjudlik {a['flows']['u']['availability_pct']}% ≈ nazariy {theo:.2f}%")
check(abs((100 - a["links"]["l2"]["downtime_pct"]) - theo) < 1.0 and a["links"]["l2"]["failures"] == 32,
      f"l2 ishlamay turgan vaqt {a['links']['l2']['downtime_pct']}%, {a['links']['l2']['failures']} nosozlik")
vals = [run_local(lp, line_sc(seed=s))["availability"]["flows"]["u"]["availability_pct"] for s in range(1, 7)]
mean = sum(vals) / len(vals)
check(abs(mean - theo) < 3.0, f"eksponensial, 6 seed o'rtachasi {mean:.2f}% ≈ nazariy {theo:.2f}% ({vals})")
outs = a["flows"]["u"]["outages"]
check(len(outs) == 32 and all(9 <= o["duration_s"] <= 11 for o in outs), f"uzilishlar ro'yxati: {len(outs)} ta, ~10 s")

# qurilma nosozligi: barcha kabellari; kabel + qurilma sabablari birlashadi
rp = json.load(open(os.path.join(ROOT, "lab", "examples", "rip_ring.json")))
scd = {"duration": 300, "apps": [], "events": [],
       "failures": {"seed": 5, "distribution": "fixed", "devices": {"mtbf_s": 50, "mttr_s": 10, "targets": ["r3"]},
                    "links": {"mtbf_s": 70, "mttr_s": 30, "targets": ["l3"]}}}
dm = F.link_down_map(rp, scd)
check(set(dm) == {"l3", "l4"} and dm["l4"][0][:2] == (50.0, 60.0),
      f"qurilma r3: l3+l4 birga uziladi ({ {k: v[:2] for k, v in dm.items()} })")
check(any(len(iv[2]) == 2 for iv in dm["l3"]) and all(b > a for a, b, _c in dm["l3"]),
      f"l3: kabel+qurilma uzilishlari birlashtirildi (intervallar {[iv[:2] for iv in dm['l3']][:3]})")

errs_ = {
    "mtbf 0": {"links": {"mtbf_s": 0, "mttr_s": 5}},
    "noma'lum kabel": {"links": {"mtbf_s": 10, "mttr_s": 5, "targets": ["l99"]}},
    "noma'lum qurilma": {"devices": {"mtbf_s": 10, "mttr_s": 5, "targets": ["zz"]}},
    "taqsimot": {"distribution": "weibull", "links": {"mtbf_s": 10, "mttr_s": 5}},
}

with TestClient(create_app("demo")) as c:
    for name, fb in errs_.items():
        sc_ = line_sc(dur=100)
        sc_["failures"] = fb
        e = c.post("/api/scenario/validate", json={"project": lp, "scenario": sc_}).json()["errors"]
        check(any("failures" in x for x in e), f"validatsiya: {name} rad etiladi ({e[:1]})")

    print("== reliability_lab (zaxirali yo'l vs zaxirasiz kabel)")
    p = c.get("/api/projects/reliability_lab?example=true").json()
    check(c.post("/api/validate", json=p).json()["errors"] == [], "reliability_lab: validatsiya")
    rid = c.post("/api/runs", json={"project": p, "scenario": "ishonchlilik", "engine": "analytic"}).json()["run_id"]
    r = wait_run(c, "reliability_lab", rid)
    av = r.get("availability") or {}
    fl = av.get("flows", {})
    gen = [e for e in r.get("events", []) if e.get("source") == "failure"]
    check(r["status"] == "done" and len(gen) == av.get("failure_events") and len(gen) > 10,
          f"analytic: {len(gen)} nosozlik hodisasi qo'llandi")
    check(fl.get("core", {}).get("availability_pct", 0) > fl.get("edge", {}).get("availability_pct", 100),
          f"zaxira yo'l: core {fl.get('core', {}).get('availability_pct')}% > edge (zaxirasiz l6) "
          f"{fl.get('edge', {}).get('availability_pct')}%")
    check(fl["core"]["availability_pct"] > av["theoretical"]["l2"],
          f"core mavjudligi bitta kabel nazariyasidan yuqori ({av['theoretical']['l2']}%)")
    c.delete(f"/api/runs/reliability_lab/{rid}")

    # tadqiqot: MTTR oshsa mavjudlik kamayadi
    lp2 = copy.deepcopy(lp)
    lp2["scenarios"] = {"f": line_sc(dur=1200)}
    st = c.post("/api/studies", json={"project": lp2, "scenario": "f", "engine": "analytic",
                                      "param": {"kind": "failure", "group": "links", "field": "mttr_s"},
                                      "values": [5, 20, 60]}).json()
    t0 = time.time()
    while time.time() - t0 < 120:
        runs = [x for x in c.get("/api/runs/fline").json()["runs"] if x.get("study_id") == st.get("study_id")]
        if len(runs) == 3 and not any(x.get("status") == "running" for x in runs):
            break
        time.sleep(0.3)
    av_by = {}
    for x in runs:
        full = c.get(f"/api/runs/fline/{x['run_id']}").json()
        av_by[full.get("study_value")] = full["availability"]["flows"]["u"]["availability_pct"]
        c.delete(f"/api/runs/fline/{x['run_id']}")
    check(len(av_by) == 3 and av_by[5] > av_by[20] > av_by[60], f"tadqiqot mttr_s: mavjudlik {av_by}")

    if os.environ.get("NETLAB_SKIP_NS3") != "1":
        eng = {e["id"]: e for e in c.get("/api/engines").json()["engines"]}
        if eng.get("ns3", {}).get("available"):
            out = {}
            for e_ in ("analytic", "ns3"):
                rid = c.post("/api/runs", json={"project": p, "scenario": "qisqa", "engine": e_}).json()["run_id"]
                rr = wait_run(c, "reliability_lab", rid)
                out[e_] = (rr["status"], (rr.get("availability") or {}).get("flows", {}), rr.get("wall_s"))
                c.delete(f"/api/runs/reliability_lab/{rid}")
            (sa, fa, _w), (sn, fn, wn) = out["analytic"], out["ns3"]
            check(sn == "done" and abs(fn["edge"]["availability_pct"] - fa["edge"]["availability_pct"]) < 6
                  and fn["core"]["availability_pct"] > 95,
                  f"ns3 (60 s, {wn} s): edge {fn['edge']['availability_pct']}% (analytic {fa['edge']['availability_pct']}%), "
                  f"core {fn['core']['availability_pct']}%")
        else:
            print("  --   ns-3 yo'q")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

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

    # ---- ssenariylar: analitik (har doim) + ns-3 (o'rnatilgan bo'lsa) ----
    p = c.get("/api/projects/ospf_lab?example=true").json()
    engines = {e["id"]: e["available"] for e in c.get("/api/engines").json()["engines"]}
    check(engines["analytic"] and not engines["mininet"], "dvigatellar: analitik bor, mininet demo'da yo'q")
    bad_sc = {"duration": 10, "apps": [{"id": "x", "type": "udp", "src": "pc1", "dst": "nope"}], "events": []}
    check(len(c.post("/api/scenario/validate", json={"project": p, "scenario": bad_sc}).json()["errors"]) >= 2,
          "noto'g'ri ssenariy rad etiladi")
    want = ["analytic"] + (["ns3"] if engines["ns3"] and os.environ.get("NETLAB_SKIP_NS3") != "1" else [])
    for eng in want:
        rid = c.post("/api/runs", json={"project": p, "scenario": "uzilish", "engine": eng}).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 240:
            if not any(r.get("status") == "running" for r in c.get("/api/runs/ospf_lab").json()["runs"]):
                break
            time.sleep(0.5)
        r = c.get(f"/api/runs/ospf_lab/{rid}").json()
        check(r["status"] == "done", f"{eng}: ishga tushirish tugadi ({r.get('error')})")
        v = r["flows"]["video"]["series"]
        before = [x["thr_bps"] for x in v if 5 <= x["t"] <= 9]
        during = [x["thr_bps"] for x in v if 12 <= x["t"] <= 15]
        after = [x["thr_bps"] for x in v if 20 <= x["t"] <= 28]
        check(before and min(before) > 4e6, f"{eng}: uzilishdan oldin video ~5 Mbit/s")
        check(during and max(during) < 1e5, f"{eng}: kabel uzilganda video to'xtaydi")
        check(after and min(after) > 4e6, f"{eng}: tiklangandan keyin video qaytadi")
        c.delete(f"/api/runs/ospf_lab/{rid}")
    # ---- parametrik tadqiqot (analitik): kabel tezligi oshsa FTP o'tkazuvchanligi oshadi ----
    st = c.post("/api/studies", json={"project": p, "scenario": "asosiy", "engine": "analytic",
                                      "param": {"kind": "link", "link": "l4", "field": "bw"},
                                      "values": [10, 50, 100]}).json()
    t0 = time.time()
    while time.time() - t0 < 60:
        mem = [r for r in c.get("/api/runs/ospf_lab").json()["runs"] if r.get("study_id") == st["study_id"]]
        if len(mem) == 3 and all(r["status"] == "done" for r in mem):
            break
        time.sleep(0.3)
    mem.sort(key=lambda r: r["study_index"])
    ftp = [c.get(f"/api/runs/ospf_lab/{r['run_id']}").json()["flows"]["ftp"]["summary"]["avg_thr_bps"] for r in mem]
    check(len(ftp) == 3 and ftp[0] < ftp[1] < ftp[2], f"tadqiqot: l4.bw 10/50/100 -> ftp o'sadi {[round(x / 1e6, 1) for x in ftp]}")
    for r in mem:
        c.delete(f"/api/runs/ospf_lab/{r['run_id']}")

    # ---- RIP konvergensiyasi: asosiy kabel uzilganda trafik bir muddat to'xtaydi, keyin zaxira yo'l ----
    rp = c.get("/api/projects/rip_ring?example=true").json()
    for eng in want:
        rid = c.post("/api/runs", json={"project": rp, "scenario": "uzilish", "engine": eng}).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 240:
            if not any(r.get("status") == "running" for r in c.get("/api/runs/rip_ring").json()["runs"]):
                break
            time.sleep(0.5)
        r = c.get(f"/api/runs/rip_ring/{rid}").json()
        v = {int(x["t"]): x["thr_bps"] for x in r["flows"]["video"]["series"]}
        gap = sum(1 for t in range(11, 50) if v.get(t, 0) < 1e5)
        check(3 <= gap <= 32 and v.get(55, 0) > 3e6, f"{eng}: RIP uzilish {gap}s, keyin zaxira yo'l (r3) orqali tiklanadi")
        c.delete(f"/api/runs/rip_ring/{rid}")

    # ---- Wi-Fi: radio modeli, ping, qamrovdan tashqari, analitik/ns-3 o'tkazuvchanlik ----
    wp = c.get("/api/projects/wifi_ofis?example=true").json()
    check(c.post("/api/validate", json=wp).json()["errors"] == [], "wifi_ofis: validatsiya")
    c.post("/api/net/start", json={"project": wp})
    check("0% packet loss" in ping(c, "lap3", "10.0.0.10"), "Wi-Fi: lap3 -> srv1 ping (64 m, MCS3)")
    far = json.loads(json.dumps(wp)); far["devices"]["lap3"]["x"], far["devices"]["lap3"]["y"] = -400, -400
    c.post("/api/net/stop"); c.post("/api/net/start", json={"project": far})
    check("100% packet loss" in ping(c, "lap3", "10.0.0.10", 1), "Wi-Fi: qamrovdan tashqarida ping o'tmaydi")
    c.post("/api/net/stop")
    bad = json.loads(json.dumps(wp)); bad["links"]["w2"]["a"]["dev"] = "lap1"   # lap1 ikki marta Wi-Fi'da
    check(any("bitta Wi-Fi" in e for e in c.post("/api/validate", json=bad).json()["errors"]),
          "Wi-Fi: bitta stansiya ikki AP/kabelga ulanmaydi")
    for eng in want:
        rid = c.post("/api/runs", json={"project": wp, "scenario": "yolgiz", "engine": eng}).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 240:
            if not any(r.get("status") == "running" for r in c.get("/api/runs/wifi_ofis").json()["runs"]):
                break
            time.sleep(0.5)
        r = c.get(f"/api/runs/wifi_ofis/{rid}").json()
        thr = r["flows"]["d1"]["summary"]["avg_thr_bps"] / 1e6
        check(70 <= thr <= 100, f"{eng}: Wi-Fi TCP (23 m, 802.11ax 20 MHz) 70..100 Mbit/s: {thr:.1f}")
        c.delete(f"/api/runs/wifi_ofis/{rid}")

    if "ns3" not in want:
        print("  --   ns-3 o'rnatilmagan (pip install ns3) — ns-3 testi o'tkazib yuborildi")

with TestClient(create_app("demo", token="t0k")) as c:
    check(c.get("/api/info").status_code == 401, "token: tokensiz 401")
    check(c.get("/api/info?token=t0k").status_code == 200, "token: to'g'ri token 200")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

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
        # kabel navbati/tashlanish: 14 Mbit/s video 10 Mbit/s r1-r2 (l4) kabelida
        sc_over = {**p["scenarios"]["asosiy"], "duration": 8,
                   "apps": [{"id": "v", "type": "udp", "src": "pc1", "dst": "srv2", "rate": "14Mbps",
                             "size": 1000, "start": 1, "stop": 7}]}
        p_over = {**p, "scenarios": {**p["scenarios"], "ortiqcha": sc_over}}
        rid = c.post("/api/runs", json={"project": p_over, "scenario": "ortiqcha", "engine": eng}).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 240:
            if not any(r.get("status") == "running" for r in c.get("/api/runs/ospf_lab").json()["runs"]):
                break
            time.sleep(0.5)
        r = c.get(f"/api/runs/ospf_lab/{rid}").json()
        l4 = [x for x in (r.get("links") or {}).get("l4", []) if 3 <= x["t"] <= 6]
        q = [x.get("queue_ab", 0) for x in l4]
        d = [x.get("drops_ab", 0) for x in l4]
        check(l4 and min(q) > 800 and 350 < sum(d) / len(d) < 650,
              f"{eng}: l4 navbati to'la (~1000) va ~500 paket/s tashlanadi: q={q} drops={d}")
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
        check(3 <= gap <= 40 and v.get(55, 0) > 3e6, f"{eng}: RIP uzilish {gap}s, keyin zaxira yo'l (r3) orqali tiklanadi")
        if eng == "analytic":
            cv = r.get("convergence") or [{}]
            r1 = [x for x in (r.get("nodes") or {}).get("r1", []) if 3 <= x["t"] <= 9]
            check(cv[0].get("converged_s") == 15 and {"r1", "r2"} <= set(cv[0].get("devices", [])),
                  f"analytic: konvergensiya vaqti {cv[0].get('converged_s')} s (rip_convergence_s=15)")
            check(r1 and min(x["fwd_pps"] for x in r1) > 400, "analytic: r1 forwarding pps (qurilma statistikasi)")
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

    # ---- Wi-Fi: kanal xalaqiti (tadqiqot ap2.channel = 36,40) va harakatlanuvchi stansiya ----
    kp = c.get("/api/projects/wifi_kampus?example=true").json()
    check(c.post("/api/validate", json=kp).json()["errors"] == [], "wifi_kampus: validatsiya")
    for eng in want:
        st = c.post("/api/studies", json={"project": kp, "scenario": "xalaqit", "engine": eng,
                                          "param": {"kind": "ap", "ap": "ap2", "field": "channel"}, "values": [36, 40]}).json()
        t0 = time.time()
        while time.time() - t0 < 300:
            mem = [r for r in c.get("/api/runs/wifi_kampus").json()["runs"] if r.get("study_id") == st["study_id"]]
            if len(mem) == 2 and all(r["status"] != "running" for r in mem):
                break
            time.sleep(0.5)
        mem.sort(key=lambda r: r["study_index"])
        tot = [sum(f["summary"]["avg_thr_bps"] for f in c.get(f"/api/runs/wifi_kampus/{r['run_id']}").json()["flows"].values()) / 1e6
               for r in mem]
        check(len(tot) == 2 and tot[0] < 0.7 * tot[1], f"{eng}: bir kanal (36/36) {tot[0]:.0f} < turli kanal (36/40) {tot[1]:.0f} Mbit/s" if len(tot) == 2 else f"{eng}: kanal tadqiqoti")
        for r in mem:
            c.delete(f"/api/runs/wifi_kampus/{r['run_id']}")
        rid = c.post("/api/runs", json={"project": kp, "scenario": "yurish", "engine": eng}).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 300:
            if not any(r.get("status") == "running" for r in c.get("/api/runs/wifi_kampus").json()["runs"]):
                break
            time.sleep(0.5)
        v = {int(x["t"]): x["thr_bps"] / 1e6 for x in c.get(f"/api/runs/wifi_kampus/{rid}").json()["flows"]["m"]["series"]}
        check(v.get(4, 0) > 50 and 5 < v.get(22, 0) < 50 and v.get(38, 1) < 0.1,
              f"{eng}: harakat — {v.get(4, 0):.0f} -> {v.get(22, 0):.0f} -> {v.get(38, 0):.0f} Mbit/s")
        c.delete(f"/api/runs/wifi_kampus/{rid}")

    # ---- Wi-Fi roaming: mob1 ap1 -> ap2 (bitta ESS) ----
    rp2 = c.get("/api/projects/wifi_roaming?example=true").json()
    for eng in want:
        rid = c.post("/api/runs", json={"project": rp2, "scenario": "yurish", "engine": eng}).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 300:
            if not any(r.get("status") == "running" for r in c.get("/api/runs/wifi_roaming").json()["runs"]):
                break
            time.sleep(0.5)
        r = c.get(f"/api/runs/wifi_roaming/{rid}").json()
        roams = [e for e in r.get("events", []) if e.get("action") == "roam"]
        v = {int(x["t"]): x["thr_bps"] / 1e6 for x in r["flows"]["v"]["series"]}
        check(len(roams) == 1 and roams[0]["to"] == "ap2" and 20 <= roams[0]["t"] <= 34 and v.get(10, 0) > 15 and v.get(40, 0) > 15,
              f"{eng}: roaming ap1->ap2 t={roams[0]['t'] if roams else '?'}s, video {v.get(10, 0):.0f} -> {v.get(40, 0):.0f} Mbit/s")
        c.delete(f"/api/runs/wifi_roaming/{rid}")

    # ---- VLAN: access/trunk, router-on-a-stick, izolyatsiya ----
    vp = c.get("/api/projects/vlan_lab?example=true").json()
    check(c.post("/api/validate", json=vp).json()["errors"] == [], "vlan_lab: validatsiya")
    c.post("/api/net/start", json={"project": vp})
    check("0% packet loss" in ping(c, "pc1", "10.0.10.13"), "vlan: pc1 -> pc3 (VLAN10, trunk)")
    check("0% packet loss" in ping(c, "pc1", "10.0.20.14"), "vlan: pc1 -> pc4 (inter-VLAN r1 orqali)")
    check("100% packet loss" in ping(c, "pc1", "10.0.10.15", 1), "vlan: pc5 (boshqa VLAN) izolyatsiya")
    c.post("/api/net/stop")
    bad_v = json.loads(json.dumps(vp))
    bad_v["devices"]["s1"]["config"]["ports"]["1"] = {"mode": "access", "vlan": 5000}
    check(any("VLAN" in e for e in c.post("/api/validate", json=bad_v).json()["errors"]), "vlan: noto'g'ri VLAN id rad etiladi")
    for eng in want:
        rid = c.post("/api/runs", json={"project": vp, "scenario": "vlanlar", "engine": eng}).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 120:
            if not any(r.get("status") == "running" for r in c.get("/api/runs/vlan_lab").json()["runs"]):
                break
            time.sleep(0.3)
        r = c.get(f"/api/runs/vlan_lab/{rid}").json()
        if eng == "ns3":
            check(r["status"] == "error" and "trunk" in (r.get("error") or ""), f"ns3: VLAN trunk — tushunarli xato ({r.get('error', '')[:60]})")
        else:
            l4 = [x for x in r["links"]["l4"] if 3 <= x["t"] <= 18]
            inter = r["flows"]["inter"]["summary"]["avg_thr_bps"]
            check(l4 and min(x["ab_bps"] for x in l4) > 11e6 and min(x["ba_bps"] for x in l4) > 11e6 and inter > 11e6,
                  f"{eng}: inter-VLAN 12 Mbit/s trunk kabelidan (l4) ikki yo'nalishda o'tadi")
        c.delete(f"/api/runs/vlan_lab/{rid}")

    # ---- ilova modellari: VoIP (MOS), HTTP (sahifa vaqti), FTP (fayl vaqti) ----
    ap_ = c.get("/api/projects/ospf_lab?example=true").json()
    ap_["scenarios"]["ilovalar"] = {"duration": 30, "events": [], "apps": [
        {"id": "tel", "type": "voip", "src": "pc1", "dst": "srv2", "codec": "g711", "start": 1, "stop": 29},
        {"id": "web", "type": "http", "src": "pc2", "dst": "srv1", "page": "500KB", "think": 3, "start": 1, "stop": 29},
        {"id": "fayl", "type": "ftp", "src": "pc1", "dst": "srv1", "file": "5MB", "start": 3, "stop": 29}]}
    bad_a = json.loads(json.dumps(ap_["scenarios"]["ilovalar"]))
    bad_a["apps"][0]["codec"] = "opus"
    check(len(c.post("/api/scenario/validate", json={"project": ap_, "scenario": bad_a}).json()["errors"]) == 1,
          "ilovalar: noma'lum kodek rad etiladi")
    for eng in want:
        rid = c.post("/api/runs", json={"project": ap_, "scenario": "ilovalar", "engine": eng}).json()["run_id"]
        t0 = time.time()
        while time.time() - t0 < 300 and any(r.get("status") == "running" for r in c.get("/api/runs/ospf_lab").json()["runs"]):
            time.sleep(0.5)
        r = c.get(f"/api/runs/ospf_lab/{rid}").json()
        a_ = r.get("apps") or {}
        tel, web, ftp = a_.get("tel", {}), a_.get("web", {}), a_.get("fayl", {})
        check(r["status"] == "done" and (tel.get("avg_mos") or 0) > 2.5 and web.get("pages_done") == 10
              and (web.get("avg_page_s") or 9) < 2 and ftp.get("completed") and 3.5 < ftp["transfer_s"] < 8,
              f"{eng}: VoIP MOS {tel.get('avg_mos')}, HTTP {web.get('pages_done')}/10 sahifa {web.get('avg_page_s')} s, "
              f"FTP 5MB {ftp.get('transfer_s')} s")
        c.delete(f"/api/runs/ospf_lab/{rid}")

    # ---- QoS: fifo / prio / fq_codel — VoIP MOS TCP+UDP yuklamasi ostida (tadqiqot: l4.qdisc) ----
    qp = c.get("/api/projects/ospf_lab?example=true").json()
    qp["scenarios"]["qos"] = {"duration": 30, "events": [], "apps": [
        {"id": "tel", "type": "voip", "src": "pc1", "dst": "srv2", "codec": "g711", "start": 1, "stop": 29},
        {"id": "fayl", "type": "ftp", "src": "pc1", "dst": "srv1", "file": "20MB", "start": 3, "stop": 29},
        {"id": "video", "type": "udp", "src": "srv1", "dst": "pc2", "rate": "9Mbps", "size": 1000, "start": 15, "stop": 25}]}
    for eng in want:
        st = c.post("/api/studies", json={"project": qp, "scenario": "qos", "engine": eng,
                                          "param": {"kind": "link", "link": "l4", "field": "qdisc"},
                                          "values": ["fifo", "prio", "fq_codel"]}).json()
        t0 = time.time()
        runs = []
        while time.time() - t0 < 600:
            runs = [r for r in c.get("/api/runs/ospf_lab").json()["runs"] if r.get("study_id") == st.get("study_id")]
            if len(runs) == 3 and not any(r.get("status") == "running" for r in runs):
                break
            time.sleep(0.5)
        mos = {}
        for r in runs:
            full = c.get(f"/api/runs/ospf_lab/{r['run_id']}").json()
            mos[full.get("study_value")] = (full.get("apps") or {}).get("tel", {}).get("avg_mos")
            c.delete(f"/api/runs/ospf_lab/{r['run_id']}")
        check(all(mos.get(k) for k in ("fifo", "prio", "fq_codel")) and mos["fifo"] < 3.5
              and mos["prio"] > 4.2 and mos["fq_codel"] > 4.0,
              f"{eng}: QoS tadqiqoti — VoIP MOS fifo {mos.get('fifo')}, prio {mos.get('prio')}, fq_codel {mos.get('fq_codel')}")

    # ---- DHCP + NAT ----
    npj = c.get("/api/projects/nat_dhcp_lab?example=true").json()
    check(c.post("/api/validate", json=npj).json()["errors"] == [], "nat_dhcp_lab: validatsiya")
    bad_n = json.loads(json.dumps(npj))
    bad_n["devices"]["r1"]["config"]["dhcp_server"]["pools"][0]["end"] = "10.9.9.9"
    check(any("DHCP" in e for e in c.post("/api/validate", json=bad_n).json()["errors"]), "dhcp: subnetdan tashqari pool rad etiladi")
    c.post("/api/net/start", json={"project": npj})
    check("0% packet loss" in ping(c, "pc1", "198.51.100.10"), "nat: pc1 (DHCP 10.0.1.100) -> srv1 NAT orqali")
    check("100% packet loss" in ping(c, "srv1", "10.0.1.100", 1), "nat: tashqaridan srv1 -> pc1 o'tmaydi")
    c.post("/api/net/stop")
    no_nat = json.loads(json.dumps(npj))
    no_nat["devices"]["r1"]["config"].pop("nat")
    c.post("/api/net/start", json={"project": no_nat})
    check("100% packet loss" in ping(c, "pc1", "198.51.100.10", 1), "nat: NAT'siz javob qaytmaydi (isp ichki tarmoqni bilmaydi)")
    c.post("/api/net/stop")
    rid = c.post("/api/runs", json={"project": npj, "scenario": "internet", "engine": "analytic"}).json()["run_id"]
    t0 = time.time()
    while time.time() - t0 < 60 and any(r.get("status") == "running" for r in c.get("/api/runs/nat_dhcp_lab").json()["runs"]):
        time.sleep(0.3)
    r = c.get(f"/api/runs/nat_dhcp_lab/{rid}").json()
    fs = r["flows"]
    check(fs["web"]["summary"]["avg_thr_bps"] > 12e6 and fs["video"]["summary"]["avg_thr_bps"] > 4.5e6
          and fs["kirish"]["summary"]["loss_pct"] == 100,
          "analytic: NAT orqali TCP ~15 + UDP 5 Mbit/s (20 Mbit/s WAN), tashqi ping 100% yo'qoladi")
    c.delete(f"/api/runs/nat_dhcp_lab/{rid}")

    if "ns3" not in want:
        print("  --   ns-3 o'rnatilmagan (pip install ns3) — ns-3 testi o'tkazib yuborildi")

with TestClient(create_app("demo", token="t0k")) as c:
    check(c.get("/api/info").status_code == 401, "token: tokensiz 401")
    check(c.get("/api/info?token=t0k").status_code == 200, "token: to'g'ri token 200")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

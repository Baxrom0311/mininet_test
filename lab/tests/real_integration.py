"""Real (Mininet) driver integratsiya testi — root + Mininet + OVS kerak.

    sudo python3 -m lab.tests.real_integration            # hammasi
    sudo python3 -m lab.tests.real_integration l2 ospf    # tanlanganlari

Har bir test topologiyani ko'taradi, namespace'larda buyruq bajaradi
(node.cmd), link/qurilma jonli amallarini tekshiradi va to'xtatadi.
FRR / os-ken / BMv2 bo'lmasa tegishli test o'tkazib yuboriladi.
"""

import copy
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lab.drivers.base import EventBus  # noqa: E402
from lab.drivers.mininet_driver import MininetDriver  # noqa: E402

EX = os.path.join(ROOT, "lab", "examples")
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)
    return cond


def load(name):
    with open(os.path.join(EX, name + ".json")) as f:
        return json.load(f)


def link(a, pa, b, pb, **p):
    return {"a": {"dev": a, "port": pa}, "b": {"dev": b, "port": pb},
            "params": {"bw": 50, "delay": "1ms", "jitter": "0ms", "loss": 0, "queue": 1000,
                       "medium": "wire", **p}, "up": True}


def host(name, i, ip, gw=""):
    return {"id": i, "type": "host", "x": 0, "y": 0,
            "config": {"interfaces": {f"{name}-eth1": {"ip": ip}}, "gateway": gw}}


def ping(drv, src, dst_ip, count=2, wait=2):
    out = drv.net.get(src).cmd(f"ping -c {count} -W {wait} -i 0.3 {dst_ip}")
    m = re.search(r"(\d+)% packet loss", out)
    return m is not None and int(m.group(1)) < 100, out


def wait_ping(drv, src, dst_ip, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ok, _ = ping(drv, src, dst_ip, count=1, wait=1)
        if ok:
            return time.time() - t0
        time.sleep(1)
    return None


# ───────────────────────────── testlar ─────────────────────────────
def t_l2(drv, caps):
    proj = {"version": 1, "name": "l2", "devices": {
        "h1": host("h1", 1, "10.0.0.1/24"), "h2": host("h2", 2, "10.0.0.2/24"),
        "h3": host("h3", 3, "10.0.0.3/24"),
        "s1": {"id": 4, "type": "switch", "x": 0, "y": 0, "config": {"mode": "standalone", "stp": False}}},
        "links": {"l1": link("h1", 1, "s1", 1), "l2": link("s1", 2, "h2", 1)},
        "p4_programs": {}, "controllers": {}, "scenarios": {}}
    drv.start(proj)
    check(ping(drv, "h1", "10.0.0.2")[0], "h1 -> h2 ping (OVS standalone)")
    ins = drv.inspect("s1")
    titles = [s["title"] for s in ins["sections"]]
    check(any("FDB" in t for t in titles), f"inspect s1 bo'limlari: {titles[:4]}")
    drv.update_link("l1", up=False)
    check(not ping(drv, "h1", "10.0.0.2", count=1, wait=1)[0], "link down -> ping yo'q")
    drv.update_link("l1", up=True)
    check(wait_ping(drv, "h1", "10.0.0.2", 10) is not None, "link up -> ping qaytdi")
    # tezlik (tc htb) — bw=5 Mbit
    drv.update_link("l1", params={"bw": 5})
    q = drv.net.get("h1").cmd("tc class show dev h1-eth1")
    check("5Mbit" in q, f"bw=5 tc class: {q.strip()[:80]}")
    # jonli qurilma + kabel qo'shish
    p2 = copy.deepcopy(drv.project)
    p2["links"]["l3"] = link("s1", 3, "h3", 1)
    drv.add_device(p2, "h3")
    drv.add_link(p2, "l3")
    check(wait_ping(drv, "h3", "10.0.0.1", 10) is not None, "jonli h3 + l3 -> ping h1")
    drv.remove_link("l3")
    check(not ping(drv, "h3", "10.0.0.1", count=1, wait=1)[0], "l3 o'chirildi -> ping yo'q")
    drv.remove_device("h3")
    check("h3" not in drv.project["devices"], "h3 o'chirildi")


def t_ospf(drv, caps):
    if not caps["frr"]:
        print("  skip (FRR yo'q)")
        return
    proj = load("ospf_lab")
    drv.start(proj)
    hosts = [n for n, d in proj["devices"].items() if d["type"] in ("host", "server")]
    ips = {n: next(iter(d["config"]["interfaces"].values()))["ip"].split("/")[0]
           for n, d in proj["devices"].items() if n in hosts}
    a, b = hosts[0], hosts[-1]
    dt = wait_ping(drv, a, ips[b], 90)
    check(dt is not None, f"OSPF: {a} -> {b} ping (konvergensiya {dt and round(dt, 1)} s)")
    routers = [n for n, d in proj["devices"].items() if d["type"] == "router"]
    r = routers[0]
    nb = drv.net.get(r).cmd(f"vtysh -N {r} -c 'show ip ospf neighbor'")
    check("Full" in nb, f"{r} OSPF qo'shnilari Full: {nb.count('Full')}")
    ins = drv.inspect(r)
    check(any("route" in s["title"].lower() or "marshrut" in s["title"].lower() for s in ins["sections"]),
          f"inspect {r}: {[s['title'] for s in ins['sections']][:5]}")
    cfg = drv.frr_running_config(r)
    check("router ospf" in (cfg if isinstance(cfg, str) else json.dumps(cfg)), "running-config ichida 'router ospf'")
    # uzilish: yo'ldagi birinchi router-router kabelini uzamiz -> muqobil yo'l
    rr = [lid for lid, l in proj["links"].items()
          if proj["devices"][l["a"]["dev"]]["type"] == "router" and proj["devices"][l["b"]["dev"]]["type"] == "router"]
    if len(rr) >= 3:
        drv.update_link(rr[0], up=False)
        dt = wait_ping(drv, a, ips[b], 90)
        check(dt is not None, f"{rr[0]} uzildi -> muqobil yo'l ({dt and round(dt, 1)} s)")
        drv.update_link(rr[0], up=True)


def t_sdn(drv, caps):
    if not caps["sdn"]:
        print("  skip (os-ken yo'q)")
        return
    proj = load("sdn_router_lab")
    drv.start(proj)
    hosts = [n for n, d in proj["devices"].items() if d["type"] in ("host", "server")]
    ips = {n: next(iter(d["config"]["interfaces"].values()))["ip"].split("/")[0] for n in hosts
           for d in [proj["devices"][n]]}
    a, b = hosts[0], hosts[-1]
    dt = wait_ping(drv, a, ips[b], 40)
    check(dt is not None, f"SDN: {a} -> {b} ping ({dt and round(dt, 1)} s)")
    for c in proj.get("controllers", {}):
        log = drv.controller_log(c)
        print("    ctrl log:", (log if isinstance(log, str) else json.dumps(log))[-200:].replace("\n", " | "))


def t_p4(drv, caps):
    if not caps["p4"]:
        print("  skip (BMv2 yo'q)")
        return
    proj = load("p4_l2_lab")
    drv.start(proj)
    hosts = [n for n, d in proj["devices"].items() if d["type"] in ("host", "server")]
    ips = {n: next(iter(proj["devices"][n]["config"]["interfaces"].values()))["ip"].split("/")[0] for n in hosts}
    check(wait_ping(drv, hosts[0], ips[hosts[-1]], 20) is not None, "P4: ping")


def t_rip(drv, caps):
    if not caps["frr"]:
        print("  skip (FRR yo'q)")
        return
    proj = load("rip_ring")
    drv.start(proj)
    ip = next(iter(proj["devices"]["srv1"]["config"]["interfaces"].values()))["ip"].split("/")[0]
    dt = wait_ping(drv, "pc1", ip, 90)
    check(dt is not None, f"RIP: pc1 -> srv1 ({dt and round(dt, 1)} s)")
    drv.update_link("l2", up=False)
    dt = wait_ping(drv, "pc1", ip, 240)
    check(dt is not None, f"RIP: l2 uzildi -> r3 orqali ({dt and round(dt, 1)} s)")


def t_emu(drv, caps):
    """Mininet emulyatsiya dvigateli: iperf3 UDP/TCP + ping, kabel uzilishi."""
    import shutil
    from lab.engines import emulation
    if not shutil.which("iperf3"):
        print("  skip (iperf3 yo'q)")
        return
    proj = {"version": 1, "name": "emu", "devices": {
        "h1": host("h1", 1, "10.0.0.1/24"), "h2": host("h2", 2, "10.0.0.2/24"),
        "s1": {"id": 3, "type": "switch", "x": 0, "y": 0, "config": {"mode": "standalone", "stp": False}}},
        "links": {"l1": link("h1", 1, "s1", 1, bw=10), "l2": link("s1", 2, "h2", 1, bw=10)},
        "p4_programs": {}, "controllers": {}, "scenarios": {}}
    sc = {"duration": 12, "apps": [
        {"id": "udp", "type": "udp", "src": "h1", "dst": "h2", "rate": "4Mbps", "size": 1000, "start": 1, "stop": 11},
        {"id": "tcp", "type": "tcp", "src": "h1", "dst": "h2", "start": 1, "stop": 11},
        {"id": "png", "type": "ping", "src": "h1", "dst": "h2", "interval": 0.2, "start": 1, "stop": 11}],
        "events": [{"t": 8, "action": "link_down", "link": "l2"}]}
    drv.start(proj)
    time.sleep(1)
    res = emulation.run(drv, sc, log=lambda m: print("    log:", m))
    f = res["flows"]
    u = [p["thr_bps"] for p in f["udp"]["series"] if 3 <= p["t"] <= 7]
    t = [p["thr_bps"] for p in f["tcp"]["series"] if 3 <= p["t"] <= 7]
    print("    udp", [round(x / 1e6, 2) for x in u], "tcp", [round(x / 1e6, 2) for x in t])
    check(u and 3e6 < sum(u) / len(u) < 4.6e6, "UDP ~4 Mbit/s")
    check(t and sum(t) / len(t) < 8e6 and sum(t) / len(t) > 2e6, "TCP qolgan ~6 Mbit/s (bw=10 cheklovi)")
    late = [p["loss_pct"] for p in f["png"]["series"] if p["t"] >= 10]
    check(late and min(late) > 50, f"t=8 uzilishdan keyin ping yo'qoladi {late}")
    check(res["events"], f"hodisa qo'llandi: {res['events']}")


def t_queue(drv, caps):
    """Navbat (pfifo/netem limit) va tashlanishlar; update_link'dan keyin TCP (offload) ishlaydi."""
    import shutil
    from lab.engines import emulation
    if not shutil.which("iperf3"):
        print("  skip (iperf3 yo'q)")
        return
    proj = {"version": 1, "name": "q", "devices": {
        "h1": host("h1", 1, "10.0.0.1/24"), "h2": host("h2", 2, "10.0.0.2/24"),
        "s1": {"id": 3, "type": "switch", "x": 0, "y": 0, "config": {"mode": "standalone", "stp": False}}},
        "links": {"l1": link("h1", 1, "s1", 1, bw=50), "l2": link("s1", 2, "h2", 1, bw=10, queue=100)},
        "p4_programs": {}, "controllers": {}, "scenarios": {}}
    drv.start(proj)
    time.sleep(1)
    sc = {"duration": 6, "apps": [{"id": "udp", "type": "udp", "src": "h1", "dst": "h2", "rate": "20Mbps",
                                   "size": 1000, "start": 1, "stop": 5}], "events": []}
    res = emulation.run(drv, sc)
    pts = [p for p in res["links"]["l2"] if 2 <= p["t"] <= 5]
    qmax = max((p["queue_ab"] for p in pts), default=0)
    drops = sum(p["drops_ab"] for p in pts)
    print("    l2 queue_ab", [p["queue_ab"] for p in pts], "drops_ab", [p["drops_ab"] for p in pts])
    check(50 <= qmax <= 110, f"navbat ~queue=100 paket (max {qmax})")
    # 20 Mbit/s -> 10 Mbit/s: ~1250 paket/s ortiqcha, 4 soniya
    check(3500 < drops < 6500, f"ortiqcha 10 Mbit/s tashlanadi ({drops} paket, kutilgan ~5000)")
    drv.update_link("l2", params={**proj["links"]["l2"]["params"], "bw": 8})
    out = drv.net.get("h2").cmd("iperf3 -s -1 -D")
    time.sleep(0.5)
    out = drv.net.get("h1").cmd("timeout 10 iperf3 -c 10.0.0.2 -t 2 -f m")
    m = re.findall(r"([\d.]+) Mbits/sec\s+\d*\s*receiver", out)
    check(bool(m) and 5 < float(m[-1]) < 9, f"update_link(bw=8) dan keyin TCP ishlaydi: {m}")


def t_conv(drv, caps):
    """Qurilma statistikasi + routing konvergensiyasi (FRR RIP halqa, Mininet dvigateli)."""
    import shutil
    from lab.engines import emulation
    if not caps["frr"] or not shutil.which("iperf3"):
        print("  skip (FRR/iperf3 yo'q)")
        return
    proj = load("rip_ring")
    for n in ("r1", "r2", "r3"):     # tez RIP: update 10 s (config.rip_timers -> vtysh `timers basic`)
        proj["devices"][n]["config"]["rip_timers"] = {"update": 10, "timeout": 60, "garbage": 40}
    drv.start(proj)
    tb = drv.frr_running_config("r1")
    check("timers basic 10 60 40" in (tb if isinstance(tb, str) else json.dumps(tb)), "r1: timers basic 10 60 40 qo'llandi")
    ip = next(iter(proj["devices"]["srv1"]["config"]["interfaces"].values()))["ip"].split("/")[0]
    check(wait_ping(drv, "pc1", ip, 90) is not None, "RIP yo'llari tayyor")
    # muqobil yo'l keyingi periodik update'da (update=10 -> 10 s gacha)
    sc = {"duration": 30, "apps": [
        {"id": "v", "type": "udp", "src": "pc1", "dst": "srv1", "rate": "2Mbps", "size": 1000, "start": 1, "stop": 29},
        {"id": "p", "type": "ping", "src": "pc1", "dst": "srv1", "interval": 0.2, "start": 1, "stop": 29}],
        "events": [{"t": 8, "action": "link_down", "link": "l2"}]}
    res = emulation.run(drv, sc)
    r1 = [x for x in res["nodes"]["r1"] if 3 <= x["t"] <= 7]
    print("    r1", r1[:2])
    check(r1 and min(x["fwd_pps"] for x in r1) > 200, "r1 forwarding ~250 pkt/s (2 Mbit/s video + ping)")
    print("    route_events", [(e["t"], e["dev"], len(e["added"]), len(e["removed"])) for e in res["route_events"]])
    cv = res["convergence"]
    print("    convergence", cv)
    check(cv and cv[0]["converged_s"] is not None and 0 <= cv[0]["converged_s"] <= 14,
          f"l2 uzilishidan keyin RIP konvergensiyasi o'lchandi: {cv and cv[0]['converged_s']} s")
    after = [x["thr_bps"] for x in res["flows"]["v"]["series"] if 24 <= x["t"] <= 28]
    check(after and min(after) > 1.5e6, "konvergensiyadan keyin video r3 orqali tiklandi")


def t_vlan(drv, caps):
    """802.1Q: access/trunk (OVS tag/trunks), router-on-a-stick subinterfeyslar, izolyatsiya."""
    proj = load("vlan_lab")
    if not caps.get("vlan"):
        # kernelda 8021q yo'q: router-on-a-stick o'rniga r1 ikki access portga (VLAN10/VLAN20)
        print("    (8021q yo'q — r1 subinterfeyslari o'rniga s1 port4=VLAN10, port5=VLAN20)")
        r1 = proj["devices"]["r1"]["config"]
        r1["interfaces"] = {"r1-eth1": {"ip": "10.0.10.1/24"}, "r1-eth2": {"ip": "10.0.20.1/24"}}
        proj["devices"]["s1"]["config"]["ports"]["4"] = {"mode": "access", "vlan": 10}
        proj["devices"]["s1"]["config"]["ports"]["5"] = {"mode": "access", "vlan": 20}
        proj["links"]["l8"] = link("s1", 5, "r1", 2, bw=20)
    drv.start(proj)
    check(wait_ping(drv, "pc1", "10.0.10.13", 15) is not None, "VLAN10: pc1 -> pc3 (s1-s2 trunk orqali)")
    check(wait_ping(drv, "pc1", "10.0.20.14", 15) is not None, "inter-VLAN: pc1 -> pc4 (r1-eth1.10 -> r1-eth1.20)")
    check(not ping(drv, "pc1", "10.0.10.15", count=2, wait=1)[0], "izolyatsiya: pc5 (VLAN20 portida) javob bermaydi")
    tr = drv.net.get("pc1").cmd("traceroute -n -w 1 -q 1 10.0.20.14")
    check("10.0.10.1" in tr, f"traceroute r1 orqali: {' | '.join(tr.strip().splitlines()[1:3])}")
    show = drv.net.get("s1").cmd("ovs-vsctl --columns=name,tag,trunks,vlan_mode list port s1-eth1 s1-eth3")
    check("10" in show and "trunk" in show, "OVS port tag/trunks o'rnatilgan")
    # jonli: pc5 portini VLAN10 ga o'tkazamiz -> endi yetadi
    p2 = copy.deepcopy(drv.project)
    p2["devices"]["s2"]["config"]["ports"]["4"] = {"mode": "access", "vlan": 10}
    drv.apply_device_config(p2, "s2")
    check(wait_ping(drv, "pc1", "10.0.10.15", 10) is not None, "jonli: s2 port4 -> VLAN10, pc5 endi yetadi")


def t_natdhcp(drv, caps):
    """DHCP (dnsmasq rezervatsiya = model manzillari) + NAT masquerade + emulyatsiya."""
    import shutil
    from lab.engines import emulation
    from lab.services import resolve_dhcp
    proj = load("nat_dhcp_lab")
    _rp, leases = resolve_dhcp(proj)
    drv.start(proj)
    for ls in leases:
        got = drv.net.get(ls["client"]).cmd(f"ip -4 -o addr show dev {ls['iface']}")
        check(f"inet {ls['ip']}/" in got, f"DHCP: {ls['client']} = {ls['ip']} (model bilan bir xil)")
    rt = drv.net.get("pc1").cmd("ip route show default")
    check("10.0.1.1" in rt, f"DHCP: default gateway {rt.strip()}")
    ins = drv.inspect("r1")
    tbl = next((s_ for s_ in ins["sections"] if s_["title"].startswith("DHCP")), None)
    check(tbl and len(tbl.get("rows") or []) == len(leases), f"inspect r1: DHCP lizinglari {tbl and tbl.get('rows')}")
    check(wait_ping(drv, "pc1", "198.51.100.10", 10) is not None, "NAT: pc1 -> srv1 (isp ichki tarmoqni bilmaydi)")
    seen = drv.net.get("srv1").cmd("timeout 3 tcpdump -nni srv1-eth1 -c 2 icmp 2>/dev/null & "
                                   "sleep 0.5; true")
    drv.net.get("pc1").cmd("ping -c 2 -W 1 198.51.100.10 >/dev/null")
    time.sleep(2.6)
    out = drv.net.get("srv1").cmd("wait; true")
    print("    srv1 tcpdump:", (seen + out).strip().splitlines()[-1:] )
    nat = drv.net.get("r1").cmd("iptables -t nat -L POSTROUTING -n -v")
    check("MASQUERADE" in nat, "iptables MASQUERADE o'rnatilgan")
    check(not ping(drv, "srv1", leases[0]["ip"], count=2, wait=1)[0], "tashqaridan (srv1 -> pc1) o'tmaydi")
    # NAT'siz: javob qaytmaydi
    p2 = copy.deepcopy(drv.project)
    p2["devices"]["r1"]["config"].pop("nat")
    drv.apply_device_config(p2, "r1")
    check(not ping(drv, "pc1", "198.51.100.10", count=2, wait=1)[0], "NAT o'chirilsa javob qaytmaydi")
    p2["devices"]["r1"]["config"]["nat"] = {"outside": "r1-eth2"}
    drv.apply_device_config(p2, "r1")
    check(wait_ping(drv, "pc1", "198.51.100.10", 10) is not None, "NAT qayta yoqildi -> ishlaydi")
    if shutil.which("iperf3"):
        sc = copy.deepcopy(proj["scenarios"]["internet"])
        sc["duration"] = 10
        for a in sc["apps"]:
            a["stop"] = min(a["stop"], 9)
        res = emulation.run(drv, sc)
        f = res["flows"]
        web = [x["thr_bps"] for x in f["web"]["series"] if 3 <= x["t"] <= 8]
        print("    web Mbit/s", [round(x / 1e6, 1) for x in web], "video", f["video"]["summary"]["avg_thr_bps"])
        check(web and sum(web) / len(web) > 8e6, "emulyatsiya: TCP pc1 -> srv1 NAT orqali (~15 Mbit/s)")
        check((f["kirish"]["summary"].get("loss_pct") or 0) > 90, "emulyatsiya: srv1 -> pc1 ping o'tmaydi")


def t_apps(drv, caps):
    """Ilova modellari (VoIP MOS, HTTP sahifa vaqti, FTP fayl vaqti) — Mininet dvigateli."""
    import shutil
    from lab import apps as am
    from lab.engines import emulation
    if not caps["frr"] or not shutil.which("iperf3"):
        print("  skip (FRR/iperf3 yo'q)")
        return
    proj = load("ospf_lab")
    drv.start(proj)
    check(wait_ping(drv, "pc1", "10.0.2.11", 60) is not None, "OSPF tayyor")
    sc = {"duration": 20, "apps": [
        {"id": "tel", "type": "voip", "src": "pc1", "dst": "srv2", "codec": "g711", "start": 1, "stop": 19},
        {"id": "web", "type": "http", "src": "pc2", "dst": "srv1", "page": "500KB", "think": 3, "start": 1, "stop": 19},
        {"id": "fayl", "type": "ftp", "src": "pc1", "dst": "srv1", "file": "5MB", "start": 3, "stop": 19}], "events": []}
    res = am.postprocess(emulation.run(drv, am.expand(sc)), sc)
    ap = res["apps"]
    print("   ", json.dumps(ap, ensure_ascii=False))
    check(ap["tel"]["avg_mos"] and ap["tel"]["avg_mos"] > 3.0, f"VoIP MOS {ap['tel']['avg_mos']} ({ap['tel']['quality']})")
    check(ap["web"]["pages_done"] >= ap["web"]["pages"] - 1 and (ap["web"]["avg_page_s"] or 9) < 3,
          f"HTTP: {ap['web']['pages_done']}/{ap['web']['pages']} sahifa, o'rtacha {ap['web']['avg_page_s']} s")
    check(ap["fayl"]["completed"] and 3 < ap["fayl"]["transfer_s"] < 12,
          f"FTP 5 MB 10 Mbit/s kabelda {ap['fayl']['transfer_s']} s")


def t_qos(drv, caps):
    """QoS: fifo vs prio (HTB sinflari + DSCP u32 filtr) — VoIP MOS TCP yuklamasi ostida."""
    import shutil
    from lab import apps as am
    from lab.engines import emulation
    if not shutil.which("iperf3"):
        print("  skip (iperf3 yo'q)")
        return
    out = {}
    for qd in ("fifo", "prio"):
        proj = {"version": 1, "name": "qos", "devices": {
            "h1": host("h1", 1, "10.0.0.1/24"), "h2": host("h2", 2, "10.0.0.2/24"),
            "s1": {"id": 3, "type": "switch", "x": 0, "y": 0, "config": {"mode": "standalone", "stp": False}}},
            "links": {"l1": link("h1", 1, "s1", 1, bw=100), "l2": link("s1", 2, "h2", 1, bw=10, queue=500, qdisc=qd)},
            "p4_programs": {}, "controllers": {}, "scenarios": {}}
        drv.start(proj)
        time.sleep(1)
        if qd == "prio":
            cl = drv.net.get("s1").cmd("tc class show dev s1-eth2; tc filter show dev s1-eth2 | grep -c flowid")
            check("5:10" in cl and "5:20" in cl, "prio: HTB sinflari 5:10 / 5:20 va DSCP filtrlari")
        sc = {"duration": 14, "events": [], "apps": [
            {"id": "tel", "type": "voip", "src": "h2", "dst": "h1", "codec": "g711", "start": 1, "stop": 13},
            {"id": "big", "type": "udp", "src": "h1", "dst": "h2", "rate": "14Mbps", "size": 1000, "start": 3, "stop": 13}]}
        res = am.postprocess(emulation.run(drv, am.expand(sc)), sc)
        t = res["apps"]["tel"]
        late = [p for p in res["flows"]["tel"]["series"] if 5 <= p["t"] <= 12]
        out[qd] = (sum(p["mos"] for p in late) / len(late) if late else 0, t)
        print(f"    {qd}: MOS (yuklama ostida) {out[qd][0]:.2f}, delay {t['avg_delay_ms']} ms, loss {t['loss_pct']}%")
        drv.stop()
    check(out["fifo"][0] < 3.0, f"fifo: 14 Mbit/s UDP 10 Mbit/s kabelda VoIP yomon (MOS {out['fifo'][0]:.2f})")
    check(out["prio"][0] > 4.0, f"prio: EF ustuvor — VoIP a'lo (MOS {out['prio'][0]:.2f})")


TESTS = {"l2": t_l2, "ospf": t_ospf, "rip": t_rip, "emu": t_emu, "queue": t_queue, "conv": t_conv, "vlan": t_vlan, "natdhcp": t_natdhcp, "apps": t_apps, "qos": t_qos, "sdn": t_sdn, "p4": t_p4}


def _register_feature_tests():
    """Alohida fayllardagi real testlar (BGP, STP, Firewall). Ular shu moduldan
    yordamchilarni import qiladi — `-m` bilan ishga tushganda bu modul `__main__`,
    import qilingani esa alohida nusxa; xatolar ikkalasidan ham yig'iladi."""
    from lab.tests import real_bgp, real_failures, real_fw, real_lag, real_mcast, real_stp, real_topogen, real_vrrp
    TESTS.update({"bgp": real_bgp.t_bgp, "stp": real_stp.t_stp, "fw": real_fw.t_fw, "fw_emu": real_fw.t_fw_emu,
                  "mcast": real_mcast.t_mcast, "vrrp": real_vrrp.t_vrrp, "lag": real_lag.t_lag, "failures": real_failures.t_failures,
                  "topogen": real_topogen.t_topogen})
    return sys.modules.get("lab.tests.real_integration")


def main(argv):
    if os.geteuid() != 0:
        print("root kerak: sudo python3 -m lab.tests.real_integration")
        return 2
    other = _register_feature_tests()
    drv = MininetDriver(EventBus())
    caps = drv.capabilities()
    print("caps:", caps)
    for name in (argv or list(TESTS)):
        print(f"== {name}", flush=True)
        t0 = time.time()
        try:
            TESTS[name](drv, caps)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(False, f"{name}: istisno {e}")
        finally:
            try:
                drv.stop()
            except Exception as e:  # noqa: BLE001
                check(False, f"{name}: stop {e}")
        print(f"   ({time.time() - t0:.1f} s)")
    fails = FAILS + (other.FAILS if other is not None and other.FAILS is not FAILS else [])
    print("\nNATIJA:", "OK" if not fails else f"{len(fails)} xato: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

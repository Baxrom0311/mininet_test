"""Mininet emulyatsiya dvigateli — ssenariyni ISHLAYOTGAN real tarmoqda bajaradi.

Haqiqiy Linux TCP/UDP steki, haqiqiy FRR/OVS/P4/SDN: o'lchovlar real vaqtda.
  * udp  : iperf3 -u (server tomonida har soniyalik qabul/jitter/loss) + yo'l RTT uchun ping
  * tcp  : iperf3 (klient tomonida har soniyalik throughput, retransmit) + ping
  * ping : ping -D -i <interval>
  * hodisalar: driver.update_link() (tc / link down-up) aniq vaqtda
  * kabellar: /proc/<pid>/net/dev orqali har soniyalik yuklama
Jarayonlar `mnexec -a <pid>` bilan (node.cmd emas) — Mininet shell'ini bloklamaydi.
"""

import re
import subprocess
import threading
import time
import os
import sys

MCAST_TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcast_tool.py")

from lab.model import ifname

_IPERF_LINE = re.compile(r"\[\s*\d+\]\s+([\d.]+)-([\d.]+)\s+sec\s+[\d.]+\s+\w?Bytes\s+([\d.]+)\s+([KMG]?)bits/sec(.*)$")
_UDP_TAIL = re.compile(r"([\d.]+)\s+ms\s+(\d+)/(\d+)")
_PING = re.compile(r"^\[(\d+\.\d+)\].*icmp_seq=(\d+).*time=([\d.]+)\s*ms")


def _mult(u):
    return {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}[u]


_OVS_TYPES = ("switch", "sdnrouter", "wifiap")


def _fix_userspace_drops(project, link_series, sw_pk, sw_prev):
    """Userspace (netdev) OVS port socket'i ENOBUFS'da paketni qayta yuboradi — har
    urinish qdisc drops'ga qo'shiladi (~1.5x ortiqcha). Switch uchun haqiqiy yo'qotish
    = kirgan - chiqqan paketlar (shu soniyada); qdisc drops shu songa proporsional
    kichraytiriladi. Faqat switch tomonidagi (egress) yo'nalishlar tuzatiladi."""
    per_sw = {}     # switch -> [(lid, dir)], rx, tx
    for lid, (rxa, txa, rxb, txb) in sw_pk.items():
        l = project["links"][lid]
        p0 = sw_prev.get(lid, (rxa, txa, rxb, txb))
        sw_prev[lid] = (rxa, txa, rxb, txb)
        for side, d, rx, tx, rx0, tx0 in (("a", "ab", rxa, txa, p0[0], p0[1]), ("b", "ba", rxb, txb, p0[2], p0[3])):
            dev = l[side]["dev"]
            if project["devices"][dev]["type"] not in _OVS_TYPES:
                continue
            e = per_sw.setdefault(dev, {"dirs": [], "rx": 0, "tx": 0})
            e["dirs"].append((lid, d))
            e["rx"] += rx - rx0
            e["tx"] += tx - tx0
    for e in per_sw.values():
        qd = sum(link_series[lid][-1][f"drops_{d}"] for lid, d in e["dirs"] if link_series[lid])
        true = max(e["rx"] - e["tx"], 0)
        if qd <= 0:
            continue
        f = min(true / qd, 1.0)
        for lid, d in e["dirs"]:
            pt = link_series[lid][-1]
            pt[f"drops_{d}"] = round(pt[f"drops_{d}"] * f)


def run(driver, sc, progress=lambda f: None, log=lambda m: None):
    procs, stop_ev = [], threading.Event()
    try:
        return _run(driver, sc, progress, log, procs, stop_ev)
    finally:
        stop_ev.set()
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()


def _run(driver, sc, progress, log, procs, stop_ev):
    from lab.drivers.mininet_driver import QdiscSampler, _read_net_dev   # lazy
    if not driver.running or driver.name != "real":
        raise RuntimeError("Mininet dvigateli uchun real driverda tarmoq ishlab turishi kerak")
    with driver.lock:
        project = driver.project
        pid = {n: driver.net.get(n).pid for n in project["devices"]}
    duration = float(sc.get("duration", 30))
    t_wall = time.time()
    threads = []
    raw = {}           # app_id -> {"thr": {sec: bps}, "rtt": {sec: [ms]}, "loss": {sec: pct}, "jit": {sec: ms}}
    T0 = time.time() + 2.0    # serverlar ishga tushishi uchun 2s

    from lab.services import resolve_dhcp
    rproj = resolve_dhcp(project)[0]     # DHCP lizinglari (dnsmasq rezervatsiyalari bilan bir xil)

    def ip_of(dev):
        for ifn, ic in sorted(((rproj["devices"][dev].get("config") or {}).get("interfaces") or {}).items()):
            if ic.get("ip"):
                return ic["ip"].split("/")[0]
        return None

    def spawn(dev, argv):
        p = subprocess.Popen(["mnexec", "-a", str(pid[dev])] + argv, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        procs.append(p)
        return p

    def reader(p, fn, on_eof=None):
        def go():
            for line in p.stdout:
                try:
                    fn(line.rstrip())
                except Exception:  # noqa: BLE001
                    pass
            if on_eof:
                try:
                    p.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
                on_eof()
        t = threading.Thread(target=go, daemon=True); t.start(); threads.append(t)

    def at(t_rel, fn):
        def go():
            if not stop_ev.wait(max(T0 + t_rel - time.time(), 0)):
                fn()
        t = threading.Thread(target=go, daemon=True); t.start(); threads.append(t)

    apps = []
    port_users, shared_ports, port_count = {}, {}, {}
    mc_groups = {}          # multicast ilova id -> UDP port
    for a in sc.get("apps") or []:
        if a.get("dport") not in (None, "") and a.get("type") == "tcp":
            k_ = (a["dst"], int(a["dport"]))
            port_count[k_] = port_count.get(k_, 0) + 1
    for i, a in enumerate(sc.get("apps") or []):
        aid = a.get("id") or f"a{i + 1}"
        dip = a.get("dst_ip") or ip_of(a["dst"])
        start, stop = float(a.get("start", 1)), min(float(a.get("stop", duration)), duration)
        rec = {"thr": {}, "rtt": {}, "loss": {}, "jit": {}, "retr": {}}
        raw[aid] = rec
        apps.append({"id": aid, "type": a["type"], "src": a["src"], "dst": a["dst"], "start": start, "stop": stop,
                     "label": a.get("label") or f"{a['type']} {a['src']}→{a['dst']}",
                     **({"bytes": int(a["bytes"])} if a.get("bytes") else {}),
                     **({"group": a["group"], "role": a.get("role")} if a.get("group") else {})})
        if not dip:
            log(f"{aid}: {a['dst']} da IP yo'q — o'tkazib yuborildi")
            continue
        port = 5201 + i
        if a.get("dport") not in (None, "") and a["type"] != "ping":
            key = (a["dst"], int(a["dport"]))
            if a["type"] == "udp" and key in port_users:
                log(f"{aid}: {a['dst']}:{a['dport']} band (UDP) — {port} port ishlatiladi")
            else:
                port = int(a["dport"])         # firewall qoidalari haqiqiy port bo'yicha ishlashi uchun
                port_users.setdefault(key, []).append(aid)
        dur = max(int(round(stop - start)), 1)
        if a.get("mcast"):
            # multicast: guruh uchun BITTA yuboruvchi; qabul qiluvchi guruhga qo'shiladi (IGMP -> pimd)
            g = a["mcast"]
            if g not in mc_groups:
                mc_groups[g] = 5600 + len(mc_groups)
                from lab.model import parse_rate
                send_argv = [sys.executable, MCAST_TOOL, "send", a["mgroup"], str(mc_groups[g]),
                             str(parse_rate(a.get("rate", "1Mbps"))), str(int(a.get("size", 1000))), str(dur), "16"]
                at(start, lambda d_=a["src"], argv=send_argv: reader(spawn(d_, argv), lambda _l: None))

            def mc_parser(line, rec=rec):
                v = line.split()
                if len(v) == 7 and v[0] == "STAT":
                    sec = int(float(v[1]) - T0) + 1
                    b_, n_, lost_ = int(v[2]), int(v[3]), int(v[4])
                    rec["thr"][sec] = b_ * 8
                    rec["loss"][sec] = lost_ / (n_ + lost_) * 100 if n_ + lost_ else None
                    if float(v[5]) >= 0:
                        rec["rtt"].setdefault(sec, []).append(2 * float(v[5]))    # seriyada RTT/2 = bir tomonlama
                    if float(v[6]) >= 0:
                        rec["jit"][sec] = float(v[6])
            recv_argv = [sys.executable, MCAST_TOOL, "recv", a["mgroup"], str(mc_groups[g]), str(dur + 2.5)]
            at(max(start - 1.5, 0), lambda d_=a["dst"], argv=recv_argv, f=mc_parser: reader(spawn(d_, argv), f))
            continue

        def ping_parser(rec=rec):
            def f(line):
                m = _PING.match(line)
                if m:
                    sec = int(float(m.group(1)) - T0) + 1
                    rec["rtt"].setdefault(sec, []).append(float(m.group(3)))
            return f

        interval = float(a.get("interval", 0.2 if a["type"] == "ping" else 0.5))
        tos = (int(a["dscp"]) << 2) if a.get("dscp") not in (None, "") else 0
        ping_argv = ["ping", "-D", "-n"] + (["-Q", str(tos)] if tos else []) + ["-i", str(max(interval, 0.2)), "-w", str(dur), "-s",
                     str(max(int(a.get("size", 56)) - 8, 8) if a["type"] == "ping" else 56), dip]
        if not (a["type"] == "tcp" and a.get("bytes")):     # qisqa uzatishlarga parallel ping shart emas
            at(start, lambda a=a, argv=ping_argv, rec=rec: reader(spawn(a["src"], argv), ping_parser(rec)))
        if a["type"] == "ping":
            rec["expected_per_s"] = 1.0 / max(interval, 0.2)
            continue
        shared = shared_ports.get((a["dst"], port))
        if shared is None:
            # bir (dst, port) da bir nechta TCP ilova (masalan HTTP sahifalari, port 80) — bitta doimiy server
            multi = a["type"] == "tcp" and port_count.get((a["dst"], port), 0) > 1
            srv = spawn(a["dst"], ["iperf3", "-s"] + ([] if multi else ["-1"]) + ["-p", str(port), "-i", "1", "--forceflush"])
            shared_ports[(a["dst"], port)] = srv
            if a["type"] == "tcp":
                reader(srv, lambda _l: None)     # server chiqishi ham o'qilsin (pipe to'lmasin)
        else:
            srv = shared
        nbytes = int(a.get("bytes") or 0) if a["type"] == "tcp" else 0
        cli = ["iperf3", "-c", dip, "-p", str(port), "-i", "1", "--forceflush"] + (["-S", str(tos)] if tos else []) + (
            ["-n", str(nbytes)] if nbytes else ["-t", str(dur)])
        if nbytes:
            rec["bytes"] = nbytes
        if a["type"] == "udp":
            cli += ["-u", "-b", str(a.get("rate", "1M")).replace("bps", ""), "-l", str(int(a.get("size", 1000)))]

            def srv_parser(line, rec=rec, start=start):
                m = _IPERF_LINE.search(line)
                if m and float(m.group(2)) - float(m.group(1)) <= 1.5:
                    sec = int(start + float(m.group(2)) + 0.5)
                    rec["thr"][sec] = float(m.group(3)) * _mult(m.group(4))
                    u = _UDP_TAIL.search(m.group(5))
                    if u:
                        rec["jit"][sec] = float(u.group(1))
                        lost, tot = int(u.group(2)), int(u.group(3))
                        rec["loss"][sec] = lost / tot * 100 if tot else None
            reader(srv, srv_parser)
        else:
            if a.get("rate"):
                cli += ["-b", str(a["rate"]).replace("bps", "")]

            def cli_parser(line, rec=rec, start=start):
                m = _IPERF_LINE.search(line)
                if m and float(m.group(2)) - float(m.group(1)) <= 1.5 and "sender" not in line and "receiver" not in line:
                    sec = int(start + float(m.group(2)) + 0.5)
                    rec["thr"][sec] = float(m.group(3)) * _mult(m.group(4))
                    tail = m.group(5).split()
                    if tail and tail[0].isdigit():
                        rec["retr"][sec] = int(tail[0])
                if m and "receiver" in line:
                    rec["recv_ok"] = True

            def eof(rec=rec, t_start=start):
                if rec.get("bytes") and rec.get("recv_ok"):
                    rec["done_t"] = round(time.time() - T0, 3)
            at(start, lambda a=a, cli=cli, f=cli_parser, e=eof: reader(spawn(a["src"], cli), f, e))
            continue
        # klient chiqishi ham o'qilishi shart — aks holda pipe to'lib iperf3 bloklanadi
        at(start, lambda a=a, cli=cli: reader(spawn(a["src"], cli), lambda _l: None))

    applied = []
    for e in sorted(sc.get("events") or [], key=lambda e: float(e.get("t", 0))):
        if e.get("action") == "move":       # harakat — pastdagi har soniyalik siklda
            applied.append({**e})
            continue

        def fire(e=e):
            try:
                if e["action"] in ("link_down", "link_up"):
                    driver.update_link(e["link"], up=(e["action"] == "link_up"))
                else:
                    merged = {**(project["links"][e["link"]].get("params") or {}), **(e.get("params") or {})}
                    driver.update_link(e["link"], params=merged)
                applied.append({"t": round(time.time() - T0, 2), **e})
                log(f"t={time.time() - T0:.1f}s: {e['action']} {e['link']}")
            except Exception as ex:  # noqa: BLE001
                log(f"hodisa xatosi: {ex}")
        at(float(e.get("t", 0)), fire)

    # kabel yuklamasi
    link_series = {lid: [] for lid in project["links"]}
    prev = {}
    sw_prev = {}
    userspace = getattr(driver, "_datapath", "kernel") == "user"
    from lab.mobility import has_moves, positions_at
    movers = has_moves(sc)
    k = 0
    from lab.engines.nodestats import L3_NS, RouteTracker, SnmpSampler, convergence, read_proc_routes, read_proc_snmp
    l3 = [n for n, d in project["devices"].items() if d["type"] in L3_NS]
    node_series = {n: [] for n in l3}
    rtrack, snmp_s = RouteTracker(), SnmpSampler()
    for n in l3:            # boshlang'ich holat (t=0) — keyingi o'zgarishlar route_events'ga
        rtrack.update(0, n, read_proc_routes(pid[n]))
        snmp_s.point(0, n, read_proc_snmp(pid[n]), set())
    while k < int(duration):
        k += 1
        if stop_ev.wait(max(T0 + k - time.time(), 0)):
            break
        if movers:     # Wi-Fi stansiya harakati: TCLink tezligi radio modelidan qayta hisoblanadi
            try:
                driver.wifi_refresh(positions_at(project, sc, float(k)))
            except Exception as ex:  # noqa: BLE001
                log(f"harakat xatosi: {ex}")
        cache = {}
        sw_pk = {}
        qs = QdiscSampler()
        for lid, l in project["links"].items():
            pa, pb = pid[l["a"]["dev"]], pid[l["b"]["dev"]]
            for p_ in (pa, pb):
                if p_ not in cache:
                    cache[p_] = _read_net_dev(p_)
            ia = cache[pa].get(ifname(l["a"]["dev"], l["a"]["port"]))
            ib = cache[pb].get(ifname(l["b"]["dev"], l["b"]["port"]))
            if not ia or not ib:
                continue
            qa = qs.read(pa).get(ifname(l["a"]["dev"], l["a"]["port"]), (0, 0, 0, 0))
            qb = qs.read(pb).get(ifname(l["b"]["dev"], l["b"]["port"]), (0, 0, 0, 0))
            cur = (ia[3], ib[3], qa[2], qb[2])
            p0 = prev.get(lid, cur); prev[lid] = cur
            ab, ba = (cur[0] - p0[0]) * 8, (cur[1] - p0[1]) * 8
            bw = float((l.get("params") or {}).get("bw", 100)) * 1e6
            link_series[lid].append({"t": float(k), "ab_bps": ab, "ba_bps": ba, "util": round(min(max(ab, ba) / bw, 1), 4),
                                     "queue_ab": qa[0], "queue_ba": qb[0],
                                     "drops_ab": max(cur[2] - p0[2], 0), "drops_ba": max(cur[3] - p0[3], 0)})
            sw_pk[lid] = (ia[1], ia[4], ib[1], ib[4])     # rx/tx paketlar (a, b tomon)
        if userspace:
            _fix_userspace_drops(project, link_series, sw_pk, sw_prev)
        for n in l3:
            rts = read_proc_routes(pid[n])
            rtrack.update(time.time() - T0, n, rts)
            node_series[n].append(snmp_s.point(k, n, read_proc_snmp(pid[n]), rts))
        progress(k / duration)

    time.sleep(2.0)    # oxirgi iperf3 intervallari
    stop_ev.set()
    for p in procs:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass

    flows = {}
    for a in apps:
        rec = raw[a["id"]]
        series = []
        for sec in range(1, int(duration) + 1):
            if not (a["start"] < sec <= a["stop"] + 1e-9):
                continue
            rtts = rec["rtt"].get(sec, [])
            pt = {"t": float(sec), "thr_bps": round(rec["thr"].get(sec, 0)),
                  "delay_ms": round(sum(rtts) / len(rtts) / 2, 3) if rtts else None,
                  "jitter_ms": rec["jit"].get(sec), "loss_pct": rec["loss"].get(sec)}
            if a["type"] == "ping":
                pt["rtt_ms"] = round(sum(rtts) / len(rtts), 3) if rtts else None
                exp = rec.get("expected_per_s", 5)
                pt["loss_pct"] = round(max(0.0, 1 - len(rtts) / exp) * 100, 1)
                pt["thr_bps"] = 0
            elif rtts:
                pt["rtt_ms"] = round(sum(rtts) / len(rtts), 3)
            if rec["retr"].get(sec) is not None:
                pt["retransmits"] = rec["retr"][sec]
            series.append(pt)
        val = lambda key: [p[key] for p in series if p.get(key) is not None]  # noqa: E731
        avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
        summ = {"avg_thr_bps": round(sum(p["thr_bps"] for p in series) / len(series)) if series else 0,
                "avg_delay_ms": avg(val("delay_ms")), "avg_jitter_ms": avg(val("jitter_ms")),
                "loss_pct": avg(val("loss_pct"))}
        if a["type"] == "ping" or val("rtt_ms"):
            summ["avg_rtt_ms"] = avg(val("rtt_ms"))
        flows[a["id"]] = {**a, "series": series, "summary": summ}
        if a.get("bytes"):
            flows[a["id"]]["done_t"] = rec.get("done_t")
    return {"engine": "mininet", "engine_label": "Mininet (real emulyatsiya)", "duration": duration,
            "flows": flows, "links": link_series, "events": applied,
            "nodes": node_series, "route_events": rtrack.events,
            "convergence": convergence(applied, rtrack.events, duration), "wall_s": round(time.time() - t_wall, 2),
            "notes": ["real vaqt, haqiqiy Linux TCP/UDP/FRR/OVS — natijalar host CPU yuklamasiga bog'liq",
                      "delay_ms = parallel ping RTT/2 (bir tomonlama kechikish taxmini)"]}

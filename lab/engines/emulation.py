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

from lab.model import ifname

_IPERF_LINE = re.compile(r"\[\s*\d+\]\s+([\d.]+)-([\d.]+)\s+sec\s+[\d.]+\s+\w?Bytes\s+([\d.]+)\s+([KMG]?)bits/sec(.*)$")
_UDP_TAIL = re.compile(r"([\d.]+)\s+ms\s+(\d+)/(\d+)")
_PING = re.compile(r"^\[(\d+\.\d+)\].*icmp_seq=(\d+).*time=([\d.]+)\s*ms")


def _mult(u):
    return {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}[u]


def run(driver, sc, progress=lambda f: None, log=lambda m: None):
    from lab.drivers.mininet_driver import _read_net_dev   # lazy
    if not driver.running or driver.name != "real":
        raise RuntimeError("Mininet dvigateli uchun real driverda tarmoq ishlab turishi kerak")
    with driver.lock:
        project = driver.project
        pid = {n: driver.net.get(n).pid for n in project["devices"]}
    duration = float(sc.get("duration", 30))
    t_wall = time.time()
    procs, threads = [], []
    raw = {}           # app_id -> {"thr": {sec: bps}, "rtt": {sec: [ms]}, "loss": {sec: pct}, "jit": {sec: ms}}
    stop_ev = threading.Event()
    T0 = time.time() + 2.0    # serverlar ishga tushishi uchun 2s

    def ip_of(dev):
        for ifn, ic in sorted(((project["devices"][dev].get("config") or {}).get("interfaces") or {}).items()):
            if ic.get("ip"):
                return ic["ip"].split("/")[0]
        return None

    def spawn(dev, argv):
        p = subprocess.Popen(["mnexec", "-a", str(pid[dev])] + argv, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        procs.append(p)
        return p

    def reader(p, fn):
        def go():
            for line in p.stdout:
                try:
                    fn(line.rstrip())
                except Exception:  # noqa: BLE001
                    pass
        t = threading.Thread(target=go, daemon=True); t.start(); threads.append(t)

    def at(t_rel, fn):
        def go():
            if not stop_ev.wait(max(T0 + t_rel - time.time(), 0)):
                fn()
        t = threading.Thread(target=go, daemon=True); t.start(); threads.append(t)

    apps = []
    for i, a in enumerate(sc.get("apps") or []):
        aid = a.get("id") or f"a{i + 1}"
        dip = a.get("dst_ip") or ip_of(a["dst"])
        start, stop = float(a.get("start", 1)), min(float(a.get("stop", duration)), duration)
        rec = {"thr": {}, "rtt": {}, "loss": {}, "jit": {}, "retr": {}}
        raw[aid] = rec
        apps.append({"id": aid, "type": a["type"], "src": a["src"], "dst": a["dst"], "start": start, "stop": stop,
                     "label": a.get("label") or f"{a['type']} {a['src']}→{a['dst']}"})
        if not dip:
            log(f"{aid}: {a['dst']} da IP yo'q — o'tkazib yuborildi")
            continue
        port = 5201 + i
        dur = max(int(round(stop - start)), 1)

        def ping_parser(rec=rec):
            def f(line):
                m = _PING.match(line)
                if m:
                    sec = int(float(m.group(1)) - T0) + 1
                    rec["rtt"].setdefault(sec, []).append(float(m.group(3)))
            return f

        interval = float(a.get("interval", 0.2 if a["type"] == "ping" else 0.5))
        ping_argv = ["ping", "-D", "-n", "-i", str(max(interval, 0.2)), "-w", str(dur), "-s",
                     str(max(int(a.get("size", 56)) - 8, 8) if a["type"] == "ping" else 56), dip]
        at(start, lambda a=a, argv=ping_argv, rec=rec: reader(spawn(a["src"], argv), ping_parser(rec)))
        if a["type"] == "ping":
            rec["expected_per_s"] = 1.0 / max(interval, 0.2)
            continue
        srv = spawn(a["dst"], ["iperf3", "-s", "-1", "-p", str(port), "-i", "1", "--forceflush"])
        cli = ["iperf3", "-c", dip, "-p", str(port), "-t", str(dur), "-i", "1", "--forceflush"]
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
            at(start, lambda a=a, cli=cli, f=cli_parser: reader(spawn(a["src"], cli), f))
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
    from lab.mobility import has_moves, positions_at
    movers = has_moves(sc)
    k = 0
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
        for lid, l in project["links"].items():
            pa, pb = pid[l["a"]["dev"]], pid[l["b"]["dev"]]
            for p_ in (pa, pb):
                if p_ not in cache:
                    cache[p_] = _read_net_dev(p_)
            ia = cache[pa].get(ifname(l["a"]["dev"], l["a"]["port"]))
            ib = cache[pb].get(ifname(l["b"]["dev"], l["b"]["port"]))
            if not ia or not ib:
                continue
            cur = (ia[3], ib[3])
            p0 = prev.get(lid, cur); prev[lid] = cur
            ab, ba = (cur[0] - p0[0]) * 8, (cur[1] - p0[1]) * 8
            bw = float((l.get("params") or {}).get("bw", 100)) * 1e6
            link_series[lid].append({"t": float(k), "ab_bps": ab, "ba_bps": ba, "util": round(min(max(ab, ba) / bw, 1), 4)})
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
    return {"engine": "mininet", "engine_label": "Mininet (real emulyatsiya)", "duration": duration,
            "flows": flows, "links": link_series, "events": applied, "wall_s": round(time.time() - t_wall, 2),
            "notes": ["real vaqt, haqiqiy Linux TCP/UDP/FRR/OVS — natijalar host CPU yuklamasiga bog'liq",
                      "delay_ms = parallel ping RTT/2 (bir tomonlama kechikish taxmini)"]}

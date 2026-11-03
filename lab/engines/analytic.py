"""Analitik (oqim darajasidagi) dvigatel — soniyalar ichida natija beradi.

OPNET'dagi "Flow Analysis" g'oyasiga o'xshash: paketlar emas, oqimlar
modellashtiriladi. Har 1 soniyalik oraliq uchun:
  1. Yo'l: demo driverdagi L2/L3 forwarding (Net.forward) — hodisalar (kabel uzilishi)
     hisobga olingan holda.
  2. UDP (inelastik): taklif qilingan tezlik; kabel sig'imidan oshsa — proporsional
     kesiladi (ortig'i yo'qotish).
  3. TCP (elastik): qolgan sig'im max-min adolat (progressive filling) bilan; loss
     bo'lsa Mathis chegarasi: MSS*1.22 / (RTT*sqrt(p)).
  4. Kechikish = tarqalish + uzatish + navbat (M/M/1: rho/(1-rho)*t_tx; to'yinganda
     navbat uzunligi bilan cheklangan).
Natija aniq emas (paket darajasidagi effektlar, TCP dinamikasi yo'q) — tez taxmin
va "nima bo'ladi?" tahlili uchun. Aniq natija: ns-3 yoki Mininet dvigateli.
"""

import copy
import ipaddress
import math
import time

from lab.drivers.demo_driver import Net, _ms
from lab.model import normalize_link_params, parse_rate

MSS_BITS = 1448 * 8


def run(project, sc, progress=lambda f: None, log=lambda m: None):
    t_wall = time.time()
    proj = copy.deepcopy(project)
    duration = float(sc.get("duration", 30))
    apps = []
    for i, a in enumerate(sc.get("apps") or []):
        apps.append({**a, "id": a.get("id") or f"a{i + 1}",
                     "label": a.get("label") or f"{a.get('type')} {a.get('src')}→{a.get('dst')}",
                     "start": float(a.get("start", 1)), "stop": min(float(a.get("stop", duration)), duration)})
    events = sorted(sc.get("events") or [], key=lambda e: float(e.get("t", 0)))
    series = {a["id"]: [] for a in apps}
    link_series = {lid: [] for lid in proj["links"]}
    applied, ev_i = [], 0
    steps = int(math.ceil(duration))
    for k in range(steps):
        t0, t1 = float(k), float(min(k + 1, duration))
        while ev_i < len(events) and float(events[ev_i].get("t", 0)) <= t0 + 1e-9:
            e = events[ev_i]; ev_i += 1
            l = proj["links"].get(e.get("link"))
            if l:
                if e["action"] == "link_down":
                    l["up"] = False
                elif e["action"] == "link_up":
                    l["up"] = True
                elif e["action"] == "link_params":
                    l["params"] = normalize_link_params({**(l.get("params") or {}), **(e.get("params") or {})})
            applied.append({"t": t0, **e})
            log(f"t={t0:.0f}s: {e['action']} {e.get('link')}")
        n = Net(proj)
        cap = {}
        for lid, l in proj["links"].items():
            bw = float((l.get("params") or {}).get("bw", 100)) * 1e6
            cap[(lid, "ab")] = cap[(lid, "ba")] = bw
        flows = []   # har aktiv ilova uchun holat
        for a in apps:
            st = {"app": a, "ok": False, "path": [], "back": [], "rate": 0.0}
            if a["start"] <= t0 + 1e-9 < a["stop"]:
                dip = a.get("dst_ip") or (str(n.ip_of(a["dst"]).ip) if n.ip_of(a["dst"]) else None)
                sip = n.ip_of(a["src"])
                if dip and sip:
                    ok, why, _h, path, _d = n.forward(a["src"], ipaddress.ip_address(dip))
                    ok2, _w2, _h2, back, _d2 = n.forward(a["dst"], sip.ip) if ok else (False, "", [], [], 0)
                    st.update(ok=ok and ok2, why=why, path=path, back=back)
                st["active"] = True
            flows.append(st)

        # 1) UDP/ping — inelastik
        load = {k2: 0.0 for k2 in cap}
        for f in flows:
            a = f["app"]
            if not f.get("active") or not f["ok"] or a["type"] == "tcp":
                continue
            if a["type"] == "ping":
                f["offered"] = int(a.get("size", 64)) * 8 / float(a.get("interval", 0.2))
            else:
                f["offered"] = parse_rate(a.get("rate", "1Mbps"))
            for hop in f["path"]:
                load[hop] += f["offered"]
        scale = {k2: (min(1.0, cap[k2] / v) if v > 0 else 1.0) for k2, v in load.items()}
        used = {k2: 0.0 for k2 in cap}
        for f in flows:
            if "offered" in f:
                s_min = min([scale[h] for h in f["path"]] or [1.0])
                f["rate"] = f["offered"] * s_min
                f["overload_drop"] = 1 - s_min
                for hop in f["path"]:
                    used[hop] += f["rate"]
        # 2) TCP — max-min adolat (progressive filling) qolgan sig'imda
        tcp = [f for f in flows if f.get("active") and f["ok"] and f["app"]["type"] == "tcp"]
        for f in tcp:
            f["rate"] = 0.0
            f["cap"] = parse_rate(f["app"]["rate"]) if f["app"].get("rate") else float("inf")
            loss_p = 1 - math.prod(1 - float((proj["links"][lid].get("params") or {}).get("loss", 0)) / 100
                                   for lid, _d in f["path"])
            if loss_p > 0:
                rtt = 2 * sum(max(_ms((proj["links"][lid].get("params") or {}).get("delay", "1ms")), 0.05) / 1000
                              for lid, _d in f["path"])
                f["cap"] = min(f["cap"], MSS_BITS * 1.22 / (max(rtt, 1e-4) * math.sqrt(loss_p)))
        remaining = {k2: max(cap[k2] - used[k2], 0.0) for k2 in cap}
        active = [f for f in tcp if f["path"]]
        frozen = set()
        for _ in range(64):
            live = [f for f in active if id(f) not in frozen]
            if not live:
                break
            cnt = {}
            for f in live:
                for hop in f["path"]:
                    cnt[hop] = cnt.get(hop, 0) + 1
            inc = min([remaining[h] / c for h, c in cnt.items()] + [f["cap"] - f["rate"] for f in live])
            if inc <= 1e-6:
                inc = 0
            for f in live:
                f["rate"] += inc
                for hop in f["path"]:
                    remaining[hop] -= inc
            for f in live:
                if f["rate"] >= f["cap"] - 1e-6 or any(remaining[h] <= 1e-3 for h in f["path"]):
                    frozen.add(id(f))
        for f in tcp:
            for hop in f["path"]:
                used[hop] += f["rate"]
        tcp_on = {h for f in tcp for h in f["path"] if f["rate"] > 0 and f["cap"] == float("inf")}

        # 3) kechikish / yo'qotish
        def hop_delay(h, pkt_bits):
            lid, _d = h
            p = proj["links"][lid].get("params") or {}
            c = cap[h]
            tx = pkt_bits / c
            rho = min(used[h] / c, 0.999)
            qmax = int(p.get("queue", 1000)) * 12000 / c
            if scale.get(h, 1) < 1 or h in tcp_on:          # to'yingan: navbat to'la (UDP) / yarim (TCP)
                q = qmax if scale.get(h, 1) < 1 else 0.25 * qmax   # TCP: taxminiy turg'un navbat
            else:
                q = min(rho / (1 - rho) * tx, qmax)
            return _ms(p.get("delay", "1ms")) / 1000 + tx + q, q

        for f in flows:
            a = f["app"]
            if not f.get("active"):
                continue
            pt = {"t": t1, "thr_bps": 0, "delay_ms": None, "jitter_ms": None, "loss_pct": 100.0}
            if f["ok"]:
                bits = int(a.get("size", 1000 if a["type"] != "ping" else 64)) * 8 if a["type"] != "tcp" else 12000
                d_fwd, q_sum = 0.0, 0.0
                for h in f["path"]:
                    dd, q = hop_delay(h, bits); d_fwd += dd; q_sum += q
                link_loss = 1 - math.prod(1 - float((proj["links"][lid].get("params") or {}).get("loss", 0)) / 100
                                          for lid, _d in f["path"])
                tot_loss = 1 - (1 - link_loss) * (1 - f.get("overload_drop", 0))
                thr = f["rate"] * (1 - link_loss) if a["type"] != "tcp" else f["rate"]
                pt.update(thr_bps=round(thr), delay_ms=round(d_fwd * 1000, 3),
                          jitter_ms=round(q_sum * 1000 * 0.15 + 0.01, 3),
                          loss_pct=round(tot_loss * 100 if a["type"] != "tcp" else link_loss * 100, 2))
                if a["type"] == "ping":
                    d_back = sum(hop_delay(h, bits)[0] for h in f["back"])
                    pt["rtt_ms"] = round((d_fwd + d_back) * 1000, 3)
            series[a["id"]].append(pt)
        for lid in proj["links"]:
            ab, ba = used[(lid, "ab")], used[(lid, "ba")]
            link_series[lid].append({"t": t1, "ab_bps": round(ab), "ba_bps": round(ba),
                                     "util": round(min(max(ab, ba) / cap[(lid, "ab")], 1.0), 4)})
        progress((k + 1) / steps)

    flows_out = {}
    for a in apps:
        s = series[a["id"]]
        ok = [p for p in s if p["delay_ms"] is not None]
        mean = lambda key, pts: round(sum(p[key] for p in pts) / len(pts), 3) if pts else None  # noqa: E731
        summ = {"avg_thr_bps": round(sum(p["thr_bps"] for p in s) / len(s)) if s else 0,
                "avg_delay_ms": mean("delay_ms", ok), "avg_jitter_ms": mean("jitter_ms", ok),
                "loss_pct": mean("loss_pct", s)}
        if a["type"] == "ping":
            summ["avg_rtt_ms"] = mean("rtt_ms", [p for p in ok if p.get("rtt_ms") is not None])
        flows_out[a["id"]] = {"id": a["id"], "label": a["label"], "type": a["type"], "src": a["src"], "dst": a["dst"],
                              "start": a["start"], "stop": a["stop"], "series": s, "summary": summ}
    return {"engine": "analytic", "engine_label": "Analitik (oqim modeli)", "duration": duration,
            "flows": flows_out, "links": link_series, "events": applied, "wall_s": round(time.time() - t_wall, 3),
            "notes": ["oqim darajasidagi taxmin: UDP qat'iy, TCP max-min adolat + Mathis, navbat M/M/1",
                      "dinamik routing: demo modeli (dinamik daemonli routerlar o'zaro barcha subnetlarni biladi)"]}

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
from lab.model import normalize_link_params, parse_rate, rip_timers
from lab.mobility import apply_positions, has_moves, positions_at
from lab.wifi import ap_config, channels_overlap, ess_key, max_range_m, meters_per_px
from lab.wifi import estimate as wifi_estimate
from lab.wifi import link_estimate as wifi_link_estimate
from lab.engines.nodestats import L3_NS, RouteTracker, convergence, route_key

MSS_BITS = 1448 * 8


class _StaleNet(Net):
    """Konvergensiya oynasi: RIP routerlar uzilishdan OLDINGI routing jadvalini ishlatadi
    (forwarding esa joriy kabel holati bo'yicha — uzilgan kabelga yuborilgan paket yo'qoladi)."""

    def __init__(self, project, old_project, rip_set):
        super().__init__(project)
        self._old = Net(old_project)
        self._rip = rip_set

    def routes(self, dev):
        return self._old.routes(dev) if dev in self._rip else super().routes(dev)


def run(project, sc, progress=lambda f: None, log=lambda m: None):
    t_wall = time.time()
    from lab.services import resolve_dhcp
    proj = copy.deepcopy(resolve_dhcp(project)[0])    # DHCP mijozlar — lizing manzillari
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
    # RIP konvergensiyasi: kabel uzilgandan keyin RIP routerlar eski yo'llarni rip_conv s
    # davomida ishlatadi (keyingi periodik update'gacha; 30s davr -> kutilgan ~15s).
    rip_set = {n for n, d in proj["devices"].items() if d["type"] == "router"
               and "ripd" in ((d.get("config") or {}).get("daemons") or [])
               and not ({"ospfd", "isisd", "ospf6d"} & set((d.get("config") or {}).get("daemons") or []))}
    # Muqobil yo'l qo'shni routerning keyingi PERIODIK update'ida keladi: kutish ~ U(0, update)
    # -> o'rtacha update/2 (FRR/ns-3 standart 30 s -> 15 s). rip_convergence_s — qo'lda almashtirish.
    rip_upd = max((rip_timers(proj["devices"][n])["update"] for n in rip_set), default=30)
    rip_conv = float(sc["rip_convergence_s"]) if sc.get("rip_convergence_s") not in (None, "") else rip_upd / 2
    stale = None   # (eski loyiha nusxasi, qachongacha)
    movers = has_moves(sc)
    handover = {}   # stansiya -> aloqa uzilgan vaqt (roaming kutilmoqda)
    # cheklangan TCP uzatishlar (FTP fayl / HTTP sahifa): qolgan bayt, tugash vaqti, ulanish+slow start
    remaining = {a["id"]: int(a["bytes"]) for a in apps if a.get("type") == "tcp" and a.get("bytes")}
    done_at, setup_left = {}, {}
    node_series = {dv: [] for dv, d in proj["devices"].items() if d["type"] in L3_NS}
    rtrack = RouteTracker()
    steps = int(math.ceil(duration))
    for k in range(steps):
        t0, t1 = float(k), float(min(k + 1, duration))
        while ev_i < len(events) and float(events[ev_i].get("t", 0)) <= t0 + 1e-9:
            e = events[ev_i]; ev_i += 1
            l = proj["links"].get(e.get("link"))
            if l and e["action"] == "link_down" and rip_set and rip_conv > 0:
                stale = (copy.deepcopy(proj), t0 + rip_conv)
                log(f"RIP: {rip_conv:.0f}s konvergensiya (eski yo'llar)")
            if l:
                if e["action"] == "link_down":
                    l["up"] = False
                elif e["action"] == "link_up":
                    l["up"] = True
                elif e["action"] == "link_params":
                    l["params"] = normalize_link_params({**(l.get("params") or {}), **(e.get("params") or {})})
            applied.append({"t": t0, **e})
            log(f"t={t0:.0f}s: {e['action']} {e.get('link') or e.get('device')}")
        if movers:     # harakat: oraliq o'rtasidagi pozitsiya
            apply_positions(proj, positions_at(project, sc, (t0 + t1) / 2))
        # roaming (ESS = bir xil SSID + diapazon + kanal): boshida eng kuchli AP; keyin "yopishqoq"
        # klient — joriy AP bilan aloqa uzilgach 1 qadam (beacon yo'qolishi + skan) uzilish, so'ng
        # eng kuchli AP'ga o'tadi (ns-3 StaWifiMac xatti-harakatiga mos)
        for lid, l in proj["links"].items():
            if (l.get("params") or {}).get("medium") != "wifi":
                continue
            side = "a" if proj["devices"][l["a"]["dev"]]["type"] == "wifiap" else "b"
            sta = l["b" if side == "a" else "a"]["dev"]
            cur_ap = l[side]["dev"]
            key = ess_key(cur_ap, proj["devices"][cur_ap])
            sd = proj["devices"][sta]

            def best_ap():
                cands = []
                for an, ad in proj["devices"].items():
                    if ad["type"] == "wifiap" and ess_key(an, ad) == key:
                        e_ = wifi_estimate(math.hypot(ad["x"] - sd["x"], ad["y"] - sd["y"]) * meters_per_px(proj), ap_config(ad))
                        if e_["ok"]:
                            cands.append((e_["rssi_dbm"], an))
                return max(cands)[1] if cands else None

            def repoint(new_ap):
                used_ports = {x[e]["port"] for x in proj["links"].values() for e in ("a", "b") if x[e]["dev"] == new_ap}
                l[side] = {"dev": new_ap, "port": max(used_ports | {0}) + 1}

            if k == 0:
                b0 = best_ap()
                if b0 and b0 != cur_ap:
                    repoint(b0); applied.append({"t": t0, "action": "roam", "device": sta, "from": cur_ap, "to": b0})
                continue
            est_now = wifi_link_estimate(proj, lid)
            if est_now and est_now["ok"]:
                handover.pop(sta, None)
            elif sta not in handover:
                handover[sta] = t0              # bu qadam — uzilish (beacon'lar yo'qoldi)
            else:
                b1 = best_ap()
                if b1 and b1 != cur_ap:
                    repoint(b1)
                    applied.append({"t": t0, "action": "roam", "device": sta, "from": cur_ap, "to": b1})
                    log(f"t={t0:.0f}s: roaming {sta}: {cur_ap} -> {b1}")
                    handover.pop(sta, None)
        if stale and t0 < stale[1]:
            stale[0]["devices"] = proj["devices"]   # eski routing jadvali, lekin joriy pozitsiyalar
            n = _StaleNet(proj, stale[0], rip_set)
        else:
            n = Net(proj)
        cap = {}
        wifi_of = {}    # lid -> (ap, stansiya foydali sig'imi bit/s)
        for lid, l in proj["links"].items():
            bw = float((l.get("params") or {}).get("bw", 100)) * 1e6
            est = wifi_link_estimate(proj, lid)
            if est:
                bw = max(est["thr_mbps"], 0.001) * 1e6
                wifi_of[lid] = (est["ap"], bw)
            cap[(lid, "ab")] = cap[(lid, "ba")] = bw
        # efir vaqti: bir kanalda (chastotalar kesishadi) va bir-birini eshitadigan AP'lar
        # (masofa <= aloqa chegarasi) bitta umumiy efirni bo'lishadi — union-find
        aps = sorted({ap_ for ap_, _c in wifi_of.values()})
        parent = {ap_: ap_ for ap_ in aps}

        def root(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        cfgs = {ap_: ap_config(proj["devices"][ap_]) for ap_ in aps}
        mpp = meters_per_px(proj)
        for i_, a1 in enumerate(aps):
            for a2 in aps[i_ + 1:]:
                d1, d2 = proj["devices"][a1], proj["devices"][a2]
                dist = math.hypot(d1["x"] - d2["x"], d1["y"] - d2["y"]) * mpp
                if channels_overlap(cfgs[a1], cfgs[a2]) and dist <= max(max_range_m(cfgs[a1]), max_range_m(cfgs[a2])):
                    parent[root(a1)] = root(a2)
        pool = {ap_: root(ap_) for ap_ in aps}
        for lid in list(wifi_of):
            ap_, c_ = wifi_of[lid]
            wifi_of[lid] = (pool[ap_], c_)        # endi kalit — efir guruhi
            cap[("air", pool[ap_])] = 1.0

        max_sta = {}
        for ap_, c_ in wifi_of.values():
            max_sta[ap_] = max(max_sta.get(ap_, 0), c_)

        def speed(path):
            # efir vaqti bo'yicha adolat (A-MPDU'li zamonaviy Wi-Fi): har stansiya teng efir vaqti
            # oladi -> tezligi o'z sig'imiga proporsional. Simli oqimlar uchun 1.
            sp = 1.0
            for lid, _d in path:
                if lid in wifi_of:
                    ap_, c_ = wifi_of[lid]
                    sp = min(sp, c_ / max_sta[ap_])
            return sp

        def resources(path, tcp=False):
            # kabel resurslari (bit/s, og'irlik 1) + Wi-Fi efir vaqti (og'irlik 1/stansiya sig'imi):
            # sekin (uzoq) stansiya bir xil bit uchun ko'proq efir oladi — Wi-Fi "performance anomaly"
            out = [(h, 1.0) for h in path]
            for lid, _d in path:
                if lid in wifi_of:
                    # TCP: qaytuvchi ACK'lar ham efir oladi (~9%)
                    out.append((("air", wifi_of[lid][0]), (1.09 if tcp else 1.0) / wifi_of[lid][1]))
            return out
        flows = []   # har aktiv ilova uchun holat
        for a in apps:
            st = {"app": a, "ok": False, "path": [], "back": [], "rate": 0.0}
            if a["start"] <= t0 + 1e-9 < a["stop"] and a["id"] not in done_at:
                dip = a.get("dst_ip") or (str(n.ip_of(a["dst"]).ip) if n.ip_of(a["dst"]) else None)
                sip = n.ip_of(a["src"])
                if dip and sip:
                    ok, why, _h, path, _d = n.forward(a["src"], ipaddress.ip_address(dip))
                    ok2, _w2, _h2, back, _d2 = (n.reply(a["src"], sip.ip, a["dst"], ipaddress.ip_address(dip), _h)
                                                if ok else (False, "", [], [], 0))
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
            f["res"] = resources(f["path"])
            for key, w in f["res"]:
                load[key] += f["offered"] * w
        scale = {k2: (min(1.0, cap[k2] / v) if v > 0 else 1.0) for k2, v in load.items()}
        used = {k2: 0.0 for k2 in cap}
        for f in flows:
            if "offered" in f:
                s_min = min([scale[key] for key, _w in f["res"]] or [1.0])
                f["rate"] = f["offered"] * s_min
                f["overload_drop"] = 1 - s_min
                for key, w in f["res"]:
                    used[key] += f["rate"] * w
        # 2) TCP — max-min adolat (progressive filling) qolgan sig'imda
        tcp = [f for f in flows if f.get("active") and f["ok"] and f["app"]["type"] == "tcp"]
        for f in tcp:
            f["rate"] = 0.0
            f["res"] = resources(f["path"], tcp=True)
            f["spd"] = speed(f["path"])
            f["cap"] = parse_rate(f["app"]["rate"]) if f["app"].get("rate") else float("inf")
            loss_p = 1 - math.prod(1 - float((proj["links"][lid].get("params") or {}).get("loss", 0)) / 100
                                   for lid, _d in f["path"])
            if loss_p > 0:
                rtt = 2 * sum(max(_ms((proj["links"][lid].get("params") or {}).get("delay", "1ms")), 0.05) / 1000
                              for lid, _d in f["path"])
                f["cap"] = min(f["cap"], MSS_BITS * 1.22 / (max(rtt, 1e-4) * math.sqrt(loss_p)))
        def alloc(live_tcp):
            """max-min progressive filling qolgan (UDP'dan keyingi) sig'imda."""
            for f in live_tcp:
                f["rate"] = 0.0
            remaining_c = {k2: max(cap[k2] - used[k2], 0.0) for k2 in cap}
            active_ = [f for f in live_tcp if f["path"]]
            frozen = set()
            for _ in range(64):
                live = [f for f in active_ if id(f) not in frozen]
                if not live:
                    break
                cnt = {}
                for f in live:
                    for key, w in f["res"]:
                        cnt[key] = cnt.get(key, 0) + w * f["spd"]
                inc = min([remaining_c[k2] / c for k2, c in cnt.items()] + [(f["cap"] - f["rate"]) / f["spd"] for f in live])
                if inc <= 1e-6:
                    inc = 0
                for f in live:
                    f["rate"] += inc * f["spd"]
                    for key, w in f["res"]:
                        remaining_c[key] -= inc * f["spd"] * w
                for f in live:
                    if f["rate"] >= f["cap"] - 1e-6 or any(remaining_c[key] <= 1e-6 * cap[key] for key, _w in f["res"]):
                        frozen.add(id(f))

        # Qadam ichida hodisaga asoslangan: cheklangan uzatish (FTP/HTTP) tugasa, uning ulushi
        # qolgan oqimlarga o'tadi (taqsimlash qayta hisoblanadi). rate = qadam bo'yicha o'rtacha.
        dt_ = t1 - t0
        live = [f for f in tcp if f["path"]]
        acc = {id(f): 0.0 for f in tcp}
        got = {id(f): 0.0 for f in tcp}
        for f in live:
            aid = f["app"]["id"]
            if aid in remaining and aid not in setup_left:
                # 3-way handshake (1 RTT) + slow start (initcwnd 10 MSS, har RTT x2) — tarqalish RTT bo'yicha
                rtt0 = sum(2 * _ms((proj["links"][lid].get("params") or {}).get("delay", "1ms")) / 1000
                           + 12000 / cap[(lid, d)] for lid, d in f["path"])
                rounds = math.ceil(math.log2(1 + remaining[aid] / (10 * 1448)))
                setup_left[aid] = rtt0 * (1 + rounds)
        t_left = dt_
        for _ in range(32):
            if t_left <= 1e-9 or not live:
                break
            alloc(live)
            step = t_left
            for f in live:
                aid = f["app"]["id"]
                if aid in remaining and f["rate"] > 0:
                    step = min(step, setup_left[aid] + remaining[aid] * 8 / f["rate"])
            step = max(step, 1e-6)
            for f in list(live):
                aid = f["app"]["id"]
                acc[id(f)] += f["rate"] * step
                if aid in remaining:
                    su = min(setup_left[aid], step)
                    setup_left[aid] -= su
                    bits = f["rate"] * (step - su)
                    got[id(f)] += bits
                    remaining[aid] -= bits / 8
                    if remaining[aid] <= 1e-3:
                        remaining[aid] = 0
                        done_at[aid] = t0 + (dt_ - t_left) + step
                        live.remove(f)
            t_left -= step
        for f in tcp:
            f["rate"] = acc[id(f)] / dt_ if dt_ > 0 else 0.0
            if f["app"]["id"] in remaining:
                f["thr_fixed"] = got[id(f)] / dt_ if dt_ > 0 else 0.0
        for f in tcp:
            for key, w in f["res"]:
                used[key] += f["rate"] * w
        tcp_on = {h for f in tcp for h in f["path"] if f["rate"] > 0 and f["cap"] == float("inf")}
        air_sat = {k2[1] for k2 in cap if k2[0] == "air" and used[k2] >= 0.97}

        # 3) kechikish / yo'qotish
        def hop_delay(h, pkt_bits):
            lid, _d = h
            p = proj["links"][lid].get("params") or {}
            c = cap[h]
            tx = pkt_bits / c
            rho = min(used[h] / c, 0.999)
            qmax = int(p.get("queue", 1000)) * 12000 / c
            if lid in wifi_of and wifi_of[lid][0] in air_sat:   # efir to'yingan — navbat o'sadi
                return _ms(p.get("delay", "1ms")) / 1000 + tx + 0.25 * qmax, 0.25 * qmax
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
                if "thr_fixed" in f:
                    pt["thr_bps"] = round(f["thr_fixed"])
            series[a["id"]].append(pt)
        for lid in proj["links"]:
            ab, ba = used[(lid, "ab")], used[(lid, "ba")]
            pt = {"t": t1, "ab_bps": round(ab), "ba_bps": round(ba),
                  "util": round(min(max(ab, ba) / cap[(lid, "ab")], 1.0), 4)}
            for d in ("ab", "ba"):
                h = (lid, d)
                q_s = hop_delay(h, 12000)[1] if used[h] > 0 else 0.0
                pt[f"queue_{d}"] = round(q_s * cap[h] / 12000, 1)          # navbatdagi paketlar (1500 B)
                pt[f"drops_{d}"] = round(max(load[h] - cap[h], 0) / 8000 * (t1 - t0))   # ortiqcha UDP (1000 B)
            link_series[lid].append(pt)
        # qurilma statistikasi: marshrutlar (joriy/eski RIP jadvali), forwarding pps, egress drops
        cnt = {dv: {"fwd": 0.0, "rx": 0.0, "tx": 0.0} for dv in node_series}
        for f in flows:
            if not f.get("active") or not f["ok"] or not f["path"] or f["rate"] <= 0:
                continue
            a = f["app"]
            pps = f["rate"] / (int(a.get("size", 1000 if a["type"] != "ping" else 64)) * 8 if a["type"] != "tcp" else 12000)
            hops = [(proj["links"][lid]["a" if d == "ab" else "b"]["dev"], proj["links"][lid]["b" if d == "ab" else "a"]["dev"])
                    for lid, d in f["path"]]
            for i_, (src_, dst_) in enumerate(hops):
                if src_ in cnt:
                    cnt[src_]["tx"] += pps
                if dst_ in cnt:
                    cnt[dst_]["rx"] += pps
                    if i_ < len(hops) - 1:
                        cnt[dst_]["fwd"] += pps
        for dv in node_series:
            rts = {route_key(r[0], r[1], r[2]) for r in n.routes(dv)}
            rtrack.update(t0, dv, rts)
            disc = sum(link_series[lid][-1][f"drops_{d}"] for lid, l in proj["links"].items()
                       for side, d in (("a", "ab"), ("b", "ba")) if l[side]["dev"] == dv)
            node_series[dv].append({"t": t1, "routes": len(rts), "fwd_pps": round(cnt[dv]["fwd"], 1),
                                    "rx_pps": round(cnt[dv]["rx"], 1), "tx_pps": round(cnt[dv]["tx"], 1),
                                    "noroute": 0, "discards": disc})
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
        if a["id"] in remaining:
            flows_out[a["id"]].update(bytes=int(a["bytes"]), done_t=round(done_at[a["id"]], 3) if a["id"] in done_at else None)
        if a.get("group"):
            flows_out[a["id"]].update(group=a["group"], role=a.get("role"))
    return {"engine": "analytic", "engine_label": "Analitik (oqim modeli)", "duration": duration,
            "flows": flows_out, "links": link_series, "events": applied,
            "nodes": node_series, "route_events": rtrack.events,
            "convergence": convergence(applied, rtrack.events, duration), "wall_s": round(time.time() - t_wall, 3),
            "notes": ["oqim darajasidagi taxmin: UDP qat'iy, TCP max-min adolat + Mathis, navbat M/M/1",
                      "dinamik routing: demo modeli (dinamik daemonli routerlar o'zaro barcha subnetlarni biladi)",
                      "RIP: kabel uzilgandan keyin update/2 (rip_timers, standart 30 s -> 15 s) yoki rip_convergence_s davomida eski yo'llar",
                      "Wi-Fi: SNR→MCS (ns-3 ga kalibrlangan), efir vaqti bo'yicha adolat (A-MPDU), TCP ACK efiri ~9%",
                      "Wi-Fi: bir kanalda va bir-birini eshitadigan AP'lar umumiy efirni bo'lishadi; harakat — oraliq o'rtasidagi pozitsiya",
                      "Wi-Fi roaming: ESS (SSID+diapazon+kanal) ichida, joriy AP uzilgach 1s uzilish bilan eng kuchli AP'ga"]}

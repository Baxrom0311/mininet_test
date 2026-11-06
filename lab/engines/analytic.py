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
import json
import ipaddress
import math
import time

from lab.drivers.demo_driver import Net, _ms
from lab.model import dscp_class, normalize_link_params, parse_rate, rip_timers
from lab.mobility import apply_positions, has_moves, positions_at
from lab.wifi import ap_config, channels_overlap, ess_key, max_range_m, meters_per_px
from lab.wifi import estimate as wifi_estimate
from lab.wifi import link_estimate as wifi_link_estimate
from lab.mcast import branch as mc_branch, flood_keys as mc_flood_keys
from lab.engines.nodestats import L3_NS, RouteTracker, convergence, route_key
from lab.engines.ospf_convergence import OspfConvergence
from lab.firewall import _why as fw_why
from lab.firewall import check_path as fw_check_path
from lab.firewall import check_roundtrip as fw_roundtrip

MSS_BITS = 1448 * 8


class _StaleNet(Net):
    """Konvergensiya oynasi: RIP routerlar uzilishdan OLDINGI routing jadvalini ishlatadi
    (forwarding esa joriy kabel holati bo'yicha — uzilgan kabelga yuborilgan paket yo'qoladi)."""

    def __init__(self, project, old_project, rip_set, extra=()):
        super().__init__(project)
        # [(eski Net, qurilmalar to'plami)] — RIP (uzilishdan oldingi) va BGP (tiklanishdan oldingi) oynalari
        self._ov = ([(Net(old_project), rip_set)] if old_project is not None else []) + \
                   [(Net(o), st) for o, st in extra]

    def routes(self, dev):
        for net_, st in self._ov:
            if dev in st:
                return net_.routes(dev)
        return super().routes(dev)


def run(project, sc, progress=lambda f: None, log=lambda m: None):
    t_wall = time.time()
    from lab.addressing import project_for_family
    project = project_for_family(project, sc.get("ip_version", 4))
    from lab.services import resolve_dhcp
    # Keep the immutable reference positions separate from mutable simulation state.
    proj = copy.deepcopy(resolve_dhcp(project)[0])
    duration = float(sc.get("duration", 30))
    apps = []
    for i, a in enumerate(sc.get("apps") or []):
        apps.append({**a, "id": a.get("id") or f"a{i + 1}",
                     "label": a.get("label") or f"{a.get('type')} {a.get('src')}→{a.get('dst')}",
                     "start": float(a.get("start", 1)), "stop": min(float(a.get("stop", duration)), duration),
                     # firewall uchun port: dport yoki Mininet dvigateli bilan bir xil iperf3 porti
                     "_dport": int(a["dport"]) if a.get("dport") not in (None, "") else 5201 + i})
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
    # BGP: kabel uzilsa (interfeys DOWN) sessiya darhol tushadi va marshrut olib tashlanadi;
    # tiklanganda sessiya qayta o'rnatilib e'lonlar kelguncha bgp_convergence_s (real FRR,
    # `timers connect 5`: ~2 s) davomida BGP routerlar eski (kabelsiz) jadvalni ishlatadi.
    from lab.bgp import bgp_cfg
    bgp_set = {n for n, d in proj["devices"].items() if bgp_cfg(d)}
    bgp_conv = float(sc["bgp_convergence_s"]) if sc.get("bgp_convergence_s") not in (None, "") else 2.0
    bgp_stale = None
    ospf = OspfConvergence(sc)
    movers = has_moves(sc)
    handover = {}   # stansiya -> aloqa uzilgan vaqt (roaming kutilmoqda)
    # cheklangan TCP uzatishlar (FTP fayl / HTTP sahifa): qolgan bayt, tugash vaqti, ulanish+slow start
    remaining = {a["id"]: int(a["bytes"]) for a in apps if a.get("type") == "tcp" and a.get("bytes")}
    done_at, setup_left = {}, {}
    last_path = {}      # ilova -> oxirgi qadamdagi (yo'l, sabab) — survivability tahlili uchun
    node_series = {dv: [] for dv, d in proj["devices"].items() if d["type"] in L3_NS}
    rtrack = RouteTracker()
    from lab.stp import compute as stp_compute, convergence_time as stp_conv_time, stp_enabled
    stp_on = any(stp_enabled(d) for d in proj["devices"].values())
    stp_up, stp_fwd, stp_hold = {}, None, {}
    # VRRP: master o'zgarishlari vaqt modeli (Master_Down_Interval, preempt) — lab/vrrp.py
    from lab import vrrp as _vrrp
    vrrp_tl = _vrrp.timeline(proj, sc)
    if vrrp_tl:
        prev_m = {}
        for tt, key, r in vrrp_tl:
            old_m = prev_m.get(key)
            prev_m[key] = r
            if tt <= 0 or old_m == r:
                continue
            if old_m:
                rtrack.events.append({"t": tt, "dev": old_m, "added": [], "removed": [f"VRRP {key} master"]})
            if r:
                rtrack.events.append({"t": tt, "dev": r, "added": [f"VRRP {key} master"], "removed": []})
            log(f"VRRP {key}: t={tt:.1f}s master -> {r or 'yo`q (Master_Down_Interval kutilmoqda)'}")
    from lab.lag import failover_s as lag_failover_s, groups as lag_groups
    lag_links = {lid for g in lag_groups(proj) for lid in g["links"]}
    lag_failover, lag_hold = lag_failover_s(sc), {}
    steps = int(math.ceil(duration))
    net_cache = [None]
    for k in range(steps):
        t0, t1 = float(k), float(min(k + 1, duration))
        if vrrp_tl:
            proj["_vrrp_master"] = _vrrp.masters_at(vrrp_tl, t0 + 0.5)    # qadam o'rtasidagi holat
        while ev_i < len(events) and float(events[ev_i].get("t", 0)) <= t0 + 1e-9:
            e = events[ev_i]; ev_i += 1
            l = proj["links"].get(e.get("link"))
            if l and e["action"] in ("link_down", "link_up") and (
                    (l.get("up") is not False) != (e["action"] == "link_up")):
                ospf.before_change(proj, t0)
            if l and e["action"] == "link_down" and rip_set and rip_conv > 0:
                stale = (copy.deepcopy(proj), t0 + rip_conv)
                log(f"RIP: {rip_conv:.0f}s konvergensiya (eski yo'llar)")
            if l and e["action"] == "link_up" and bgp_set and bgp_conv > 0 and l.get("up") is False:
                bgp_stale = (copy.deepcopy(proj), t0 + bgp_conv)
                log(f"BGP: {bgp_conv:.0f}s sessiya qayta o'rnatilmoqda")
            if l and e["action"] == "link_down" and l.get("up") is not False and lag_links and e["link"] in lag_links:
                lag_hold[e["link"]] = t0 + lag_failover      # bond a'zosi: failover tugaguncha hash eski
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
        bgp_ov = ()
        if bgp_stale and t0 < bgp_stale[1]:
            bgp_stale[0]["devices"] = proj["devices"]
            bgp_ov = ((bgp_stale[0], bgp_set),)
        if lag_links:
            # bond failover: uzilgan a'zo qadamning ko'p qismida hali "tanlangan" bo'lsa — shu
            # a'zoga hash qilingan oqimlar yo'qoladi; keyin qolgan a'zolarga qayta taqsimlanadi
            hold_now = sorted(lid for lid, u in lag_hold.items() if t0 + 0.5 < u)
            if hold_now:
                proj["_lag_hold"] = hold_now
            else:
                proj.pop("_lag_hold", None)
        if stp_on:
            # STP konvergensiyasi: blocking/disabled -> forwarding o'tayotgan portlar `hold` da
            # (802.1D: 2*forward_delay [+max_age], RSTP ~1 s); forwarding -> blocking — darhol.
            up_now = {lid: l.get("up") is not False for lid, l in proj["links"].items()}
            failed = [lid for lid, u in up_now.items() if stp_up.get(lid, True) and not u]
            changed = up_now != stp_up
            stp_up = up_now
            proj.pop("_stp_hold", None)
            if changed or stp_fwd is None:
                st_new = stp_compute(proj)
                fwd_new = {(x, p_) for x, b in st_new["bridges"].items() for p_, v in b["ports"].items()
                           if v["state"] == "forwarding"}
                if stp_fwd is not None:
                    for x, p_ in sorted(fwd_new - stp_fwd):
                        stp_hold[(x, p_)] = t0 + stp_conv_time(proj, x, failed, sc)
                        log(f"STP: {x} port {p_} -> forwarding {stp_hold[(x, p_)] - t0:.0f} s dan keyin")
                    for key in list(stp_hold):
                        if key not in fwd_new:
                            stp_hold.pop(key)
                stp_fwd = fwd_new
            for key in [k_ for k_, t_ in stp_hold.items() if t0 + 1e-9 >= t_]:
                stp_hold.pop(key)
            if stp_hold:
                proj["_stp_hold"] = sorted(stp_hold)
        # Net (marshrut/L2 keshlari bilan) holat o'zgarmagan qadamlarda qayta ishlatiladi:
        # imzo = kabellar + qurilmalar (pozitsiya/sozlama) + ichki holatlar (_stp_hold, _lag_hold,
        # _vrrp_master) + faol eski-marshrut oynalari (RIP/BGP).
        st_on = bool(stale and t0 < stale[1])
        sig = (json.dumps(proj["links"], sort_keys=True, default=str),
               json.dumps(proj["devices"], sort_keys=True, default=str),
               json.dumps({k_: v_ for k_, v_ in proj.items() if k_.startswith("_")}, sort_keys=True, default=str),
               id(stale[0]) if st_on else None, tuple(id(o) for o, _s in bgp_ov),
               ospf.deadline if ospf.pending(t0) else None)
        if net_cache[0] is not None and net_cache[0][0] == sig:
            n = net_cache[0][1]
        elif st_on:
            stale[0]["devices"] = proj["devices"]   # eski routing jadvali, lekin joriy pozitsiyalar
            n = _StaleNet(proj, stale[0], rip_set, bgp_ov)
        elif bgp_ov:
            n = _StaleNet(proj, None, set(), bgp_ov)
        else:
            n = Net(proj)
        if net_cache[0] is None or net_cache[0][0] != sig:
            n = ospf.apply(n, t0)
        net_cache[0] = (sig, n)
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
        has_fw = any((d.get("config") or {}).get("firewall") for d in proj["devices"].values())
        flows = []   # har aktiv ilova uchun holat
        for a in apps:
            st = {"app": a, "ok": False, "path": [], "back": [], "rate": 0.0}
            if a["start"] <= t0 + 1e-9 < a["stop"] and a["id"] not in done_at:
                dip = a.get("dst_ip") or (str(n.ip_of(a["dst"]).ip) if n.ip_of(a["dst"]) else None)
                sip = n.ip_of(a["src"])
                if dip and sip:
                    n.flow_key = (a["src"], a["dst"], a["id"])     # bond (LAG) a'zosi hash'i
                    ok, why, _h, path, _d = n.forward(a["src"], ipaddress.ip_address(dip))
                    ok2, _w2, _h2, back, _d2 = (n.reply(a["src"], sip.ip, a["dst"], ipaddress.ip_address(dip), _h)
                                                if ok else (False, "", [], [], 0))
                    st.update(ok=ok and ok2, why=why, path=path, back=back)
                    if a.get("mcast") and ok:      # multicast: shoxchada PIM bo'lishi shart
                        okb, whyb, txp = mc_branch(n, a["src"], ipaddress.ip_address(dip), _h)
                        st["tx"] = txp
                        if not okb:
                            st.update(ok=False, why=whyb)
                    if ok and ok2 and has_fw:
                        pkt = {"proto": "icmp" if a["type"] == "ping" else a["type"], "src": str(sip.ip),
                               "dst": dip, "dport": None if a["type"] == "ping" else a["_dport"], "sport": 40000}
                        if a["type"] == "udp":      # bir tomonlama
                            okf, v_, w_, c_, r_ = fw_check_path(n, a["src"], dip, _h, pkt)
                            whyf = None if okf else fw_why(v_, w_, c_, r_, pkt)
                        else:                      # tcp/ping: so'rov + javob (stateful)
                            okf, whyf, _v = fw_roundtrip(n, a["src"], sip.ip, a["dst"], dip, _h, pkt)
                        if not okf:
                            st.update(ok=False, why=whyf)
                st["active"] = True
                last_path[a["id"]] = ([[lid, d_] for lid, d_ in st["path"]], None if st["ok"] else st.get("why"))
            flows.append(st)

        # 1) UDP/ping — inelastik
        load = {k2: 0.0 for k2 in cap}
        mc_seen, mc_off = set(), {}
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
                if a.get("mcast"):                 # multicast: har kabelda BITTA nusxa
                    if (a["mcast"], key) in mc_seen:
                        continue
                    mc_seen.add((a["mcast"], key))
                    mc_off[a["mcast"]] = f["offered"]
                load[key] += f["offered"] * w
        # snooping'siz L2 segmentlarda daraxtdan tashqari flood (qabul qiluvchisiz shoxlar ham yuklanadi)
        mc_flood = {}
        for g in mc_off:
            txs = [x for f in flows if f["app"].get("mcast") == g and f.get("ok") for x in f.get("tx") or []]
            tree = {key for (g2, key) in mc_seen if g2 == g}
            mc_flood[g] = [k_ for k_ in mc_flood_keys(n, txs, tree) if k_ in cap]
            for key in mc_flood[g]:
                load[key] += mc_off[g]
        # QoS: `prio` kabelida yuqori sinf (DSCP >= 40) birinchi xizmat qilinadi — oddiy sinf
        # faqat qolgan sig'imni oladi; fifo/fq_codel — umumiy (proporsional) kesish.
        def qdisc_of(key):
            return ((proj["links"][key[0]].get("params") or {}).get("qdisc") or "fifo") if key[0] != "air" else "fifo"
        load_hi = {k2: 0.0 for k2 in cap}
        for f in flows:
            if "offered" in f and dscp_class(f["app"].get("dscp")) == 0:
                for key, w in f["res"]:
                    load_hi[key] += f["offered"] * w
        scale = {k2: (min(1.0, cap[k2] / v) if v > 0 else 1.0) for k2, v in load.items()}
        scale_cls = {}
        for k2, v in load.items():
            if qdisc_of(k2) == "prio":
                hi, lo = load_hi[k2], v - load_hi[k2]
                s_hi = min(1.0, cap[k2] / hi) if hi > 0 else 1.0
                left = max(cap[k2] - hi * s_hi, 0.0)
                scale_cls[k2] = (s_hi, min(1.0, left / lo) if lo > 0 else 1.0)
        used = {k2: 0.0 for k2 in cap}
        used_hi = {k2: 0.0 for k2 in cap}
        mc_used = {}
        for f in flows:
            if "offered" in f:
                cls = dscp_class(f["app"].get("dscp"))
                s_min = min([scale_cls[key][cls] if key in scale_cls else scale[key] for key, _w in f["res"]] or [1.0])
                f["rate"] = f["offered"] * s_min
                f["overload_drop"] = 1 - s_min
                for key, w in f["res"]:
                    if f["app"].get("mcast"):      # bitta nusxa: shu kabeldagi eng katta tezlik
                        mk = (f["app"]["mcast"], key)
                        mc_used[mk] = max(mc_used.get(mk, 0.0), f["rate"] * w)
                        continue
                    used[key] += f["rate"] * w
                    if cls == 0:
                        used_hi[key] += f["rate"] * w
        for (g, key), r_ in mc_used.items():
            used[key] += r_
        for g, keys in mc_flood.items():
            for key in keys:
                used[key] += mc_off[g] * min(1.0, scale[key])
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
        # TCP navbati faqat TOR JOYDA (to'yingan hop) yig'iladi — yo'ldagi boshqa hoplarda emas
        tcp_on = {h for f in tcp for h in f["path"]
                  if f["rate"] > 0 and f["cap"] == float("inf") and used[h] >= 0.97 * cap[h]}
        air_sat = {k2[1] for k2 in cap if k2[0] == "air" and used[k2] >= 0.97}

        # 3) kechikish / yo'qotish
        def hop_delay(h, pkt_bits, cls=1):
            lid, _d = h
            p = proj["links"][lid].get("params") or {}
            c = cap[h]
            tx = pkt_bits / c
            rho = min(used[h] / c, 0.999)
            qmax = int(p.get("queue", 1000)) * 12000 / c
            qd = p.get("qdisc") or "fifo"
            if qd == "prio" and cls == 0:
                # yuqori sinf: faqat o'z sinfi navbati (M/M/1) + bitta oddiy paket qoldiq xizmati
                rho_h = min(used_hi[h] / c, 0.999)
                q = min(rho_h / (1 - rho_h) * tx, qmax) + (12000 / c if used[h] > used_hi[h] else 0)
                return _ms(p.get("delay", "1ms")) / 1000 + tx + q, q
            q_cap = 0.005 + tx if qd == "fq_codel" else float("inf")   # CoDel: navbat ~ target 5 ms'da
            if lid in wifi_of and wifi_of[lid][0] in air_sat:   # efir to'yingan — navbat o'sadi
                return _ms(p.get("delay", "1ms")) / 1000 + tx + 0.25 * qmax, 0.25 * qmax
            if scale.get(h, 1) < 1 or h in tcp_on:          # to'yingan: navbat to'la (UDP) / yarim (TCP)
                q = qmax if scale.get(h, 1) < 1 else 0.25 * qmax   # TCP: taxminiy turg'un navbat
            else:
                q = min(rho / (1 - rho) * tx, qmax)
            q = min(q, q_cap)
            return _ms(p.get("delay", "1ms")) / 1000 + tx + q, q

        for f in flows:
            a = f["app"]
            if not f.get("active"):
                continue
            pt = {"t": t1, "t_start": t0, "t_end": t1, "thr_bps": 0,
                  "delay_ms": None, "jitter_ms": None, "loss_pct": 100.0}
            if f["ok"]:
                bits = int(a.get("size", 1000 if a["type"] != "ping" else 64)) * 8 if a["type"] != "tcp" else 12000
                d_fwd, q_sum = 0.0, 0.0
                fcls = dscp_class(a.get("dscp"))
                for h in f["path"]:
                    dd, q = hop_delay(h, bits, fcls); d_fwd += dd; q_sum += q
                link_loss = 1 - math.prod(1 - float((proj["links"][lid].get("params") or {}).get("loss", 0)) / 100
                                          for lid, _d in f["path"])
                tot_loss = 1 - (1 - link_loss) * (1 - f.get("overload_drop", 0))
                thr = f["rate"] * (1 - link_loss) if a["type"] != "tcp" else f["rate"]
                pt.update(thr_bps=round(thr), delay_ms=round(d_fwd * 1000, 3),
                          jitter_ms=round(q_sum * 1000 * 0.15 + 0.01, 3),
                          loss_pct=round(tot_loss * 100 if a["type"] != "tcp" else link_loss * 100, 2))
                if a["type"] == "ping":
                    d_back = sum(hop_delay(h, bits, fcls)[0] for h in f["back"])
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
        if stp_on:      # STP port holatlari -> route_events (konvergensiya vaqti uchun)
            for x, b in n.stp()["bridges"].items():
                rtrack.update(t0, x, {f"port{p_} {v['role']}/{v['state']}" for p_, v in b["ports"].items()})
        for dv in node_series:
            rk = n.__dict__.setdefault("_rk_cache", {})
            if dv not in rk:
                rk[dv] = {route_key(r[0], r[1], r[2]) for r in n.routes(dv)}
            rts = rk[dv]
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
        if a["id"] in last_path:
            flows_out[a["id"]].update(path=last_path[a["id"]][0], why=last_path[a["id"]][1])
    return {"engine": "analytic", "engine_label": "Analitik (oqim modeli)",
            "ip_version": sc.get("ip_version", 4), "duration": duration,
            "flows": flows_out, "links": link_series, "events": applied,
            "nodes": node_series, "route_events": rtrack.events,
            "convergence": convergence(applied, rtrack.events, duration), "wall_s": round(time.time() - t_wall, 3),
            "notes": ["oqim darajasidagi taxmin: UDP qat'iy, TCP max-min adolat + Mathis, navbat M/M/1",
                      "dinamik routing: demo modeli; IPv4 OSPF startup network/passive interfeys siyosatiga amal qiladi",
                      "RIP: kabel uzilgandan keyin update/2 (rip_timers, standart 30 s -> 15 s) yoki rip_convergence_s davomida eski yo'llar",
                      ospf.note(),
                      "Wi-Fi: SNR→MCS (ns-3 ga kalibrlangan), efir vaqti bo'yicha adolat (A-MPDU), TCP ACK efiri ~9%",
                      "Wi-Fi: bir kanalda va bir-birini eshitadigan AP'lar umumiy efirni bo'lishadi; harakat — oraliq o'rtasidagi pozitsiya",
                      "Wi-Fi roaming: ESS (SSID+diapazon+kanal) ichida, joriy AP uzilgach 1s uzilish bilan eng kuchli AP'ga"]}

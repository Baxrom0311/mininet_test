"""Survivability / nosozlik ta'siri tahlili (OPNET "Failure Impact" / N-1) — analitik model.

Har bir YAKKA nosozlik (bitta kabel; bitta qurilma = uning barcha kabellari) uchun ssenariy
ilovalari BARQAROR holatda (protokollar yaqinlashgandan keyin) qayta hisoblanadi va asosiy
(nosozliksiz) holat bilan solishtiriladi:

  * uzilgan oqimlar (yo'l yo'q yoki 100% yo'qotish) — asosiy holatda ham ishlamagan oqimlar
    (masalan firewall bloklagan) "baseline_down" hisoblanadi, nosozlik aybi emas;
  * boshqa yo'lga o'tgan oqimlar (yo'l o'zgardi), o'tkazuvchanlik o'zgarishi, VoIP MOS o'zgarishi;
  * ortiqcha yuklangan kabellar (util >= 0.95) va UDP yo'qotishi > 1% bo'lgan oqimlar;
  * kritiklik bali -> reyting, "yagona nosozlik nuqtalari" (SPOF) ro'yxati;
  * N-1 sig'im rejasi: har kabel uchun eng yomon holatdagi taklif qilingan yuklama va
    "nosozlikka chidashi uchun kerakli tezlik" (yuklama / 0.8).

Marshrutlash, VLAN, NAT, firewall, STP, LAG, VRRP, BGP, multicast — barchasi analitik
dvigatel (lab.engines.analytic + demo_driver.Net) orqali, qayta yozilmaydi.

    python3 -m lab.survivability lab/examples/rip_ring.json uzilish
"""

import copy
import json
import sys
import time

from lab import apps as appmodels

L2_TYPES = ("switch", "p4switch", "wifiap")
HOST_TYPES = ("host", "server")
STEADY_S = 4.0            # barqaror holat uchun qisqa simulyatsiya (oxirgi soniya olinadi)
OVERLOAD_UTIL = 0.95
UDP_LOSS_PCT = 1.0
PLAN_TARGET_UTIL = 0.8


def steady_scenario(sc):
    """Barcha ilovalar t=0 dan ishlaydigan, hodisasiz qisqa ssenariy. Cheklangan TCP (FTP/HTTP)
    elastik oqimga aylantiriladi — barqaror o'tkazuvchanlikni solishtirish uchun."""
    base = {k: v for k, v in sc.items() if k not in ("events", "failures")}
    base["duration"] = STEADY_S
    base["events"] = []
    apps = []
    for a in sc.get("apps") or []:
        a2 = dict(a, start=0, stop=STEADY_S)
        if a2.get("type") == "http":          # sahifalar o'rniga bitta uzluksiz TCP
            a2 = {k: v for k, v in a2.items() if k not in ("page", "think")}
            a2.update(type="tcp", src=a["dst"], dst=a["src"])
        elif a2.get("type") == "ftp":
            down = a.get("direction", "download") == "download"
            a2 = {k: v for k, v in a2.items() if k not in ("file", "direction")}
            a2.update(type="tcp", src=a["dst"] if down else a["src"], dst=a["src"] if down else a["dst"])
        apps.append(a2)
    base["apps"] = apps
    return base


def _evaluate(project, sc_steady):
    """Analitik barqaror holat: {flows: {id: {...}}, links: {lid: {...}}, mos: {voip_id: mos}}."""
    from lab.engines import analytic
    exp = appmodels.expand(sc_steady)
    res = analytic.run(project, exp)
    voip_user = {**sc_steady, "apps": [a for a in sc_steady.get("apps") or [] if a.get("type") == "voip"]}
    if voip_user["apps"]:
        res = appmodels.postprocess(res, voip_user)
    flows = {}
    for fid, f in res["flows"].items():
        if f.get("type") == "voip" and not f.get("group"):
            continue                       # yoyilgan qismlar (up/down) alohida hisoblanadi
        pts = f.get("series") or []
        last = pts[-1] if pts else {}
        alive = bool(pts) and (last.get("loss_pct") is None or last.get("loss_pct") < 99.9) and (
            (last.get("thr_bps") or 0) > 0 or last.get("rtt_ms") is not None)
        flows[fid] = {"type": f.get("type"), "label": f.get("label"), "src": f.get("src"), "dst": f.get("dst"),
                      "group": f.get("group"), "thr_bps": last.get("thr_bps") or 0, "loss_pct": last.get("loss_pct"),
                      "delay_ms": last.get("delay_ms"), "rtt_ms": last.get("rtt_ms"), "alive": alive,
                      "path": [tuple(x) for x in (f.get("path") or [])], "why": f.get("why")}
    links = {}
    for lid, pts in (res.get("links") or {}).items():
        last = pts[-1] if pts else {}
        links[lid] = {"util": last.get("util", 0.0), "ab_bps": last.get("ab_bps", 0), "ba_bps": last.get("ba_bps", 0),
                      "drops": (last.get("drops_ab") or 0) + (last.get("drops_ba") or 0)}
    mos = {aid: (a.get("avg_mos")) for aid, a in (res.get("apps") or {}).items() if a.get("type") == "voip"}
    return {"flows": flows, "links": links, "mos": mos}


def _demand(f):
    """Oqim talabi (bit/s) — asosiy holatdagi o'tkazuvchanlik; ping uchun kichik vazn."""
    return max(f["thr_bps"], 1000.0 if f["type"] == "ping" else 0.0)


def _targets(project, targets, include_hosts=False):
    out = []
    if targets in ("links", "both"):
        out += [("link", lid) for lid in sorted(project["links"])]
    if targets in ("devices", "both"):
        for name, d in sorted(project["devices"].items(), key=lambda kv: kv[1].get("id", 0)):
            if d["type"] in HOST_TYPES and not include_hosts:
                continue
            out.append(("device", name))
    return out


def _fail(project, kind, name):
    p = copy.deepcopy(project)
    if kind == "link":
        p["links"][name]["up"] = False
        return p, [name]
    down = [lid for lid, l in p["links"].items() if name in (l["a"]["dev"], l["b"]["dev"])]
    for lid in down:
        p["links"][lid]["up"] = False
    return p, down


def _convergence_hint(project, down_links, sc):
    """Qaysi protokol tiklanishi kerak va taxminiy vaqti (s) — mavjud modullar parametrlaridan."""
    from lab.model import rip_timers
    hints = {}
    near = {project["links"][lid][s]["dev"] for lid in down_links for s in ("a", "b")}
    devs = project["devices"]
    for n in near:
        d = devs.get(n) or {}
        cfg = d.get("config") or {}
        dm = set(cfg.get("daemons") or [])
        if d.get("type") == "router":
            if "ripd" in dm and not (dm & {"ospfd", "isisd"}):
                hints["RIP"] = max(hints.get("RIP", 0), rip_timers(d)["update"] / 2)
            if "bgpd" in dm or cfg.get("bgp"):
                hints["BGP"] = max(hints.get("BGP", 0),
                                   float(sc.get("bgp_convergence_s") or 0) or 0.0)   # olib tashlash — darhol
            if cfg.get("vrrp"):
                from lab.model import parse_ifname
                from lab.vrrp import mdi
                hit = [e for e in cfg["vrrp"] for lid in down_links
                       if any(project["links"][lid][s_]["dev"] == n
                              and project["links"][lid][s_]["port"] == parse_ifname(e["iface"])[1] for s_ in ("a", "b"))]
                if hit:          # master'ning VRRP interfeysi uzildi -> backup Master_Down_Interval'dan keyin
                    hints["VRRP"] = max(hints.get("VRRP", 0), max(mdi(e, sc) for e in hit))
        if d.get("type") == "switch":
            from lab.stp import convergence_time, stp_enabled
            if stp_enabled(d):
                hints["STP"] = max(hints.get("STP", 0), convergence_time(project, n, down_links, sc))
    if any(lid in _lag_links(project) for lid in down_links):
        from lab.lag import failover_s
        hints["LAG"] = failover_s(sc)
    return {k: round(v, 2) for k, v in hints.items()}


def _lag_links(project):
    try:
        from lab.lag import groups
        return {lid for g in groups(project) for lid in g["links"]}
    except Exception:  # noqa: BLE001
        return set()


def _offered(ev, flows_base, tcp=False):
    """(kabel, yo'nalish) -> taklif qilingan yuklama (bit/s): ishlayotgan oqimlarning ASOSIY talabi
    ularning joriy yo'li bo'ylab. tcp=False — faqat inelastik (UDP/ping/VoIP/multicast) talab:
    sig'im rejasi shunga asoslanadi (elastik TCP har qanday sig'imni to'ldiradi); tcp=True — TCP."""
    out = {}
    for fid, f in ev["flows"].items():
        if not f["alive"] or fid not in flows_base or (f["type"] == "tcp") != tcp:
            continue
        dem = flows_base[fid]["thr_bps"]
        for lid, d in f["path"]:
            out[(lid, d)] = out.get((lid, d), 0.0) + dem
    return out


def analyze(project, scenario, targets="links", include_hosts=False, progress=None):
    """Survivability hisobot (JSON). scenario — ssenariy nomi yoki dict."""
    t_wall = time.time()
    sc = (project.get("scenarios") or {}).get(scenario) if isinstance(scenario, str) else scenario
    if not sc:
        raise ValueError(f"ssenariy '{scenario}' yo'q")
    from lab.model import normalize_link_params
    sc_st = steady_scenario(sc)
    base = _evaluate(project, sc_st)
    bflows = base["flows"]
    total_demand = sum(_demand(f) for f in bflows.values() if f["alive"]) or 1.0
    base_off = _offered(base, bflows)
    worst_off = dict(base_off)
    worst_tcp = dict(_offered(base, bflows, tcp=True))
    worst_case = {k: "baseline" for k in base_off}
    cases = []
    tl = _targets(project, targets, include_hosts)
    for i, (kind, name) in enumerate(tl):
        pf, down = _fail(project, kind, name)
        ev = _evaluate(pf, sc_st)
        broken, rerouted, degraded, udp_loss = [], [], [], []
        broken_demand = 0.0
        for fid, bf in bflows.items():
            f = ev["flows"].get(fid)
            if not bf["alive"] or f is None:
                continue
            if kind == "device" and name in (bf["src"], bf["dst"]):
                continue                      # qurilmaning o'zi manba/manzil — tarmoq aybi emas
            if not f["alive"]:
                broken.append({"flow": fid, "label": bf["label"], "why": f.get("why")})
                broken_demand += _demand(bf)
                continue
            if f["path"] != bf["path"]:
                rerouted.append(fid)
            if bf["thr_bps"] > 0:
                ch = (f["thr_bps"] - bf["thr_bps"]) / bf["thr_bps"]
                if ch < -0.05:
                    degraded.append({"flow": fid, "label": bf["label"], "thr_change_pct": round(ch * 100, 1),
                                     "thr_bps": f["thr_bps"], "base_thr_bps": bf["thr_bps"]})
            if f["type"] == "udp" and (f.get("loss_pct") or 0) > UDP_LOSS_PCT >= (bf.get("loss_pct") or 0):
                udp_loss.append({"flow": fid, "loss_pct": f["loss_pct"]})
        over = sorted(({"link": lid, "util": round(v["util"], 3)} for lid, v in ev["links"].items()
                       if lid not in down and v["util"] >= OVERLOAD_UTIL
                       and base["links"].get(lid, {}).get("util", 0) < OVERLOAD_UTIL),
                      key=lambda x: -x["util"])
        mos = {aid: {"base": base["mos"].get(aid), "fail": m} for aid, m in ev["mos"].items()
               if base["mos"].get(aid) is not None and m is not None and abs(m - base["mos"][aid]) >= 0.05}
        max_util = max((v["util"] for lid, v in ev["links"].items() if lid not in down), default=0.0)
        score = (100 * broken_demand / total_demand + 25 * len(over)
                 + sum(-x["thr_change_pct"] for x in degraded) * 0.2 + 5 * len(udp_loss)
                 + sum(max(v["base"] - v["fail"], 0) * 10 for v in mos.values()))
        cases.append({"kind": kind, "target": name, "down_links": down, "broken": broken,
                      "broken_demand_pct": round(100 * broken_demand / total_demand, 1),
                      "rerouted": sorted(rerouted), "degraded": degraded, "udp_loss": udp_loss,
                      "overloaded": over, "max_util": round(max_util, 3), "mos": mos,
                      "convergence": _convergence_hint(project, down, sc) if (rerouted or broken) else {},
                      "score": round(score, 1)})
        for k, v in _offered(ev, bflows).items():
            if v > worst_off.get(k, 0):
                worst_off[k], worst_case[k] = v, f"{kind}:{name}"
        for k, v in _offered(ev, bflows, tcp=True).items():
            worst_tcp[k] = max(worst_tcp.get(k, 0), v)
        if progress:
            progress((i + 1) / max(len(tl), 1))
    ranked = sorted(cases, key=lambda c: (-c["score"], c["target"]))
    spof = [{"kind": c["kind"], "target": c["target"], "broken": [b["flow"] for b in c["broken"]]}
            for c in ranked if c["broken"]]
    plan = []
    for lid, l in sorted(project["links"].items()):
        bw = normalize_link_params(l.get("params"))["bw"] * 1e6
        for d in ("ab", "ba"):
            k = (lid, d)
            b, w, wt = base_off.get(k, 0.0), worst_off.get(k, 0.0), worst_tcp.get(k, 0.0)
            if w <= 0 and wt <= 0:
                continue
            need = w / PLAN_TARGET_UTIL
            plan.append({"link": lid, "dir": d, "bw_mbps": round(bw / 1e6, 3),
                         "base_load_mbps": round(b / 1e6, 3), "worst_load_mbps": round(w / 1e6, 3),
                         "worst_case": worst_case.get(k) if w > 0 else None, "worst_util": round(w / bw, 3),
                         "worst_tcp_mbps": round(wt / 1e6, 3),
                         "need_mbps": round(need / 1e6, 2), "upgrade": need > bw})
    plan.sort(key=lambda x: -x["worst_util"])
    return {"scenario": scenario if isinstance(scenario, str) else sc.get("name"), "targets": targets,
            "baseline": {"flows": {k: {kk: v[kk] for kk in ("label", "type", "thr_bps", "loss_pct", "alive", "why")}
                                   for k, v in bflows.items()},
                         "down_flows": sorted(k for k, v in bflows.items() if not v["alive"]),
                         "links": {k: round(v["util"], 3) for k, v in base["links"].items()},
                         "mos": base["mos"]},
            "cases": ranked, "spof": spof,
            "capacity_plan": plan, "upgrades": [p for p in plan if p["upgrade"]],
            "wall_s": round(time.time() - t_wall, 2),
            "notes": ["barqaror holat (protokollar yaqinlashgandan keyin), analitik model",
                      "FTP/HTTP — elastik TCP sifatida; talab = asosiy holat o'tkazuvchanligi",
                      "sig'im rejasi faqat inelastik talab (UDP/ping/VoIP/multicast) bo'yicha; TCP — worst_tcp_mbps (ma'lumot)",
                      f"ortiqcha yuklama: util >= {OVERLOAD_UTIL}; sig'im rejasi maqsadi util <= {PLAN_TARGET_UTIL}",
                      "convergence — taxminiy tiklanish vaqti (RIP update/2, STP/RSTP, VRRP MDI, LAG failover)"]}


def format_table(rep):
    lines = [f"Survivability: {rep['scenario']} ({len(rep['cases'])} nosozlik, {rep['wall_s']} s)"]
    if rep["baseline"]["down_flows"]:
        lines.append(f"  asosiy holatda ishlamaydi: {', '.join(rep['baseline']['down_flows'])}")
    lines.append(f"  {'nishon':<14}{'ball':>7}  {'uzildi':<24}{'yo`l o`zgardi':<20}{'max util':>9}  tiklanish")
    for c in rep["cases"]:
        lines.append(f"  {c['kind'][0]}:{c['target']:<12}{c['score']:>7}  "
                     f"{','.join(b['flow'] for b in c['broken'])[:23]:<24}{','.join(c['rerouted'])[:19]:<20}"
                     f"{c['max_util']:>9}  {', '.join(f'{k} {v}s' for k, v in c['convergence'].items())}")
    if rep["upgrades"]:
        lines.append("  N-1 sig'im rejasi:")
        for p in rep["upgrades"]:
            lines.append(f"    {p['link']} {p['dir']}: {p['bw_mbps']} -> >= {p['need_mbps']} Mbit/s "
                         f"(eng yomon: {p['worst_case']}, yuklama {p['worst_load_mbps']} Mbit/s)")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("ishlatish: python3 -m lab.survivability <loyiha.json> <ssenariy> [links|devices|both] [--json]")
        sys.exit(2)
    with open(sys.argv[1]) as f:
        proj = json.load(f)
    tg = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "both"
    rep = analyze(proj, sys.argv[2], tg)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if "--json" in sys.argv else format_table(rep))

"""Ilova modellari (OPNET "Application Config"): VoIP, HTTP, FTP.

Dvigatellar faqat oddiy ilovalarni biladi (udp / tcp / ping; tcp `bytes` bilan —
cheklangan uzatish, natijada `done_t`). Bu modul yuqori darajali ilovalarni shu
oddiylarga YOYADI (expand) va natijadan ilova metrikalarini HISOBLAYDI (postprocess):

voip  {src, dst, codec: g711|g729, start, stop}
      -> ikki yo'nalishli UDP (RTP: 20 ms freym, 50 paket/s), metrika — MOS (ITU-T G.107
         E-model: R = 93.2 - Id(kechikish) - Ie_eff(yo'qotish); jitter bufer = 2*jitter).
http  {src: mijoz, dst: server, page: "500KB", think: 5, start, stop}
      -> har `think` soniyada bitta sahifa (server -> mijoz TCP, `page` bayt);
         metrika — sahifa javob vaqti (page_s), yuklangan/jami sahifalar.
ftp   {src: mijoz, dst: server, file: "20MB", direction: download|upload, start, stop}
      -> bitta TCP uzatish; metrika — yuklash vaqti (transfer_s), o'rtacha tezlik.
"""

import copy
import math
import re

CODECS = {
    # payload (bayt, 20 ms), Ie, Bpl (PLC bilan), kodek+paketlash kechikishi (ms)
    "g711": {"payload": 160, "ie": 0, "bpl": 25.1, "codec_ms": 20.0, "label": "G.711 (64 kbit/s)"},
    "g729": {"payload": 20, "ie": 11, "bpl": 19.0, "codec_ms": 35.0, "label": "G.729 (8 kbit/s)"},
}
RTP_HDR = 12
PPS = 50
MAX_PAGES = 60
MODEL_TYPES = ("voip", "http", "ftp", "mcast")


def parse_bytes(v):
    if isinstance(v, (int, float)):
        return int(v)
    m = re.match(r"^\s*([\d.]+)\s*([KMG]?)i?B?\s*$", str(v), re.I)
    if not m:
        raise ValueError(f"hajm noto'g'ri: {v!r} (masalan 500KB, 20MB)")
    return int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}[m.group(2).upper()])


def validate_app(a, dur):
    errs = []
    t = a.get("type")
    if t == "voip" and (a.get("codec") or "g711") not in CODECS:
        errs.append(f"kodek {tuple(CODECS)} dan biri")
    try:
        if t == "http":
            parse_bytes(a.get("page", "500KB"))
            if float(a.get("think", 5)) <= 0:
                errs.append("think > 0")
        if t == "ftp":
            parse_bytes(a.get("file", "10MB"))
            if a.get("direction", "download") not in ("download", "upload"):
                errs.append("direction: download | upload")
    except ValueError as e:
        errs.append(str(e))
    return errs


def mos_from(delay_ms, jitter_ms, loss_pct, codec="g711"):
    """ITU-T G.107 E-model (soddalashtirilgan) -> (R, MOS). delay_ms — bir tomonlama tarmoq kechikishi."""
    if delay_ms is None:
        return None, None
    c = CODECS.get(codec, CODECS["g711"])
    d = delay_ms + 2 * (jitter_ms or 0) + c["codec_ms"]          # og'izdan-quloqqa
    idd = 0.024 * d + (0.11 * (d - 177.3) if d > 177.3 else 0)
    ppl = max(loss_pct or 0, 0)
    ie_eff = c["ie"] + (95 - c["ie"]) * ppl / (ppl + c["bpl"])
    r = max(0.0, min(100.0, 93.2 - idd - ie_eff))
    mos = 1 + 0.035 * r + 7e-6 * r * (r - 60) * (100 - r) if r > 0 else 1.0
    return round(r, 1), round(max(1.0, min(4.5, mos)), 2)


def has_models(sc):
    return any(a.get("type") in MODEL_TYPES for a in sc.get("apps") or [])


def expand(sc):
    """Ssenariy nusxasi: voip/http/ftp -> udp/tcp oddiy ilovalar (`group`, `role` bilan)."""
    if not has_models(sc):
        return sc
    out = copy.deepcopy(sc)
    dur = float(sc.get("duration", 30))
    apps = []
    for i, a in enumerate(sc.get("apps") or []):
        aid = a.get("id") or f"a{i + 1}"
        t = a.get("type")
        st, sp = float(a.get("start", 1)), min(float(a.get("stop", dur)), dur)
        if t == "voip":
            c = CODECS[a.get("codec") or "g711"]
            size = c["payload"] + RTP_HDR
            rate = f"{size * 8 * PPS / 1e6:.4f}Mbps"
            for role, s_, d_ in (("up", a["src"], a["dst"]), ("down", a["dst"], a["src"])):
                apps.append({"id": f"{aid}.{role}", "type": "udp", "src": s_, "dst": d_, "rate": rate, "size": size,
                             "start": st, "stop": sp, "group": aid, "role": role,
                             "dscp": int(a["dscp"]) if a.get("dscp") not in (None, "") else 46,   # EF
                             **({"dport": int(a["dport"])} if a.get("dport") not in (None, "") else {}),
                             "label": f"{a.get('label') or aid} [{role}]"})
        elif t == "http":
            page = parse_bytes(a.get("page", "500KB"))
            think = float(a.get("think", 5))
            k, t0 = 0, st
            while t0 < sp - 1e-9 and k < MAX_PAGES:
                apps.append({"id": f"{aid}.p{k + 1}", "type": "tcp", "src": a["dst"], "dst": a["src"], "bytes": page,
                             **({"dscp": int(a["dscp"])} if a.get("dscp") not in (None, "") else {}),
                         **({"dport": int(a["dport"])} if a.get("dport") not in (None, "") else {}),
                             "start": round(t0, 3), "stop": sp, "group": aid, "role": "page",
                             "label": f"{a.get('label') or aid} sahifa {k + 1}"})
                k += 1
                t0 += think
        elif t == "ftp":
            size = parse_bytes(a.get("file", "10MB"))
            s_, d_ = (a["dst"], a["src"]) if a.get("direction", "download") == "download" else (a["src"], a["dst"])
            apps.append({"id": f"{aid}.xfer", "type": "tcp", "src": s_, "dst": d_, "bytes": size, "start": st, "stop": sp,
                         **({"dscp": int(a["dscp"])} if a.get("dscp") not in (None, "") else {}),
                         **({"dport": int(a["dport"])} if a.get("dport") not in (None, "") else {}),
                         "group": aid, "role": "xfer", "label": f"{a.get('label') or aid} [{a.get('direction', 'download')}]"})
        elif t == "mcast":
            from lab.mcast import receivers_of
            for r in receivers_of(a):
                apps.append({"id": f"{aid}.{r}", "type": "udp", "src": a["src"], "dst": r,
                             "rate": a.get("rate", "1Mbps"), "size": int(a.get("size", 1000)),
                             "start": st, "stop": sp, "group": aid, "role": "rcv",
                             "mcast": aid, "mgroup": a.get("group"),
                             **({"dscp": int(a["dscp"])} if a.get("dscp") not in (None, "") else {}),
                             "label": f"{a.get('label') or aid} → {r}"})
        else:
            apps.append(a)
    out["apps"] = apps
    return out


def _done_time(fl, nbytes):
    """Engine done_t bo'lmasa: soniyalik thr_bps yig'indisidan interpolyatsiya."""
    if fl.get("done_t") is not None:
        return fl["done_t"]
    need, cum, prev_t = nbytes * 8 * 0.98, 0.0, float(fl.get("start", 0))
    for p in fl.get("series") or []:
        add = (p.get("thr_bps") or 0) * max(p["t"] - prev_t, 0)
        if add > 0 and cum + add >= need:
            return prev_t + (need - cum) / add * (p["t"] - prev_t)
        cum += add
        prev_t = p["t"]
    return None


def _interval_fields(points):
    """Keep exact interval bounds when all aggregated constituents agree."""
    bounds = {(p.get("t_start"), p.get("t_end")) for p in points}
    if len(bounds) == 1:
        start, end = next(iter(bounds))
        if start is not None and end is not None:
            return {"t_start": start, "t_end": end}
    return {}


def postprocess(res, sc):
    """Natijaga ilova metrikalarini qo'shadi: flows[<ilova>] (type voip/http/ftp) + `apps` xulosasi."""
    if not has_models(sc) or not res or not res.get("flows"):
        return res
    flows = res["flows"]
    summary = {}
    for a in sc.get("apps") or []:
        aid, t = a.get("id"), a.get("type")
        if t not in MODEL_TYPES:
            continue
        parts = {k: f for k, f in flows.items() if f.get("group") == aid or k.startswith(aid + ".")}
        base = {"id": aid, "type": t, "src": a["src"], "dst": a["dst"], "label": a.get("label") or f"{t} {a['src']}→{a['dst']}",
                "start": float(a.get("start", 1)), "stop": float(a.get("stop", res.get("duration", 30))), "parts": sorted(parts)}
        if t == "voip":
            codec = a.get("codec") or "g711"
            up, dn = parts.get(f"{aid}.up"), parts.get(f"{aid}.down")
            series = []
            by_t = {}
            for role, f in (("up", up), ("down", dn)):
                for p in (f or {}).get("series") or []:
                    by_t.setdefault(p["t"], {})[role] = p
            for tt in sorted(by_t):
                pts = by_t[tt]
                if all(p.get("delay_ms") is None and p.get("loss_pct") is None for p in pts.values()):
                    continue          # shu soniyada paket yuborilmagan (boshlanish/tugash chekkasi)
                mos_d, r_d = {}, {}
                for role, p in pts.items():
                    r_d[role], mos_d[role] = mos_from(p.get("delay_ms"), p.get("jitter_ms"), p.get("loss_pct"), codec)
                vals = [m for m in mos_d.values() if m is not None]
                series.append({"t": tt, **_interval_fields(pts.values()),
                               "thr_bps": sum((p.get("thr_bps") or 0) for p in pts.values()),
                               "delay_ms": max((p.get("delay_ms") or 0) for p in pts.values()) if any(p.get("delay_ms") is not None for p in pts.values()) else None,
                               "jitter_ms": max((p.get("jitter_ms") or 0) for p in pts.values()),
                               "loss_pct": max((p.get("loss_pct") or 0) for p in pts.values()),
                               "mos": min(vals) if vals else 1.0, "mos_up": mos_d.get("up"), "mos_down": mos_d.get("down"),
                               "r_factor": min(v for v in r_d.values() if v is not None) if any(v is not None for v in r_d.values()) else 0})
            mos = [p["mos"] for p in series]
            sm = {"codec": CODECS[codec]["label"], "avg_mos": round(sum(mos) / len(mos), 2) if mos else None,
                  "min_mos": min(mos) if mos else None,
                  "avg_delay_ms": round(sum(p["delay_ms"] for p in series if p["delay_ms"] is not None) / max(1, sum(1 for p in series if p["delay_ms"] is not None)), 3),
                  "loss_pct": round(sum(p["loss_pct"] for p in series) / len(series), 2) if series else None,
                  "avg_thr_bps": round(sum(p["thr_bps"] for p in series) / len(series)) if series else 0}
            sm["quality"] = ("a'lo" if (sm["avg_mos"] or 0) >= 4.0 else "yaxshi" if (sm["avg_mos"] or 0) >= 3.6
                             else "qoniqarli" if (sm["avg_mos"] or 0) >= 3.1 else "yomon")
            flows[aid] = {**base, "series": series, "summary": sm}
        elif t == "mcast":
            per, by_t = {}, {}
            for k, f in parts.items():
                rcv = k.split(".", 1)[1]
                sm_ = f.get("summary") or {}
                per[rcv] = {"avg_thr_bps": sm_.get("avg_thr_bps"), "loss_pct": sm_.get("loss_pct"),
                            "avg_delay_ms": sm_.get("avg_delay_ms"), "avg_jitter_ms": sm_.get("avg_jitter_ms")}
                for p in f.get("series") or []:
                    by_t.setdefault(p["t"], []).append(p)
            series = []
            for tt in sorted(by_t):
                pts = by_t[tt]
                dl = [p["delay_ms"] for p in pts if p.get("delay_ms") is not None]
                ls = [p["loss_pct"] for p in pts if p.get("loss_pct") is not None]
                series.append({"t": tt, **_interval_fields(pts),
                               "thr_bps": round(sum(p.get("thr_bps") or 0 for p in pts) / len(pts)),
                               "delay_ms": max(dl) if dl else None, "loss_pct": round(sum(ls) / len(ls), 2) if ls else None,
                               "receivers_ok": sum(1 for p in pts if (p.get("loss_pct") if p.get("loss_pct") is not None else 100) < 50)})
            from lab.model import parse_rate
            want = parse_rate(a.get("rate", "1Mbps"))
            reached = [r for r, v in per.items() if (v["avg_thr_bps"] or 0) >= 0.5 * want]
            losses = [v["loss_pct"] for v in per.values() if v["loss_pct"] is not None]
            sm = {"group": a.get("group"), "receivers": len(per), "reached": len(reached),
                  "reached_list": sorted(reached), "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
                  "avg_thr_bps": round(sum((v["avg_thr_bps"] or 0) for v in per.values()) / len(per)) if per else 0,
                  "per_receiver": per}
            flows[aid] = {**base, "series": series, "summary": sm}
        elif t in ("http", "ftp"):
            nbytes = parse_bytes(a.get("page", "500KB") if t == "http" else a.get("file", "10MB"))
            items = []
            for k in sorted(parts, key=lambda x: float(parts[x].get("start", 0))):
                f = parts[k]
                dt = _done_time(f, nbytes)
                st = float(f.get("start", 0))
                items.append({"id": k, "start": st, "done_t": round(dt, 3) if dt is not None else None,
                              "time_s": round(dt - st, 3) if dt is not None else None})
            agg, interval_points = {}, {}
            for f in parts.values():
                for p in f.get("series") or []:
                    agg[p["t"]] = agg.get(p["t"], 0) + (p.get("thr_bps") or 0)
                    interval_points.setdefault(p["t"], []).append(p)
            series = [{"t": tt, **_interval_fields(interval_points[tt]), "thr_bps": v} for tt, v in sorted(agg.items())]
            done = [x["time_s"] for x in items if x["time_s"] is not None]
            if t == "http":
                for x in items:
                    pt = next((p for p in series if p["t"] >= (x["done_t"] or 1e18)), None)
                    if pt is not None:
                        pt["page_s"] = x["time_s"]
                sm = {"pages": len(items), "pages_done": len(done),
                      "avg_page_s": round(sum(done) / len(done), 3) if done else None,
                      "max_page_s": max(done) if done else None, "page_bytes": nbytes}
            else:
                sm = {"bytes": nbytes, "transfer_s": done[0] if done else None,
                      "avg_rate_bps": round(nbytes * 8 / done[0]) if done and done[0] > 0 else None,
                      "completed": bool(done)}
            flows[aid] = {**base, "series": series, "summary": sm, "items": items}
        summary[aid] = {"type": t, **flows[aid]["summary"]}
    for f in flows.values():
        if f.get("group"):
            f["hidden"] = True       # UI: yoyilgan qismlar (ixtiyoriy ko'rsatish)
    res["apps"] = summary
    return res


def mos_label(m):
    return "—" if m is None else f"{m:.2f}"


__all__ = ["CODECS", "MODEL_TYPES", "expand", "postprocess", "mos_from", "parse_bytes", "validate_app", "math"]

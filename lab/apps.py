"""Ilova modellari (OPNET "Application Config"): VoIP, HTTP, FTP, Video, HTTP/3.

Dvigatellar faqat oddiy ilovalarni biladi (udp / tcp / ping; tcp `bytes` bilan —
cheklangan uzatish, natijada `done_t`). Bu modul yuqori darajali ilovalarni shu
oddiylarga YOYADI (expand) va natijadan ilova metrikalarini HISOBLAYDI (postprocess):

voip   {src, dst, codec: g711|g729, start, stop}
       -> ikki yo'nalishli UDP (RTP: 20 ms freym, 50 paket/s), metrika — MOS (ITU-T G.107
          E-model: R = 93.2 - Id(kechikish) - Ie_eff(yo'qotish); jitter bufer = 2*jitter).
http   {src: mijoz, dst: server, page: "500KB", think: 5, start, stop}
       -> har `think` soniyada bitta sahifa (server -> mijoz TCP, `page` bayt);
          metrika — sahifa javob vaqti (page_s), yuklangan/jami sahifalar.
ftp    {src: mijoz, dst: server, file: "20MB", direction: download|upload, start, stop}
       -> bitta TCP uzatish; metrika — yuklash vaqti (transfer_s), o'rtacha tezlik.
video  {src: mijoz, dst: server, quality: 1080p|720p|480p|360p, target_buffer: 20}
       -> HLS/DASH Adaptiv Video Oqimi (ABR); metrika — bufer to'liqligi, stall (rebuffering)
          soni va davomiyligi, ITU-T P.1203 QoE bahosi (1.0 - 5.0).
http3  {src: mijoz, dst: server, streams: 4, rate: "4Mbps", start, stop}
       -> QUIC/UDP asosida multiplekslangan oqimlar va 0-RTT ulanish; metrika —
          agregatlangan o'tkazuvchanlik, kechikish va paket yo'qotish.
"""

import copy
from decimal import Decimal, InvalidOperation
import math
import re

from lab.validation_numbers import MAX_UINT64, number

CODECS = {
    # payload (bayt, 20 ms), Ie, Bpl (PLC bilan), kodek+paketlash kechikishi (ms)
    "g711": {"payload": 160, "ie": 0, "bpl": 25.1, "codec_ms": 20.0, "label": "G.711 (64 kbit/s)"},
    "g729": {"payload": 20, "ie": 11, "bpl": 19.0, "codec_ms": 35.0, "label": "G.729 (8 kbit/s)"},
}
RTP_HDR = 12
PPS = 50
MAX_PAGES = 60
MODEL_TYPES = ("voip", "http", "ftp", "mcast", "video", "http3")

# OPNET-Grade Video (HLS/DASH) ABR Pog'onalari
VIDEO_LADDER = (
    {"quality": "1080p", "bitrate_bps": 5_000_000, "width": 1920, "height": 1080, "fps": 30, "p1203_base": 4.8},
    {"quality": "720p", "bitrate_bps": 2_500_000, "width": 1280, "height": 720, "fps": 30, "p1203_base": 4.0},
    {"quality": "480p", "bitrate_bps": 1_200_000, "width": 854, "height": 480, "fps": 30, "p1203_base": 3.2},
    {"quality": "360p", "bitrate_bps": 600_000, "width": 640, "height": 360, "fps": 30, "p1203_base": 2.3},
)
VIDEO_QUALITIES = tuple(item["quality"] for item in VIDEO_LADDER)
VIDEO_PROFILES = {item["quality"]: item for item in VIDEO_LADDER}


def calculate_p1203(quality_time_or_profile=None, total_duration=30.0, stall_count=0, total_stall_sec=0.0, quality_switches=0):
    """ITU-T P.1203 QoE balli hisobi (1.0 dan 5.0 gacha).

    quality_time_or_profile: profil nomi ('1080p', '720p', ...), {quality: duration_s} lug'ati,
    yoki o'rtacha bitrate (bps).
    total_duration: umumiy ko'rish vaqti (soniya).
    stall_count: qayta buferlash (qotish) soni.
    total_stall_sec: jami qotish vaqti (soniya).
    quality_switches: sifat o'zgarishlari soni.
    """
    if isinstance(quality_time_or_profile, dict):
        total_time = sum(quality_time_or_profile.values())
        if total_time > 0:
            base_qoe = sum(VIDEO_PROFILES.get(q, VIDEO_PROFILES["720p"])["p1203_base"] * (t / total_time)
                           for q, t in quality_time_or_profile.items())
        else:
            base_qoe = 4.0
    elif isinstance(quality_time_or_profile, str) and quality_time_or_profile in VIDEO_PROFILES:
        base_qoe = VIDEO_PROFILES[quality_time_or_profile]["p1203_base"]
    elif isinstance(quality_time_or_profile, (int, float)):
        bps = float(quality_time_or_profile)
        if bps >= 4_500_000:
            base_qoe = 4.8
        elif bps >= 2_000_000:
            base_qoe = 4.0
        elif bps >= 1_000_000:
            base_qoe = 3.2
        else:
            base_qoe = 2.3
    else:
        base_qoe = 4.0

    dur = max(float(total_duration or 1.0), 1.0)
    stall_ratio = max(0.0, float(total_stall_sec)) / dur
    stall_impairment = 1.8 * math.log(1.0 + max(0.0, float(total_stall_sec))) + 0.25 * max(0, int(stall_count)) + 1.5 * stall_ratio
    switch_impairment = 0.04 * max(0, int(quality_switches))

    score = base_qoe - stall_impairment - switch_impairment
    return round(max(1.0, min(5.0, score)), 2)


def parse_bytes(v):
    """Return an exact positive byte count; legacy KiB/MiB/GiB are decimal units."""
    message = f"hajm 1..{MAX_UINT64} butun bayt bo'lsin (masalan 500KB, 20MB)"
    try:
        if isinstance(v, bool):
            raise ValueError(message)
        if isinstance(v, int):
            value = Decimal(v)
        elif isinstance(v, float):
            value = Decimal(str(v))
        elif isinstance(v, str):
            m = re.fullmatch(r"\s*([\d.]+)\s*([KMG]?)i?B?\s*", v, re.I)
            if not m:
                raise ValueError(message)
            value = Decimal(m.group(1))
            # Construct the scaled decimal directly, without rounding through the
            # Decimal context (or a float) before checking the integer boundary.
            sign, digits, exponent = value.as_tuple()
            exponent += {"": 0, "K": 3, "M": 6, "G": 9}[m.group(2).upper()]
            value = Decimal((sign, digits, exponent))
        else:
            raise ValueError(message)
        if not value.is_finite() or not 0 < value <= MAX_UINT64 or value != value.to_integral_value():
            raise ValueError(message)
        return int(value)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise ValueError(message) from None


def validate_app(a, dur):
    if not isinstance(a, dict):
        return ["ilova obyekt bo'lsin"]
    errs = []
    t = a.get("type")
    if t == "voip":
        codec = a.get("codec")
        if codec is None or codec == "":
            codec = "g711"
        if not isinstance(codec, str) or codec not in CODECS:
            errs.append(f"kodek {tuple(CODECS)} dan biri")
    if t == "http":
        try:
            parse_bytes(a.get("page", "500KB"))
        except ValueError as e:
            errs.append(str(e))
        try:
            if number(a.get("think", 5), "think") <= 0:
                raise ValueError("think > 0")
        except ValueError as e:
            errs.append(str(e))
    if t == "ftp":
        try:
            parse_bytes(a.get("file", "10MB"))
        except ValueError as e:
            errs.append(str(e))
        if a.get("direction", "download") not in ("download", "upload"):
            errs.append("direction: download | upload")
    if t == "video":
        quality = a.get("quality") or a.get("initial_quality")
        if quality is not None and quality != "" and quality not in VIDEO_PROFILES:
            errs.append(f"video sifati {VIDEO_QUALITIES} dan biri bo'lsin")
        if a.get("target_buffer") is not None:
            try:
                if number(a.get("target_buffer"), "target_buffer") <= 0:
                    raise ValueError("target_buffer > 0")
            except ValueError as e:
                errs.append(str(e))
        if a.get("segment_duration") is not None:
            try:
                if number(a.get("segment_duration"), "segment_duration") <= 0:
                    raise ValueError("segment_duration > 0")
            except ValueError as e:
                errs.append(str(e))
    if t == "http3":
        if a.get("streams") is not None:
            try:
                s_val = int(a.get("streams"))
                if s_val < 1 or s_val > 128:
                    raise ValueError("streams 1..128 oralig'ida bo'lsin")
            except (ValueError, TypeError):
                errs.append("streams butun son (1..128) bo'lsin")
        if a.get("page") is not None and a.get("page") != "":
            try:
                parse_bytes(a.get("page"))
            except ValueError as e:
                errs.append(str(e))
        if a.get("rate") is not None and a.get("rate") != "":
            from lab.model import parse_rate
            try:
                if parse_rate(a.get("rate")) < 1:
                    errs.append("rate >= 1 bit/s bo'lsin")
            except Exception as e:
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
    """Ssenariy nusxasi: voip/http/ftp/video/http3 -> udp/tcp oddiy ilovalar (`group`, `role` bilan)."""
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
        elif t == "video":
            s_, d_ = (a["dst"], a["src"]) if a.get("direction", "download") == "download" else (a["src"], a["dst"])
            apps.append({
                "id": f"{aid}.stream",
                "type": "tcp",
                "src": s_,
                "dst": d_,
                "start": st,
                "stop": sp,
                "group": aid,
                "role": "video",
                **({"rate": a["rate"]} if a.get("rate") not in (None, "") else {}),
                **({"dscp": int(a["dscp"])} if a.get("dscp") not in (None, "") else {}),
                **({"dport": int(a["dport"])} if a.get("dport") not in (None, "") else {}),
                "label": f"{a.get('label') or aid} [video]"
            })
        elif t == "http3":
            num_streams = int(a.get("streams") or 4)
            num_streams = max(1, min(num_streams, 64))
            total_rate = a.get("rate") or "4Mbps"
            from lab.model import parse_rate
            try:
                total_bps = parse_rate(total_rate)
            except Exception:
                total_bps = 4_000_000
            stream_bps = max(1000, int(total_bps / num_streams))
            stream_rate = f"{stream_bps / 1e6:.4f}Mbps" if stream_bps >= 1_000_000 else f"{stream_bps / 1e3:.2f}kbps"
            packet_size = int(a.get("size") or 1250)
            packet_size = max(64, min(packet_size, 1450))

            s_, d_ = (a["dst"], a["src"]) if a.get("direction", "download") == "download" else (a["src"], a["dst"])
            for s_idx in range(1, num_streams + 1):
                apps.append({
                    "id": f"{aid}.s{s_idx}",
                    "type": "udp",
                    "src": s_,
                    "dst": d_,
                    "rate": stream_rate,
                    "size": packet_size,
                    "start": st,   # 0-RTT: darhol boshlanadi
                    "stop": sp,
                    "group": aid,
                    "role": f"quic_s{s_idx}",
                    "quic_stream_id": s_idx,
                    "zero_rtt": True,
                    **({"dscp": int(a["dscp"])} if a.get("dscp") not in (None, "") else {}),
                    **({"dport": int(a["dport"])} if a.get("dport") not in (None, "") else {}),
                    "label": f"{a.get('label') or aid} [QUIC s{s_idx}]"
                })
        else:
            apps.append(a)
    out["apps"] = apps
    return out


def _explicit_intervals(points):
    """Validate exact intervals; absence means legacy timestamp-only samples."""
    if not any("t_start" in p or "t_end" in p for p in points):
        return None
    intervals = []
    for point in points:
        start, end = point.get("t_start"), point.get("t_end")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end):
            raise ValueError("invalid application sample interval")
        intervals.append((start, end, point))
    intervals.sort(key=lambda item: item[0])
    if any(a[1] > b[0] + 1e-9 for a, b in zip(intervals, intervals[1:])):
        raise ValueError("duplicate or overlapping application sample intervals")
    return intervals


def _weighted_summary(points, fields):
    """Duration-weight explicit samples and retain arithmetic legacy summaries."""
    intervals = _explicit_intervals(points)
    weighted = [(end - start, point) for start, end, point in intervals] if intervals is not None else [(1., p) for p in points]
    summary = {}
    for name, (field, digits, default) in fields.items():
        present = [(weight, p[field]) for weight, p in weighted if p.get(field) is not None]
        total = math.fsum(weight for weight, value in present)
        value = math.fsum(value * (weight / total) for weight, value in present) if total else None
        summary[name] = (round(value, digits) if digits else round(value)) if value is not None else default
    return summary


def _done_time(fl, nbytes):
    """Interpolate transfer completion over observed intervals, or legacy timestamp deltas."""
    if fl.get("done_t") is not None:
        return fl["done_t"]
    intervals = _explicit_intervals(fl.get("series") or [])
    if intervals is not None:
        need, cum = nbytes * 8 * .98, 0.
        for start, end, point in intervals:
            start, end = max(start, float(fl.get("start", 0))), min(end, float(fl.get("stop", end)))
            if end <= start:
                continue
            rate = point.get("thr_bps") or 0
            add = rate * (end - start)
            if add > 0 and cum + add >= need:
                return start + (need - cum) / rate
            cum += add
        return None
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
    if any(start is not None or end is not None for start, end in bounds):
        raise ValueError("aggregated application sample intervals do not agree")
    return {}


def postprocess(res, sc):
    """Natijaga ilova metrikalarini qo'shadi: flows[<ilova>] (type voip/http/ftp) + `apps` xulosasi."""
    if not has_models(sc) or not res or not res.get("flows"):
        return res
    flows = res["flows"]
    summary = {}
    for i, a in enumerate(sc.get("apps") or []):
        aid, t = a.get("id") or f"a{i + 1}", a.get("type")
        if t not in MODEL_TYPES:
            continue
        parts = {k: f for k, f in flows.items() if f.get("group") == aid or k.startswith(aid + ".")}
        dst = a.get("dst") if a.get("dst") is not None else a.get("receivers")
        base = {"id": aid, "type": t, "src": a["src"], "dst": dst, "label": a.get("label") or f"{t} {a['src']}→{dst}",
                "start": float(a.get("start", 1)), "stop": float(a.get("stop", res.get("duration", 30))), "parts": sorted(parts)}
        if t == "voip":
            codec = a.get("codec") or "g711"
            up, dn = parts.get(f"{aid}.up"), parts.get(f"{aid}.down")
            series = []
            by_t = {}
            for role, f in (("up", up), ("down", dn)):
                _explicit_intervals((f or {}).get("series") or [])
                for p in (f or {}).get("series") or []:
                    by_t.setdefault(p.get("t_end", p["t"]), {})[role] = p
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
            sm = {"codec": CODECS[codec]["label"], "min_mos": min(mos) if mos else None,
                  **_weighted_summary(series, {"avg_mos": ("mos", 2, None),
                      "avg_delay_ms": ("delay_ms", 3, 0), "loss_pct": ("loss_pct", 2, None),
                      "avg_thr_bps": ("thr_bps", 0, 0)})}
            sm["quality"] = ("a'lo" if (sm["avg_mos"] or 0) >= 4.0 else "yaxshi" if (sm["avg_mos"] or 0) >= 3.6
                             else "qoniqarli" if (sm["avg_mos"] or 0) >= 3.1 else "yomon")
            flows[aid] = {**base, "series": series, "summary": sm}
        elif t == "mcast":
            per, by_t = {}, {}
            for k, f in parts.items():
                rcv = k.split(".", 1)[1]
                sm_ = f.get("summary") or {}
                if _explicit_intervals(f.get("series") or []) is not None:
                    sm_ = _weighted_summary(f["series"], {"avg_thr_bps": ("thr_bps", 0, 0),
                        "loss_pct": ("loss_pct", 2, None), "avg_delay_ms": ("delay_ms", 3, None),
                        "avg_jitter_ms": ("jitter_ms", 3, None)})
                per[rcv] = {"avg_thr_bps": sm_.get("avg_thr_bps"), "loss_pct": sm_.get("loss_pct"),
                            "avg_delay_ms": sm_.get("avg_delay_ms"), "avg_jitter_ms": sm_.get("avg_jitter_ms")}
                for p in f.get("series") or []:
                    by_t.setdefault(p.get("t_end", p["t"]), []).append(p)
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
                _explicit_intervals(f.get("series") or [])
                for p in f.get("series") or []:
                    end = p.get("t_end", p["t"])
                    agg[end] = agg.get(end, 0) + (p.get("thr_bps") or 0)
                    interval_points.setdefault(end, []).append(p)
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
        elif t == "video":
            target_buf = float(a.get("target_buffer", 20.0))
            init_buf = float(a.get("initial_buffer", 4.0))
            by_t = {}
            for k, f in parts.items():
                _explicit_intervals((f or {}).get("series") or [])
                for p in (f or {}).get("series") or []:
                    by_t.setdefault(p.get("t_end", p["t"]), []).append(p)

            series = []
            stalls = []
            quality_time = {q: 0.0 for q in VIDEO_QUALITIES}
            cur_q = a.get("quality", "720p")
            if cur_q not in VIDEO_PROFILES:
                cur_q = "720p"
            buf_sec = init_buf
            stall_cnt = 0
            tot_stall_sec = 0.0
            switches = 0
            prev_t = float(a.get("start", 0))

            if by_t:
                for tt in sorted(by_t):
                    pts = by_t[tt]
                    meas_bw = sum((p.get("thr_bps") or 0) for p in pts)
                    dl = [p["delay_ms"] for p in pts if p.get("delay_ms") is not None]
                    jt = [p["jitter_ms"] for p in pts if p.get("jitter_ms") is not None]
                    ls = [p["loss_pct"] for p in pts if p.get("loss_pct") is not None]

                    bounds = [(p.get("t_start"), p.get("t_end")) for p in pts if p.get("t_start") is not None and p.get("t_end") is not None]
                    if bounds:
                        dt = max(0.01, bounds[0][1] - bounds[0][0])
                    else:
                        dt = max(0.01, tt - prev_t) if tt > prev_t else 1.0
                    prev_t = tt

                    # ABR adaptatsiya (throughput + buffer gibrid qoidasi)
                    safe_bw = meas_bw * 0.85
                    old_q = cur_q
                    if buf_sec < 2.0:
                        cur_q = "360p"
                    elif buf_sec < 4.0:
                        cur_idx = [i for i, x in enumerate(VIDEO_LADDER) if x["quality"] == cur_q][0]
                        cur_q = VIDEO_LADDER[min(len(VIDEO_LADDER) - 1, cur_idx + 1)]["quality"]
                    elif buf_sec >= 8.0:
                        cand = "360p"
                        for item in VIDEO_LADDER:
                            if safe_bw >= item["bitrate_bps"]:
                                cand = item["quality"]
                                break
                        cur_q = cand
                    else:
                        if safe_bw < VIDEO_PROFILES[cur_q]["bitrate_bps"]:
                            cur_idx = [i for i, x in enumerate(VIDEO_LADDER) if x["quality"] == cur_q][0]
                            cur_q = VIDEO_LADDER[min(len(VIDEO_LADDER) - 1, cur_idx + 1)]["quality"]

                    if cur_q != old_q:
                        switches += 1

                    # Bufer to'liqligi dinamikasi
                    bitrate = VIDEO_PROFILES[cur_q]["bitrate_bps"]
                    sec_added = (meas_bw / bitrate) * dt if bitrate > 0 else 0.0
                    sec_played = dt
                    next_buf = buf_sec + sec_added - sec_played

                    if next_buf <= 0:
                        st_dur = abs(next_buf)
                        stall_cnt += 1
                        tot_stall_sec += st_dur
                        buf_sec = 0.0
                        stalls.append({"t": round(tt, 3), "duration_s": round(st_dur, 3), "quality": cur_q})
                        is_stall = True
                    else:
                        st_dur = 0.0
                        buf_sec = min(target_buf, next_buf)
                        is_stall = False

                    quality_time[cur_q] += dt
                    series.append({
                        "t": tt,
                        **_interval_fields(pts),
                        "thr_bps": meas_bw,
                        "delay_ms": max(dl) if dl else None,
                        "jitter_ms": max(jt) if jt else 0.0,
                        "loss_pct": round(sum(ls) / len(ls), 2) if ls else 0.0,
                        "quality": cur_q,
                        "bitrate_bps": bitrate,
                        "resolution": f"{VIDEO_PROFILES[cur_q]['width']}x{VIDEO_PROFILES[cur_q]['height']}",
                        "buffer_sec": round(buf_sec, 2),
                        "stall": is_stall,
                        "stall_dur": round(st_dur, 3)
                    })
            else:
                quality_time[cur_q] = max(1.0, float(base["stop"] - base["start"]))

            tot_dur = max(sum(quality_time.values()), float(base["stop"] - base["start"]), 1.0)
            qoe = calculate_p1203(quality_time, tot_dur, stall_cnt, tot_stall_sec, switches)
            sm = {
                "qoe_score": qoe,
                "mos": qoe,
                "stall_count": stall_cnt,
                "total_stall_sec": round(tot_stall_sec, 3),
                "stall_ratio": round(tot_stall_sec / tot_dur, 4) if tot_dur > 0 else 0.0,
                "quality_switches": switches,
                "final_quality": cur_q,
                "avg_buffer_sec": round(sum(p["buffer_sec"] for p in series) / len(series), 2) if series else round(init_buf, 2),
                "avg_bitrate_bps": round(sum(p["bitrate_bps"] for p in series) / len(series)) if series else VIDEO_PROFILES[cur_q]["bitrate_bps"],
                "quality_distribution": {q: round(t_ / tot_dur * 100, 1) for q, t_ in quality_time.items()},
                "quality": ("a'lo" if qoe >= 4.2 else "yaxshi" if qoe >= 3.6 else "qoniqarli" if qoe >= 3.0 else "yomon")
            }
            flows[aid] = {**base, "series": series, "summary": sm, "stalls": stalls}
        elif t == "http3":
            by_t = {}
            for k, f in parts.items():
                _explicit_intervals((f or {}).get("series") or [])
                for p in (f or {}).get("series") or []:
                    by_t.setdefault(p.get("t_end", p["t"]), []).append(p)
            series = []
            for tt in sorted(by_t):
                pts = by_t[tt]
                dl = [p["delay_ms"] for p in pts if p.get("delay_ms") is not None]
                jt = [p["jitter_ms"] for p in pts if p.get("jitter_ms") is not None]
                ls = [p["loss_pct"] for p in pts if p.get("loss_pct") is not None]
                series.append({
                    "t": tt,
                    **_interval_fields(pts),
                    "thr_bps": sum((p.get("thr_bps") or 0) for p in pts),
                    "delay_ms": round(sum(dl) / len(dl), 3) if dl else None,
                    "jitter_ms": round(max(jt), 3) if jt else 0.0,
                    "loss_pct": round(sum(ls) / len(ls), 2) if ls else 0.0,
                    "streams_active": len(pts),
                })
            thrs = [p["thr_bps"] for p in series]
            delays = [p["delay_ms"] for p in series if p["delay_ms"] is not None]
            jitters = [p["jitter_ms"] for p in series]
            losses = [p["loss_pct"] for p in series]
            sm = {
                "protocol": "HTTP/3 (QUIC/UDP)",
                "streams": len(parts),
                "zero_rtt": True,
                "multiplexed": True,
                "avg_thr_bps": round(sum(thrs) / len(thrs)) if thrs else 0,
                "max_thr_bps": max(thrs) if thrs else 0,
                "avg_delay_ms": round(sum(delays) / len(delays), 3) if delays else None,
                "avg_jitter_ms": round(sum(jitters) / len(jitters), 2) if jitters else 0.0,
                "loss_pct": round(sum(losses) / len(losses), 2) if losses else 0.0,
            }
            flows[aid] = {**base, "series": series, "summary": sm}
        summary[aid] = {"type": t, **flows[aid]["summary"]}
    for f in flows.values():
        if f.get("group"):
            f["hidden"] = True       # UI: yoyilgan qismlar (ixtiyoriy ko'rsatish)
    res["apps"] = summary
    return res


def evaluate_sla(run, sla_spec=None):
    """SLA talablarini tekshirish va muvofiqlik tahlili (SLA compliance engine).

    Parametrlar:
      run: natija lug'ati (res, run_obj) - flows va apps metrikalarini o'z ichiga oladi.
      sla_spec: SLA talablari (min_throughput_bps, max_delay_ms, max_jitter_ms,
                max_loss_pct, min_mos, target_compliance_pct).

    Qaytaradi:
      {
          "compliance_pct": float,
          "total_violations": int,
          "total_evaluations": int,
          "compliant_evaluations": int,
          "is_compliant": bool,
          "thresholds": dict,
          "violations": list,
          "summary_by_metric": dict,
          "summary_by_flow": dict,
      }
    """
    if not isinstance(run, dict):
        run = {}

    spec = sla_spec if isinstance(sla_spec, dict) else {}
    if "thresholds" in spec and isinstance(spec["thresholds"], dict):
        spec = {**spec, **spec["thresholds"]}
    if "rules" in spec and isinstance(spec["rules"], dict):
        spec = {**spec, **spec["rules"]}

    def _get_float(keys):
        for k in keys:
            if k in spec and spec[k] not in (None, ""):
                try:
                    return float(spec[k])
                except (ValueError, TypeError):
                    pass
        return None

    min_thr = None
    for k in ("min_throughput_bps", "min_throughput", "min_thr_bps", "min_rate"):
        if k in spec and spec[k] not in (None, ""):
            v = spec[k]
            if isinstance(v, (int, float)):
                min_thr = float(v)
                break
            elif isinstance(v, str):
                from lab.model import parse_rate
                try:
                    min_thr = float(parse_rate(v))
                    break
                except Exception:
                    try:
                        min_thr = float(v)
                        break
                    except Exception:
                        pass

    max_delay = _get_float(("max_delay_ms", "max_delay", "max_rtt_ms"))
    max_jitter = _get_float(("max_jitter_ms", "max_jitter"))
    max_loss = _get_float(("max_loss_pct", "max_loss"))
    min_mos = _get_float(("min_mos", "min_qoe"))

    # Standart OPNET SLA qiymatlari (agar birorta chegara berilmagan bo'lsa)
    if (min_thr is None and max_delay is None and max_jitter is None
            and max_loss is None and min_mos is None):
        min_thr = 1_000_000.0
        max_delay = 50.0
        max_jitter = 10.0
        max_loss = 1.0
        min_mos = 4.0

    target_compliance = _get_float(("target_compliance_pct", "target_compliance")) or 99.0

    thresholds = {
        "min_throughput_bps": min_thr,
        "max_delay_ms": max_delay,
        "max_jitter_ms": max_jitter,
        "max_loss_pct": max_loss,
        "min_mos": min_mos,
    }

    violations = []
    total_evals = 0
    compliant_evals = 0

    metric_stats = {m: {"evaluations": 0, "violations": 0}
                    for m in ("throughput", "delay", "jitter", "loss", "mos")}
    flow_stats = {}

    def _record(flow_id, t, metric_category, metric_name, val, thresh, condition, is_violation, sev="warning"):
        nonlocal total_evals, compliant_evals
        total_evals += 1
        metric_stats[metric_category]["evaluations"] += 1
        f_entry = flow_stats.setdefault(flow_id, {"evaluations": 0, "violations": 0})
        f_entry["evaluations"] += 1

        if is_violation:
            metric_stats[metric_category]["violations"] += 1
            f_entry["violations"] += 1
            violations.append({
                "timestamp": round(float(t), 3) if t is not None else 0.0,
                "flow_id": str(flow_id),
                "metric": metric_name,
                "value": round(float(val), 3) if isinstance(val, float) else val,
                "threshold": round(float(thresh), 3) if isinstance(thresh, float) else thresh,
                "condition": condition,
                "severity": sev,
                "message": f"Oqim '{flow_id}' {metric_name}={val} SLA chegarasidan buzildi ({condition} {thresh})",
            })
        else:
            compliant_evals += 1

    flows = run.get("flows") or {}
    evaluated_flows_with_series = set()

    for fid, fl in flows.items():
        if not isinstance(fl, dict):
            continue
        series = fl.get("series") or []
        if series:
            evaluated_flows_with_series.add(fid)
            for p in series:
                t = p.get("t_end", p.get("t", 0.0))

                # Throughput
                if min_thr is not None and p.get("thr_bps") is not None:
                    val = float(p["thr_bps"])
                    viol = val < min_thr
                    sev = "critical" if val < min_thr * 0.5 else "warning"
                    _record(fid, t, "throughput", "throughput_bps", val, min_thr, ">=", viol, sev)

                # Delay
                if max_delay is not None and p.get("delay_ms") is not None:
                    val = float(p["delay_ms"])
                    viol = val > max_delay
                    sev = "critical" if val > max_delay * 2.0 else "warning"
                    _record(fid, t, "delay", "delay_ms", val, max_delay, "<=", viol, sev)

                # Jitter
                if max_jitter is not None and p.get("jitter_ms") is not None:
                    val = float(p["jitter_ms"])
                    viol = val > max_jitter
                    sev = "critical" if val > max_jitter * 2.0 else "warning"
                    _record(fid, t, "jitter", "jitter_ms", val, max_jitter, "<=", viol, sev)

                # Loss
                if max_loss is not None and p.get("loss_pct") is not None:
                    val = float(p["loss_pct"])
                    viol = val > max_loss
                    sev = "critical" if val > max_loss * 3.0 or val > 10.0 else "warning"
                    _record(fid, t, "loss", "loss_pct", val, max_loss, "<=", viol, sev)

                # MOS / QoE
                if min_mos is not None:
                    mos_val = p.get("mos") if p.get("mos") is not None else p.get("qoe_score")
                    if mos_val is not None:
                        val = float(mos_val)
                        viol = val < min_mos
                        sev = "critical" if val < 3.0 else "warning"
                        _record(fid, t, "mos", "mos", val, min_mos, ">=", viol, sev)

    # Check flows with only summary
    for fid, fl in flows.items():
        if not isinstance(fl, dict) or fid in evaluated_flows_with_series:
            continue
        sm = fl.get("summary") or {}
        t = fl.get("start", 0.0)

        if min_thr is not None and sm.get("avg_thr_bps") is not None:
            val = float(sm["avg_thr_bps"])
            viol = val < min_thr
            sev = "critical" if val < min_thr * 0.5 else "warning"
            _record(fid, t, "throughput", "throughput_bps", val, min_thr, ">=", viol, sev)

        if max_delay is not None and sm.get("avg_delay_ms") is not None:
            val = float(sm["avg_delay_ms"])
            viol = val > max_delay
            sev = "critical" if val > max_delay * 2.0 else "warning"
            _record(fid, t, "delay", "delay_ms", val, max_delay, "<=", viol, sev)

        if max_jitter is not None and sm.get("avg_jitter_ms") is not None:
            val = float(sm["avg_jitter_ms"])
            viol = val > max_jitter
            sev = "critical" if val > max_jitter * 2.0 else "warning"
            _record(fid, t, "jitter", "jitter_ms", val, max_jitter, "<=", viol, sev)

        if max_loss is not None and sm.get("loss_pct") is not None:
            val = float(sm["loss_pct"])
            viol = val > max_loss
            sev = "critical" if val > max_loss * 3.0 else "warning"
            _record(fid, t, "loss", "loss_pct", val, max_loss, "<=", viol, sev)

        if min_mos is not None:
            mos_val = sm.get("avg_mos") if sm.get("avg_mos") is not None else (sm.get("mos") if sm.get("mos") is not None else sm.get("qoe_score"))
            if mos_val is not None:
                val = float(mos_val)
                viol = val < min_mos
                sev = "critical" if val < 3.0 else "warning"
                _record(fid, t, "mos", "mos", val, min_mos, ">=", viol, sev)

    # Also evaluate apps summary if min_mos is set and app wasn't checked
    if min_mos is not None:
        apps = run.get("apps") or {}
        for aid, a_info in apps.items():
            if aid in evaluated_flows_with_series or aid in flow_stats:
                continue
            if isinstance(a_info, dict):
                mos_val = a_info.get("avg_mos") if a_info.get("avg_mos") is not None else (a_info.get("mos") if a_info.get("mos") is not None else a_info.get("qoe_score"))
                if mos_val is not None:
                    val = float(mos_val)
                    viol = val < min_mos
                    sev = "critical" if val < 3.0 else "warning"
                    _record(aid, 0.0, "mos", "mos", val, min_mos, ">=", viol, sev)

    compliance_pct = round((compliant_evals / total_evals) * 100.0, 2) if total_evals > 0 else 100.0

    for m_data in metric_stats.values():
        e = m_data["evaluations"]
        m_data["compliance_pct"] = round(((e - m_data["violations"]) / e) * 100.0, 2) if e > 0 else 100.0

    for f_data in flow_stats.values():
        e = f_data["evaluations"]
        f_data["compliance_pct"] = round(((e - f_data["violations"]) / e) * 100.0, 2) if e > 0 else 100.0

    return {
        "compliance_pct": compliance_pct,
        "total_violations": len(violations),
        "total_evaluations": total_evals,
        "compliant_evaluations": compliant_evals,
        "is_compliant": (compliance_pct >= target_compliance) if total_evals > 0 else True,
        "thresholds": thresholds,
        "violations": violations,
        "summary_by_metric": metric_stats,
        "summary_by_flow": flow_stats,
    }


def mos_label(m):
    return "—" if m is None else f"{m:.2f}"


__all__ = [
    "CODECS", "MODEL_TYPES", "VIDEO_LADDER", "VIDEO_QUALITIES", "VIDEO_PROFILES",
    "calculate_p1203", "evaluate_sla", "expand", "postprocess", "mos_from",
    "parse_bytes", "validate_app", "math"
]

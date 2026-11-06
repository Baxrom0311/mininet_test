"""Tasodifiy nosozlik/tiklanish modeli (OPNET "Failure/Recovery"): MTBF / MTTR.

Ssenariy `failures` maydoni:
    {"seed": 1, "distribution": "exponential" | "fixed",
     "links":   {"mtbf_s": 300, "mttr_s": 30, "targets": ["l4", "l5"] | "all"},
     "devices": {"mtbf_s": 600, "mttr_s": 60, "targets": ["r2"]}}

expand(project, sc) -> ssenariy nusxasi, `events` ga generatsiya qilingan oddiy
link_down / link_up hodisalari qo'shiladi (`source: "failure"`, `cause: [...]`). Shuning
uchun uchala dvigatel (analitik, ns-3, Mininet) ularni boshqa hodisalardek bajaradi.
Har nishon uchun ish/nosozlik davrlari (eksponensial yoki qat'iy) seed+nishon bo'yicha
DETERMINISTIK; qurilma nosozligi — uning barcha kabellari uzilishi. Bir kabelni bir
nechta sabab (kabel o'zi + qurilma) uzsa, kabel barcha sabablar tugaguncha uzilgan qoladi
(intervallar birlashmasi).

postprocess(res, sc_user, sc_exp) -> res["availability"]: har oqim mavjudligi (%), uzilishlar
ro'yxati, kabellar ishlamay turgan vaqt (%), nazariy A = MTBF / (MTBF + MTTR).
"""

import copy
import math
import random

GROUPS = ("links", "devices")
DISTRIBUTIONS = ("exponential", "fixed")
MAX_EVENTS = 5000


def _targets(project, group, spec):
    t = (spec or {}).get("targets", "all")
    if group == "links":
        return sorted(project.get("links") or {}) if t == "all" else list(t or [])
    if t == "all":
        return sorted(n for n, d in (project.get("devices") or {}).items()
                      if d.get("type") not in ("host", "server"))
    return list(t or [])


def validate(project, sc):
    f = sc.get("failures")
    if not f:
        return []
    errs = []
    if not isinstance(f, dict):
        return ["failures: obyekt bo'lsin"]
    if f.get("distribution", "exponential") not in DISTRIBUTIONS:
        errs.append(f"failures.distribution: {' | '.join(DISTRIBUTIONS)}")
    try:
        int(f.get("seed", 1))
    except (TypeError, ValueError):
        errs.append("failures.seed butun son bo'lsin")
    if not any(f.get(g) for g in GROUPS):
        errs.append("failures: links yoki devices kerak")
    for g in GROUPS:
        spec = f.get(g)
        if not spec:
            continue
        for k in ("mtbf_s", "mttr_s"):
            try:
                if float(spec.get(k, 0)) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errs.append(f"failures.{g}.{k} > 0 bo'lsin")
        tg = spec.get("targets", "all")
        if tg != "all" and not isinstance(tg, list):
            errs.append(f"failures.{g}.targets: ro'yxat yoki \"all\"")
            continue
        pool = project.get("links") if g == "links" else project.get("devices")
        for t in _targets(project, g, spec):
            if t not in (pool or {}):
                errs.append(f"failures.{g}: '{t}' loyihada yo'q")
    if not errs:
        n = len([e for e in expand(project, sc)["events"] if e.get("source") == "failure"])
        if n > MAX_EVENTS:
            errs.append(f"failures: {n} hodisa > {MAX_EVENTS} (MTBF juda kichik yoki davomiylik juda uzun)")
    return errs


def down_intervals(seed, group, target, mtbf, mttr, duration, dist="exponential"):
    """[(boshlanish, tugash)] — nishon ishlamay turgan oraliqlar, [0, duration] ichida."""
    rng = random.Random(f"netlab-failure:{seed}:{group}:{target}")
    out, t = [], 0.0
    for _ in range(100000):
        up = rng.expovariate(1.0 / mtbf) if dist == "exponential" else mtbf
        t += up
        if t >= duration:
            break
        dn = rng.expovariate(1.0 / mttr) if dist == "exponential" else mttr
        out.append((round(t, 3), round(min(t + dn, duration), 3)))
        t += dn
    return out


def _union(ivs):
    out = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def link_down_map(project, sc):
    """{link_id: [(boshlanish, tugash, [sabablar])]} — generatsiya qilingan uzilishlar."""
    f = sc.get("failures") or {}
    if not f:
        return {}
    dur = float(sc.get("duration", 30))
    seed, dist = int(f.get("seed", 1)), f.get("distribution", "exponential")
    raw = {}       # lid -> [(a, b, cause)]
    for g in GROUPS:
        spec = f.get(g)
        if not spec:
            continue
        mtbf, mttr = float(spec["mtbf_s"]), float(spec["mttr_s"])
        for tgt in _targets(project, g, spec):
            ivs = down_intervals(seed, g, tgt, mtbf, mttr, dur, dist)
            if g == "links":
                lids = [tgt]
            else:
                lids = [lid for lid, l in project["links"].items() if tgt in (l["a"]["dev"], l["b"]["dev"])]
            for lid in lids:
                raw.setdefault(lid, []).extend((a, b, tgt) for a, b in ivs)
    out = {}
    for lid, ivs in raw.items():
        merged = []
        for a, b in _union([(a, b) for a, b, _c in ivs]):
            causes = sorted({c for a2, b2, c in ivs if a2 < b + 1e-9 and b2 > a - 1e-9})
            merged.append((a, b, causes))
        out[lid] = merged
    return out


def expand(project, sc):
    """failures -> oddiy link_down/link_up hodisalari (vaqt bo'yicha saralangan)."""
    if not sc.get("failures"):
        return sc
    out = copy.deepcopy(sc)
    dur = float(sc.get("duration", 30))
    ev = list(out.get("events") or [])
    for lid, ivs in sorted(link_down_map(project, sc).items()):
        for a, b, causes in ivs:
            ev.append({"t": a, "action": "link_down", "link": lid, "source": "failure", "cause": causes})
            if b < dur - 1e-6:
                ev.append({"t": b, "action": "link_up", "link": lid, "source": "failure", "cause": causes})
    out["events"] = sorted(ev, key=lambda e: (float(e.get("t", 0)), 0 if e.get("action") == "link_up" else 1))
    return out


def theoretical(project, sc):
    f = sc.get("failures") or {}
    out = {}
    for g in GROUPS:
        spec = f.get(g)
        if spec:
            a = float(spec["mtbf_s"]) / (float(spec["mtbf_s"]) + float(spec["mttr_s"]))
            for t in _targets(project, g, spec):
                out[t] = round(a * 100, 3)
    return out


def _flow_up(p, ftype):
    if ftype == "ping":
        return p.get("rtt_ms") is not None
    if ftype == "voip":
        return (p.get("thr_bps") or 0) > 0 and (p.get("loss_pct") or 0) < 100
    return (p.get("thr_bps") or 0) > 0


def flow_availability(fl):
    """Return availability, observed outage intervals and observed active seconds."""
    st, sp = float(fl.get("start", 0)), float(fl.get("stop", 0))
    points = fl.get("series") or []
    if any("t_start" in p or "t_end" in p for p in points):
        if not math.isfinite(st) or not math.isfinite(sp) or not 0 <= st <= sp:
            raise ValueError("invalid availability flow active time")
        intervals = []
        for point in points:
            start, end = point.get("t_start"), point.get("t_end")
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end):
                raise ValueError("invalid availability sample interval")
            intervals.append((start, end, point))
        intervals.sort(key=lambda item: item[0])
        if any(a[1] > b[0] + 1e-9 for a, b in zip(intervals, intervals[1:])):
            raise ValueError("duplicate or overlapping availability sample intervals")
        observed, up, outages = [], [], []
        for start, end, point in intervals:
            start, end = max(start, st), min(end, sp)
            if start >= end:
                continue
            observed.append(end - start)
            if _flow_up(point, fl.get("type")):
                up.append(end - start)
            elif outages and abs(outages[-1]["end"] - start) < 1e-9:
                outages[-1]["end"] = end
                outages[-1]["duration_s"] = round(end - outages[-1]["start"], 3)
            else:
                outages.append({"start": start, "end": end, "duration_s": round(end - start, 3)})
        total = math.fsum(observed)
        return (round(100 * math.fsum(up) / total, 3), outages, total) if total else (None, [], 0)
    # Timestamp-only archives preserve their historical one-second interpretation.
    pts = [p for p in fl.get("series") or [] if st + 1 <= p["t"] <= sp + 1e-9]
    if not pts:
        return None, [], 0
    ok = [_flow_up(p, fl.get("type")) for p in pts]
    outages, cur = [], None
    for p, u in zip(pts, ok):
        if not u and cur is None:
            cur = p["t"] - 1
        elif u and cur is not None:
            outages.append({"start": cur, "end": p["t"] - 1, "duration_s": round(p["t"] - 1 - cur, 3)})
            cur = None
    if cur is not None:
        outages.append({"start": cur, "end": pts[-1]["t"], "duration_s": round(pts[-1]["t"] - cur, 3)})
    return round(100.0 * sum(ok) / len(ok), 3), outages, len(ok)


def postprocess(res, sc_user, sc_exp=None, project=None):
    """res["availability"] — faqat ssenariyda `failures` bo'lsa."""
    if not sc_user.get("failures") or not res or not res.get("flows"):
        return res
    sc_exp = sc_exp or sc_user
    dur = float(sc_user.get("duration", res.get("duration", 30)))
    flows = {}
    for fid, fl in res["flows"].items():
        if fl.get("hidden"):
            continue
        t = fl.get("type")
        if t in ("http", "ftp"):
            sm = fl.get("summary") or {}
            if t == "http":
                a = 100.0 * sm.get("pages_done", 0) / sm["pages"] if sm.get("pages") else None
            else:
                a = 100.0 if sm.get("completed") else 0.0
            flows[fid] = {"type": t, "availability_pct": None if a is None else round(a, 3),
                          "basis": "sahifalar" if t == "http" else "fayl yakunlandi", "outages": []}
            continue
        a, outs, n = flow_availability(fl)
        if a is None:
            continue
        flows[fid] = {"type": t, "availability_pct": a, "outages": outs, "observed_s": n,
                      "downtime_s": round(sum(o["duration_s"] for o in outs), 3)}
    links = {}
    gen = [e for e in sc_exp.get("events") or [] if e.get("source") == "failure"]
    down_since = {}
    for e in sorted(sc_exp.get("events") or [], key=lambda e: float(e.get("t", 0))):
        lid = e.get("link")
        if e.get("action") == "link_down" and lid not in down_since:
            down_since[lid] = float(e["t"])
        elif e.get("action") == "link_up" and lid in down_since:
            links.setdefault(lid, []).append((down_since.pop(lid), float(e["t"])))
    for lid, t0 in down_since.items():
        links.setdefault(lid, []).append((t0, dur))
    link_out = {lid: {"downtime_s": round(sum(b - a for a, b in ivs), 3),
                      "downtime_pct": round(100.0 * sum(b - a for a, b in ivs) / dur, 3),
                      "failures": len(ivs), "intervals": [[round(a, 3), round(b, 3)] for a, b in ivs]}
                for lid, ivs in sorted(links.items())}
    vals = [f["availability_pct"] for f in flows.values() if f.get("availability_pct") is not None]
    res["availability"] = {
        "flows": flows, "links": link_out,
        "mean_flow_availability": round(sum(vals) / len(vals), 3) if vals else None,
        "min_flow_availability": min(vals) if vals else None,
        "theoretical": theoretical(project, sc_user) if project else {},
        "failure_events": len(gen),
        "note": "mavjudlik — kuzatilgan faol vaqtdan trafik o'tgan vaqt ulushi; aniq oraliqlar vaqt bilan vaznlanadi, eski seriyalar 1 s; "
                "http — yuklangan sahifalar, ftp — fayl yakunlandi",
    }
    return res


def nines(pct):
    """99.9 -> 3.0 (necha "to'qqiz")."""
    if pct is None or pct >= 100:
        return None
    return round(-math.log10(1 - pct / 100.0), 2)

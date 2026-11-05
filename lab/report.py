"""Hisobot eksporti (OPNET "Report"): bitta natija, natijalarni solishtirish, parametrik
tadqiqot (1D/2D) va survivability tahlili uchun mustaqil HTML (tashqi resurssiz: CSS va
SVG grafiklar ichida) hamda CSV to'plami (zip).

    python3 -m lab.report lab/results/ospf_lab/<run>.json -o hisobot.html
    python3 -m lab.report lab/results/ospf_lab/ -o solishtirish.html     # papkadagi barcha natijalar
    python3 -m lab.report lab/results/ospf_lab/<run>.json --zip -o natija.zip

API: GET /api/runs/{p}/{id}/report.html, GET /api/runs/{p}/{id}/export.zip,
POST /api/reports/compare, GET /api/studies/{p}/{sid}/report.html,
POST /api/analysis/survivability/report.
"""

import csv
import html
import io
import json
import math
import os
import sys
import time
import zipfile

# UI bilan bir xil dataviz palitrasi (lab/ui/src/index.css: --s1..--s6, --seq-lo/hi)
PAL_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"]
PAL_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"]
SEQ_LO, SEQ_HI = "#cde2fb", "#0d366b"

CSS = """
:root{--bg:#ffffff;--fg:#111418;--mut:#5b6470;--line:#e3e6ea;--card:#f7f8fa;--grid:#eceff2;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#6250d6;--bad:#c62828;--ok:#1b8a4b}
@media (prefers-color-scheme:dark){:root{--bg:#0d0f12;--fg:#e8eaed;--mut:#9aa3ad;--line:#262b31;--card:#14171b;
--grid:#1f2328;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#9085e9;--bad:#ef5350;--ok:#4caf7d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:16px 0 6px}.mut{color:var(--mut)}
table{border-collapse:collapse;width:100%;margin:6px 0 12px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--mut);font-size:12px;text-transform:none}td.n,th.n{text-align:right}
.tw{overflow-x:auto}.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin:8px 0}
.kv div{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.kv b{display:block;font-size:12px;color:var(--mut);font-weight:500}.kv span{font-size:16px;font-weight:600}
.charts{display:grid;grid-template-columns:repeat(auto-fill,minmax(480px,1fr));gap:12px}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px}
figcaption{font-size:12px;color:var(--mut);margin:0 0 4px}svg{width:100%;height:auto;display:block}
svg text{fill:var(--mut);font-size:10px}.leg{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;margin-top:4px}
.leg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.bad{color:var(--bad);font-weight:600}.ok{color:var(--ok)}ul{margin:4px 0 8px 18px;padding:0}
footer{margin-top:40px;font-size:12px;color:var(--mut)}
@media (max-width:560px){.charts{grid-template-columns:1fr}}
"""


# ─────────────────────────── yordamchilar ───────────────────────────
def e(v):
    return html.escape("" if v is None else str(v), quote=True)


def _num(v, nd=2):
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "ha" if v else "yo'q"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return "—"
        if abs(v) >= 1000 and float(v).is_integer():
            return f"{int(v):,}".replace(",", " ")
        return f"{v:.{nd}f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    return str(v)


def mbps(bps):
    return "—" if bps is None else f"{bps / 1e6:.3f}".rstrip("0").rstrip(".")


def table(cols, rows, num=()):
    """cols: sarlavhalar; rows: qiymatlar (allaqachon satr yoki son); num — o'ngga tekislanadigan ustunlar."""
    if not rows:
        return '<p class="mut">ma\'lumot yo\'q</p>'
    h = "".join(f'<th class="n">{e(c)}</th>' if i in num else f"<th>{e(c)}</th>" for i, c in enumerate(cols))
    body = []
    for r in rows:
        tds = []
        for i, v in enumerate(r):
            cls = ' class="n"' if i in num else ""
            raw = v if isinstance(v, _Raw) else e(_num(v) if not isinstance(v, str) else v)
            tds.append(f"<td{cls}>{raw}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


class _Raw(str):
    """Allaqachon xavfsiz HTML (escape qilinmaydi)."""


def kv(items):
    return '<div class="kv">' + "".join(f"<div><b>{e(k)}</b><span>{e(_num(v))}</span></div>" for k, v in items) + "</div>"


def _nice(lo, hi):
    if hi <= lo:
        hi = lo + 1
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / 4))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= 5:
            step *= m
            break
    return math.floor(lo / step) * step, math.ceil(hi / step) * step, step


def svg_lines(series, title, unit="", w=640, h=220, xlabels=None):
    """series: [(nom, [(x, y|None), ...])]. xlabels — kategorik o'q (x = indeks)."""
    pts = [(x, y) for _n, s in series for x, y in s if y is not None]
    if not pts:
        return ""
    ml, mr, mt, mb = 52, 12, 10, 26
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    if x1 == x0:
        x1 = x0 + 1
    y0, y1, ystep = _nice(min(0.0, min(ys)), max(ys))
    px = lambda x: ml + (x - x0) / (x1 - x0) * (w - ml - mr)          # noqa: E731
    py = lambda y: h - mb - (y - y0) / (y1 - y0) * (h - mt - mb)       # noqa: E731
    g = []
    yv = y0
    while yv <= y1 + 1e-9:
        g.append(f'<line x1="{ml}" x2="{w - mr}" y1="{py(yv):.1f}" y2="{py(yv):.1f}" stroke="var(--grid)"/>'
                 f'<text x="{ml - 6}" y="{py(yv) + 3:.1f}" text-anchor="end">{e(_num(round(yv, 6)))}</text>')
        yv += ystep
    if xlabels:
        for i, lab in enumerate(xlabels):
            g.append(f'<text x="{px(i):.1f}" y="{h - 8}" text-anchor="middle">{e(lab)}</text>')
    else:
        x_0, x_1, xstep = _nice(x0, x1)
        xv = x_0
        while xv <= x_1 + 1e-9:
            if x0 - 1e-9 <= xv <= x1 + 1e-9:
                g.append(f'<text x="{px(xv):.1f}" y="{h - 8}" text-anchor="middle">{e(_num(round(xv, 6)))}</text>')
            xv += xstep
    paths, leg = [], []
    for i, (name, s) in enumerate(series):
        col = f"var(--s{i % 6 + 1})"
        segs, cur = [], []
        for x, y in s:
            if y is None:
                if cur:
                    segs.append(cur)
                cur = []
            else:
                cur.append(f"{px(x):.1f},{py(y):.1f}")
        if cur:
            segs.append(cur)
        for sg in segs:
            if len(sg) == 1:
                cx, cy = sg[0].split(",")
                paths.append(f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="{col}"/>')
            else:
                paths.append(f'<polyline fill="none" stroke="{col}" stroke-width="1.8" points="{" ".join(sg)}"/>')
        leg.append(f'<span><i style="background:{col}"></i>{e(name)}</span>')
    svg = (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{e(title)}">'
           f'<line x1="{ml}" x2="{ml}" y1="{mt}" y2="{h - mb}" stroke="var(--line)"/>{"".join(g)}{"".join(paths)}</svg>')
    return (f"<figure><figcaption>{e(title)}{(' (' + e(unit) + ')') if unit else ''}</figcaption>{svg}"
            f'<div class="leg">{"".join(leg)}</div></figure>')


def _lerp(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def svg_heatmap(xlabels, ylabels, matrix, title, unit=""):
    """matrix[j][i] — y=j qatori, x=i ustuni (None — bo'sh)."""
    vals = [v for row in matrix for v in row if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    cw, ch, ml, mt = 64, 30, 90, 8
    w, h = ml + cw * len(xlabels) + 10, mt + ch * len(ylabels) + 40
    cells = []
    for j, row in enumerate(matrix):
        for i, v in enumerate(row):
            x, y = ml + i * cw, mt + j * ch
            if v is None:
                cells.append(f'<rect x="{x}" y="{y}" width="{cw - 2}" height="{ch - 2}" fill="var(--grid)"/>')
                continue
            t = (v - lo) / (hi - lo) if hi > lo else 0.5
            fill = _lerp(SEQ_LO, SEQ_HI, t)
            tc = "#ffffff" if t > 0.55 else "#0d1b2a"
            cells.append(f'<rect x="{x}" y="{y}" width="{cw - 2}" height="{ch - 2}" rx="3" fill="{fill}">'
                         f'<title>{e(_num(v))}</title></rect>'
                         f'<text x="{x + cw / 2 - 1}" y="{y + ch / 2 + 3}" text-anchor="middle" style="fill:{tc}">{e(_num(round(v, 3)))}</text>')
    for i, lab in enumerate(xlabels):
        cells.append(f'<text x="{ml + i * cw + cw / 2}" y="{mt + ch * len(ylabels) + 14}" text-anchor="middle">{e(lab)}</text>')
    for j, lab in enumerate(ylabels):
        cells.append(f'<text x="{ml - 6}" y="{mt + j * ch + ch / 2 + 3}" text-anchor="end">{e(lab)}</text>')
    return (f'<figure class="heatmap"><figcaption>{e(title)}{(" (" + e(unit) + ")") if unit else ""}</figcaption>'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{e(title)}">{"".join(cells)}</svg></figure>')


def page(title, body, sub=""):
    return (f'<!doctype html><html lang="uz"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(title)}</title><style>{CSS}</style></head><body><main>'
            f'<h1>{e(title)}</h1>{"<p class=mut>" + e(sub) + "</p>" if sub else ""}{body}'
            f'<footer>NetLab hisoboti · {e(time.strftime("%Y-%m-%d %H:%M"))}</footer></main></body></html>')


def _ts(t):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(t)))
    except (TypeError, ValueError):
        return "—"


def _visible_flows(run):
    return {k: f for k, f in (run.get("flows") or {}).items() if not f.get("hidden")}


def _series(f, key, scale=1.0):
    return [(p.get("t"), (p.get(key) * scale) if isinstance(p.get(key), (int, float)) else None)
            for p in f.get("series") or [] if p.get("t") is not None]


def run_label(run):
    return run.get("label") or f"{run.get('scenario_name') or '?'} · {run.get('engine') or '?'}"


# ─────────────────────────── bitta natija ───────────────────────────
def _run_body(run, charts=True):
    out = []
    topo = run.get("topology") or {}
    out.append(kv([("Ssenariy", run.get("scenario_name")), ("Dvigatel", run.get("engine_label") or run.get("engine")),
                   ("Holat", run.get("status")), ("Davomiylik, s", run.get("duration")),
                   ("Hisoblash vaqti, s", run.get("wall_s")), ("Yaratilgan", _ts(run.get("created"))),
                   ("Qurilmalar", topo.get("devices")), ("Kabellar", topo.get("links"))]))
    if run.get("study_id"):
        out.append(f'<p class="mut">Tadqiqot {e(run["study_id"])}: {e(run.get("label"))}</p>')
    if run.get("error"):
        out.append(f'<p class="bad">Xato: {e(run["error"])}</p>')
    sc = run.get("scenario") or {}
    if sc.get("notes"):
        out.append(f'<p>{e(sc["notes"])}</p>')
    flows = _visible_flows(run)
    # oqimlar xulosasi
    out.append("<h2>Oqimlar</h2>")
    rows = []
    for fid, f in flows.items():
        s = f.get("summary") or {}
        rows.append([fid, f.get("label") or "", f.get("type") or "", f"{f.get('src')} → {f.get('dst')}",
                     mbps(s.get("avg_thr_bps")), s.get("avg_delay_ms"), s.get("avg_jitter_ms"),
                     s.get("loss_pct"), s.get("avg_rtt_ms")])
    out.append(table(["ID", "Nomi", "Tur", "Yo'nalish", "O'tkazuvchanlik, Mbit/s", "Kechikish, ms",
                      "Jitter, ms", "Yo'qotish, %", "RTT, ms"], rows, num=(4, 5, 6, 7, 8)))
    # ilova modellari
    apps = run.get("apps") or {}
    if apps:
        out.append("<h2>Ilovalar (VoIP / HTTP / FTP / multicast)</h2>")
        rows = []
        for aid, a in apps.items():
            t = a.get("type")
            if t == "voip":
                main = f"MOS {_num(a.get('avg_mos'))} (min {_num(a.get('min_mos'))}), {a.get('quality') or ''}"
            elif t == "http":
                main = f"{_num(a.get('pages_done'))}/{_num(a.get('pages'))} sahifa, o'rtacha {_num(a.get('avg_page_s'))} s"
            elif t == "ftp":
                main = (f"{_num(a.get('transfer_s'))} s, {mbps(a.get('avg_rate_bps'))} Mbit/s"
                        if a.get("completed") else "yakunlanmadi")
            else:
                main = ", ".join(f"{k}={_num(v)}" for k, v in a.items() if k != "type" and not isinstance(v, (dict, list)))
            rows.append([aid, t or "", main])
        out.append(table(["Ilova", "Tur", "Natija"], rows))
    # mavjudlik
    av = run.get("availability") or {}
    if av:
        out.append("<h2>Mavjudlik (MTBF/MTTR nosozliklari)</h2>")
        out.append(kv([("O'rtacha oqim mavjudligi, %", av.get("mean_flow_availability")),
                       ("Eng past, %", av.get("min_flow_availability"))]))
        rows = [[k, v.get("type") or "", v.get("availability_pct"), v.get("downtime_s"), len(v.get("outages") or [])]
                for k, v in (av.get("flows") or {}).items()]
        out.append(table(["Oqim", "Tur", "Mavjudlik, %", "Uzilish, s", "Uzilishlar soni"], rows, num=(2, 3, 4)))
        th = av.get("theoretical") or {}
        if th:
            out.append(table(["Nishon", "Nazariy mavjudlik"], [[k, v] for k, v in th.items()], num=(1,)))
    # grafiklar
    if charts and flows:
        out.append("<h2>Grafiklar</h2><div class='charts'>")
        out.append(svg_lines([(fid, _series(f, "thr_bps", 1e-6)) for fid, f in flows.items()
                              if f.get("type") != "ping"], "O'tkazuvchanlik", "Mbit/s"))
        out.append(svg_lines([(fid, _series(f, "delay_ms")) for fid, f in flows.items()], "Kechikish", "ms"))
        out.append(svg_lines([(fid, _series(f, "loss_pct")) for fid, f in flows.items()], "Yo'qotish", "%"))
        mos = [(fid, _series(f, "mos")) for fid, f in flows.items() if f.get("type") == "voip"]
        if mos:
            out.append(svg_lines(mos, "VoIP MOS", "1..4.5"))
        links = run.get("links") or {}
        top = sorted(links, key=lambda lid: -max((p.get("util") or 0) for p in links[lid] or [{}]))[:6]
        if top:
            out.append(svg_lines([(lid, _series({"series": links[lid]}, "util", 100.0)) for lid in top],
                                 "Kabel yuklamasi (eng yuklangan 6 ta)", "%"))
        out.append("</div>")
    # kabellar
    links = run.get("links") or {}
    if links:
        out.append("<h2>Kabellar</h2>")
        rows = []
        for lid, pts in sorted(links.items()):
            pts = pts or []
            if not pts:
                continue
            mx = lambda k: max((p.get(k) or 0) for p in pts)  # noqa: E731
            sm = lambda k: sum((p.get(k) or 0) for p in pts)  # noqa: E731
            rows.append([lid, round(mx("util") * 100, 1), mbps(mx("ab_bps")), mbps(mx("ba_bps")),
                         max(mx("queue_ab"), mx("queue_ba")), sm("drops_ab") + sm("drops_ba")])
        out.append(table(["Kabel", "Max yuklama, %", "Max a→b, Mbit/s", "Max b→a, Mbit/s", "Max navbat, pkt",
                          "Tashlangan, pkt"], rows, num=(1, 2, 3, 4, 5)))
    # qurilmalar
    nodes = run.get("nodes") or {}
    if nodes:
        out.append("<h2>Qurilmalar (L3)</h2>")
        rows = []
        for dv, pts in sorted(nodes.items()):
            pts = pts or []
            if not pts:
                continue
            rows.append([dv, min(p.get("routes") or 0 for p in pts), max(p.get("routes") or 0 for p in pts),
                         max(p.get("fwd_pps") or 0 for p in pts), sum(p.get("noroute") or 0 for p in pts),
                         sum(p.get("discards") or 0 for p in pts)])
        out.append(table(["Qurilma", "Marshrut (min)", "Marshrut (max)", "Max tranzit, pkt/s", "Marshrutsiz, pkt",
                          "Tashlangan, pkt"], rows, num=(1, 2, 3, 4, 5)))
    conv = run.get("convergence") or []
    if conv:
        out.append("<h2>Konvergensiya</h2>")
        out.append(table(["t, s", "Hodisa", "Nishon", "Konvergensiya, s", "Qurilmalar"],
                         [[c.get("t"), c.get("action") or "", c.get("link") or "",
                           c.get("converged_s"), ", ".join(c.get("devices") or [])] for c in conv], num=(0, 3)))
    rev = run.get("route_events") or []
    if rev:
        out.append(f"<h3>Marshrut o'zgarishlari ({len(rev)})</h3>")
        out.append(table(["t, s", "Qurilma", "Qo'shildi", "Olib tashlandi"],
                         [[r.get("t"), r.get("dev") or "", ", ".join(r.get("added") or []),
                           ", ".join(r.get("removed") or [])] for r in rev[:60]], num=(0,)))
        if len(rev) > 60:
            out.append(f'<p class="mut">… yana {len(rev) - 60} ta (CSV eksportda to\'liq)</p>')
    evs = run.get("events") or []
    if evs:
        out.append("<h2>Hodisalar</h2>")
        out.append(table(["t, s", "Amal", "Nishon", "Manba"],
                         [[x.get("t"), x.get("action") or "", x.get("link") or x.get("device") or "",
                           x.get("source") or x.get("cause") or ""] for x in evs[:200]], num=(0,)))
    if run.get("notes"):
        out.append("<h2>Izohlar</h2><ul>" + "".join(f"<li>{e(n)}</li>" for n in run["notes"]) + "</ul>")
    return "".join(out)


def run_report(run):
    return page(f"Natija: {run_label(run)}", _run_body(run), sub=f"run_id {run.get('run_id') or '—'}")


# ─────────────────────────── solishtirish ───────────────────────────
def compare_report(runs):
    runs = [r for r in runs if r]
    if not runs:
        return page("Solishtirish", '<p class="mut">natija yo\'q</p>')
    out = ["<h2>Natijalar</h2>"]
    out.append(table(["#", "Nomi", "Ssenariy", "Dvigatel", "Holat", "Hisoblash, s", "Yaratilgan"],
                     [[i + 1, run_label(r), r.get("scenario_name") or "", r.get("engine_label") or r.get("engine") or "",
                       r.get("status") or "", r.get("wall_s"), _ts(r.get("created"))] for i, r in enumerate(runs)],
                     num=(0, 5)))
    common = None
    for r in runs:
        ks = set(_visible_flows(r))
        common = ks if common is None else common & ks
    common = sorted(common or [])
    if common:
        out.append("<h2>Oqimlar bo'yicha</h2>")
        for metric, title, f_ in (("avg_thr_bps", "O'tkazuvchanlik, Mbit/s", mbps), ("avg_delay_ms", "Kechikish, ms", None),
                                  ("loss_pct", "Yo'qotish, %", None)):
            rows = []
            for fid in common:
                row = [fid]
                for r in runs:
                    v = ((r["flows"][fid].get("summary") or {}).get(metric))
                    row.append(f_(v) if f_ else v)
                rows.append(row)
            out.append(f"<h3>{e(title)}</h3>")
            out.append(table(["Oqim"] + [f"#{i + 1}" for i in range(len(runs))], rows,
                             num=tuple(range(1, len(runs) + 1))))
        out.append("<div class='charts'>")
        for fid in common[:8]:
            out.append(svg_lines([(f"#{i + 1} {run_label(r)}", _series(r["flows"][fid], "thr_bps", 1e-6))
                                  for i, r in enumerate(runs)], f"{fid}: o'tkazuvchanlik", "Mbit/s"))
        out.append("</div>")
    apps = sorted(set().union(*[set(r.get("apps") or {}) for r in runs]))
    if apps:
        out.append("<h2>Ilovalar</h2>")
        rows = []
        for aid in apps:
            row = [aid]
            for r in runs:
                a = (r.get("apps") or {}).get(aid) or {}
                row.append(_app_value(a))
            rows.append(row)
        out.append(table(["Ilova"] + [f"#{i + 1}" for i in range(len(runs))], rows))
    return page(f"Solishtirish: {len(runs)} natija", "".join(out))


def _app_value(a):
    t = a.get("type")
    if t == "voip":
        return f"MOS {_num(a.get('avg_mos'))}"
    if t == "http":
        return f"{_num(a.get('avg_page_s'))} s/sahifa"
    if t == "ftp":
        return f"{_num(a.get('transfer_s'))} s" if a.get("completed") else "yakunlanmadi"
    if t == "mcast":
        return f"{_num(a.get('reached'))} qabul, yo'qotish {_num(a.get('avg_loss_pct'))}%"
    return "—" if not a else ""


def _metric_of(run, fid, metric):
    if metric == "mos":
        return ((run.get("apps") or {}).get(fid) or {}).get("avg_mos")
    if metric == "page_s":
        return ((run.get("apps") or {}).get(fid) or {}).get("avg_page_s")
    if metric == "transfer_s":
        return ((run.get("apps") or {}).get(fid) or {}).get("transfer_s")
    f = (run.get("flows") or {}).get(fid)
    if not f:
        return None
    v = (f.get("summary") or {}).get(metric)
    return v / 1e6 if (metric == "avg_thr_bps" and v is not None) else v


METRICS = [("avg_thr_bps", "O'tkazuvchanlik", "Mbit/s"), ("avg_delay_ms", "Kechikish", "ms"),
           ("loss_pct", "Yo'qotish", "%"), ("mos", "VoIP MOS", ""), ("page_s", "HTTP sahifa vaqti", "s"),
           ("transfer_s", "FTP uzatish vaqti", "s")]


# ─────────────────────────── tadqiqot ───────────────────────────
def _plabel(p):
    if isinstance(p, dict):
        return p.get("label") or f"{p.get('link') or p.get('app') or p.get('ap') or p.get('router') or ''}.{p.get('field')}"
    return str(p or "")


def study_report(runs):
    runs = [r for r in runs if r and r.get("status") == "done"]
    if not runs:
        return page("Tadqiqot", '<p class="mut">tugagan natija yo\'q</p>')
    p1 = _plabel(runs[0].get("study_param"))
    two_d = any(r.get("study_param2") for r in runs)
    flows = sorted(set().union(*[set(_visible_flows(r)) for r in runs]) | set().union(*[set(r.get("apps") or {}) for r in runs]))
    out = [kv([("Parametr", p1), ("2-parametr", _plabel(runs[0].get("study_param2")) if two_d else "—"),
               ("Natijalar", len(runs)), ("Ssenariy", runs[0].get("scenario_name")),
               ("Dvigatel", runs[0].get("engine_label") or runs[0].get("engine"))])]
    if not two_d:
        rs = sorted(runs, key=lambda r: r.get("study_index") or 0)
        xl = [str(r.get("study_value")) for r in rs]
        out.append("<h2>Natijalar jadvali</h2>")
        rows = []
        for r in rs:
            row = [str(r.get("study_value"))]
            for fid in flows:
                v = _metric_of(r, fid, "avg_thr_bps")
                row.append(v if v is not None else _metric_of(r, fid, "mos"))
            rows.append(row)
        out.append(table([p1] + [f"{f} (Mbit/s | MOS)" for f in flows], rows, num=tuple(range(1, len(flows) + 1))))
        out.append("<h2>Grafiklar</h2><div class='charts'>")
        for metric, title, unit in METRICS:
            ser = [(fid, [(i, _metric_of(r, fid, metric)) for i, r in enumerate(rs)]) for fid in flows]
            ser = [s for s in ser if any(y is not None for _x, y in s[1])]
            if ser:
                out.append(svg_lines(ser, f"{title} vs {p1}", unit, xlabels=xl))
        out.append("</div>")
    else:
        p2 = _plabel(runs[0].get("study_param2"))
        xs = sorted({(r.get("study_index") or 0, str(r.get("study_value"))) for r in runs})
        ys = sorted({(r.get("study_index2") or 0, str(r.get("study_value2"))) for r in runs})
        grid = {(r.get("study_index") or 0, r.get("study_index2") or 0): r for r in runs}
        out.append(f"<h2>Issiqlik xaritalari ({e(p1)} × {e(p2)})</h2><div class='charts'>")
        for fid in flows:
            for metric, title, unit in METRICS:
                m = [[_metric_of(grid[(i, j)], fid, metric) if (i, j) in grid else None for i, _x in xs] for j, _y in ys]
                if any(v is not None for row in m for v in row):
                    out.append(svg_heatmap([x for _i, x in xs], [f"{p2}={y}" for _j, y in ys], m,
                                           f"{fid}: {title}", unit))
        out.append("</div>")
    return page(f"Tadqiqot: {p1}", "".join(out), sub=f"study_id {runs[0].get('study_id') or '—'}")


# ─────────────────────────── survivability ───────────────────────────
def _tgt(c):
    t = c.get("target")
    return " + ".join(map(str, t)) if isinstance(t, (list, tuple)) else str(t)


def survivability_report(rep):
    out = []
    base = rep.get("baseline") or {}
    cases = rep.get("cases") or []
    out.append(kv([("Ssenariy", rep.get("scenario")), ("Nishonlar", rep.get("targets")),
                   ("Ko'rib chiqilgan nosozliklar", len(cases)), ("SPOF", len(rep.get("spof") or [])),
                   ("Kuchaytirish kerak", len(rep.get("upgrades") or [])), ("Hisoblash, s", rep.get("wall_s"))]))
    if base.get("down_flows"):
        out.append(f'<p class="mut">Asosiy holatda ishlamaydigan oqimlar (nosozlik emas): {e(", ".join(base["down_flows"]))}</p>')
    out.append("<h2>Yakka nosozlik nuqtalari (SPOF)</h2>")
    spof = rep.get("spof") or []
    out.append(table(["Tur", "Nishon", "Uziladigan oqimlar"],
                     [[s.get("kind") or "", _tgt(s), _Raw('<span class="bad">' + e(", ".join(s.get("broken") or [])) + "</span>")]
                      for s in spof]) if spof else '<p class="ok">Yakka nosozlik nuqtasi yo\'q</p>')
    out.append("<h2>Kritiklik reytingi</h2>")
    known = {"kind", "target", "score", "broken", "broken_demand_pct", "rerouted", "overloaded", "max_util",
             "convergence", "down_links", "degraded", "udp_loss", "mos"}
    rows = []
    for c in cases:
        extra = {k: v for k, v in c.items() if k not in known}
        conv = ", ".join(f"{k} {_num(v)} s" for k, v in (c.get("convergence") or {}).items())
        mos = ", ".join(f"{k}: {_num(v.get('base'))}→{_num(v.get('fail'))}" for k, v in (c.get("mos") or {}).items())
        rows.append([c.get("kind") or "", _tgt(c), c.get("score"),
                     ", ".join(b.get("flow", "") for b in c.get("broken") or []), c.get("broken_demand_pct"),
                     ", ".join(c.get("rerouted") or []),
                     ", ".join(f"{o.get('link')} {round((o.get('util') or 0) * 100)}%" for o in c.get("overloaded") or []),
                     round((c.get("max_util") or 0) * 100, 1), conv, mos,
                     "; ".join(f"{k}={json.dumps(v, ensure_ascii=False, default=str)[:80]}" for k, v in extra.items())])
    out.append(table(["Tur", "Nishon", "Ball", "Uziladi", "Talab, %", "Yo'li o'zgaradi", "Ortiqcha yuk",
                      "Max yuklama, %", "Tiklanish", "MOS", "Boshqa"], rows, num=(2, 4, 7)))
    plan = rep.get("capacity_plan") or []
    if plan:
        out.append("<h2>Sig'im rejasi (N-1)</h2>")
        out.append(table(["Kabel", "Yo'n.", "Tezlik, Mbit/s", "Asosiy yuk", "Eng yomon yuk", "Eng yomon holat",
                          "Yuklama", "Kerak, Mbit/s", "Kuchaytirish"],
                         [[p_.get("link"), p_.get("dir"), p_.get("bw_mbps"), p_.get("base_load_mbps"),
                           p_.get("worst_load_mbps"), p_.get("worst_case") or "", p_.get("worst_util"), p_.get("need_mbps"),
                           _Raw('<span class="bad">ha</span>') if p_.get("upgrade") else "—"] for p_ in plan],
                         num=(2, 3, 4, 6, 7)))
    if rep.get("notes"):
        out.append("<h2>Izohlar</h2><ul>" + "".join(f"<li>{e(n)}</li>" for n in rep["notes"]) + "</ul>")
    return page(f"Survivability: {rep.get('scenario') or ''}", "".join(out))


# ─────────────────────────── CSV ───────────────────────────
def _csv(rows, header):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def csv_bundle(runs):
    """runs: bitta natija yoki ro'yxat -> zip bayt (flows/links/nodes/apps/availability/convergence/... .csv)."""
    if isinstance(runs, dict):
        runs = [runs]
    files = {"flows.csv": [], "flow_summary.csv": [], "links.csv": [], "nodes.csv": [], "apps.csv": [],
             "availability.csv": [], "convergence.csv": [], "route_events.csv": [], "events.csv": []}
    for r in runs:
        rid = r.get("run_id") or ""
        for fid, f in (r.get("flows") or {}).items():
            s = f.get("summary") or {}
            files["flow_summary.csv"].append([rid, fid, f.get("type"), f.get("src"),
                                              json.dumps(f.get("dst")) if isinstance(f.get("dst"), list) else f.get("dst"),
                                              s.get("avg_thr_bps"), s.get("avg_delay_ms"), s.get("avg_jitter_ms"),
                                              s.get("loss_pct"), s.get("avg_rtt_ms"), bool(f.get("hidden"))])
            for p in f.get("series") or []:
                files["flows.csv"].append([rid, fid, p.get("t"), p.get("thr_bps"), p.get("delay_ms"), p.get("jitter_ms"),
                                           p.get("loss_pct"), p.get("rtt_ms"), p.get("mos")])
        for lid, pts in (r.get("links") or {}).items():
            for p in pts or []:
                files["links.csv"].append([rid, lid, p.get("t"), p.get("ab_bps"), p.get("ba_bps"), p.get("util"),
                                           p.get("queue_ab"), p.get("queue_ba"), p.get("drops_ab"), p.get("drops_ba")])
        for dv, pts in (r.get("nodes") or {}).items():
            for p in pts or []:
                files["nodes.csv"].append([rid, dv, p.get("t"), p.get("routes"), p.get("fwd_pps"), p.get("rx_pps"),
                                           p.get("tx_pps"), p.get("noroute"), p.get("discards")])
        for aid, a in (r.get("apps") or {}).items():
            files["apps.csv"].append([rid, aid, a.get("type"), json.dumps({k: v for k, v in a.items() if k != "type"},
                                                                          ensure_ascii=False, default=str)])
        for fid, a in ((r.get("availability") or {}).get("flows") or {}).items():
            files["availability.csv"].append([rid, fid, a.get("availability_pct"), a.get("downtime_s"),
                                              len(a.get("outages") or [])])
        for c in r.get("convergence") or []:
            files["convergence.csv"].append([rid, c.get("t"), c.get("action"), c.get("link"), c.get("converged_s"),
                                             " ".join(c.get("devices") or [])])
        for ev in r.get("route_events") or []:
            files["route_events.csv"].append([rid, ev.get("t"), ev.get("dev"), " | ".join(ev.get("added") or []),
                                              " | ".join(ev.get("removed") or [])])
        for ev in r.get("events") or []:
            files["events.csv"].append([rid, ev.get("t"), ev.get("action"), ev.get("link") or ev.get("device"),
                                        ev.get("source") or ""])
    headers = {
        "flows.csv": ["run_id", "flow", "t", "thr_bps", "delay_ms", "jitter_ms", "loss_pct", "rtt_ms", "mos"],
        "flow_summary.csv": ["run_id", "flow", "type", "src", "dst", "avg_thr_bps", "avg_delay_ms", "avg_jitter_ms",
                             "loss_pct", "avg_rtt_ms", "hidden"],
        "links.csv": ["run_id", "link", "t", "ab_bps", "ba_bps", "util", "queue_ab", "queue_ba", "drops_ab", "drops_ba"],
        "nodes.csv": ["run_id", "device", "t", "routes", "fwd_pps", "rx_pps", "tx_pps", "noroute", "discards"],
        "apps.csv": ["run_id", "app", "type", "summary_json"],
        "availability.csv": ["run_id", "flow", "availability_pct", "downtime_s", "outages"],
        "convergence.csv": ["run_id", "t", "action", "target", "converged_s", "devices"],
        "route_events.csv": ["run_id", "t", "device", "added", "removed"],
        "events.csv": ["run_id", "t", "action", "target", "source"],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in files.items():
            if rows or name in ("flows.csv", "flow_summary.csv"):
                z.writestr(name, _csv(rows, headers[name]))
        z.writestr("results.json", json.dumps(runs if len(runs) > 1 else runs[0], ensure_ascii=False, default=str))
    return buf.getvalue()


# ─────────────────────────── CLI ───────────────────────────
def _load(path):
    if os.path.isdir(path):
        out = []
        for f in sorted(os.listdir(path)):
            if f.endswith(".json"):
                with open(os.path.join(path, f)) as fh:
                    out.append(json.load(fh))
        return out
    with open(path) as fh:
        return json.load(fh)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="NetLab hisobot eksporti")
    ap.add_argument("src", help="natija .json, natijalar papkasi yoki survivability .json")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--zip", action="store_true", help="CSV to'plami (zip)")
    ap.add_argument("--study", action="store_true", help="papkadagi natijalar — tadqiqot hisoboti")
    a = ap.parse_args(argv)
    data = _load(a.src)
    if a.zip:
        with open(a.out, "wb") as f:
            f.write(csv_bundle(data))
        return
    if isinstance(data, list):
        doc = study_report(data) if a.study else compare_report(data)
    elif "cases" in data and "capacity_plan" in data:
        doc = survivability_report(data)
    else:
        doc = run_report(data)
    with open(a.out, "w") as f:
        f.write(doc)


if __name__ == "__main__":
    sys.exit(main())

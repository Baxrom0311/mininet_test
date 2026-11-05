"""Hisobot eksporti (lab/report.py + API): natija, solishtirish, 1D/2D tadqiqot, survivability, CSV zip.

    python3 -m lab.tests.smoke_report
"""

import io
import json
import os
import sys
import time
import zipfile
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from lab.server import create_app  # noqa: E402

FAILS = []
SCRATCH = os.environ.get("NETLAB_SCRATCH") or "/tmp"


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


class _P(HTMLParser):
    VOID = {"meta", "br", "hr", "img", "input", "link", "col", "area", "base", "wbr", "source", "rect", "circle",
            "line", "polyline", "path"}

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> kutilmagan (stek: {self.stack[-3:]})")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def html_ok(doc, name, must=()):
    p = _P()
    p.feed(doc)
    p.close()
    ext = [x for x in ("http://", "https://", "src=\"//", "@import") if x in doc]
    check(not p.errors and not p.stack and not ext and all(m in doc for m in must),
          f"{name}: HTML to'g'ri ({len(doc) // 1024} KB), tashqi resurs yo'q, bo'limlar {list(must)}"
          + (f" | xato {p.errors[:2]} ochiq {p.stack[-3:]} tashqi {ext} yo'q {[m for m in must if m not in doc]}"
             if p.errors or p.stack or ext or not all(m in doc for m in must) else ""))


def wait_runs(c, project, pred, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rs = c.get(f"/api/runs/{project}").json()["runs"]
        mine = [r for r in rs if pred(r)]
        if mine and not any(r.get("status") == "running" for r in mine):
            return mine
        time.sleep(0.3)
    return []


def run_one(c, project, scenario, engine="analytic"):
    rid = c.post("/api/runs", json={"project": project, "scenario": scenario, "engine": engine}).json()["run_id"]
    wait_runs(c, project["name"], lambda r: r["run_id"] == rid)
    return rid


with TestClient(create_app("demo")) as c:
    ospf = c.get("/api/projects/ospf_lab?example=true").json()
    ospf["scenarios"]["ilovalar"] = {"duration": 20, "events": [], "apps": [
        {"id": "tel", "type": "voip", "src": "pc1", "dst": "srv2", "codec": "g711", "start": 1, "stop": 19},
        {"id": "web", "type": "http", "src": "pc2", "dst": "srv1", "page": "500KB", "think": 3, "start": 1, "stop": 19},
        {"id": "fayl", "type": "ftp", "src": "pc1", "dst": "srv1", "file": "5MB", "start": 3, "stop": 19}]}
    r1 = run_one(c, ospf, "uzilish")
    r2 = run_one(c, ospf, "ilovalar")
    rel = c.get("/api/projects/reliability_lab?example=true").json()
    r3 = run_one(c, rel, "qisqa")

    # ---- bitta natija ----
    rr = c.get(f"/api/runs/ospf_lab/{r1}/report.html")
    check(rr.status_code == 200 and rr.headers["content-type"].startswith("text/html")
          and "inline" in rr.headers.get("content-disposition", ""), f"run report: {rr.status_code} {rr.headers.get('content-type')}")
    doc = rr.text
    html_ok(doc, "run (uzilish)", must=("Oqimlar", "Grafiklar", "Kabellar", "Konvergensiya", "<svg", "video"))
    with open(os.path.join(SCRATCH, "netlab_report_run.html"), "w") as f:
        f.write(doc)
    check(len(doc.encode()) < 2 * 1024 * 1024, f"hajm {len(doc.encode()) // 1024} KB < 2 MB")
    doc2 = c.get(f"/api/runs/ospf_lab/{r2}/report.html").text
    html_ok(doc2, "run (ilovalar)", must=("Ilovalar", "MOS", "sahifa", "VoIP MOS"))
    doc3 = c.get(f"/api/runs/reliability_lab/{r3}/report.html").text
    html_ok(doc3, "run (mavjudlik)", must=("Mavjudlik", "Nazariy mavjudlik"))
    check(c.get("/api/runs/ospf_lab/yoq-natija/report.html").status_code == 404, "mavjud bo'lmagan natija -> 404")

    # ---- CSV zip ----
    z = c.get(f"/api/runs/ospf_lab/{r1}/export.zip")
    check(z.status_code == 200 and z.headers["content-type"] == "application/zip"
          and "attachment" in z.headers.get("content-disposition", ""), "export.zip: 200, application/zip, attachment")
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    names = set(zf.namelist())
    need = {"flows.csv", "flow_summary.csv", "links.csv", "nodes.csv", "convergence.csv", "results.json"}
    heads = {n: zf.read(n).decode().splitlines()[0] for n in names if n.endswith(".csv")}
    check(need <= names and heads["flows.csv"].startswith("run_id,flow,t,thr_bps")
          and heads["links.csv"].startswith("run_id,link,t") and len(zf.read("flows.csv").decode().splitlines()) > 10,
          f"zip: {sorted(names)}")
    za = zipfile.ZipFile(io.BytesIO(c.get(f"/api/runs/reliability_lab/{r3}/export.zip").content))
    check("availability.csv" in za.namelist(), "zip (mavjudlik): availability.csv bor")
    zb = zipfile.ZipFile(io.BytesIO(c.get(f"/api/runs/ospf_lab/{r2}/export.zip").content))
    check("apps.csv" in zb.namelist() and "voip" in zb.read("apps.csv").decode(), "zip (ilovalar): apps.csv voip bilan")

    # ---- solishtirish ----
    cr = c.post("/api/reports/compare", json={"project": "ospf_lab", "run_ids": [r1, r2]})
    check(cr.status_code == 200 and cr.headers["content-type"].startswith("text/html"), f"compare: {cr.status_code}")
    html_ok(cr.text, "compare", must=("Solishtirish", "#1", "#2"))
    check(c.post("/api/reports/compare", json={"project": "ospf_lab", "run_ids": []}).status_code == 400,
          "compare: bo'sh ro'yxat -> 400")

    # ---- 1D va 2D tadqiqot ----
    st = c.post("/api/studies", json={"project": ospf, "scenario": "asosiy", "engine": "analytic",
                                      "param": {"kind": "link", "link": "l4", "field": "bw"},
                                      "values": [5, 10, 20]}).json()
    wait_runs(c, "ospf_lab", lambda r: r.get("study_id") == st["study_id"])
    time.sleep(0.5)
    sr = c.get(f"/api/studies/ospf_lab/{st['study_id']}/report.html")
    check(sr.status_code == 200, f"study 1D: {sr.status_code}")
    html_ok(sr.text, "study 1D", must=("Tadqiqot", "polyline", "l4.bw"))
    st2 = c.post("/api/studies", json={"project": ospf, "scenario": "asosiy", "engine": "analytic",
                                       "param": {"kind": "link", "link": "l4", "field": "bw"}, "values": [5, 20],
                                       "param2": {"kind": "app", "app": "video", "field": "rate"},
                                       "values2": ["2Mbps", "8Mbps"]}).json()
    wait_runs(c, "ospf_lab", lambda r: r.get("study_id") == st2["study_id"])
    time.sleep(0.5)
    sr2 = c.get(f"/api/studies/ospf_lab/{st2['study_id']}/report.html")
    check(sr2.status_code == 200, f"study 2D: {sr2.status_code}")
    html_ok(sr2.text, "study 2D", must=("Issiqlik xaritalari", 'class="heatmap"', "<rect"))
    check(c.get("/api/studies/ospf_lab/yoq/report.html").status_code == 404, "study: topilmadi -> 404")

    # ---- survivability ----
    sv = c.post("/api/analysis/survivability/report", json={"project": ospf, "scenario": "asosiy", "targets": "both"})
    check(sv.status_code == 200 and sv.headers["content-type"].startswith("text/html"), f"survivability report: {sv.status_code}")
    html_ok(sv.text, "survivability", must=("Yakka nosozlik nuqtalari", "Kritiklik reytingi", "Sig'im rejasi", "l4"))

    # ---- escape: foydalanuvchi matni HTML sifatida chiqmaydi ----
    from lab.report import run_report
    evil = c.get(f"/api/runs/ospf_lab/{r1}").json()
    evil["label"] = "<script>alert(1)</script>"
    evil["flows"]["video"]["label"] = "<img src=x onerror=alert(1)>"
    d = run_report(evil)
    check("<script>alert" not in d and "<img src=x" not in d and "&lt;script&gt;" in d, "escape: label/flow nomi xavfsiz")
    # eski natija (yangi maydonlarsiz) ham ishlaydi
    old = {"run_id": "eski", "scenario_name": "x", "engine": "analytic", "status": "done",
           "flows": {"a": {"type": "udp", "src": "h1", "dst": "h2", "series": [{"t": 1, "thr_bps": 1e6}], "summary": {}}}}
    html_ok(run_report(old), "eski natija (minimal maydonlar)", must=("Oqimlar",))

    # ---- token himoyasi ----
with TestClient(create_app("demo", token="t0k")) as c2:
    check(c2.get("/api/runs/ospf_lab/x/report.html").status_code == 401, "token: hisobot tokensiz 401")

with TestClient(create_app("demo")) as c:      # faqat shu test yaratgan natijalar o'chiriladi
    sids = {st["study_id"], st2["study_id"]}
    for r in c.get("/api/runs/ospf_lab").json()["runs"]:
        if r["run_id"] in (r1, r2) or r.get("study_id") in sids:
            c.delete(f"/api/runs/ospf_lab/{r['run_id']}")
    c.delete(f"/api/runs/reliability_lab/{r3}")

# ---- CLI ----
import subprocess  # noqa: E402

from lab.report import csv_bundle  # noqa: E402

tmp = os.path.join(SCRATCH, "netlab_cli_run.json")
with open(tmp, "w") as f:
    json.dump(old, f)
out = os.path.join(SCRATCH, "netlab_cli.html")
rc = subprocess.run([sys.executable, "-m", "lab.report", tmp, "-o", out], capture_output=True, text=True,
                    cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
check(rc.returncode == 0 and os.path.getsize(out) > 1000, f"CLI: python3 -m lab.report -> {rc.returncode} {rc.stderr[-200:]}")
check(len(csv_bundle([old, old])) > 200, "csv_bundle: bir nechta natija")

print(f"\n{'MUVAFFAQIYATSIZ: ' + str(len(FAILS)) if FAILS else 'Hammasi o`tdi'}")
sys.exit(1 if FAILS else 0)

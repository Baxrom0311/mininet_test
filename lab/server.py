"""NetLab backend: FastAPI (REST) + WebSocket (hodisalar, terminallar) + statik UI.

    python3 -m lab.netlab --driver demo           # Mininet'siz
    sudo python3 -m lab.netlab --driver real      # Mininet/OVS/FRR/BMv2

Xavfsizlik: real driver qurilmalarga root terminal beradi. Shuning uchun server
standart holatda faqat 127.0.0.1 da tinglaydi; boshqa manzilda ishga
tushirilsa token majburiy (cookie orqali tekshiriladi).
"""

import asyncio
import glob
import json
import os
import re
import secrets
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from lab.drivers.base import DriverError, EventBus, p4_compile_standalone, p4_pipeline_from_json
from lab.engines.run_queue import QueuedRunManager as RunManager
from lab.web_security import same_origin
from lab.model import (APP_TYPES, DEVICE_TYPES, EVENT_ACTIONS, FRR_DAEMONS, PROJECT_VERSION, empty_project,
                       validate_project, validate_scenario)

LAB_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(LAB_DIR)
WEB_DIR = os.path.join(LAB_DIR, "web")                 # klassik (vanilla JS) UI -> /classic
WEB_REACT_DIR = os.path.join(LAB_DIR, "web-react")     # React + shadcn UI (lab/ui dan yig'ilgan) -> /
SHARED_CSS = os.path.join(REPO_DIR, "studio", "web", "css")
PROJECTS_DIR = os.path.join(LAB_DIR, "projects")
EXAMPLES_DIR = os.path.join(LAB_DIR, "examples")
TEMPLATES_DIR = os.path.join(LAB_DIR, "templates")
PROJ_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
COOKIE = "netlab_token"


def _topology_options(body):
    # Preserve form clients' omitted-value convention for known options. Unknown
    # keys reach the generator's schema check instead of being silently dropped.
    from lab.topogen_plan import DEFAULTS
    optional = set().union(*DEFAULTS.values(), {"ipv6"})
    return {key: value for key, value in body.items()
            if key != "kind" and not (key in optional and value in (None, ""))}


def _read_templates():
    out = {"p4": {}, "sdn": {}}
    for kind, ext in (("p4", "*.p4"), ("sdn", "*.py")):
        for path in sorted(glob.glob(os.path.join(TEMPLATES_DIR, kind, ext))):
            with open(path) as f:
                out[kind][os.path.splitext(os.path.basename(path))[0]] = f.read()
    return out


def create_app(driver_name="demo", token=None):
    bus = EventBus()
    if driver_name == "real":
        from lab.drivers.mininet_driver import MininetDriver
        driver = MininetDriver(bus)
    else:
        from lab.drivers.demo_driver import DemoDriver
        driver = DemoDriver(bus)

    app = FastAPI(title="NetLab", docs_url="/api/docs", redoc_url=None)
    app.state.driver = driver
    app.state.bus = bus
    app.state.token = token
    runs = RunManager(bus, driver)
    app.state.runs = runs
    os.makedirs(PROJECTS_DIR, exist_ok=True)

    @app.on_event("startup")
    async def _startup():
        bus.bind_loop(asyncio.get_running_loop())

    @app.on_event("shutdown")
    def _shutdown():
        runs.close()
        try:
            driver.stop()
        except Exception:  # noqa: BLE001
            pass

    # ───────────── token himoyasi ─────────────
    def _authorized(cookies, query):
        if not app.state.token:
            return True
        t = cookies.get(COOKIE) or query.get("token")
        return bool(t) and secrets.compare_digest(t, app.state.token)

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request.headers.get("origin"), request.url):
            return JSONResponse({"error": "Origin denied"}, status_code=403)
        if not app.state.token:
            return await call_next(request)
        q = request.query_params
        if q.get("token") and secrets.compare_digest(q.get("token"), app.state.token):
            resp = RedirectResponse(url=request.url.path) if request.url.path == "/" else await call_next(request)
            resp.set_cookie(COOKIE, app.state.token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
            return resp
        if not _authorized(request.cookies, q):
            return JSONResponse({"error": "token kerak: http://host:port/?token=..."}, status_code=401)
        return await call_next(request)

    @app.exception_handler(DriverError)
    async def _driver_error(_req, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _value_error(_req, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    # ───────────── ma'lumot ─────────────
    @app.get("/api/info")
    def info():
        from lab import wifi as W
        wifi_model = {"mcs": W.MCS, "width_mult": {str(k): v for k, v in W.WIDTH_MULT.items()},
                      "efficiency": W.EFFICIENCY, "ref_loss": {str(k): v for k, v in W.REF_LOSS.items()},
                      "min_rssi": W.MIN_RSSI, "exponent": W.EXPONENT, "ap_defaults": W.AP_DEFAULTS,
                      "standards": W.STANDARDS, "channels": W.CHANNELS}
        from lab import apps as A
        from lab.model import APP_TYPES
        app_models = {"types": list(APP_TYPES) + list(A.MODEL_TYPES),
                      "codecs": {k: {"label": v["label"], "payload": v["payload"]} for k, v in A.CODECS.items()},
                      "defaults": {"voip": {"codec": "g711"}, "http": {"page": "500KB", "think": 5},
                                   "ftp": {"file": "10MB", "direction": "download"},
                                   "video": {"quality": "720p", "target_buffer": 20.0},
                                   "http3": {"streams": 4, "rate": "4Mbps"}}}
        return {**driver.info(), "device_types": DEVICE_TYPES, "frr_daemons": FRR_DAEMONS, "wifi": wifi_model,
                "app_models": app_models, "project_version": PROJECT_VERSION, "status": driver.status()}

    @app.get("/api/resources")
    def resources():
        return runs.resource_status()

    @app.get("/api/templates")
    def templates():
        return _read_templates()

    # ───────────── loyihalar ─────────────
    def _proj_path(name, examples=False):
        if not PROJ_NAME_RE.match(name or ""):
            raise HTTPException(400, "Loyiha nomi: harf/raqam/_/- (1-40)")
        return os.path.join(EXAMPLES_DIR if examples else PROJECTS_DIR, f"{name}.json")

    @app.get("/api/projects")
    def list_projects():
        def ls(d, example):
            out = []
            for p in sorted(glob.glob(os.path.join(d, "*.json"))):
                n = os.path.splitext(os.path.basename(p))[0]
                out.append({"name": n, "example": example, "mtime": os.path.getmtime(p)})
            return out
        return {"projects": ls(PROJECTS_DIR, False), "examples": ls(EXAMPLES_DIR, True)}

    @app.get("/api/projects/{name}")
    def get_project(name: str, example: bool = False):
        path = _proj_path(name, example)
        if not os.path.exists(path):
            raise HTTPException(404, "topilmadi")
        with open(path) as f:
            return json.load(f)

    @app.put("/api/projects/{name}")
    def save_project(name: str, project: dict = Body(...)):
        project["name"] = name
        errors = validate_project(project)
        path = _proj_path(name)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(project, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return {"ok": True, "warnings": errors}

    @app.delete("/api/projects/{name}")
    def delete_project(name: str):
        path = _proj_path(name)
        if os.path.exists(path):
            os.remove(path)
        return {"ok": True}

    @app.post("/api/validate")
    def validate(project: dict = Body(...)):
        return {"errors": validate_project(project)}

    @app.get("/api/new")
    def new_project(name: str = "untitled"):
        return empty_project(name)

    # ───────────── tarmoq ─────────────
    @app.get("/api/net/status")
    def net_status():
        return driver.status()

    @app.get("/api/net/project")
    def net_project():
        """Ishlayotgan tarmoq loyihasi (sahifa qayta yuklanganda UI shu bilan sinxronlanadi)."""
        return {"running": driver.running, "project": driver.project}

    @app.post("/api/net/start")
    def net_start(project: dict = Body(..., embed=True)):
        return driver.start(project)

    @app.post("/api/net/stop")
    def net_stop():
        return driver.stop()

    @app.post("/api/net/device/{name}")
    def net_add_device(name: str, project: dict = Body(..., embed=True)):
        return driver.add_device(project, name)

    @app.delete("/api/net/device/{name}")
    def net_remove_device(name: str):
        return driver.remove_device(name)

    @app.put("/api/net/device/{name}/config")
    def net_device_config(name: str, project: dict = Body(..., embed=True)):
        return driver.apply_device_config(project, name)

    @app.post("/api/net/link/{lid}")
    def net_add_link(lid: str, project: dict = Body(..., embed=True)):
        return driver.add_link(project, lid)

    @app.delete("/api/net/link/{lid}")
    def net_remove_link(lid: str):
        return driver.remove_link(lid)

    @app.patch("/api/net/link/{lid}")
    def net_update_link(lid: str, body: dict = Body(...)):
        return driver.update_link(lid, params=body.get("params"), up=body.get("up"))

    @app.get("/api/net/inspect/{name}")
    def net_inspect(name: str):
        return driver.inspect(name)

    @app.post("/api/net/capture")
    def net_capture(body: dict = Body(...)):
        driver.set_capture(body.get("on", True))
        return driver.status()

    # P4
    @app.post("/api/p4/compile")
    def p4_compile(body: dict = Body(...)):
        """Tarmoqsiz kompilyatsiya + pipeline ko'rinishi (p4c bo'lmasa — manba tahlili)."""
        src = body.get("source", "")
        res = p4_compile_standalone(src, os.path.join("/tmp", "netlab-compile"))
        if res["ok"]:
            res["pipeline"] = p4_pipeline_from_json(res["json_path"])
        else:
            from lab.drivers.demo_driver import parse_p4_outline
            res["pipeline"] = parse_p4_outline(src)
            res["outline_only"] = True
        res.pop("json_path", None)
        return res

    @app.post("/api/net/p4/{name}/load")
    def p4_load(name: str, project: dict = Body(..., embed=True)):
        res = driver.p4_load(project, name)
        if res.get("ok") and res.get("json_path"):
            res["pipeline"] = p4_pipeline_from_json(res["json_path"])
        res.pop("json_path", None)
        return res

    @app.post("/api/net/p4/{name}/runtime")
    def p4_runtime(name: str, body: dict = Body(...)):
        return driver.p4_runtime(name, body.get("commands", ""))

    # SDN
    @app.post("/api/net/sdn/{cname}/restart")
    def sdn_restart(cname: str, project: dict = Body(..., embed=True)):
        return driver.controller_restart(project, cname)

    @app.get("/api/net/sdn/{cname}/log")
    def sdn_log(cname: str):
        return {"log": driver.controller_log(cname)}

    # FRR
    @app.get("/api/net/frr/{name}/running-config")
    def frr_running(name: str):
        return {"config": driver.frr_running_config(name)}

    # ───────────── ssenariylar va natijalar (OPNET: DES -> Results -> Compare) ─────────────
    @app.get("/api/engines")
    def engines():
        return {"engines": runs.engines(), "app_types": APP_TYPES, "event_actions": EVENT_ACTIONS}

    @app.post("/api/scenario/validate")
    def scenario_validate(body: dict = Body(...)):
        project = body.get("project")
        errors = validate_project(project)
        if not errors:
            from lab.project_shape import project_for_execution
            errors = validate_scenario(project_for_execution(project), body.get("scenario"))
        return {"errors": errors}

    @app.post("/api/traffic/import")
    def traffic_import(body: dict = Body(...)):
        from lab.traffic import import_matrix
        return import_matrix(body.get("project"), body)

    @app.post("/api/scenario/preflight")
    def scenario_preflight(body: dict = Body(...)):
        return runs.preflight(body.get("project"), body.get("scenario"), body.get("engine", "analytic"))

    @app.post("/api/runs")
    def run_start(body: dict = Body(...)):
        return runs.start(body.get("project") or {}, body.get("scenario"), body.get("engine", "analytic"))

    @app.post("/api/runs/{project}/{run_id}/cancel")
    def run_cancel(project: str, run_id: str):
        try:
            return runs.cancel(project, run_id)
        except FileNotFoundError:
            raise HTTPException(404, "run not found")

    @app.post("/api/studies")
    def study_start(body: dict = Body(...)):
        return runs.start_study(body.get("project") or {}, body.get("scenario"), body.get("engine", "analytic"),
                                body.get("param") or {}, body.get("values") or [],
                                body.get("param2") or None, body.get("values2") or None)

    @app.get("/api/studies/{project}/{study_id}")
    def study_status(project: str, study_id: str):
        from lab.engines.study_status import status
        try:
            return status(runs, project, study_id)
        except FileNotFoundError:
            raise HTTPException(404, "tadqiqot topilmadi")

    @app.post("/api/studies/{project}/{study_id}/cancel")
    def study_cancel(project: str, study_id: str):
        from lab.engines.study_status import cancel
        try:
            return cancel(runs, project, study_id)
        except FileNotFoundError:
            raise HTTPException(404, "tadqiqot topilmadi")

    @app.post("/api/experiments")
    def experiment_start(body: dict = Body(...)):
        return runs.start_experiment(body.get("project") or {}, body.get("scenario"), body.get("engine", "analytic"),
                                     body.get("repetitions", 10), body.get("seed", 1))

    @app.post("/api/campaigns/preview")
    def campaign_preview(body: dict = Body(...)):
        return runs.campaigns.preview(body)

    @app.post("/api/campaigns")
    def campaign_start(body: dict = Body(...)):
        return runs.campaigns.start(body)

    def _campaign_call(method, project, cid):
        try:
            return method(project, cid)
        except FileNotFoundError:
            raise HTTPException(404, "campaign not found")

    @app.get("/api/campaigns/{project}")
    def campaign_list(project: str, limit: int = 100, offset: int = 0):
        return runs.campaigns.store.list(project, limit, offset)

    @app.get("/api/campaigns/{project}/{cid}")
    def campaign_get(project: str, cid: str):
        return _campaign_call(runs.campaigns.status, project, cid)

    @app.post("/api/campaigns/{project}/{cid}/cancel")
    def campaign_cancel(project: str, cid: str):
        return _campaign_call(runs.campaigns.cancel, project, cid)

    @app.get("/api/campaigns/{project}/{cid}/report.json")
    def campaign_json(project: str, cid: str, compact: bool = False):
        report = _campaign_call(runs.campaigns.analysis, project, cid)[1]
        if compact:
            from lab.campaign_report import compact_report
            report = compact_report(report)
        return report

    @app.get("/api/campaigns/{project}/{cid}/report.html")
    def campaign_html(project: str, cid: str):
        from lab.campaign_report import campaign_report
        report = _campaign_call(runs.campaigns.analysis, project, cid)[1]
        return _html(campaign_report(report), f"{cid}.html")

    @app.get("/api/campaigns/{project}/{cid}/export.zip")
    def campaign_zip(project: str, cid: str):
        from lab.campaign_report import campaign_bundle
        plan, report, cohorts = _campaign_call(runs.campaigns.analysis, project, cid)
        return Response(campaign_bundle(plan, report, cohorts), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{cid}.zip"'})

    def _experiment_runs(project, experiment_id):
        if not isinstance(experiment_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", experiment_id):
            raise ValueError("invalid experiment_id")
        rs = [runs.get(project, r["run_id"]) for r in runs.list(project, experiment_id=experiment_id)]
        if not rs:
            raise HTTPException(404, "experiment not found")
        from lab.experiment_inputs import validate_cohort
        validate_cohort(rs)
        rs.sort(key=lambda r: r["replication_index"])
        return rs

    def _experiment(project, experiment_id, start_s=None, end_s=None):
        rs = _experiment_runs(project, experiment_id)
        from lab.experiments import summarize
        return rs, summarize(rs, start_s=start_s, end_s=end_s)

    def _comparison(body):
        from lab.experiment_comparison import compare
        baseline = _experiment_runs(body.get("project"), body.get("baseline_id"))
        candidate = _experiment_runs(body.get("project"), body.get("candidate_id"))
        result = compare(baseline, candidate, start_s=body.get("start_s"), end_s=body.get("end_s"))
        return baseline, candidate, result

    @app.post("/api/experiments/compare")
    def experiment_compare(body: dict = Body(...)):
        return _comparison(body)[2]

    @app.post("/api/experiments/compare/report.html")
    def experiment_compare_html(body: dict = Body(...)):
        from lab.experiment_comparison_report import comparison_report
        return _html(comparison_report(_comparison(body)[2]), "experiment-comparison.html")

    @app.post("/api/experiments/compare/export.zip")
    def experiment_compare_zip(body: dict = Body(...)):
        from lab.experiment_comparison_report import comparison_bundle
        baseline, candidate, result = _comparison(body)
        return Response(comparison_bundle(baseline, candidate, result), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="experiment-comparison.zip"'})

    @app.get("/api/experiments/{project}/{experiment_id}")
    def experiment_get(project: str, experiment_id: str, start_s: float | None = None, end_s: float | None = None):
        return _experiment(project, experiment_id, start_s, end_s)[1]

    @app.get("/api/experiments/{project}/{experiment_id}/report.html")
    def experiment_html(project: str, experiment_id: str, start_s: float | None = None, end_s: float | None = None):
        from lab.report import experiment_report
        return _html(experiment_report(_experiment(project, experiment_id, start_s, end_s)[1]), f"{experiment_id}.html")

    @app.get("/api/experiments/{project}/{experiment_id}/export.zip")
    def experiment_zip(project: str, experiment_id: str, start_s: float | None = None, end_s: float | None = None):
        from lab.report import experiment_bundle
        rs, summary = _experiment(project, experiment_id, start_s, end_s)
        return Response(experiment_bundle(rs, summary), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{experiment_id}.zip"'})

    @app.post("/api/topogen")
    def topogen(body: dict = Body(...)):
        """Topologiya generatori (ring | star | tree | campus) -> loyiha JSON (saqlanmaydi)."""
        from lab.topogen import generate
        p = generate(body.get("kind"), **_topology_options(body))
        errs = validate_project(p)
        if errs:
            raise ValueError("; ".join(errs[:5]))
        return p

    @app.post("/api/topogen/plan")
    def topogen_plan(body: dict = Body(...)):
        """Count nodes, links and applications without allocating a topology."""
        from lab.topogen import plan
        return plan(body.get("kind"), **_topology_options(body))

    @app.post("/api/analysis/survivability")
    def survivability(body: dict = Body(...)):
        """Nosozliklar ta'siri (N-1; order=2 — N-2 juftliklar; srlg — umumiy xavf guruhlari) + sig'im
        rejasi — analitik model, sinxron (N-1 bir necha soniya, N-2 o'nlab soniyagacha)."""
        from lab.survivability import analyze
        project = body.get("project") or {}
        errs = validate_project(project)
        if errs:
            raise ValueError("; ".join(errs[:5]))
        sc = body.get("scenario")
        sc_obj = (project.get("scenarios") or {}).get(sc) if isinstance(sc, str) else sc
        if not sc_obj:
            raise ValueError(f"ssenariy '{sc}' yo'q")
        errs = validate_scenario(project, sc_obj)
        if errs:
            raise ValueError("; ".join(errs[:5]))
        targets = body.get("targets", "both")
        if targets not in ("links", "devices", "both"):
            raise ValueError("targets: links | devices | both")
        try:
            order = int(body.get("order") or 1)
            max_cases = int(body.get("max_cases") or 2000)
        except (TypeError, ValueError):
            raise ValueError("order / max_cases butun son bo'lsin")
        if order not in (1, 2) or not 1 <= max_cases <= 20000:
            raise ValueError("order: 1 | 2; max_cases 1..20000")
        rep = analyze(project, sc_obj, targets, include_hosts=bool(body.get("include_hosts")),
                      order=order, srlg=body.get("srlg"), max_cases=max_cases,
                      exclude_access=bool(body.get("exclude_access")))
        rep["scenario"] = sc if isinstance(sc, str) else rep.get("scenario")
        return rep

    @app.post("/api/analysis/survivability/report")
    def survivability_report_html(body: dict = Body(...)):
        from lab.report import survivability_report
        return _html(survivability_report(survivability(body)), "survivability.html")

    @app.post("/api/analysis/sla")
    def analyze_sla(body: dict = Body(...)):
        """SLA (Service Level Agreement) muvofiqlik tahlili."""
        from lab.apps import evaluate_sla

        run_obj = body.get("run")
        if run_obj is None:
            project = body.get("project")
            run_id = body.get("run_id")
            if not project or not run_id:
                raise HTTPException(400, "project va run_id yoki run obyekti berilishi kerak")
            try:
                run_obj = runs.get(project, run_id)
            except FileNotFoundError:
                raise HTTPException(404, "natija topilmadi")

        if not isinstance(run_obj, dict):
            raise HTTPException(400, "run obyekti noto'g'ri formatda")

        sla_spec = body.get("sla") or body.get("sla_spec") or body.get("spec") or body.get("rules")
        if sla_spec is None:
            candidate_keys = {"min_throughput_bps", "min_throughput", "max_delay_ms", "max_delay",
                              "max_jitter_ms", "max_jitter", "max_loss_pct", "max_loss", "min_mos",
                              "target_compliance_pct"}
            if any(k in body for k in candidate_keys):
                sla_spec = {k: body[k] for k in candidate_keys if k in body}
            else:
                sla_spec = {}

        return evaluate_sla(run_obj, sla_spec)

    @app.get("/api/runs/{project}")
    def run_list(project: str, limit: int | None = None, offset: int = 0, status: str | None = None,
                 engine: str | None = None, study_id: str | None = None, experiment_id: str | None = None):
        filters = {"status": status, "engine": engine, "study_id": study_id, "experiment_id": experiment_id}
        if limit is None and offset == 0 and all(v is None for v in filters.values()):
            return {"runs": runs.list(project)}
        return runs.page(project, 100 if limit is None else limit, offset, **filters)

    @app.post("/api/runs/{project}/reindex")
    def run_reindex(project: str):
        return runs.reindex(project)

    @app.get("/api/results/{project}/invalid")
    def invalid_results(project: str, limit: int = 100, offset: int = 0):
        return runs.invalid_results(project, limit, offset)

    @app.get("/api/runs/{project}/{run_id}")
    def run_get(project: str, run_id: str):
        try:
            return runs.get(project, run_id)
        except FileNotFoundError:
            raise HTTPException(404, "natija topilmadi")

    @app.patch("/api/runs/{project}/{run_id}")
    def run_label(project: str, run_id: str, body: dict = Body(...)):
        return runs.update_label(project, run_id, body.get("label", ""))

    @app.delete("/api/runs/{project}/{run_id}")
    def run_delete(project: str, run_id: str):
        runs.delete(project, run_id)
        return {"ok": True}

    # ───────────── hisobotlar (lab/report.py): mustaqil HTML + CSV zip ─────────────
    def _run_or_404(project, run_id):
        try:
            return runs.get(project, run_id)
        except FileNotFoundError:
            raise HTTPException(404, "natija topilmadi")

    def _html(doc, fname):
        return HTMLResponse(doc, headers={"Content-Disposition": f'inline; filename="{fname}"'})

    @app.get("/api/runs/{project}/{run_id}/report.html")
    def run_report_html(project: str, run_id: str):
        from lab.report import run_report
        return _html(run_report(_run_or_404(project, run_id)), f"{run_id}.html")

    @app.get("/api/runs/{project}/{id}/report.tex")
    def run_report_tex(project: str, id: str):
        from lab.report_latex import generate_run_latex
        return Response(generate_run_latex(_run_or_404(project, id)), media_type="application/x-tex",
                        headers={"Content-Disposition": f'attachment; filename="{id}.tex"'})

    @app.get("/api/runs/{project}/{run_id}/report.tex", include_in_schema=False)
    def run_report_tex_alias(project: str, run_id: str):
        from lab.report_latex import generate_run_latex
        return Response(generate_run_latex(_run_or_404(project, run_id)), media_type="application/x-tex",
                        headers={"Content-Disposition": f'attachment; filename="{run_id}.tex"'})

    @app.get("/api/runs/{project}/{run_id}/window")
    def run_window(project: str, run_id: str, start_s: float | None = None, end_s: float | None = None):
        from lab.analysis_windows import analyze
        return analyze(_run_or_404(project, run_id), start_s, end_s)

    @app.get("/api/runs/{project}/{run_id}/export.zip")
    def run_export_zip(project: str, run_id: str):
        from lab.report import csv_bundle
        data = csv_bundle(_run_or_404(project, run_id))
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})

    @app.post("/api/reports/compare")
    def report_compare(body: dict = Body(...)):
        from lab.report import compare_report
        project, ids = body.get("project"), body.get("run_ids") or []
        if not isinstance(ids, list) or not (1 <= len(ids) <= 20):
            raise ValueError("run_ids: 1..20 ta natija")
        return _html(compare_report([_run_or_404(project, rid) for rid in ids]), "solishtirish.html")

    @app.post("/api/reports/latex")
    def report_latex(body: dict = Body(...)):
        from lab.report_latex import generate_run_latex, generate_comparison_latex
        if not isinstance(body, dict):
            raise ValueError("so'rov tanasi lug'at bo'lishi kerak")

        # Direct run payload
        if "run" in body and isinstance(body["run"], dict):
            run = body["run"]
            fname = f"{run.get('run_id', 'report')}.tex"
            return Response(generate_run_latex(run), media_type="application/x-tex",
                            headers={"Content-Disposition": f'attachment; filename="{fname}"'})

        # Direct runs array
        if "runs" in body and isinstance(body["runs"], list):
            target_runs = body["runs"]
            if not target_runs:
                raise ValueError("runs: kamida bitta natija kerak")
            if len(target_runs) == 1:
                return Response(generate_run_latex(target_runs[0]), media_type="application/x-tex",
                                headers={"Content-Disposition": f'attachment; filename="{target_runs[0].get("run_id", "report")}.tex"'})
            return Response(generate_comparison_latex(target_runs), media_type="application/x-tex",
                            headers={"Content-Disposition": 'attachment; filename="solishtirish.tex"'})

        # Project and run_ids or run_id or id
        project = body.get("project")
        ids = body.get("run_ids")
        if ids is None and "run_id" in body:
            ids = [body["run_id"]]
        if ids is None and "id" in body:
            ids = [body["id"]]
        if not isinstance(ids, list) or not (1 <= len(ids) <= 20):
            raise ValueError("run_ids: 1..20 ta natija")

        target_runs = [_run_or_404(project, rid) for rid in ids]
        if len(target_runs) == 1:
            doc = generate_run_latex(target_runs[0])
            fname = f"{ids[0]}.tex"
        else:
            doc = generate_comparison_latex(target_runs)
            fname = "solishtirish.tex"

        return Response(doc, media_type="application/x-tex",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    @app.get("/api/studies/{project}/{study_id}/report.html")
    def study_report_html(project: str, study_id: str):
        from lab.report import study_report
        from lab.engines.study_status import status
        rows = runs.list(project, study_id=study_id)
        rs = []
        for row in rows:
            try:
                rs.append(runs.get(project, row["run_id"]))
            except FileNotFoundError:
                pass  # A concurrent deletion remains visible through the surviving rows' planned count.
        if not rs:
            raise HTTPException(404, "tadqiqot natijalari topilmadi")
        try:
            state = status(runs, project, study_id)
        except FileNotFoundError:
            raise HTTPException(404, "tadqiqot natijalari topilmadi")
        return _html(study_report(rs, state), f"{study_id}.html")

    # ───────────── Telemetriya: L4 Observatory ─────────────
    @app.get("/api/telemetry/streams")
    @app.get("/api/telemetry/l4/streams")
    def telemetry_streams():
        from lab.telemetry import telemetry_manager
        return {"streams": telemetry_manager.get_active_streams()}

    @app.get("/api/telemetry/stream/{stream_id:path}")
    @app.get("/api/telemetry/l4/streams/{stream_id:path}")
    def telemetry_stream_detail(stream_id: str):
        from urllib.parse import unquote
        from lab.telemetry import telemetry_manager
        detail = telemetry_manager.get_stream_detail(stream_id)
        if not detail and ("%" in stream_id or "+" in stream_id):
            detail = telemetry_manager.get_stream_detail(unquote(stream_id))
        if not detail:
            raise HTTPException(404, f"Oqim '{stream_id}' topilmadi")
        return detail

    @app.get("/api/telemetry/stats")
    @app.get("/api/telemetry/l4/stats")
    def telemetry_stats():
        from lab.telemetry import telemetry_manager
        return telemetry_manager.get_global_stats()

    @app.get("/api/telemetry/drops")
    @app.get("/api/telemetry/l4/drops")
    def telemetry_drops(limit: int = 50):
        from lab.telemetry import telemetry_manager
        safe_limit = max(1, min(int(limit), 1000))
        return {"drops": telemetry_manager.get_recent_drops(limit=safe_limit)}

    @app.get("/api/telemetry/queues")
    @app.get("/api/telemetry/l4/queues")
    def telemetry_queues(dev: Optional[str] = None, bloat_only: bool = False):
        from lab.telemetry import telemetry_manager
        queues = telemetry_manager.get_queues(dev=dev)
        if not queues and not getattr(telemetry_manager, "_running", False):
            telemetry_manager.poll_once()
            queues = telemetry_manager.get_queues(dev=dev)
        if bloat_only:
            queues = [q for q in queues if q.get("bufferbloat_detected")]
        return {
            "queues": queues,
            "bufferbloat": telemetry_manager.get_bufferbloat_report(),
            "summary": telemetry_manager.get_queue_summary(),
        }

    @app.get("/api/telemetry/bufferbloat")
    @app.get("/api/telemetry/l4/bufferbloat")
    def telemetry_bufferbloat():
        from lab.telemetry import telemetry_manager
        return telemetry_manager.get_bufferbloat_report()

    @app.get("/api/telemetry/queue/{dev:path}")
    @app.get("/api/telemetry/l4/queues/{dev:path}")
    def telemetry_queue_detail(dev: str):
        from lab.telemetry import telemetry_manager
        queues = telemetry_manager.get_queues(dev=dev)
        if not queues:
            raise HTTPException(404, f"Interfeys '{dev}' navbat telemetriyasi topilmadi")
        history = telemetry_manager.get_queue_history(dev=dev)
        return {
            "dev": dev,
            "latest": queues,
            "history": history,
        }

    @app.get("/api/dataplane/health")
    @app.get("/api/net/dataplane/health")
    def dataplane_health():
        from lab.telemetry import telemetry_manager
        drv_st = driver.status()
        q_summary = telemetry_manager.get_queue_summary()
        bloat = telemetry_manager.get_bufferbloat_report()
        g_stats = telemetry_manager.get_global_stats()
        return {
            "status": "ok",
            "healthy": True,
            "driver": drv_st,
            "queues": q_summary,
            "bufferbloat": bloat.get("summary", {}),
            "telemetry": g_stats,
        }

    @app.post("/api/telemetry/dissect")
    @app.post("/api/telemetry/l4/dissect")
    def telemetry_dissect(body: dict):
        from lab.telemetry import telemetry_manager
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON body lug'at (dict) bo'lishi kerak")
        raw_hex = body.get("hex")
        raw_b64 = body.get("base64")
        if raw_hex is not None:
            clean_hex = str(raw_hex).strip()
            if clean_hex.lower().startswith("0x"):
                clean_hex = clean_hex[2:]
            clean_hex = re.sub(r"\s+", "", clean_hex)
            if not clean_hex:
                raise HTTPException(400, "Bo'sh hex ma'lumot")
            try:
                raw_bytes = bytes.fromhex(clean_hex)
            except ValueError as e:
                raise HTTPException(400, f"Noto'g'ri hex format: {e}")
        elif raw_b64 is not None:
            clean_b64 = str(raw_b64).strip()
            if not clean_b64:
                raise HTTPException(400, "Bo'sh base64 ma'lumot")
            import base64
            try:
                raw_bytes = base64.b64decode(clean_b64)
            except Exception as e:
                raise HTTPException(400, f"Noto'g'ri base64 format: {e}")
        else:
            raise HTTPException(400, "'hex' yoki 'base64' maydoni kerak")
        return telemetry_manager.dissect_raw(raw_bytes)

    @app.post("/api/pdu/inspect")
    def pdu_inspect(body: dict):
        from lab.telemetry import telemetry_manager
        if not isinstance(body, dict):
            body = {}
        pkt = body.get("packet") if isinstance(body.get("packet"), dict) else body
        device = str(body.get("device") or "Router")
        dev_type = str(body.get("device_type") or "router")
        return telemetry_manager.inspect_pdu(pkt, device_name=device, device_type=dev_type)

    # ───────────── OPNET Workloads & SLA & LaTeX Hisobot ─────────────
    @app.get("/api/telemetry/l4/workloads/voip")
    def telemetry_workload_voip(
        delay_ms: float = 30.0,
        jitter_ms: float = 2.0,
        loss_pct: float = 0.0,
        codec: str = "G.711",
    ):
        from lab.telemetry import telemetry_manager
        return telemetry_manager.evaluate_voip_call(
            delay_ms=delay_ms, jitter_ms=jitter_ms, loss_pct=loss_pct, codec=codec
        )

    @app.get("/api/telemetry/l4/workloads/video")
    def telemetry_workload_video():
        from lab.telemetry import telemetry_manager
        return telemetry_manager.get_video_streaming_stats()

    @app.get("/api/telemetry/l4/sla")
    def telemetry_sla():
        from lab.telemetry import telemetry_manager
        return telemetry_manager.get_sla_report()

    @app.get("/api/telemetry/l4/report/latex")
    def telemetry_latex_report(title: str = "NetLab Research Evaluation Report"):
        from lab.telemetry import telemetry_manager
        latex = telemetry_manager.export_latex_report(title=title)
        return Response(content=latex, media_type="text/plain; charset=utf-8")

    @app.post("/api/telemetry/simulate")
    def telemetry_simulate(body: dict):
        from lab.telemetry import telemetry_manager, TCPInfoCollector
        if not isinstance(body, dict):
            body = {}
        src_ip = str(body.get("src_ip") or "10.0.1.10")
        try:
            src_port = int(body.get("src_port") or 45678)
        except (ValueError, TypeError):
            src_port = 45678
        dst_ip = str(body.get("dst_ip") or "10.0.2.20")
        try:
            dst_port = int(body.get("dst_port") or 80)
        except (ValueError, TypeError):
            dst_port = 80
        try:
            steps = max(1, min(int(body.get("steps") or 10), 200))
        except (ValueError, TypeError):
            steps = 10
        has_loss = bool(body.get("has_loss", False))
        algo = str(body.get("algo") or "cubic")

        for step in range(steps):
            is_loss = has_loss and (step == steps // 2 or (steps > 7 and step == 7))
            sample = TCPInfoCollector.simulate_sample(
                src_ip, src_port, dst_ip, dst_port, step, has_loss=is_loss, algo=algo
            )
            telemetry_manager.ingest_tcp_sample(sample)
            if getattr(telemetry_manager, "queue_tracker", None):
                try:
                    telemetry_manager.queue_tracker.simulate_step(float(step))
                except Exception:
                    pass
        return {"ok": True, "stream_id": f"{src_ip}:{src_port}->{dst_ip}:{dst_port}", "steps": steps}

    @app.websocket("/ws/telemetry")
    async def ws_telemetry(ws: WebSocket):
        if not same_origin(ws.headers.get("origin"), ws.url):
            await ws.close(code=1008, reason="Origin denied")
            return
        if not _authorized(ws.cookies, ws.query_params):
            await ws.close(code=4401)
            return
        await ws.accept()
        from lab.telemetry import telemetry_manager
        try:
            telemetry_manager.bind_loop(asyncio.get_running_loop())
        except Exception:
            pass
        q = telemetry_manager.subscribe()
        await ws.send_json({"type": "init", "streams": telemetry_manager.get_active_streams()})

        async def pump():
            while True:
                ev = await q.get()
                batch = [ev]
                while not q.empty() and len(batch) < 50:
                    batch.append(q.get_nowait())
                await ws.send_text(json.dumps(batch if len(batch) > 1 else ev, default=str))

        task = asyncio.create_task(pump())
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            task.cancel()
            telemetry_manager.unsubscribe(q)

    # ───────────── WebSocket: hodisalar ─────────────
    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        if not same_origin(ws.headers.get("origin"), ws.url):
            await ws.close(code=1008, reason="Origin denied")
            return
        if not _authorized(ws.cookies, ws.query_params):
            await ws.close(code=4401)
            return
        await ws.accept()
        q = bus.subscribe()
        await ws.send_json({"type": "state", **driver.status()})

        async def pump():
            while True:
                ev = await q.get()
                batch = [ev]
                # packet hodisalarini to'plab yuborish (kamroq kadr)
                while not q.empty() and len(batch) < 200:
                    batch.append(q.get_nowait())
                await ws.send_text(json.dumps(batch if len(batch) > 1 else ev, default=str))

        task = asyncio.create_task(pump())
        try:
            while True:
                await ws.receive_text()   # ping/keepalive
        except WebSocketDisconnect:
            pass
        finally:
            task.cancel()
            bus.unsubscribe(q)

    # ───────────── WebSocket: terminal ─────────────
    @app.websocket("/ws/term/{name}")
    async def ws_term(ws: WebSocket, name: str, shell: str = "default"):
        if not same_origin(ws.headers.get("origin"), ws.url):
            await ws.close(code=1008, reason="Origin denied")
            return
        if not _authorized(ws.cookies, ws.query_params):
            await ws.close(code=4401)
            return
        await ws.accept()
        loop = asyncio.get_running_loop()
        try:
            term = await loop.run_in_executor(None, driver.open_terminal, name, shell)
        except DriverError as e:
            await ws.send_bytes(f"\r\n\x1b[31m{e}\x1b[0m\r\n".encode())
            await ws.close()
            return
        q = asyncio.Queue()
        term.on_output = lambda data: loop.call_soon_threadsafe(q.put_nowait, data)
        term.on_exit = lambda: loop.call_soon_threadsafe(q.put_nowait, None)
        term.start()

        async def pump():
            while True:
                data = await q.get()
                if data is None:
                    await ws.send_bytes(b"\r\n\x1b[2m[sessiya tugadi]\x1b[0m\r\n")
                    await ws.close()
                    return
                while not q.empty():
                    nxt = q.get_nowait()
                    if nxt is None:
                        q.put_nowait(None)
                        break
                    data += nxt
                await ws.send_bytes(data)

        task = asyncio.create_task(pump())
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("t") == "in":
                    term.write(msg.get("d", "").encode())
                elif msg.get("t") == "resize":
                    term.resize(int(msg.get("cols", 80)), int(msg.get("rows", 24)))
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            task.cancel()
            term.close()

    # ───────────── statik UI ─────────────
    if os.path.isdir(SHARED_CSS):
        app.mount("/shared", StaticFiles(directory=SHARED_CSS), name="shared")
    app.mount("/classic", StaticFiles(directory=WEB_DIR, html=True), name="classic")
    root_dir = WEB_REACT_DIR if os.path.exists(os.path.join(WEB_REACT_DIR, "index.html")) else WEB_DIR
    app.mount("/", StaticFiles(directory=root_dir, html=True), name="web")
    return app

"""Ssenariy ishga tushirishlari (runs): dvigatel tanlash, fon ijrosi, natijalarni saqlash.

Dvigatellar:
    analytic — oqim modeli (har doim mavjud, soniyalar)
    ns3      — ns-3 DES (pip install ns3), alohida jarayon
    mininet  — real emulyatsiya (real driverda tarmoq ishlab turganda)

Natijalar: lab/results/<loyiha>/<run_id>.json (gitignore). Progress va loglar
EventBus orqali {"type": "run", ...} hodisalari sifatida UI'ga boradi.
"""

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

from lab.model import normalize_link_params, parse_rate, validate_project, validate_scenario

STUDY_LINK_FIELDS = ("bw", "delay", "loss", "queue")
STUDY_APP_FIELDS = ("rate", "size")
MAX_STUDY_VALUES = 25

LAB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(LAB_DIR, "results")
_SAFE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")


def _ns3_available():
    return importlib.util.find_spec("ns") is not None or importlib.util.find_spec("ns3") is not None


class RunManager:
    def __init__(self, bus, driver):
        self.bus = bus
        self.driver = driver
        self.active = {}     # run_id -> {"progress", "engine", ...}
        self._lock = threading.Lock()
        os.makedirs(RESULTS_DIR, exist_ok=True)

    def engines(self):
        real_running = self.driver.name == "real" and self.driver.running
        return [
            {"id": "analytic", "label": "Analitik (oqim modeli)", "available": True,
             "hint": "soniyalar ichida; oqim darajasidagi taxmin (OPNET Flow Analysis kabi)"},
            {"id": "ns3", "label": "ns-3 (diskret-hodisali simulyatsiya)", "available": _ns3_available(),
             "hint": "paket darajasida, deterministik, real vaqtdan tez" if _ns3_available()
             else "o'rnatilmagan: pip install ns3"},
            {"id": "mininet", "label": "Mininet (real emulyatsiya)", "available": real_running,
             "hint": "haqiqiy TCP/UDP/FRR/OVS, real vaqtda" if real_running
             else "real driverda tarmoqni ishga tushiring"},
        ]

    # ---------- saqlash ----------
    def _dir(self, project_name):
        if not _SAFE.match(project_name or ""):
            raise ValueError("loyiha nomi noto'g'ri")
        d = os.path.join(RESULTS_DIR, project_name)
        os.makedirs(d, exist_ok=True)
        return d

    def list(self, project_name):
        d = self._dir(project_name)
        out = []
        for f in sorted(os.listdir(d), reverse=True):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, f)) as fh:
                    r = json.load(fh)
                out.append({k: r.get(k) for k in ("run_id", "scenario_name", "engine", "engine_label", "created",
                                                   "duration", "wall_s", "status", "label", "study_id",
                                                   "study_param", "study_value", "study_index")})
            except (OSError, ValueError):
                continue
        for rid, a in self.active.items():
            if a["project"] == project_name:
                out.insert(0, {"run_id": rid, "scenario_name": a["scenario_name"], "engine": a["engine"],
                               "status": "running", "progress": a["progress"], "created": a["created"],
                               "study_id": a.get("study_id"), "label": a.get("label")})
        return out

    def get(self, project_name, run_id):
        if not _SAFE.match(run_id or ""):
            raise ValueError("run_id noto'g'ri")
        with open(os.path.join(self._dir(project_name), run_id + ".json")) as f:
            return json.load(f)

    def delete(self, project_name, run_id):
        if not _SAFE.match(run_id or ""):
            raise ValueError("run_id noto'g'ri")
        p = os.path.join(self._dir(project_name), run_id + ".json")
        if os.path.exists(p):
            os.remove(p)

    def update_label(self, project_name, run_id, label):
        r = self.get(project_name, run_id)
        r["label"] = str(label)[:80]
        self._save(project_name, r)
        return r

    def _save(self, project_name, result):
        p = os.path.join(self._dir(project_name), result["run_id"] + ".json")
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(result, f)
        os.replace(tmp, p)

    # ---------- ishga tushirish ----------
    def start(self, project, scenario_name, engine):
        project = copy.deepcopy(project)
        sc = (project.get("scenarios") or {}).get(scenario_name)
        if not sc:
            raise ValueError(f"ssenariy '{scenario_name}' yo'q")
        errs = validate_project(project) + validate_scenario(project, sc)
        if errs:
            raise ValueError("; ".join(errs[:5]))
        eng = {e["id"]: e for e in self.engines()}.get(engine)
        if not eng:
            raise ValueError(f"dvigatel '{engine}' noma'lum")
        if not eng["available"]:
            raise ValueError(f"{eng['label']}: {eng['hint']}")
        pname = project.get("name") or "untitled"
        self._dir(pname)
        run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{engine}-{scenario_name}"[:40]
        run_id = re.sub(r"[^A-Za-z0-9_\-]", "_", run_id)
        with self._lock:
            if any(a["engine"] == "mininet" for a in self.active.values()) and engine == "mininet":
                raise ValueError("Mininet'da bir vaqtda faqat bitta ssenariy ishlaydi")
            self.active[run_id] = {"project": pname, "scenario_name": scenario_name, "engine": engine,
                                   "progress": 0.0, "created": time.time()}
        threading.Thread(target=self._worker, args=(run_id, pname, project, scenario_name, sc, engine),
                         daemon=True).start()
        return {"run_id": run_id}

    def _pub(self, run_id, **kw):
        a = self.active.get(run_id) or {}
        if "progress" in kw:
            a["progress"] = kw["progress"]
        self.bus.publish({"type": "run", "run_id": run_id, "project": a.get("project"),
                          "engine": a.get("engine"), **kw})

    # ---------- parametrik tadqiqot (OPNET "parametric study") ----------
    def start_study(self, project, scenario_name, engine, param, values):
        """Bitta parametrni qiymatlar ro'yxati bo'yicha o'zgartirib, har biri uchun alohida
        ishga tushirish (ketma-ket). param: {"kind": "link", "link", "field": bw|delay|loss|queue}
        yoki {"kind": "app", "app", "field": rate|size}."""
        project = copy.deepcopy(project)
        sc = (project.get("scenarios") or {}).get(scenario_name)
        if not sc:
            raise ValueError(f"ssenariy '{scenario_name}' yo'q")
        errs = validate_project(project) + validate_scenario(project, sc)
        if errs:
            raise ValueError("; ".join(errs[:5]))
        eng = {e["id"]: e for e in self.engines()}.get(engine)
        if not eng or not eng["available"]:
            raise ValueError(f"dvigatel '{engine}' mavjud emas")
        values = list(values or [])
        if not (2 <= len(values) <= MAX_STUDY_VALUES):
            raise ValueError(f"qiymatlar soni 2..{MAX_STUDY_VALUES}")
        kind, field = param.get("kind"), param.get("field")
        variants = []
        for v in values:
            p2, sc2 = copy.deepcopy(project), copy.deepcopy(sc)
            if kind == "link":
                lid = param.get("link")
                if lid not in p2["links"] or field not in STUDY_LINK_FIELDS:
                    raise ValueError("kabel yoki maydon noto'g'ri")
                newp = normalize_link_params({**(p2["links"][lid].get("params") or {}), field: v})
                p2["links"][lid]["params"] = newp
                if engine == "mininet":   # jonli tarmoqda — t=0 hodisasi bilan qo'llanadi
                    sc2["events"] = [{"t": 0, "action": "link_params", "link": lid, "params": {field: newp[field]}}] + list(sc2.get("events") or [])
            elif kind == "app":
                app = next((a for a in sc2.get("apps") or [] if a.get("id") == param.get("app")), None)
                if not app or field not in STUDY_APP_FIELDS:
                    raise ValueError("ilova yoki maydon noto'g'ri")
                if field == "rate":
                    parse_rate(v)
                    app["rate"] = str(v) if not isinstance(v, (int, float)) else f"{v}Mbps"
                else:
                    app["size"] = int(v)
            else:
                raise ValueError("param.kind: link | app")
            variants.append((v, p2, sc2))
        pname = project.get("name") or "untitled"
        self._dir(pname)
        study_id = re.sub(r"[^A-Za-z0-9_\-]", "_", time.strftime("%Y%m%d-%H%M%S") + f"-study-{scenario_name}"[:40])
        pdesc = {**param, "label": (f"{param.get('link')}.{field}" if kind == "link" else f"{param.get('app')}.{field}")}

        def go():
            n = len(variants)
            for i, (v, p2, sc2) in enumerate(variants):
                rid = f"{study_id}-v{i + 1:02d}"
                with self._lock:
                    self.active[rid] = {"project": pname, "scenario_name": scenario_name, "engine": engine,
                                        "progress": 0.0, "created": time.time(), "study_id": study_id,
                                        "label": f"{pdesc['label']}={v}"}
                self.bus.publish({"type": "study", "study_id": study_id, "project": pname, "index": i, "total": n,
                                  "value": v, "run_id": rid})
                self._worker(rid, pname, p2, scenario_name, sc2, engine,
                             extra={"study_id": study_id, "study_param": pdesc, "study_value": v, "study_index": i,
                                    "label": f"{pdesc['label']}={v}"})
            self.bus.publish({"type": "study", "study_id": study_id, "project": pname, "done": True, "total": n})

        threading.Thread(target=go, daemon=True).start()
        return {"study_id": study_id, "runs": len(variants)}

    def _worker(self, run_id, pname, project, sc_name, sc, engine, extra=None):
        prog = lambda f: self._pub(run_id, progress=round(f, 3))  # noqa: E731
        log = lambda m: self._pub(run_id, log=m)                  # noqa: E731
        t0 = time.time()
        try:
            if engine == "analytic":
                from lab.engines import analytic
                res = analytic.run(project, sc, prog, log)
            elif engine == "ns3":
                res = self._run_ns3(project, sc, prog, log)
            else:
                from lab.engines import emulation
                res = emulation.run(self.driver, sc, prog, log)
            res.update(status="done")
        except Exception as e:  # noqa: BLE001
            res = {"engine": engine, "status": "error", "error": f"{type(e).__name__}: {e}", "flows": {}}
            log(f"XATO: {e}")
        res.update(run_id=run_id, project=pname, scenario_name=sc_name, scenario=sc, created=t0,
                   topology={"devices": len(project["devices"]), "links": len(project["links"])})
        res.setdefault("wall_s", round(time.time() - t0, 2))
        if extra:
            res.update(extra)
        self._save(pname, res)
        self.active.pop(run_id, None)
        self._pub(run_id, done=True, status=res["status"], error=res.get("error"))

    def _run_ns3(self, project, sc, prog, log):
        with tempfile.TemporaryDirectory(prefix="netlab-ns3-") as td:
            inp, out = os.path.join(td, "in.json"), os.path.join(td, "out.json")
            with open(inp, "w") as f:
                json.dump({"project": project, "scenario": sc}, f)
            env = dict(os.environ, PYTHONPATH=os.path.dirname(LAB_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""))
            p = subprocess.Popen([sys.executable, "-m", "lab.engines.ns3_runner", inp, out],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            tail = []
            for line in p.stdout:
                line = line.strip()
                try:
                    msg = json.loads(line)
                except ValueError:
                    if line and "materialize symbols" not in line:
                        tail.append(line)
                        tail[:] = tail[-30:]
                    continue
                if "progress" in msg:
                    prog(msg["progress"])
                if msg.get("log"):
                    log(msg["log"])
            p.wait(timeout=30)
            if p.returncode != 0 or not os.path.exists(out):
                raise RuntimeError(f"ns-3 jarayoni xato bilan tugadi (kod {p.returncode}): " + " | ".join(tail[-6:]))
            with open(out) as f:
                return json.load(f)

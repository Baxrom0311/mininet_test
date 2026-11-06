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
import uuid

from lab.model import normalize_link_params, parse_rate, validate_project, validate_scenario

STUDY_LINK_FIELDS = ("bw", "delay", "loss", "queue", "qdisc")
STUDY_APP_FIELDS = ("rate", "size", "codec", "page", "think", "file")
MAX_STUDY_VALUES = 25

LAB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(LAB_DIR, "results")
_SAFE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")


def _ns3_available():
    return importlib.util.find_spec("ns") is not None or importlib.util.find_spec("ns3") is not None


def new_run_id():
    # 48 chars leaves room for study variant suffixes within the 60-char API limit.
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex


class RunManager:
    def __init__(self, bus, driver):
        self.bus = bus
        self.driver = driver
        self.active = {}     # run_id -> {"progress", "engine", ...}
        self._lock = threading.Lock()
        self._emulation_lock = threading.Lock()
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
                                                   "study_param", "study_value", "study_index",
                                                   "study_param2", "study_value2", "study_index2",
                                                   "experiment_id", "replication_index", "replication_count", "seed", "rng_run")})
            except (OSError, ValueError):
                continue
        with self._lock:
            active = list(self.active.items())
        for rid, a in active:
            if a["project"] == project_name:
                out.insert(0, {"run_id": rid, "scenario_name": a["scenario_name"], "engine": a["engine"],
                               "status": "running", "progress": a["progress"], "created": a["created"],
                               "study_id": a.get("study_id"), "label": a.get("label"),
                               **{k: a[k] for k in ("experiment_id", "replication_index", "replication_count", "seed", "rng_run") if k in a}})
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
    def _prepare(self, project, scenario_name, engine):
        if not isinstance(project, dict) or not isinstance(scenario_name, str) or not isinstance(engine, str):
            raise ValueError("project: object; scenario/engine: string")
        project = copy.deepcopy(project)
        scenarios = project.get("scenarios") or {}
        if not isinstance(scenarios, dict):
            raise ValueError("scenarios: object")
        sc = scenarios.get(scenario_name)
        if not sc:
            raise ValueError(f"ssenariy '{scenario_name}' yo'q")
        if not isinstance(sc, dict):
            raise ValueError("scenario: object")
        if engine == "mininet" and sc.get("ip_version", 4) == 6:
            raise ValueError("IPv6 faqat analytic va ns3 dvigatellarida qo'llab-quvvatlanadi")
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
        return project, sc, pname

    def start(self, project, scenario_name, engine, *, extra=None):
        project, sc, pname = self._prepare(project, scenario_name, engine)
        run_id = new_run_id()
        with self._lock:
            if any(a["engine"] == "mininet" for a in self.active.values()) and engine == "mininet":
                raise ValueError("Mininet'da bir vaqtda faqat bitta ssenariy ishlaydi")
            self.active[run_id] = {"project": pname, "scenario_name": scenario_name, "engine": engine,
                                   "progress": 0.0, "created": time.time(), **(extra or {})}
        args = (run_id, pname, project, scenario_name, sc, engine)
        if extra:
            self._launch_worker(*args, extra)
        else:
            self._launch_worker(*args)
        return {"run_id": run_id}

    def _launch_worker(self, *args):
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _study_worker(self, *args, **kwargs):
        return self._worker(*args, **kwargs)

    def _pub(self, run_id, **kw):
        a = self.active.get(run_id) or {}
        if "progress" in kw:
            a["progress"] = kw["progress"]
        self.bus.publish({"type": "run", "run_id": run_id, "project": a.get("project"),
                          "engine": a.get("engine"), **kw})

    # ---------- parametrik tadqiqot (OPNET "parametric study") ----------
    MAX_STUDY_RUNS = 49

    @staticmethod
    def _apply_param(p2, sc2, param, v, engine):
        """Bitta parametr qiymatini loyiha/ssenariy nusxasiga qo'llaydi (joyida)."""
        kind, field = param.get("kind"), param.get("field")
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
            elif field == "size":
                app["size"] = int(v)
            elif field == "think":
                app["think"] = float(v)
            elif field in ("page", "file"):
                app[field] = str(v) if not isinstance(v, (int, float)) else f"{v}MB"
            else:
                app["codec"] = str(v)
        elif kind == "ap":
            ap = param.get("ap")
            if (p2["devices"].get(ap) or {}).get("type") != "wifiap" or field not in ("tx_power", "channel"):
                raise ValueError("Wi-Fi AP yoki maydon noto'g'ri")
            if engine == "mininet":
                raise ValueError("AP radio tadqiqoti Mininet dvigatelida qo'llab-quvvatlanmaydi (radio yo'q)")
            p2["devices"][ap].setdefault("config", {})[field] = float(v) if field == "tx_power" else int(v)
        elif kind == "router":
            r = param.get("router")
            d = p2["devices"].get(r) or {}
            if d.get("type") != "router" or field not in ("rip_update", "rip_timeout"):
                raise ValueError("router yoki maydon noto'g'ri (rip_update | rip_timeout)")
            if engine == "mininet":
                raise ValueError("RIP taymer tadqiqoti Mininet dvigatelida qo'llab-quvvatlanmaydi (FRR qayta ishga tushadi)")
            targets = [r] if not param.get("all") else [n for n, x in p2["devices"].items() if x.get("type") == "router"]
            for n in targets:
                cfg = p2["devices"][n].setdefault("config", {})
                tm = dict(cfg.get("rip_timers") or {})
                tm[field.split("_", 1)[1]] = int(v)
                if field == "rip_update" and int(tm.get("timeout", 180)) <= int(v):
                    tm["timeout"] = int(v) * 6
                cfg["rip_timers"] = tm
        elif kind == "failure":
            f = sc2.get("failures")
            if not f or field not in ("mtbf_s", "mttr_s", "seed"):
                raise ValueError("ssenariyda failures yo'q yoki maydon noto'g'ri (mtbf_s | mttr_s | seed)")
            if field == "seed":
                f["seed"] = int(v)
            else:
                grp = param.get("group") or ("links" if f.get("links") else "devices")
                if not f.get(grp):
                    raise ValueError(f"failures.{grp} yo'q")
                f[grp][field] = float(v)
        else:
            raise ValueError("param.kind: link | app | ap | router | failure")

    @staticmethod
    def _param_label(param):
        who = param.get("link") or param.get("app") or param.get("ap") or param.get("router")
        if param.get("kind") == "failure":
            who = "failures" if param.get("field") == "seed" else f"failures.{param.get('group') or 'links'}"
        if param.get("kind") == "router" and param.get("all"):
            who = "routerlar"
        return f"{who}.{param.get('field')}"

    def start_study(self, project, scenario_name, engine, param, values, param2=None, values2=None):
        """Parametrik tadqiqot (ketma-ket ishga tushirishlar). Bitta parametr — chiziqli grafik;
        ikkinchi parametr (param2/values2) berilsa — barcha kombinatsiyalar (to'r), natija issiqlik
        xaritasi. param: {"kind": "link"|"app"|"ap", "link"|"app"|"ap": nom, "field": ...}."""
        project = copy.deepcopy(project)
        sc = (project.get("scenarios") or {}).get(scenario_name)
        if not sc:
            raise ValueError(f"ssenariy '{scenario_name}' yo'q")
        if engine == "mininet" and sc.get("ip_version", 4) == 6:
            raise ValueError("IPv6 faqat analytic va ns3 dvigatellarida qo'llab-quvvatlanadi")
        errs = validate_project(project) + validate_scenario(project, sc)
        if errs:
            raise ValueError("; ".join(errs[:5]))
        eng = {e["id"]: e for e in self.engines()}.get(engine)
        if not eng or not eng["available"]:
            raise ValueError(f"dvigatel '{engine}' mavjud emas")
        values = list(values or [])
        if not (2 <= len(values) <= MAX_STUDY_VALUES):
            raise ValueError(f"qiymatlar soni 2..{MAX_STUDY_VALUES}")
        two_d = bool(param2)
        values2 = list(values2 or []) if two_d else [None]
        if two_d:
            if len(values2) < 2:
                raise ValueError("ikkinchi parametr uchun kamida 2 qiymat")
            if self._param_label(param) == self._param_label(param2):
                raise ValueError("ikki parametr bir xil bo'lmasin")
            if len(values) * len(values2) > self.MAX_STUDY_RUNS:
                raise ValueError(f"kombinatsiyalar soni {len(values) * len(values2)} > {self.MAX_STUDY_RUNS}")
        variants = []
        for j, v2 in enumerate(values2):
            for i, v in enumerate(values):
                p2, sc2 = copy.deepcopy(project), copy.deepcopy(sc)
                self._apply_param(p2, sc2, param, v, engine)
                if two_d:
                    self._apply_param(p2, sc2, param2, v2, engine)
                variants.append((i, j, v, v2, p2, sc2))
        pname = project.get("name") or "untitled"
        self._dir(pname)
        study_id = new_run_id()
        pdesc = {**param, "label": self._param_label(param)}
        pdesc2 = {**param2, "label": self._param_label(param2)} if two_d else None

        def one(k, i, j, v, v2, p2, sc2, n):
            rid = f"{study_id}-v{k + 1:02d}"
            label = f"{pdesc['label']}={v}" + (f", {pdesc2['label']}={v2}" if two_d else "")
            with self._lock:
                self.active[rid] = {"project": pname, "scenario_name": scenario_name, "engine": engine,
                                    "progress": 0.0, "created": time.time(), "study_id": study_id, "label": label}
            self.bus.publish({"type": "study", "study_id": study_id, "project": pname, "index": k, "total": n,
                              "value": label, "run_id": rid})
            extra = {"study_id": study_id, "study_param": pdesc, "study_value": v, "study_index": i, "label": label}
            if two_d:
                extra.update(study_param2=pdesc2, study_value2=v2, study_index2=j)
            self._study_worker(rid, pname, p2, scenario_name, sc2, engine, extra=extra)

        def go():
            n = len(variants)
            par = self.study_parallelism(engine)
            if par <= 1:
                for k, var in enumerate(variants):
                    one(k, *var, n)
            else:
                # ns-3: har ishga tushirish alohida jarayon — variantlar bir vaqtda (CPU yadrolari bo'yicha)
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=par) as ex:
                    for f in [ex.submit(one, k, *var, n) for k, var in enumerate(variants)]:
                        f.result()
            self.bus.publish({"type": "study", "study_id": study_id, "project": pname, "done": True, "total": n})

        threading.Thread(target=go, daemon=True).start()
        return {"study_id": study_id, "runs": len(variants)}

    @staticmethod
    def study_parallelism(engine):
        """Legacy runner capacity at study start; the API queue also adapts during execution."""
        if engine != "ns3":
            return 1
        env = os.environ.get("NETLAB_NS3_PARALLEL", "").strip()
        from lab.engines.resources import MIB, ResourceMonitor, cpu_capacity, memory_reserve, positive_integer
        if env:
            return positive_integer(env, "NETLAB_NS3_PARALLEL")
        resources = ResourceMonitor().read()
        memory = resources.memory_available_bytes
        if memory is None:
            return 1
        return max(1, min(64, cpu_capacity(resources), (memory - memory_reserve(resources)) // (2048 * MIB)))

    def _worker(self, run_id, pname, project, sc_name, sc, engine, extra=None):
        prog = lambda f: self._pub(run_id, progress=round(f, 3))  # noqa: E731
        log = lambda m: self._pub(run_id, log=m)                  # noqa: E731
        t0 = time.time()
        from lab import apps as appmodels
        from lab import failures as failmodel
        from lab.reproducibility import capture, digest
        sc_user = sc
        provenance = {"schema": 1, "error": "capture did not complete"}
        emulation_locked = False
        try:
            provenance = capture(project, sc_name, sc_user, None, engine)
            if engine == "mininet":
                emulation_locked = self._emulation_lock.acquire(blocking=False)
                if not emulation_locked:
                    raise ValueError("Mininet'da bir vaqtda faqat bitta ssenariy ishlaydi")
            sc_fail = failmodel.expand(project, sc)     # MTBF/MTTR -> link_down/link_up
            sc = appmodels.expand(sc_fail)              # voip/http/ftp -> udp/tcp
            provenance["effective_scenario"] = copy.deepcopy(sc)
            provenance["effective_scenario_sha256"] = digest(sc)
            if engine == "analytic":
                from lab.engines import analytic
                res = analytic.run(project, sc, prog, log)
            elif engine == "ns3":
                res = self._run_ns3(project, sc, prog, log)
            else:
                from lab.engines import emulation
                res = emulation.run(self.driver, sc, prog, log)
            res.update(status="done")
            res = appmodels.postprocess(res, sc_user)     # MOS, sahifa/fayl vaqti
            res = failmodel.postprocess(res, sc_user, sc_fail, project)   # mavjudlik (availability)
        except Exception as e:  # noqa: BLE001
            res = {"engine": engine, "status": "error", "error": f"{type(e).__name__}: {e}", "flows": {}}
            log(f"XATO: {e}")
        finally:
            if emulation_locked:
                self._emulation_lock.release()
        res.update(run_id=run_id, project=pname, scenario_name=sc_name, scenario=sc_user, created=t0,
                   topology={"devices": len(project["devices"]), "links": len(project["links"])})
        res.setdefault("wall_s", round(time.time() - t0, 2))
        res["reproducibility"] = provenance
        if extra:
            res.update(extra)
        try:
            self._save(pname, res)
        finally:
            self._pub(run_id, done=True, status=res["status"], error=res.get("error"))
            with self._lock:
                self.active.pop(run_id, None)

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

"""Driver interfeysi va umumiy yordamchilar.

Driver — tarmoqni "jonli" ushlab turuvchi qism. Ikkita amalga oshirish bor:
    real  (mininet_driver.MininetDriver) — Mininet/OVS/FRR/BMv2, root kerak
    demo  (demo_driver.DemoDriver)       — Mininet'siz, UI sinovi/o'rganish uchun

Server (lab/server.py) faqat shu interfeysga tayanadi.
"""

import asyncio
import copy
import threading
import time


class EventBus:
    """Istalgan thread'dan chaqiriladigan publish() -> barcha WebSocket
    obunachilarga (asyncio navbatlari). To'lgan navbatga hodisa tashlanadi
    (sekin klient serverni bloklamasin)."""

    def __init__(self):
        self._loop = None
        self._subs = set()
        self._lock = threading.Lock()

    def bind_loop(self, loop):
        self._loop = loop

    def subscribe(self):
        q = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self, event):
        if self._loop is None:
            return
        event.setdefault("ts", time.time())
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            self._loop.call_soon_threadsafe(self._put, q, event)

    @staticmethod
    def _put(q, event):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def log(self, level, msg, device=None):
        self.publish({"type": "log", "level": level, "msg": msg, "device": device})


class Terminal:
    """Qurilma terminali. `on_output(bytes)` istalgan thread'dan chaqiriladi,
    `on_exit()` jarayon tugaganda."""

    def __init__(self):
        self.on_output = lambda data: None
        self.on_exit = lambda: None

    def write(self, data: bytes):
        raise NotImplementedError

    def resize(self, cols: int, rows: int):
        pass

    def close(self):
        pass


class DriverError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xato (HTTP 400)."""


class BaseDriver:
    name = "base"

    def __init__(self, bus: EventBus):
        self.bus = bus
        self.project = None
        self.started_at = None
        self.lock = threading.RLock()   # Mininet thread-safe emas — barcha amallar shu lock ostida
        self.capture_on = True

    # ---- holat ----
    @property
    def running(self):
        return self.project is not None

    def capabilities(self):
        return {}

    def info(self):
        return {"driver": self.name, "capabilities": self.capabilities()}

    def status(self):
        return {
            "driver": self.name,
            "running": self.running,
            "project": self.project["name"] if self.project else None,
            "started_at": self.started_at,
            "capture": self.capture_on,
        }

    def _require_running(self):
        if not self.running:
            raise DriverError("Tarmoq ishga tushirilmagan")

    def _set_project(self, project, *, collection=None, key=None, device_type=None):
        """Validate a detached candidate before replacing the running document.

        Live operations may require a named target in the candidate too. A bad
        request must leave the previous project available to the running network.
        """
        from lab.model import validate_project
        from lab.project_shape import project_for_execution
        candidate = copy.deepcopy(project)
        errors = validate_project(candidate)
        if errors:
            raise DriverError("; ".join(errors[:5]))
        if collection is not None:
            items = candidate.get(collection) or {}
            if not isinstance(key, str) or key not in items:
                raise DriverError(f"{collection}.{key}: loyihada yo'q")
            if device_type is not None and items[key].get("type") != device_type:
                raise DriverError(f"{key}: qurilma turi {device_type} bo'lsin")
        self.project = project_for_execution(candidate)

    def _publish_state(self):
        self.bus.publish({"type": "state", **self.status()})

    # ---- amallar (driverlar qayta yozadi) ----
    def start(self, project): raise NotImplementedError
    def stop(self): raise NotImplementedError
    def add_device(self, project, name): raise NotImplementedError
    def remove_device(self, name): raise NotImplementedError
    def add_link(self, project, link_id): raise NotImplementedError
    def remove_link(self, link_id): raise NotImplementedError
    def update_link(self, link_id, params=None, up=None): raise NotImplementedError
    def apply_device_config(self, project, name): raise NotImplementedError
    def inspect(self, name): raise NotImplementedError
    def open_terminal(self, name, shell="default"): raise NotImplementedError
    def p4_load(self, project, name): raise NotImplementedError
    def p4_runtime(self, name, commands): raise NotImplementedError
    def controller_restart(self, project, cname): raise NotImplementedError
    def controller_log(self, cname): raise NotImplementedError
    def frr_running_config(self, name): raise NotImplementedError

    def set_capture(self, on):
        self.capture_on = bool(on)
        self._publish_state()


def p4_compile_standalone(source, workdir):
    """P4 manbani p4c-bm2-ss bilan kompilyatsiya qiladi (tarmoq ishlamasa ham
    ishlaydi). Qaytaradi: {ok, log, json_path}."""
    import os
    import shutil
    import subprocess
    os.makedirs(workdir, exist_ok=True)
    src = os.path.join(workdir, "program.p4")
    out = os.path.join(workdir, "program.json")
    with open(src, "w") as f:
        f.write(source)
    compiler = shutil.which("p4c-bm2-ss") or shutil.which("p4c")
    if not compiler:
        return {"ok": False, "log": "p4c topilmadi (lab/install_lab.sh --p4 bilan o'rnating)", "json_path": None}
    cmd = [compiler, "--p4v", "16", "-o", out, src]
    if os.path.basename(compiler) == "p4c":
        cmd = [compiler, "--target", "bmv2", "--arch", "v1model", "-o", workdir, src]
        out = os.path.join(workdir, "program.json")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "log": "p4c: vaqt tugadi (120s)", "json_path": None}
    log = (r.stdout + r.stderr).strip()
    ok = r.returncode == 0 and os.path.exists(out)
    return {"ok": ok, "log": log or ("OK" if ok else "xato"), "json_path": out if ok else None}


def p4_pipeline_from_json(json_path):
    """BMv2 JSON'dan pipeline tuzilmasini chiqaradi (UI'dagi "hardware" ko'rinishi):
    parser holatlari, ingress/egress jadvallari (key, action'lar), deparser."""
    import json
    with open(json_path) as f:
        j = json.load(f)
    actions = {a["id"]: a["name"] for a in j.get("actions", [])}
    out = {"parsers": [], "pipelines": [], "deparsers": [], "headers": [],
           "counters": [c["name"] for c in j.get("counter_arrays", [])],
           "registers": [{"name": r["name"], "size": r.get("size"), "bitwidth": r.get("bitwidth")}
                         for r in j.get("register_arrays", [])]}
    for h in j.get("headers", []):
        if not h.get("metadata"):
            out["headers"].append(h["name"])
    for p in j.get("parsers", []):
        states = []
        for s in p.get("parse_states", []):
            ext = [op["parameters"][0]["value"] for op in s.get("parser_ops", [])
                   if op.get("op") == "extract" and op.get("parameters")]
            nxt = sorted({t.get("next_state") or "accept" for t in s.get("transitions", [])})
            states.append({"name": s["name"], "extracts": ext, "next": nxt})
        out["parsers"].append({"name": p["name"], "states": states})
    for pl in j.get("pipelines", []):
        tables = []
        for t in pl.get("tables", []):
            tables.append({
                "name": t["name"],
                "match": [{"field": ".".join(k.get("target", [])) if isinstance(k.get("target"), list) else str(k.get("target")),
                           "type": k.get("match_type")} for k in t.get("key", [])],
                "actions": [actions.get(a, str(a)) for a in t.get("action_ids", [])],
                "size": t.get("max_size"),
            })
        out["pipelines"].append({"name": pl["name"], "tables": tables})
    for d in j.get("deparsers", []):
        out["deparsers"].append({"name": d["name"], "order": d.get("order", [])})
    return out

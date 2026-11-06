"""Studio backend — JS <-> Python ko'prigi (window.pywebview.api).

Mavjud repo modullarini qayta ishlatadi: topologies, routing, analytics.data.
Simulyatsiyani WSL/Ubuntu serverda SSH (paramiko) orqali ishga tushiradi.

Metod nomlari studio/web/js/bridge.js dagi KONTRAKT bilan aynan mos bo'lishi
shart. Barcha qaytish qiymatlari JSON-serializable (dict/list/son/matn).
"""

import base64
import glob
import json
import os
import re
import shlex
import subprocess
import uuid
import threading
import time

import routing
import topologies
from studio.remote import RemoteJob
try:
    from studio.config import load_config, save_config
except ModuleNotFoundError:
    from config import load_config, save_config

_CUSTOM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "custom_topologies")
_ROUTING_MODES = ["l2_learn", "rip", "ospf", "isis", "eigrp", "bgp",
                  "ecmp", "spf", "policy", "static", "hybrid"]


def _links_to_json(topo):
    """('s1','s2') tuple kalitlarni "s1-s2" matnga (JSON uchun)."""
    return {f"{k[0]}-{k[1]}": v for k, v in topo["links"].items()}


class _StoreCache:
    """Papka bo'yicha DataStore keshi — har chaqiruvda DuckDB view qayta
    qurmaslik uchun."""
    def __init__(self):
        self._stores = {}

    def get(self, folder):
        absf = os.path.abspath(folder)
        st = self._stores.get(absf)
        if st is None:
            from analytics.data import DataStore
            st = DataStore(absf)
            self._stores[absf] = st
        return st

    def refresh(self, folder):
        absf = os.path.abspath(folder)
        self._stores.pop(absf, None)
        return self.get(folder)


# ─────────────────────────────────────────────────────────
#  SSH orqali masofaviy simulyatsiya boshqaruvi
# ─────────────────────────────────────────────────────────
class RunManager:
    """WSL serverda light_simulation.py ni ishga tushirib, progressni kuzatadi.

    Barcha masofaviy buyruqlar base64-encode qilib `wsl -d <distro> -- bash -lc
    "echo <b64> | base64 --decode | bash"` orqali yuboriladi — shu tufayli
    cmd.exe -> wsl -> bash zanjiridagi tirnoq/qochish muammolari bo'lmaydi.
    Server o'chiq bo'lsa, holat 'error' ga o'tadi (ilova yiqilmaydi)."""

    def __init__(self, cfg_provider):
        self._cfg = cfg_provider
        self._runs = {}          # run_id -> state dict
        self._counter = 0
        self._lock = threading.Lock()

    def _new_id(self, params):
        return "run_" + uuid.uuid4().hex

    def _exec_script(self, cfg, script, timeout=30):
        client = self._ssh(cfg)
        try:
            # The configured SSH endpoint is Windows OpenSSH launching WSL.
            command = subprocess.list2cmdline(self._b64cmd(cfg["wsl_distro"], script))
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            return stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")
        finally:
            client.close()

    @staticmethod
    def _b64cmd(distro, script):
        b = base64.b64encode(script.encode()).decode()
        return ["wsl", "-d", distro, "--", "bash", "-lc",
                f"echo {b} | base64 --decode | bash"]

    def _ssh(self, cfg):
        import paramiko
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(cfg["ssh_host"], username=cfg["ssh_user"],
                       password=cfg["ssh_pass"], timeout=15, banner_timeout=15)
        return client

    def test(self, cfg):
        try:
            out, err = self._exec_script(cfg, "echo studio-ok; uname -a", 20)
            if "studio-ok" in out:
                return {"ok": True, "message": out.strip().splitlines()[-1][:120]}
            return {"ok": False, "message": (err or out)[:200]}
        except Exception as e:
            return {"ok": False, "message": f"{type(e).__name__}: {e}"}

    def start(self, params):
        cfg = self._cfg()
        if not cfg.get("ssh_host"):
            return {"ok": False, "error": "SSH server sozlanmagan (Sozlamalar bo'limiga qarang)."}
        topology = params.get("topology", "three_as")
        mode = params.get("routing", "l2_learn")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", topology) or mode not in _ROUTING_MODES:
            return {"ok": False, "error": "Topologiya yoki routing nomi noto'g'ri"}
        duration = int(params.get("duration", cfg["default_duration"]))
        if not 1 <= duration <= 3600:
            return {"ok": False, "error": "Davomiylik 1..3600 s"}
        run_id = self._new_id(params)
        st = {"run_id": run_id, "state": "starting", "progress": 0, "phase": "boshlanmoqda",
              "log_tail": [], "done": False, "error": None,
              "topology": topology, "routing": mode, "_cfg": dict(cfg),
              "duration": int(params.get("duration", cfg["default_duration"])),
              "seed": int(params.get("seed", cfg["default_seed"])), "ts": time.time()}
        self._runs[run_id] = st
        threading.Thread(target=self._worker, args=(run_id, params, cfg), daemon=True).start()
        return {"ok": True, "run_id": run_id}

    def _remote_log(self, run_id):
        return RemoteJob(run_id).log

    def _worker(self, run_id, params, cfg):
        st = self._runs[run_id]
        job = RemoteJob(run_id)
        try:
            out, err = self._exec_script(cfg, job.launch(cfg["remote_repo"], st["topology"], st["routing"],
                                                        st["duration"], st["seed"]), 60)
            match = re.search(r"STUDIO_PID=(\d+)", out)
            if not match or int(match[1]) <= 1:
                raise RuntimeError("Masofaviy jarayon ishga tushmadi: " + (err or out)[:200])
            st.update(_pid=int(match[1]), state="running")
            if st.get("_stop"):
                self.stop(run_id)
            deadline = time.monotonic() + st["duration"] + 500
            while time.monotonic() < deadline:
                time.sleep(1)
                text, _ = self._exec_script(cfg, job.poll(st["_pid"]), 25)
                poll = job.parse(text)
                body = poll["log"]
                st["log_tail"] = [line for line in body.splitlines() if line.strip()][-14:]
                st["phase"] = self._phase(body)
                matches = list(re.finditer(r"\[(\d+)s/(\d+)s\]", body))
                if matches:
                    elapsed, total = map(int, matches[-1].groups())
                    st["progress"] = min(96, int(elapsed / max(total, 1) * 92) + 4)
                if poll["exit_code"] is not None or not poll["running"]:
                    if st.get("_stop"):
                        st.update(state="cancelled", done=True, phase="bekor qilindi")
                    elif poll["exit_code"] == 0 and poll["dataset"]:
                        st.update(state="done", progress=100, done=True, phase="tugadi")
                    else:
                        st.update(state="error", done=True, error=f"Masofaviy exit code: {poll['exit_code']}; dataset={poll['dataset']}")
                    return
            self._exec_script(cfg, job.stop(st["_pid"]))
            raise TimeoutError("Masofaviy simulyatsiya vaqti tugadi")
        except Exception as e:
            st.update(state="error", done=True, error=f"{type(e).__name__}: {e}")

    @staticmethod
    def _phase(body):
        for marker, name in [("[Dataset]", "dataset qurilmoqda"), ("[Traffic]", "trafik yig'ilmoqda"),
                             ("pingAll", "ulanish tekshiruvi"), ("STP", "STP konvergensiya"),
                             ("[Network]", "tarmoq qurilmoqda"), ("[Controller]", "kontroller")]:
            if marker in body:
                return name
        return "ishlamoqda"

    def status(self, run_id):
        st = self._runs.get(run_id)
        if not st:
            return {"state": "unknown", "done": True, "error": "run_id topilmadi", "progress": 0}
        return {k: v for k, v in st.items() if not k.startswith("_")}

    def stop(self, run_id):
        st = self._runs.get(run_id)
        if not st:
            return {"ok": False, "error": "run_id topilmadi"}
        if st.get("done"):
            return {"ok": True, "state": st["state"]}
        st.update(_stop=True, state="cancelling")
        if st.get("_pid"):
            try:
                self._exec_script(st["_cfg"], RemoteJob.stop(st["_pid"]))
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": True, "state": st["state"]}

    def list_runs(self):
        return sorted(
            [{"run_id": s["run_id"], "topology": s["topology"], "routing": s["routing"],
              "state": s["state"], "ts": s["ts"]} for s in self._runs.values()],
            key=lambda r: -r["ts"])

    def import_results(self, run_id):
        st = self._runs.get(run_id)
        if not st:
            return {"ok": False, "error": "run_id topilmadi"}
        if st.get("state") != "done":
            return {"ok": False, "error": "Run muvaffaqiyatli tugamagan"}
        cfg = st["_cfg"]
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dest = os.path.join(repo_root, cfg["results_dir"],
                            f"{st['topology']}_{st['routing']}_seed{st['seed']}_{run_id}", "datasets")
        os.makedirs(dest, exist_ok=True)
        remote_tar = f"/tmp/studio_ds_{run_id}.tar.gz"
        try:
            c = self._ssh(cfg)
            argv = self._b64cmd(cfg["wsl_distro"],
                                f"tar czf {shlex.quote(remote_tar)} -C {RemoteJob(run_id).data} datasets 2>/dev/null && echo TAR_OK")
            stdin, stdout, stderr = c.exec_command(subprocess.list2cmdline(argv), timeout=120)
            if "TAR_OK" not in stdout.read().decode(errors="replace"):
                c.close(); return {"ok": False, "error": "Serverda datasets topilmadi"}
            # WSL fayl tizimi -> Windows -> sftp. Oddiylik uchun WSL ichidan
            # Windows-ko'rinadigan /mnt/c ga ko'chirib, sftp bilan olamiz.
            win_tmp = f"/mnt/c/Users/{cfg['ssh_user']}/studio_ds_{run_id}.tar.gz"
            argv2 = self._b64cmd(cfg["wsl_distro"], f"cp {shlex.quote(remote_tar)} {shlex.quote(win_tmp)} && echo CP_OK")
            stdin, stdout, stderr = c.exec_command(subprocess.list2cmdline(argv2), timeout=120)
            if "CP_OK" not in stdout.read().decode(errors="replace"):
                c.close()
                return {"ok": False, "error": "Dataset arxivini ko'chirish bajarilmadi"}
            sftp = c.open_sftp()
            local_tar = os.path.join(dest, "_import.tar.gz")
            sftp.get(f"C:/Users/{cfg['ssh_user']}/studio_ds_{run_id}.tar.gz", local_tar)
            sftp.close(); c.close()
            import tarfile
            with tarfile.open(local_tar) as tf:
                from pathlib import PurePosixPath
                for member in tf.getmembers():
                    parts = PurePosixPath(member.name).parts
                    if not parts or parts[0] != "datasets" or ".." in parts or not (member.isfile() or member.isdir()):
                        raise ValueError("Dataset arxivida xavfli yo'l yoki fayl turi")
                tf.extractall(os.path.dirname(dest), filter="data")
            os.remove(local_tar)
            return {"ok": True, "folder": os.path.relpath(dest, repo_root)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ─────────────────────────────────────────────────────────
#  Asosiy Api klassi (js_api)
# ─────────────────────────────────────────────────────────
class Api:
    def __init__(self, repo_root):
        self.repo_root = repo_root
        self._window = None
        self._cache = _StoreCache()
        self._runs = RunManager(load_config)

    def set_window(self, window):
        self._window = window

    def _abs(self, rel):
        return rel if os.path.isabs(rel) else os.path.join(self.repo_root, rel)

    # ---- Sozlamalar ----
    def get_config(self):
        return load_config()

    def save_config(self, cfg):
        return {"ok": True, "config": save_config(cfg or {})}

    def test_ssh(self, cfg):
        return self._runs.test(cfg or load_config())

    # ---- Topologiyalar ----
    def routing_modes(self):
        return list(_ROUTING_MODES)

    def list_topologies(self):
        builtin = ["three_as", "five_as", "datacenter", "campus"]
        custom = sorted(f[:-5] for f in os.listdir(_CUSTOM_DIR)
                        if f.endswith(".json")) if os.path.isdir(_CUSTOM_DIR) else []
        return {"builtin": builtin, "custom": custom}

    def get_topology(self, name):
        topo = topologies.TOPOLOGIES.get(name)
        if not topo:
            return {"error": f"'{name}' topilmadi"}
        return {
            "switches": topo["switches"],
            "hosts": topo["hosts"],
            "links": _links_to_json(topo),
            "access_links": topo.get("access_links", {}),
            "positions": topo.get("_positions", {}),
        }

    def compute_paths_summary(self, name, mode):
        topo = topologies.TOPOLOGIES.get(name)
        if not topo:
            return {"error": f"'{name}' topilmadi"}
        try:
            import random
            random.seed(42)
            paths = routing.compute_paths(topo, mode)
        except Exception as e:
            return {"error": str(e)}
        sample = []
        for (s, d), v in list(paths.items())[:12]:
            p = v[0] if (isinstance(v, list) and v and isinstance(v[0], list)) else v
            sample.append({"src": s, "dst": d, "path": p})
        hop_counts = [len(v if not (v and isinstance(v[0], list)) else v[0])
                      for v in paths.values() if isinstance(v, list) and v]
        return {"pairs": len(paths), "sample": sample,
                "avg_hops": round(sum(hop_counts) / max(len(hop_counts), 1), 2)}

    def validate_topology(self, topo):
        norm = self._normalize_topo(topo)
        try:
            paths = routing.compute_paths(norm, "l2_learn")
        except Exception as e:
            return {"ok": False, "error": str(e)}
        hosts = list(norm["hosts"])
        missing = [[a, b] for a in hosts for b in hosts if a != b and (a, b) not in paths]
        if missing:
            return {"ok": False, "error": f"{len(missing)} host juftligi ulanmagan",
                    "missing": missing[:5], "pairs": len(paths)}
        return {"ok": True, "pairs": len(paths)}

    @staticmethod
    def _normalize_topo(topo):
        """Frontend'dan kelgan topo (links "s1-s2" matn kalitli) -> ichki
        (tuple kalitli) shakl, compute_paths uchun."""
        links = {}
        for k, v in (topo.get("links") or {}).items():
            if isinstance(k, str):
                a, b = k.split("-", 1)
                links[(a, b)] = v
            else:
                links[tuple(k)] = v
        return {"switches": topo.get("switches", {}), "hosts": topo.get("hosts", {}),
                "links": links, "access_links": topo.get("access_links", {})}

    def save_topology(self, name, topo):
        safe = "".join(c for c in (name or "") if c.isalnum() or c in "_-")
        if not safe:
            return {"ok": False, "error": "Nom noto'g'ri"}
        if safe in ("three_as", "five_as", "datacenter", "campus"):
            return {"ok": False, "error": "Bu nom tayyor topologiya bilan to'qnashadi"}
        v = self.validate_topology(topo)
        if not v.get("ok"):
            return {"ok": False, "error": v.get("error", "validatsiya xatosi")}
        os.makedirs(_CUSTOM_DIR, exist_ok=True)
        out = dict(topo)
        out["links"] = topo.get("links", {})  # allaqachon "s1-s2" matn kalitli
        path = os.path.join(_CUSTOM_DIR, f"{safe}.json")
        with open(path, "w") as f:
            json.dump(out, f, indent=2)
        # loader keshini yangilash uchun modulni qayta o'qitmaymiz — TOPOLOGIES
        # ga to'g'ridan-to'g'ri qo'shamiz.
        norm = self._normalize_topo(topo)
        topologies.TOPOLOGIES[safe] = norm
        return {"ok": True, "path": os.path.relpath(path, self.repo_root)}

    def delete_topology(self, name):
        path = os.path.join(_CUSTOM_DIR, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)
            topologies.TOPOLOGIES.pop(name, None)
            return {"ok": True}
        return {"ok": False, "error": "topilmadi"}

    # ---- Datasetlar ----
    def dataset_folders(self):
        base = self._abs("results")
        out = []
        if os.path.isdir(base):
            for entry in sorted(os.listdir(base)):
                p = os.path.join(base, entry)
                ds = p if os.path.isfile(os.path.join(p, "traffic_log.csv")) else os.path.join(p, "datasets")
                has = os.path.isdir(ds) and any(f.endswith(".csv") for f in (os.listdir(ds) if os.path.isdir(ds) else []))
                if has:
                    out.append({"path": os.path.relpath(ds, self.repo_root),
                                "label": entry, "has_datasets": True})
        return out

    def list_datasets(self, folder):
        try:
            st = self._cache.get(self._abs(folder))
            counts = st.row_counts()
            return {"folder": folder,
                    "tables": [{"name": n, "rows": counts.get(n, 0)} for n in st.available],
                    "routing_modes": st.routing_modes(), "errors": getattr(st, "errors", {})}
        except Exception as e:
            return {"folder": folder, "tables": [], "routing_modes": [], "errors": {"_": str(e)}}

    def query(self, folder, sql):
        try:
            st = self._cache.get(self._abs(folder))
            df = st.query_df(sql)
            return {"columns": list(df.columns),
                    "rows": df.head(1000).astype(object).where(df.notna(), None).values.tolist()}
        except Exception as e:
            return {"columns": [], "rows": [], "error": str(e)}

    def routing_compare(self, folder):
        st = self._cache.get(self._abs(folder))
        if not st.has("path_traces"):
            return {"modes": [], "avg_rtt": [], "box": {}}
        df = st.query_df("SELECT routing, real_rtt_ms FROM path_traces WHERE real_rtt_ms IS NOT NULL")
        if df.empty:
            return {"modes": [], "avg_rtt": [], "box": {}}
        modes = sorted(df["routing"].unique().tolist())
        avg = [round(float(df[df["routing"] == m]["real_rtt_ms"].mean()), 2) for m in modes]
        box = {m: [round(float(x), 2) for x in df[df["routing"] == m]["real_rtt_ms"].tolist()[:400]] for m in modes}
        return {"modes": modes, "avg_rtt": avg, "box": box}

    def anomaly_summary(self, folder):
        st = self._cache.get(self._abs(folder))
        if not st.has("anomaly_events"):
            return {"types": [], "hourly": [0] * 24}
        df = st.query_df("SELECT type, sim_hour FROM anomaly_events")
        types = [{"type": t, "count": int(c)} for t, c in df["type"].value_counts().items()]
        hourly = [0] * 24
        for h in df["sim_hour"].dropna():
            hourly[int(h) % 24] += 1
        return {"types": types, "hourly": hourly}

    def dns_summary(self, folder):
        st = self._cache.get(self._abs(folder))
        if not st.has("dns_queries"):
            return {"stages": [], "cache": {"hit": 0, "miss": 0}}
        df = st.query_df("SELECT stage, response_time_ms, cache_hit FROM dns_queries")
        order = ["cache", "root", "tld", "authoritative"]
        stages = []
        for s in order:
            sub = df[df["stage"] == s]["response_time_ms"].dropna()
            if len(sub):
                stages.append({"stage": s, "p50": round(float(sub.quantile(0.5)), 2),
                               "p90": round(float(sub.quantile(0.9)), 2)})
        hit = int(df["cache_hit"].sum()) if "cache_hit" in df else 0
        return {"stages": stages, "cache": {"hit": hit, "miss": int(len(df) - hit)}}

    def qos_summary(self, folder):
        st = self._cache.get(self._abs(folder))
        band_names = {0: "EF", 1: "AF21", 2: "BE"}
        bands, imp = [], []
        if st.has("traffic_log"):
            df = st.query_df("SELECT queue_band FROM traffic_log WHERE queue_band IS NOT NULL")
            for b, c in df["queue_band"].value_counts().sort_index().items():
                bands.append({"band": band_names.get(int(b), str(b)), "count": int(c)})
        if st.has("impairments"):
            df = st.query_df("SELECT event FROM impairments")
            imp = [{"event": e, "count": int(c)} for e, c in df["event"].value_counts().items()]
        return {"bands": bands, "impairments": imp}

    def dashboard_summary(self, folder):
        tiles = []
        try:
            st = self._cache.get(self._abs(folder))
            counts = st.row_counts()
            total = sum(counts.values())
            tiles = [
                {"label": "Jami yozuvlar", "value": f"{total:,}", "hint": folder, "accent": "accent"},
                {"label": "Routing rejimlar", "value": str(len(st.routing_modes())), "hint": "datasetda", "accent": "accent-2"},
                {"label": "Dataset turlari", "value": str(len(st.available)), "hint": "CSV jadval", "accent": "accent-3"},
                {"label": "Anomaliya hodisalari", "value": f"{counts.get('anomaly_events', 0):,}", "hint": "hujumlar", "accent": "danger"},
            ]
        except Exception as e:
            tiles = [{"label": "Xato", "value": "-", "hint": str(e)[:40], "accent": "danger"}]
        topos = self.list_topologies()
        tiles.append({"label": "Topologiyalar", "value": str(len(topos["builtin"]) + len(topos["custom"])),
                      "hint": f"{len(topos['custom'])} custom", "accent": "success"})
        return {"tiles": tiles, "recent_runs": self._runs.list_runs()[:6]}

    # ---- Simulyatsiya (SSH) ----
    def run_simulation(self, params):
        return self._runs.start(params or {})

    def run_status(self, run_id):
        return self._runs.status(run_id)

    def stop_simulation(self, run_id):
        return self._runs.stop(run_id)

    def list_runs(self):
        return self._runs.list_runs()

    def import_results(self, run_id):
        res = self._runs.import_results(run_id)
        if res.get("ok"):
            self._cache.refresh(self._abs(res["folder"]))
        return res

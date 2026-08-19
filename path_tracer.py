"""Har bir host juftligi uchun tanlangan routing rejimi bo'yicha yo'lni
kuzatib, nazariy (topologiya asosidagi) va real (ping orqali o'lchangan)
ko'rsatkichlarni birga yozadi."""

import json
import os
import random
import threading
import time

from config import DATA_DIR
from netutil import parse_ping
from routing import compute_paths


class PathTracer:
    """Graph-based path tracing + real ping RTT."""
    def __init__(self, net, topo, routing_mode):
        self.net = net
        self.topo = topo
        self.routing_mode = routing_mode
        self._running = False
        self._thread = None
        self._log = os.path.join(DATA_DIR, "stats/path_traces.jsonl")
        self._paths = compute_paths(topo, routing_mode)
        self._host_info = {}
        for h, info in topo["hosts"].items():
            sw = info["switch"]
            al = topo["access_links"].get(h, {})
            self._host_info[h] = {
                "switch": sw, "ip": info["ip"].split("/")[0], "role": info["role"],
                "as": topo["switches"].get(sw, {}).get("as", 0),
                "access_bw": al.get("bw", 10), "access_delay": al.get("delay", "1ms"),
                "access_loss": al.get("loss", 0),
            }

    def start(self):
        self._running = True
        self._trace_all_pairs()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self):
        hosts = self.net.hosts
        while self._running:
            time.sleep(15)
            if not self._running:
                break
            for _ in range(4):
                if len(hosts) < 2:
                    break
                h1, h2 = random.sample(hosts, 2)
                self._trace_and_log(h1, h2)

    def _trace_all_pairs(self):
        hosts = self.net.hosts
        count = 0
        for i, h1 in enumerate(hosts):
            for h2 in hosts[i+1:]:
                self._trace_and_log(h1, h2)
                self._trace_and_log(h2, h1)
                count += 2
        print(f"[PathTracer] {count} paths ({self.routing_mode})")

    def _trace_and_log(self, src_host, dst_host):
        trace = self._build_trace(src_host, dst_host)
        if trace:
            try:
                with open(self._log, "a") as f:
                    f.write(json.dumps(trace) + "\n")
            except OSError:
                pass

    def _get_link_params(self, sw1, sw2):
        for (s1, s2), p in self.topo["links"].items():
            if (sw1 in (s1, s2)) and (sw2 in (s1, s2)):
                return p
        return None

    def _build_trace(self, src_host, dst_host):
        src = self._host_info.get(src_host.name)
        dst = self._host_info.get(dst_host.name)
        if not src or not dst:
            return None

        path_key = (src_host.name, dst_host.name)
        sw_path = self._paths.get(path_key)
        if sw_path is None:
            return None
        # ECMP: list of paths -> random choice
        if isinstance(sw_path, list) and sw_path and isinstance(sw_path[0], list):
            sw_path = random.choice(sw_path)

        hops = []
        # Src access
        hops.append({"hop": 0, "from": src_host.name, "to": sw_path[0], "type": "access",
                      "bw_mbps": src["access_bw"],
                      "delay_ms": float(src["access_delay"].replace("ms", "")),
                      "loss_pct": src["access_loss"],
                      "as": self.topo["switches"].get(sw_path[0], {}).get("as", 0),
                      "role": self.topo["switches"].get(sw_path[0], {}).get("role", "")})
        # Switch-switch
        for i in range(len(sw_path) - 1):
            params = self._get_link_params(sw_path[i], sw_path[i+1])
            if params:
                link_type = "backbone" if params["bw"] >= 40 else "peering" if params["bw"] >= 20 else "access_agg"
                hops.append({"hop": i+1, "from": sw_path[i], "to": sw_path[i+1], "type": link_type,
                              "bw_mbps": params["bw"],
                              "delay_ms": float(params["delay"].replace("ms", "")),
                              "loss_pct": params["loss"],
                              "jitter_ms": float(params.get("jitter", "0ms").replace("ms", "")),
                              "queue_size": params.get("queue", 0),
                              "as": self.topo["switches"].get(sw_path[i+1], {}).get("as", 0),
                              "role": self.topo["switches"].get(sw_path[i+1], {}).get("role", "")})
        # Dst access
        hops.append({"hop": len(hops), "from": sw_path[-1], "to": dst_host.name, "type": "access",
                      "bw_mbps": dst["access_bw"],
                      "delay_ms": float(dst["access_delay"].replace("ms", "")),
                      "loss_pct": dst["access_loss"],
                      "as": self.topo["switches"].get(sw_path[-1], {}).get("as", 0),
                      "role": self.topo["switches"].get(sw_path[-1], {}).get("role", "")})

        # Real ping
        rtt = {}
        try:
            result = src_host.cmd(f"ping -c 3 -W 2 {dst_host.IP()}")
            rtt = parse_ping(result)
        except Exception:
            pass

        theory_delay = sum(h.get("delay_ms", 0) for h in hops)
        theory_loss = sum(h.get("loss_pct", 0) for h in hops)
        bottleneck = min(h.get("bw_mbps", 9999) for h in hops)
        as_path = []
        for h in hops:
            a = h.get("as", 0)
            if not as_path or as_path[-1] != a:
                as_path.append(a)

        return {
            "ts": time.time(), "src": src_host.name, "dst": dst_host.name,
            "src_ip": src["ip"], "dst_ip": dst["ip"],
            "src_as": src["as"], "dst_as": dst["as"],
            "routing": self.routing_mode,
            "path_switches": sw_path, "as_path": as_path,
            "num_switch_hops": len(sw_path), "num_total_hops": len(hops),
            "theoretical_delay_ms": round(theory_delay, 2),
            "theoretical_rtt_ms": round(theory_delay * 2, 2),
            "theoretical_loss_pct": round(theory_loss, 4),
            "bottleneck_bw_mbps": bottleneck,
            "real_rtt_ms": rtt.get("rtt_avg"), "real_rtt_min": rtt.get("rtt_min"),
            "real_rtt_max": rtt.get("rtt_max"), "real_loss_pct": rtt.get("loss_pct"),
            "hops": hops,
        }

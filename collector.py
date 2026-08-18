"""Xom paket (pcap) va RTT ma'lumot yig'uvchi: har bir switch interfeysida
tcpdump ishga tushiradi, davriy ravishda tasodifiy host juftliklari orasida
ping RTT o'lchaydi."""

import json
import random
import subprocess
import threading
import time

from config import DATA_DIR


class Collector:
    def __init__(self, net):
        self.net = net
        self._running = False
        self._procs = {}
        self._threads = []

    def start(self):
        self._running = True
        for sw in self.net.switches:
            for intf in sw.intfList():
                if intf.name == "lo" or not intf.name.startswith("s"):
                    continue
                try:
                    proc = subprocess.Popen(
                        ["tcpdump", "-i", intf.name, "-w", f"{DATA_DIR}/pcap/{intf.name}.pcap",
                         "-s", "96", "-c", "500000", "-q"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._procs[intf.name] = proc
                except Exception:
                    pass
        t = threading.Thread(target=self._rtt_loop, daemon=True)
        t.start()
        self._threads.append(t)
        print(f"[Collector] tcpdump: {len(self._procs)} intf, RTT: ON")

    def stop(self):
        self._running = False
        for proc in self._procs.values():
            try:
                proc.terminate(); proc.wait(timeout=5)
            except Exception:
                try: proc.kill()
                except Exception: pass
        for t in self._threads:
            t.join(timeout=5)

    def _rtt_loop(self):
        hosts = self.net.hosts
        path = f"{DATA_DIR}/stats/rtt_measurements.jsonl"
        while self._running:
            for _ in range(3):
                if len(hosts) < 2:
                    break
                h1, h2 = random.sample(hosts, 2)
                try:
                    result = h1.cmd(f"ping -c 3 -W 2 {h2.IP()}")
                except Exception:
                    result = ""
                if result:
                    rtt = self._parse_ping(result)
                    if rtt:
                        try:
                            with open(path, "a") as f:
                                f.write(json.dumps({"ts": time.time(), "src": h1.name, "dst": h2.name,
                                                     "src_ip": h1.IP(), "dst_ip": h2.IP(), **rtt}) + "\n")
                        except OSError:
                            pass
            time.sleep(10)

    @staticmethod
    def _parse_ping(output):
        result = {}
        for line in output.split("\n"):
            if "packet loss" in line:
                for part in line.split(","):
                    if "packet loss" in part:
                        try: result["loss_pct"] = float(part.strip().split("%")[0])
                        except ValueError: pass
            if "min/avg/max" in line:
                try:
                    vals = line.split("=")[1].strip().split("/")
                    result["rtt_min"] = float(vals[0])
                    result["rtt_avg"] = float(vals[1])
                    result["rtt_max"] = float(vals[2])
                    if len(vals) > 3:
                        result["rtt_mdev"] = float(vals[3].split()[0])
                except (ValueError, IndexError):
                    pass
        return result

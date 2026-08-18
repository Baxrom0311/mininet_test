"""
Keng qamrovli ma'lumot yig'ish.
ONOS API, sFlow, tcpdump, va maxsus transport metrikalar.
"""

import json
import os
import subprocess
import threading
import time

from realistic_internet.config import (
    ONOS_POLL_INTERVAL, SFLOW_POLL_INTERVAL,
    PCAP_DIR, STATS_DIR, FLOWS_DIR, ONOS_STATS_DIR, SFLOW_DATA_DIR,
    SFLOW_RT_API, SFLOW_COLLECTOR_PORT, SFLOW_SAMPLING_RATE,
    SFLOW_POLLING_INTERVAL, SFLOW_RT_CONTAINER, SFLOW_RT_IMAGE,
)


class DataCollector:
    """Barcha tarmoq ma'lumotlarini yig'ish."""

    def __init__(self, onos_manager, net, output_dir="/data"):
        self.onos = onos_manager
        self.net = net
        self.output_dir = output_dir
        self._running = False
        self._threads = []
        self._tcpdump_procs = {}

        # Kataloglar
        for d in [PCAP_DIR, STATS_DIR, FLOWS_DIR, ONOS_STATS_DIR, SFLOW_DATA_DIR]:
            os.makedirs(d, exist_ok=True)

    def start(self):
        """Barcha data collection'ni boshlash."""
        if self._running:
            return
        self._running = True

        print("[Collector] Ma'lumot yig'ish boshlanmoqda...")

        # 1. ONOS stats polling
        t1 = threading.Thread(target=self._onos_flow_stats_loop, daemon=True)
        t1.start()
        self._threads.append(t1)

        t2 = threading.Thread(target=self._onos_port_stats_loop, daemon=True)
        t2.start()
        self._threads.append(t2)

        t3 = threading.Thread(target=self._onos_topology_loop, daemon=True)
        t3.start()
        self._threads.append(t3)

        # 2. sFlow
        self._setup_sflow()

        t4 = threading.Thread(target=self._sflow_poll_loop, daemon=True)
        t4.start()
        self._threads.append(t4)

        # 3. tcpdump (asosiy inter-AS linklarda)
        self._start_tcpdump()

        # 4. Ping monitoring (har bir host juftligi uchun RTT)
        t5 = threading.Thread(target=self._rtt_monitor_loop, daemon=True)
        t5.start()
        self._threads.append(t5)

        print("[Collector] Barcha yig'ish ishlayapti")

    def stop(self):
        """Barcha collection'ni to'xtatish."""
        self._running = False

        # tcpdump to'xtatish
        for name, proc in self._tcpdump_procs.items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._tcpdump_procs.clear()

        # Thread'lar kutish
        for t in self._threads:
            t.join(timeout=10)
        self._threads.clear()

        # sFlow-RT to'xtatish
        subprocess.run(["docker", "stop", SFLOW_RT_CONTAINER], capture_output=True)
        subprocess.run(["docker", "rm", "-f", SFLOW_RT_CONTAINER], capture_output=True)

        print("[Collector] Ma'lumot yig'ish to'xtatildi")

    # ── ONOS Statistics ───────────────────────────────────

    def _onos_flow_stats_loop(self):
        """ONOS flow statistikasini davriy yig'ish."""
        path = os.path.join(ONOS_STATS_DIR, "flow_stats.jsonl")
        while self._running:
            try:
                ts = time.time()
                all_flows = self.onos.get_all_flow_stats()
                for device_id, flows in all_flows.items():
                    for flow in flows:
                        entry = {
                            "ts": ts,
                            "device_id": device_id,
                            "flow_id": flow.get("id", ""),
                            "priority": flow.get("priority", 0),
                            "state": flow.get("state", ""),
                            "packets": flow.get("packets", 0),
                            "bytes": flow.get("bytes", 0),
                            "life": flow.get("life", 0),
                            "table_id": flow.get("tableId", 0),
                            "selector": json.dumps(flow.get("selector", {})),
                            "treatment": json.dumps(flow.get("treatment", {})),
                        }
                        with open(path, "a") as f:
                            f.write(json.dumps(entry) + "\n")
            except Exception:
                pass
            time.sleep(ONOS_POLL_INTERVAL)

    def _onos_port_stats_loop(self):
        """ONOS port statistikasini davriy yig'ish."""
        path = os.path.join(ONOS_STATS_DIR, "port_stats.jsonl")
        while self._running:
            try:
                ts = time.time()
                all_ports = self.onos.get_all_port_stats()
                for device_id, ports in all_ports.items():
                    for port in ports:
                        entry = {
                            "ts": ts,
                            "device_id": device_id,
                            "port": port.get("port", 0),
                            "packets_rx": port.get("packetsReceived", 0),
                            "packets_tx": port.get("packetsSent", 0),
                            "bytes_rx": port.get("bytesReceived", 0),
                            "bytes_tx": port.get("bytesSent", 0),
                            "packets_rx_dropped": port.get("packetsRxDropped", 0),
                            "packets_tx_dropped": port.get("packetsTxDropped", 0),
                            "packets_rx_errors": port.get("packetsRxErrors", 0),
                            "packets_tx_errors": port.get("packetsTxErrors", 0),
                            "duration_sec": port.get("durationSec", 0),
                        }
                        with open(path, "a") as f:
                            f.write(json.dumps(entry) + "\n")
            except Exception:
                pass
            time.sleep(ONOS_POLL_INTERVAL)

    def _onos_topology_loop(self):
        """ONOS topologiya o'zgarishlarini kuzatish."""
        path = os.path.join(ONOS_STATS_DIR, "topology_events.jsonl")
        prev_links = set()

        while self._running:
            try:
                ts = time.time()

                # Link holatlarini tekshirish
                links = self.onos.get_links()
                current_links = set()
                for link in links:
                    src = link.get("src", {})
                    dst = link.get("dst", {})
                    key = f"{src.get('device','')}/{src.get('port','')}-{dst.get('device','')}/{dst.get('port','')}"
                    current_links.add(key)

                # O'zgarishlarni aniqlash
                new_links = current_links - prev_links
                lost_links = prev_links - current_links

                for link_key in new_links:
                    entry = {"ts": ts, "event": "link_up", "link": link_key}
                    with open(path, "a") as f:
                        f.write(json.dumps(entry) + "\n")

                for link_key in lost_links:
                    entry = {"ts": ts, "event": "link_down", "link": link_key}
                    with open(path, "a") as f:
                        f.write(json.dumps(entry) + "\n")

                if prev_links:  # birinchi tekshiruvda emas
                    if new_links or lost_links:
                        summary = {
                            "ts": ts,
                            "event": "topology_change",
                            "total_links": len(current_links),
                            "new_links": len(new_links),
                            "lost_links": len(lost_links),
                        }
                        with open(path, "a") as f:
                            f.write(json.dumps(summary) + "\n")

                prev_links = current_links

                # Devices holati
                devices = self.onos.get_devices()
                device_summary = {
                    "ts": ts,
                    "event": "device_snapshot",
                    "total_devices": len(devices),
                    "available": sum(1 for d in devices if d.get("available")),
                }
                with open(path, "a") as f:
                    f.write(json.dumps(device_summary) + "\n")

            except Exception:
                pass
            time.sleep(ONOS_POLL_INTERVAL * 2)

    # ── sFlow ─────────────────────────────────────────────

    def _setup_sflow(self):
        """OVS switchlarda sFlow yoqish va sFlow-RT ishga tushirish."""
        # sFlow-RT Docker container
        subprocess.run(["docker", "rm", "-f", SFLOW_RT_CONTAINER], capture_output=True)
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", SFLOW_RT_CONTAINER,
                "--net=host",
                SFLOW_RT_IMAGE,
            ],
            capture_output=True,
        )

        time.sleep(3)

        # Har bir OVS switchda sFlow yoqish
        for switch in self.net.switches:
            sw_name = switch.name
            try:
                subprocess.run(
                    [
                        "ovs-vsctl", "--", "--id=@sf", "create", "sflow",
                        "agent=lo",
                        f"target=\"127.0.0.1:{SFLOW_COLLECTOR_PORT}\"",
                        f"header=128",
                        f"sampling={SFLOW_SAMPLING_RATE}",
                        f"polling={SFLOW_POLLING_INTERVAL}",
                        "--", "set", "bridge", sw_name, "sflow=@sf",
                    ],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass

        print("[Collector] sFlow sozlandi")

    def _sflow_poll_loop(self):
        """sFlow-RT API dan ma'lumot yig'ish."""
        import requests

        path = os.path.join(SFLOW_DATA_DIR, "sflow_metrics.jsonl")

        # sFlow-RT tayyor bo'lishini kutish
        time.sleep(10)

        while self._running:
            try:
                # Interface counters
                r = requests.get(
                    f"{SFLOW_RT_API}/dump/ALL/ALL/json",
                    timeout=5,
                )
                if r.status_code == 200:
                    data = r.json()
                    entry = {
                        "ts": time.time(),
                        "type": "counters",
                        "data": data,
                    }
                    with open(path, "a") as f:
                        f.write(json.dumps(entry) + "\n")
            except Exception:
                pass

            try:
                # Top flows
                r = requests.get(
                    f"{SFLOW_RT_API}/activeflows/ALL/ip_source,ip_destination,ip_protocol,tcp_source_port,tcp_destination_port/json",
                    timeout=5,
                )
                if r.status_code == 200:
                    flows = r.json()
                    entry = {
                        "ts": time.time(),
                        "type": "active_flows",
                        "flow_count": len(flows),
                        "flows": flows[:50],  # top 50
                    }
                    with open(path, "a") as f:
                        f.write(json.dumps(entry) + "\n")
            except Exception:
                pass

            time.sleep(SFLOW_POLL_INTERVAL)

    # ── tcpdump ───────────────────────────────────────────

    def _start_tcpdump(self):
        """Asosiy linklarda packet capture boshlash."""
        # Inter-AS switch'larning birinchi interfeysi uchun capture
        capture_switches = ["s4", "s5", "s3", "s9", "s10", "s11", "s15", "s19"]

        for sw_name in capture_switches:
            switch = self.net.get(sw_name)
            if not switch:
                continue

            for intf in switch.intfList():
                if intf.name == "lo":
                    continue

                pcap_file = os.path.join(PCAP_DIR, f"{intf.name}.pcap")
                try:
                    proc = subprocess.Popen(
                        [
                            "tcpdump",
                            "-i", intf.name,
                            "-w", pcap_file,
                            "-s", "128",          # faqat headerlar
                            "-c", "1000000",      # max 1M paket
                            "-q",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._tcpdump_procs[intf.name] = proc
                except Exception:
                    pass

        print(f"[Collector] tcpdump boshlandi: {len(self._tcpdump_procs)} interface")

    # ── RTT Monitoring ────────────────────────────────────

    def _rtt_monitor_loop(self):
        """Host juftliklari orasida RTT o'lchash."""
        path = os.path.join(STATS_DIR, "rtt_measurements.jsonl")
        hosts = self.net.hosts

        # Har 30 sekundda tasodifiy juftliklar ping qilish
        while self._running:
            if len(hosts) < 2:
                time.sleep(30)
                continue

            # 5 ta tasodifiy juftlik
            pairs = []
            for _ in range(5):
                h1, h2 = random.sample(hosts, 2)
                pairs.append((h1, h2))

            for h1, h2 in pairs:
                try:
                    result = h1.cmd(f"ping -c 3 -W 2 {h2.IP()}")
                    rtt = self._parse_ping_rtt(result)
                    entry = {
                        "ts": time.time(),
                        "src": h1.name,
                        "dst": h2.name,
                        "src_ip": h1.IP(),
                        "dst_ip": h2.IP(),
                        "rtt_min": rtt.get("min"),
                        "rtt_avg": rtt.get("avg"),
                        "rtt_max": rtt.get("max"),
                        "rtt_mdev": rtt.get("mdev"),
                        "packet_loss_pct": rtt.get("loss", 100),
                    }
                    with open(path, "a") as f:
                        f.write(json.dumps(entry) + "\n")
                except Exception:
                    pass

            time.sleep(30)

    @staticmethod
    def _parse_ping_rtt(output: str) -> dict:
        """ping natijasidan RTT va packet loss ajratish."""
        result = {}
        for line in output.split("\n"):
            if "packet loss" in line:
                try:
                    parts = line.split(",")
                    for part in parts:
                        if "packet loss" in part:
                            result["loss"] = float(part.strip().split("%")[0])
                except (ValueError, IndexError):
                    pass
            if "rtt min/avg/max/mdev" in line or "round-trip min/avg/max" in line:
                try:
                    values = line.split("=")[1].strip().split("/")
                    result["min"] = float(values[0])
                    result["avg"] = float(values[1])
                    result["max"] = float(values[2])
                    if len(values) > 3:
                        result["mdev"] = float(values[3].split()[0])
                except (ValueError, IndexError):
                    pass
        return result

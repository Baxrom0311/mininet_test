"""
Real internet trafik generatsiyasi.
Xilma-xil ilova turlari: web, video, DNS, VoIP, SSH, bulk transfer.
"""

import json
import os
import random
import threading
import time

from realistic_internet.config import (
    TRAFFIC_PROFILES, SERVER_HOSTS, CLIENT_HOSTS, STATS_DIR,
)


class TrafficOrchestrator:
    """Barcha trafik generatsiyasini boshqarish."""

    def __init__(self, net):
        self.net = net
        self._running = False
        self._threads = []
        self._pids = {}       # host_name -> [pid, ...]
        self._log_path = os.path.join(STATS_DIR, "traffic_events.jsonl")

        # Host ob'ektlarini oldindan yig'ish
        self._servers = {}
        self._clients = {}
        for name in SERVER_HOSTS:
            h = net.get(name)
            if h:
                self._servers[name] = h
        for name in CLIENT_HOSTS:
            h = net.get(name)
            if h:
                self._clients[name] = h

    def start_all(self):
        """Barcha trafik turlarini boshlash."""
        if self._running:
            return
        self._running = True

        print("[Traffic] Serverlar ishga tushirilmoqda...")
        self._start_servers()
        time.sleep(2)

        print("[Traffic] Fon trafigi ishga tushirilmoqda...")
        self._start_background_traffic()

        print("[Traffic] Ilova trafigi ishga tushirilmoqda...")
        self._start_application_traffic()

        print("[Traffic] Barcha trafik ishlayapti")

    def stop_all(self):
        """Barcha trafikni to'xtatish."""
        self._running = False

        print("[Traffic] To'xtatilmoqda...")

        # Barcha host'lardagi jarayonlarni o'ldirish
        for host in list(self._servers.values()) + list(self._clients.values()):
            try:
                host.cmd("killall -9 iperf3 2>/dev/null; "
                         "killall -9 ITGSend ITGRecv 2>/dev/null; "
                         "kill %% 2>/dev/null")
            except Exception:
                pass

        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()

        print("[Traffic] Barcha trafik to'xtatildi")

    def _start_servers(self):
        """Server hostlarda iperf3 va boshqa serverlarni ishga tushirish."""
        for name, host in self._servers.items():
            # iperf3 server (TCP va UDP uchun)
            host.cmd("iperf3 -s -p 5201 -D")
            host.cmd("iperf3 -s -p 5202 -D")

            # HTTP server (video/web simulyatsiya)
            host.cmd("python3 -m http.server 80 &>/dev/null &")
            host.cmd("python3 -m http.server 8080 &>/dev/null &")

            self._log_event("server_started", {"host": name, "ip": host.IP()})

    def _start_background_traffic(self):
        """Har bir client'dan serverga doimiy fon trafigi."""
        server_list = list(self._servers.values())
        if not server_list:
            return

        for name, client in self._clients.items():
            # Har bir client tasodifiy serverga ulanadi
            target = random.choice(server_list)
            target_ip = target.IP()

            # Past intensivlikdagi TCP fon trafigi
            bw = random.choice(["100K", "200K", "500K", "1M"])
            client.cmd(
                f"iperf3 -c {target_ip} -p 5201 "
                f"-t 3600 -b {bw} --logfile /dev/null &"
            )

            # Periodic ping (RTT monitoring uchun)
            client.cmd(
                f"ping -i 2 -W 1 {target_ip} > /tmp/ping_{name}.log 2>&1 &"
            )

            self._log_event("background_started", {
                "client": name, "server": target.name,
                "target_ip": target_ip, "bandwidth": bw,
            })

    def _start_application_traffic(self):
        """Ilovaga oid trafik generatsiyasi (davomli loop)."""
        t = threading.Thread(target=self._traffic_loop, daemon=True)
        t.start()
        self._threads.append(t)

    def _traffic_loop(self):
        """Asosiy trafik generatsiya loop - turli ilovalarni simulyatsiya."""
        server_list = list(self._servers.values())
        client_list = list(self._clients.values())

        if not server_list or not client_list:
            return

        while self._running:
            # Tasodifiy trafik profili tanlash (weighted)
            profile_name = self._weighted_choice()
            profile = TRAFFIC_PROFILES[profile_name]

            # Tasodifiy client va server
            client = random.choice(client_list)
            server = random.choice(server_list)
            server_ip = server.IP()

            # Trafik generatsiya qilish
            try:
                if profile["protocol"] == "tcp":
                    self._gen_tcp(client, server_ip, profile, profile_name)
                else:
                    self._gen_udp(client, server_ip, profile, profile_name)
            except Exception:
                pass

            # Keyingi trafik orasida kutish (0.5-5 sek)
            time.sleep(random.uniform(0.5, 5.0))

    def _weighted_choice(self) -> str:
        """Weighted random trafik profili tanlash."""
        names = list(TRAFFIC_PROFILES.keys())
        weights = [TRAFFIC_PROFILES[n]["weight"] for n in names]
        return random.choices(names, weights=weights, k=1)[0]

    def _gen_tcp(self, client, server_ip: str, profile: dict, name: str):
        """TCP trafik generatsiya."""
        rate = profile["avg_rate_kbps"]
        duration = min(profile["duration_sec"], 120)  # max 2 min
        port = profile["port"]

        # iperf3 TCP
        bw_str = f"{rate}K"
        client.cmd(
            f"iperf3 -c {server_ip} -p 5201 "
            f"-t {duration} -b {bw_str} "
            f"--cport {random.randint(10000, 60000)} "
            f"--logfile /dev/null &"
        )

        self._log_event("app_traffic", {
            "type": name,
            "protocol": "tcp",
            "client": client.name,
            "server_ip": server_ip,
            "rate_kbps": rate,
            "duration_sec": duration,
        })

    def _gen_udp(self, client, server_ip: str, profile: dict, name: str):
        """UDP trafik generatsiya."""
        rate = profile["avg_rate_kbps"]
        duration = min(profile["duration_sec"], 120)
        port = profile["port"]

        # iperf3 UDP
        bw_str = f"{rate}K"
        client.cmd(
            f"iperf3 -c {server_ip} -p 5202 -u "
            f"-t {duration} -b {bw_str} "
            f"--logfile /dev/null &"
        )

        self._log_event("app_traffic", {
            "type": name,
            "protocol": "udp",
            "client": client.name,
            "server_ip": server_ip,
            "rate_kbps": rate,
            "duration_sec": duration,
        })

    def generate_dns_traffic(self, duration_sec=60):
        """DNS so'rovlar generatsiya (scapy bilan)."""
        client_list = list(self._clients.values())
        dns_servers = [h for n, h in self._servers.items() if "dns" in n]
        if not dns_servers or not client_list:
            return

        def _dns_loop():
            end_time = time.time() + duration_sec
            while time.time() < end_time and self._running:
                client = random.choice(client_list)
                dns_srv = random.choice(dns_servers)
                domain = f"host{random.randint(1,10000)}.example.com"

                # nslookup orqali DNS query
                client.cmd(
                    f"nslookup {domain} {dns_srv.IP()} "
                    f">/dev/null 2>&1 &"
                )
                time.sleep(random.uniform(0.1, 1.0))

        t = threading.Thread(target=_dns_loop, daemon=True)
        t.start()
        self._threads.append(t)

    def generate_burst(self, target_ip: str, rate_mbps=50, duration_sec=5):
        """Qisqa muddatli burst trafik (congestion test)."""
        clients = list(self._clients.values())
        if not clients:
            return

        for client in random.sample(clients, min(3, len(clients))):
            client.cmd(
                f"iperf3 -c {target_ip} -p 5201 "
                f"-t {duration_sec} -b {rate_mbps}M "
                f"--logfile /dev/null &"
            )

        self._log_event("burst_traffic", {
            "target_ip": target_ip,
            "rate_mbps": rate_mbps,
            "duration_sec": duration_sec,
            "client_count": min(3, len(clients)),
        })

    def _log_event(self, event_type: str, data: dict):
        """Trafik hodisasini JSONL ga yozish."""
        entry = {"ts": time.time(), "event": event_type, **data}
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

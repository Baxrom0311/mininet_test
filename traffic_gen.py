"""Trafik generatori: kunlik (diurnal) yuklanish naqshiga ega real HTTP/
iperf3/DNS/anomaliya trafigi. TCP congestion control va QoS/DSCP belgilash
ham shu yerda amalga oshiriladi."""

import json
import os
import random
import threading
import time
from collections import deque

from config import DATA_DIR

TRAFFIC_MIX = [
    ("web",     0.25, "tcp", (100, 2000)),
    ("video",   0.18, "udp", (500, 5000)),
    ("dns",     0.08, "udp", (5, 20)),
    ("bulk",    0.08, "tcp", (2000, 8000)),
    ("voip",    0.05, "udp", (50, 80)),
    ("ssh",     0.05, "tcp", (10, 50)),
    ("gaming",  0.04, "udp", (80, 150)),
    ("email",   0.03, "tcp", (30, 150)),
    ("iot",     0.04, "udp", (1, 10)),
    ("https",   0.08, "tcp", (150, 3000)),    # TLS handshake + payload
    ("streaming", 0.05, "tcp", (800, 4000)),  # Adaptive bitrate (ABR)
    ("p2p",     0.03, "tcp", (500, 6000)),    # Peer-to-peer trafik
    ("cloud",   0.04, "tcp", (200, 2500)),    # Cloud API / SaaS
]

# QoS/DiffServ: trafik turi -> (DSCP nomi, TOS bayt qiymati, navbat bandi)
# band 0 = eng yuqori ustuvorlik (EF, real-vaqt), 1 = o'rta (AF21), 2 = fon (CS1/BE)
DSCP_MAP = {
    "voip":      ("EF",   0xB8, 0),
    "gaming":    ("EF",   0xB8, 0),
    "video":     ("EF",   0xB8, 0),
    "streaming": ("EF",   0xB8, 0),
    "web":       ("AF21", 0x48, 1),
    "https":     ("AF21", 0x48, 1),
    "cloud":     ("AF21", 0x48, 1),
    "ssh":       ("AF21", 0x48, 1),
    "dns":       ("AF21", 0x48, 1),
    "bulk":      ("CS1",  0x20, 2),
    "p2p":       ("CS1",  0x20, 2),
    "email":     ("CS1",  0x20, 2),
    "iot":       ("CS1",  0x20, 2),
}
DEFAULT_DSCP = ("BE", 0x00, 2)

# Anomal/xavfli trafik turlari (kam chastotada)
ANOMALY_MIX = [
    ("port_scan",     0.25, "tcp", (1, 5)),     # nmap-like port scan
    ("syn_flood",     0.15, "tcp", (500, 3000)), # DDoS SYN flood
    ("udp_flood",     0.15, "udp", (1000, 8000)),# UDP volumetric attack
    ("dns_amplify",   0.10, "udp", (50, 200)),   # DNS amplification
    ("slowloris",     0.10, "tcp", (1, 3)),       # Slow HTTP attack
    ("ping_sweep",    0.10, "icmp", (1, 5)),      # Network reconnaissance
    ("brute_force",   0.10, "tcp", (5, 20)),      # SSH/HTTP brute force
    ("data_exfil",    0.05, "tcp", (100, 1000)),  # Data exfiltration
]

# TCP congestion control algoritmlari
TCP_CC_ALGORITHMS = ["cubic", "reno", "bbr"]


class TrafficGen:
    def __init__(self, net, topo):
        self.net = net
        self.topo = topo
        self._running = False
        self._threads = []
        self._log = os.path.join(DATA_DIR, "stats/traffic_log.jsonl")
        self._conn_log = os.path.join(DATA_DIR, "stats/connection_states.jsonl")
        self._dns_log = os.path.join(DATA_DIR, "stats/dns_queries.jsonl")
        self._anomaly_log = os.path.join(DATA_DIR, "stats/anomaly_events.jsonl")
        self._http_log = os.path.join(DATA_DIR, "stats/http_transactions.jsonl")
        self.servers = {n: net.get(n) for n, h in topo["hosts"].items() if h["role"] == "server"}
        self.clients = {n: net.get(n) for n, h in topo["hosts"].items() if h["role"] == "client"}
        self._sim_start = time.time()
        self._time_scale = 3600 / 60
        self._active_connections = deque(maxlen=500)
        self._dns_cache = {}  # DNS cache simulyatsiyasi
        self._conn_counter = 0
        self._host_cc = {}  # host_name -> TCP congestion control algoritmi

    def _safe_cmd(self, host, cmd_str):
        try:
            return host.cmd(cmd_str)
        except Exception:
            return ""

    def _get_load_factor(self):
        """Vaqtga bog'liq yuklanish koeffitsienti (diurnal pattern)."""
        elapsed = time.time() - self._sim_start
        sim_hour = (elapsed / 60) % 24
        import math
        base = 0.3
        morning_peak = 0.4 * max(0, math.exp(-0.5 * (sim_hour - 12) ** 2 / 4))
        evening_peak = 0.5 * max(0, math.exp(-0.5 * (sim_hour - 20) ** 2 / 3))
        night_dip = -0.2 * max(0, math.exp(-0.5 * (sim_hour - 4) ** 2 / 4))
        noise = random.uniform(-0.05, 0.05)
        return max(0.1, min(1.0, base + morning_peak + evening_peak + night_dip + noise))

    def start(self):
        self._running = True
        srv_list = list(self.servers.values())

        # ── 1. Real HTTP serverlar ──
        for name, host in self.servers.items():
            self._safe_cmd(host, "iperf3 -s -p 5201 -D 2>/dev/null")
            self._safe_cmd(host, "iperf3 -s -p 5202 -D 2>/dev/null")
            # Real HTTP server (Python) — yengil fayllar
            self._safe_cmd(host, "mkdir -p /tmp/www")
            self._safe_cmd(host, "dd if=/dev/urandom of=/tmp/www/small.bin bs=1K count=5 2>/dev/null")
            self._safe_cmd(host, "dd if=/dev/urandom of=/tmp/www/medium.bin bs=1K count=50 2>/dev/null")
            self._safe_cmd(host, "dd if=/dev/urandom of=/tmp/www/large.bin bs=1K count=200 2>/dev/null")
            self._safe_cmd(host, "echo '<html><body>Server " + name + "</body></html>' > /tmp/www/index.html")
            self._safe_cmd(host, "cd /tmp/www && python3 -m http.server 80 &")

        # ── 2. TCP CC algoritmlarini aralash qo'yish (client + server) ──
        # Server tomonining CC algoritmi ham muhim — server->client yuklab
        # olishda oqim server tomonidagi algoritm bilan boshqariladi.
        for name, host in list(self.clients.items()) + list(self.servers.items()):
            cc = random.choice(TCP_CC_ALGORITHMS)
            self._safe_cmd(host, f"sysctl -w net.ipv4.tcp_congestion_control={cc} 2>/dev/null")
            self._host_cc[name] = cc
            self._log_event("tcp_cc_set", name, "", "config", cc, tcp_cc=cc)

        # ── 3. Background traffic ──
        for name, client in self.clients.items():
            srv = random.choice(srv_list)
            bw = random.choice(["30K", "50K", "100K"])
            self._safe_cmd(client, f"iperf3 -c {srv.IP()} -p 5201 -t 86400 -b {bw} --logfile /dev/null &")
            self._log_event("background", name, srv.name, "tcp", bw, tcp_cc=self._host_cc.get(name))

        # ── 4. Barcha loop'lar ──
        for target in [self._app_loop, self._burst_loop, self._congestion_loop,
                       self._http_loop, self._dns_loop, self._anomaly_loop,
                       self._connection_state_loop, self._adaptive_bitrate_loop]:
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        print(f"[Traffic] {len(self.clients)} client -> {len(self.servers)} server (diurnal+http+dns+anomaly)")

    def stop(self):
        self._running = False
        for h in list(self.servers.values()) + list(self.clients.values()):
            self._safe_cmd(h, "killall -9 iperf3 2>/dev/null")
        for t in self._threads:
            t.join(timeout=5)

    def _app_loop(self):
        srv_list = list(self.servers.values())
        cli_list = list(self.clients.values())
        while self._running:
            load = self._get_load_factor()
            r = random.random()
            cumul = 0
            chosen = TRAFFIC_MIX[0]
            for mix in TRAFFIC_MIX:
                cumul += mix[1]
                if r <= cumul:
                    chosen = mix
                    break
            name, _, proto, rate_range = chosen
            # Rate scales with load factor
            base_rate = random.randint(rate_range[0], rate_range[1])
            rate = int(base_rate * load)
            duration = random.randint(2, 20)
            client = random.choice(cli_list)
            server = random.choice(srv_list)
            port = 5201 if proto == "tcp" else 5202
            flag = "" if proto == "tcp" else "-u "
            dscp_name, tos, band = DSCP_MAP.get(name, DEFAULT_DSCP)
            self._safe_cmd(client, f"iperf3 -c {server.IP()} -p {port} {flag}-t {duration} -b {rate}K "
                                    f"-S {tos} --logfile /dev/null &")
            self._log_event(name, client.name, server.name, proto, f"{rate}K",
                           load_factor=round(load, 2), tcp_cc=self._host_cc.get(client.name),
                           dscp=dscp_name, queue_band=band)
            time.sleep(random.uniform(0.3, 2.0))

    def _burst_loop(self):
        while self._running:
            time.sleep(random.randint(15, 50))
            if not self._running:
                break
            load = self._get_load_factor()
            if random.random() > load:
                continue  # Kechasi burst kam
            srv = random.choice(list(self.servers.values()))
            clients = random.sample(list(self.clients.values()), min(3, len(self.clients)))
            dscp_name, tos, band = DSCP_MAP["web"]  # burst = to'satdan interaktiv yuklanish
            for c in clients:
                rate = random.randint(2000, 8000)
                self._safe_cmd(c, f"iperf3 -c {srv.IP()} -p 5201 -t 3 -b {rate}K -S {tos} --logfile /dev/null &")
            self._log_event("burst", ",".join(c.name for c in clients), srv.name, "tcp", "high",
                           load_factor=round(load, 2),
                           tcp_cc=",".join(self._host_cc.get(c.name, "") for c in clients),
                           dscp=dscp_name, queue_band=band)

    def _congestion_loop(self):
        """Maxsus congestion - bottleneck linkni to'ldirish."""
        while self._running:
            time.sleep(random.randint(20, 60))
            if not self._running:
                break
            load = self._get_load_factor()
            if load < 0.5:
                continue
            # Eng kichik bandwidth linkni topish va uni to'ldirish
            min_bw_link = min(self.topo["links"].items(), key=lambda x: x[1]["bw"])
            bw = min_bw_link[1]["bw"]
            # Bottleneck'ni to'ldiradigan trafik
            srv = random.choice(list(self.servers.values()))
            dscp_name, tos, band = DSCP_MAP["bulk"]  # bottleneck to'ldirish = fon trafik
            for c in list(self.clients.values()):
                rate = int(bw * 1000 * 0.3)  # Link kapasitetining 30%
                self._safe_cmd(c, f"iperf3 -c {srv.IP()} -p 5201 -t 5 -b {rate}K -S {tos} --logfile /dev/null &")
            self._log_event("congestion_gen", "all_clients", srv.name, "tcp",
                           f"{bw}Mbps_link", load_factor=round(load, 2),
                           dscp=dscp_name, queue_band=band)

    # ── Real HTTP trafik (wget/curl) ──
    def _http_loop(self):
        """Real HTTP GET/POST so'rovlari — turli URL, turli o'lcham."""
        srv_list = list(self.servers.values())
        cli_list = list(self.clients.values())
        files = ["index.html", "small.bin", "medium.bin", "large.bin"]
        methods = ["GET"] * 8 + ["POST"] * 2  # 80% GET, 20% POST
        status_codes = [200] * 85 + [301] * 3 + [304] * 4 + [404] * 5 + [500] * 2 + [503] * 1
        while self._running:
            load = self._get_load_factor()
            client = random.choice(cli_list)
            server = random.choice(srv_list)
            method = random.choice(methods)
            target = random.choice(files)
            url = f"http://{server.IP()}/{target}"
            start_t = time.time()
            if method == "GET":
                result = self._safe_cmd(client,
                    f"wget -q -O /dev/null --timeout=5 {url} 2>&1; echo $?")
            else:
                payload_size = random.randint(100, 5000)
                result = self._safe_cmd(client,
                    f"curl -s -o /dev/null -w '%{{http_code}} %{{time_total}} %{{size_download}} %{{speed_download}}' "
                    f"-X POST -d '@/dev/urandom' --max-time 5 {url} 2>/dev/null || echo 'fail'")
            elapsed_ms = (time.time() - start_t) * 1000
            # Connection state tracking
            self._conn_counter += 1
            conn_id = self._conn_counter
            status = random.choice(status_codes)
            entry = {
                "ts": time.time(), "conn_id": conn_id,
                "client": client.name, "client_ip": client.IP(),
                "server": server.name, "server_ip": server.IP(),
                "method": method, "url": f"/{target}", "port": 80,
                "status_code": status, "response_time_ms": round(elapsed_ms, 2),
                "bytes_transferred": {"index.html": 100, "small.bin": 5120,
                                       "medium.bin": 51200, "large.bin": 204800}.get(target, 0),
                "keep_alive": random.random() > 0.3,
                "user_agent": random.choice(["Mozilla/5.0", "Chrome/125", "curl/8.0", "python-requests/2.31"]),
                "tls": target != "index.html" and random.random() > 0.4,
                "tcp_cc": self._host_cc.get(client.name),
            }
            try:
                with open(self._http_log, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
            self._log_event("http", client.name, server.name, "tcp", f"{method}:{target}",
                           status=status, response_ms=round(elapsed_ms, 2),
                           tcp_cc=self._host_cc.get(client.name))
            time.sleep(random.uniform(0.3, 1.5))

    # ── DNS Resolution zanjiri (root -> TLD -> authoritative) ──
    def _dns_loop(self):
        """DNS so'rovlarni simulyatsiya — real ko'p bosqichli rekursiya:
        resolver avval root'ga, keyin TLD'ga, oxirida authoritative serverga
        murojaat qiladi (har biri o'z darajasida keshlanadi)."""
        cli_list = list(self.clients.values())
        dns_servers = [h for n, h in self.servers.items() if "dns" in n.lower()]
        if not dns_servers:
            dns_servers = list(self.servers.values())[:1]
        domains = [
            ("example.com", "A", 300), ("cdn.example.com", "CNAME", 60),
            ("api.service.io", "A", 120), ("mail.example.com", "MX", 3600),
            ("video.stream.tv", "A", 30), ("login.secure.bank", "A", 180),
            ("iot-hub.device.net", "A", 600), ("game.server.gg", "A", 15),
            ("news.portal.org", "A", 90), ("search.engine.com", "A", 45),
            ("social.media.app", "A", 60), ("cloud.storage.io", "AAAA", 120),
            ("malware.bad.evil", "A", 0),  # Suspicious domain
            ("c2.hidden.onion", "A", 0),   # C2 callback attempt
        ]
        # Har daraja uchun keshlash muddati: root/TLD kamdan-kam o'zgaradi,
        # authoritative javob domenning o'z TTL'siga bog'liq.
        ROOT_TTL = 21600   # ~6 soat — root NS ma'lumoti uzoq keshlanadi
        TLD_TTL = 3600     # 1 soat — TLD NS ma'lumoti
        STAGE_MULT = {"root": (0.8, 1.2), "tld": (1.0, 1.8), "authoritative": (1.0, 2.5)}
        resolution_counter = 0
        while self._running:
            client = random.choice(cli_list)
            domain, qtype, ttl = random.choice(domains)
            dns_srv = random.choice(dns_servers)
            tld = domain.rsplit(".", 1)[-1]
            now = time.time()

            root_key = f"{client.name}:root:{tld}"
            tld_key = f"{client.name}:tld:{tld}"
            auth_key = f"{client.name}:auth:{domain}"

            def _fresh(key, default_ttl):
                c = self._dns_cache.get(key)
                return bool(c and (now - c["ts"]) < c.get("ttl", default_ttl))

            root_fresh = _fresh(root_key, ROOT_TTL)
            tld_fresh = _fresh(tld_key, TLD_TTL)
            auth_fresh = _fresh(auth_key, ttl)

            resolution_counter += 1
            resolution_id = f"{client.name}-{resolution_counter}-{int(now)}"

            if auth_fresh:
                # To'liq kesh urishi — hech qanday tarmoq so'rovi kerak emas
                entry = {
                    "ts": now, "resolution_id": resolution_id,
                    "client": client.name, "client_ip": client.IP(),
                    "dns_server": dns_srv.name, "dns_server_ip": dns_srv.IP(),
                    "domain": domain, "query_type": qtype, "ttl": ttl,
                    "stage": "cache", "referral_to": "",
                    "response_time_ms": round(random.uniform(0.1, 0.5), 2),
                    "cache_hit": True, "source": "cache",
                    "rcode": "NOERROR", "is_suspicious": False,
                }
                self._append_dns(entry)
                time.sleep(random.uniform(0.5, 4.0))
                continue

            # Bazaviy tarmoq kechikishini bitta real ping bilan o'lchaymiz,
            # so'ng har bosqich shu asosda o'z ulushini qo'shadi (real
            # zanjirda har bosqich alohida RTT sarflaydi).
            start_t = time.time()
            self._safe_cmd(client, f"ping -c 1 -W 1 {dns_srv.IP()} > /dev/null 2>&1")
            base_rtt = max((time.time() - start_t) * 1000, 0.2)

            stages = []
            if not root_fresh:
                stages.append(("root", f"tld-ns.{tld}"))
                self._dns_cache[root_key] = {"ts": now, "ttl": ROOT_TTL}
            if not tld_fresh:
                stages.append(("tld", f"ns.{domain}"))
                self._dns_cache[tld_key] = {"ts": now, "ttl": TLD_TTL}
            stages.append(("authoritative", ""))
            self._dns_cache[auth_key] = {"ts": now, "ttl": ttl}

            total_time = 0.0
            for stage_name, referral_to in stages:
                lo, hi = STAGE_MULT[stage_name]
                stage_time = base_rtt * random.uniform(lo, hi)
                total_time += stage_time

                is_final = stage_name == "authoritative"
                rcode = "NOERROR"
                is_suspicious = False
                if is_final:
                    is_suspicious = "malware" in domain or "hidden" in domain
                    if is_suspicious:
                        rcode = random.choice(["NXDOMAIN", "NOERROR", "SERVFAIL"])
                    elif random.random() < 0.02:
                        rcode = random.choice(["NXDOMAIN", "SERVFAIL", "REFUSED"])

                entry = {
                    "ts": now, "resolution_id": resolution_id,
                    "client": client.name, "client_ip": client.IP(),
                    "dns_server": dns_srv.name, "dns_server_ip": dns_srv.IP(),
                    "domain": domain, "query_type": qtype, "ttl": ttl,
                    "stage": stage_name, "referral_to": referral_to,
                    "response_time_ms": round(stage_time, 2),
                    "total_response_time_ms": round(total_time, 2) if is_final else None,
                    "cache_hit": False, "source": "recursive" if len(stages) > 1 else "authoritative",
                    "rcode": rcode, "is_suspicious": is_suspicious,
                }
                self._append_dns(entry)
            time.sleep(random.uniform(0.5, 4.0))

    def _append_dns(self, entry):
        try:
            with open(self._dns_log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    # ── Anomal trafik (scan, DDoS, exfil) ──
    def _anomaly_loop(self):
        """Xavfli/anomal trafik — IDS/IPS dataset uchun."""
        cli_list = list(self.clients.values())
        srv_list = list(self.servers.values())
        all_hosts = list(self.net.hosts)
        while self._running:
            time.sleep(random.randint(5, 20))
            if not self._running:
                break
            load = self._get_load_factor()
            # Anomallar tunda ko'p (attackerlar tunda faol)
            anomaly_prob = 0.6 if load < 0.3 else 0.35
            if random.random() > anomaly_prob:
                continue

            r = random.random()
            cumul = 0
            chosen = ANOMALY_MIX[0]
            for mix in ANOMALY_MIX:
                cumul += mix[1]
                if r <= cumul:
                    chosen = mix
                    break

            a_name, _, proto, rate_range = chosen
            attacker = random.choice(cli_list)
            target = random.choice(srv_list)
            duration = random.randint(2, 15)

            if a_name == "port_scan":
                start_port = random.randint(1, 1000)
                count = random.randint(5, 20)
                self._safe_cmd(attacker,
                    f"hping3 -S -p ++{start_port} -c {count} "
                    f"{target.IP()} 2>/dev/null &")
                details = f"ports={start_port}-{start_port+count}"
            elif a_name == "syn_flood":
                count = random.randint(50, 200)
                port = random.choice([80, 443, 22, 8080])
                self._safe_cmd(attacker,
                    f"hping3 -S -p {port} -i u10000 "
                    f"-c {count} {target.IP()} 2>/dev/null &")
                details = f"port={port} count={count}"
            elif a_name == "udp_flood":
                count = random.randint(50, 200)
                port = random.choice([53, 123, 161, 1900])
                self._safe_cmd(attacker,
                    f"hping3 --udp -p {port} -i u10000 -c {count} {target.IP()} 2>/dev/null &")
                details = f"port={port} count={count}"
            elif a_name == "dns_amplify":
                self._safe_cmd(attacker,
                    f"hping3 --udp -p 53 -d 40 -c 50 {target.IP()} 2>/dev/null &")
                details = "dns_amplification"
            elif a_name == "ping_sweep":
                subnet = target.IP().rsplit(".", 1)[0]
                self._safe_cmd(attacker, f"ping -c 1 -W 1 {subnet}.1 > /dev/null 2>&1 &")
                self._safe_cmd(attacker, f"ping -c 1 -W 1 {subnet}.2 > /dev/null 2>&1 &")
                self._safe_cmd(attacker, f"ping -c 1 -W 1 {subnet}.254 > /dev/null 2>&1 &")
                details = f"subnet={subnet}.0/24"
            elif a_name == "brute_force":
                for _ in range(random.randint(2, 5)):
                    self._safe_cmd(attacker,
                        f"curl -s -o /dev/null --max-time 2 "
                        f"http://{target.IP()}/login?user=admin&pass={random.randint(1000,9999)} 2>/dev/null &")
                details = "http_brute_force"
            elif a_name == "data_exfil":
                rate = random.randint(rate_range[0], rate_range[1])
                self._safe_cmd(attacker,
                    f"iperf3 -c {target.IP()} -p 5201 -t {min(duration,5)} -b {rate}K --logfile /dev/null &")
                details = f"exfil_rate={rate}K"
            else:
                # slowloris
                for _ in range(random.randint(2, 5)):
                    self._safe_cmd(attacker,
                        f"curl -s -o /dev/null --max-time {min(duration,5)} "
                        f"http://{target.IP()}/large.bin 2>/dev/null &")
                details = "slowloris_connections"

            entry = {
                "ts": time.time(), "type": a_name, "attacker": attacker.name,
                "attacker_ip": attacker.IP(), "target": target.name,
                "target_ip": target.IP(), "proto": proto,
                "duration_sec": duration, "details": details,
                "severity": {"port_scan": "medium", "syn_flood": "critical",
                             "udp_flood": "critical", "dns_amplify": "high",
                             "slowloris": "high", "ping_sweep": "low",
                             "brute_force": "high", "data_exfil": "critical"}.get(a_name, "medium"),
                "is_anomaly": True,
                "sim_hour": round((time.time() - self._sim_start) / 60 % 24, 1),
            }
            try:
                with open(self._anomaly_log, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
            self._log_event(f"anomaly:{a_name}", attacker.name, target.name, proto,
                           details, severity=entry["severity"])

    # ── TCP Connection state tracking ──
    def _connection_state_loop(self):
        """TCP connection holatlari — SYN, ESTABLISHED, FIN_WAIT, TIME_WAIT."""
        while self._running:
            time.sleep(8)
            if not self._running:
                break
            for host in random.sample(list(self.net.hosts), min(4, len(self.net.hosts))):
                try:
                    # ss (socket statistics) orqali connection holatlarini o'qish
                    result = self._safe_cmd(host, "ss -tan state all 2>/dev/null | tail -30")
                    if not result:
                        continue
                    states = {"LISTEN": 0, "SYN-SENT": 0, "SYN-RECV": 0,
                              "ESTAB": 0, "FIN-WAIT-1": 0, "FIN-WAIT-2": 0,
                              "CLOSE-WAIT": 0, "CLOSING": 0, "LAST-ACK": 0,
                              "TIME-WAIT": 0, "CLOSED": 0}
                    for line in result.split("\n"):
                        for state in states:
                            if state in line:
                                states[state] += 1
                    entry = {
                        "ts": time.time(), "host": host.name, "host_ip": host.IP(),
                        **states,
                        "total_connections": sum(states.values()),
                        "active_connections": states["ESTAB"] + states["SYN-SENT"] + states["SYN-RECV"],
                    }
                    try:
                        with open(self._conn_log, "a") as f:
                            f.write(json.dumps(entry) + "\n")
                    except OSError:
                        pass
                except Exception:
                    pass

    # ── Adaptive Bitrate Streaming (ABR) ──
    def _adaptive_bitrate_loop(self):
        """Video streaming — bitrate dinamik o'zgaradi tarmoq holatiga qarab."""
        cli_list = list(self.clients.values())
        srv_list = list(self.servers.values())
        quality_levels = [
            ("240p", 300), ("360p", 700), ("480p", 1500),
            ("720p", 3000), ("1080p", 6000), ("4K", 15000),
        ]
        while self._running:
            time.sleep(random.randint(5, 15))
            if not self._running:
                break
            load = self._get_load_factor()
            if random.random() > load * 0.6:
                continue
            client = random.choice(cli_list)
            server = random.choice(srv_list)
            # Avval kichik segment (bitrate probe)
            current_quality = 2  # 480p dan boshlash
            segments = random.randint(3, 8)
            for seg in range(segments):
                if not self._running:
                    break
                quality_name, bitrate = quality_levels[current_quality]
                duration = random.uniform(2, 4)  # segment davomiyligi
                rate = int(bitrate * random.uniform(0.8, 1.2))
                start_t = time.time()
                self._safe_cmd(client,
                    f"iperf3 -c {server.IP()} -p 5201 -t {int(duration)} -b {rate}K --logfile /dev/null")
                real_time = time.time() - start_t
                # Buffer ratio — agar segment tez yuklansa quality oshadi
                buffer_ratio = duration / max(real_time, 0.1)
                if buffer_ratio > 1.5 and current_quality < len(quality_levels) - 1:
                    current_quality += 1  # Quality up
                elif buffer_ratio < 0.8 and current_quality > 0:
                    current_quality -= 1  # Quality down (buffering)
                rebuffer = real_time > duration * 1.2
                entry = {
                    "ts": time.time(), "client": client.name, "server": server.name,
                    "segment": seg, "quality": quality_name, "bitrate_kbps": rate,
                    "segment_duration_sec": round(duration, 2),
                    "download_time_sec": round(real_time, 2),
                    "buffer_ratio": round(buffer_ratio, 2),
                    "rebuffer_event": rebuffer,
                    "quality_change": seg > 0,
                }
                self._log_event("abr_stream", client.name, server.name, "tcp",
                               f"{quality_name}:{rate}K", rebuffer=rebuffer,
                               buffer_ratio=round(buffer_ratio, 2))

    def _log_event(self, traffic_type, client, server, proto, rate, **extra):
        entry = {"ts": time.time(), "type": traffic_type, "client": client,
                 "server": server, "proto": proto, "rate": rate, **extra}
        try:
            with open(self._log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

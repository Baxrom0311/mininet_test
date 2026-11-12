"""TCP Info Collector: Linux kernel struct tcp_info va soket darajasidagi
barcha metrikalarni (CWND, SSTHRESH, RTT, Retransmissions, Goodput)
yig'uvchi va tahlil qiluvchi modul.

Manbalar:
- ss -tiemOn (iproute2 socket statistics)
- /proc/net/tcp va /proc/net/tcp6
- Demo/Emulyatsiya rejimi uchun sintetik oqim generatori
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
import random
import re
import socket
import struct
import subprocess
import time
from typing import Any, Dict, List, Optional


TCP_STATES = {
    "01": "ESTABLISHED",
    "02": "SYN_SENT",
    "03": "SYN_RECV",
    "04": "FIN_WAIT1",
    "05": "FIN_WAIT2",
    "06": "TIME_WAIT",
    "07": "CLOSE",
    "08": "CLOSE_WAIT",
    "09": "LAST_ACK",
    "0A": "LISTEN",
    "0B": "CLOSING",
}

RATE_MULTIPLIERS = {
    "b": 1.0,
    "bps": 1.0,
    "k": 1e3,
    "kb": 1e3,
    "kbps": 1e3,
    "kb/s": 8e3,
    "kib": 1024.0,
    "kibps": 1024.0,
    "m": 1e6,
    "mb": 1e6,
    "mbps": 1e6,
    "mb/s": 8e6,
    "mib": 1024.0 * 1024.0,
    "mibps": 1024.0 * 1024.0,
    "g": 1e9,
    "gb": 1e9,
    "gbps": 1e9,
    "gb/s": 8e9,
    "gib": 1024.0 * 1024.0 * 1024.0,
    "gibps": 1024.0 * 1024.0 * 1024.0,
    "t": 1e12,
    "tb": 1e12,
    "tbps": 1e12,
    "tib": 1024.0 * 1024.0 * 1024.0 * 1024.0,
    "tibps": 1024.0 * 1024.0 * 1024.0 * 1024.0,
}


def parse_rate_string(s: str) -> float:
    """Masalan '125Mbps', '10.5Kbps', '1.2Gbps', '2388648bps', '30.6Mbps' ni bit/sekundga o'tkazadi."""
    s = s.strip()
    if not s:
        return 0.0
    m = re.match(r"^([\d.]+)\s*([a-zA-Z/]+)?$", s)
    if not m:
        return 0.0
    try:
        val = float(m.group(1))
    except ValueError:
        return 0.0
    unit = (m.group(2) or "bps").lower()
    return val * RATE_MULTIPLIERS.get(unit, 1.0)


def hex_to_ip(hex_str: str) -> str:
    """Kichik endianli 16-lik hex IP ni (masalan '0100007F') '127.0.0.1' ga o'tkazadi."""
    try:
        if len(hex_str) == 8:
            addr_int = int(hex_str, 16)
            return socket.inet_ntop(socket.AF_INET, struct.pack("<I", addr_int))
        elif len(hex_str) == 32:
            raw = bytes.fromhex(hex_str)
            dwords = struct.unpack("<4I", raw)
            be_raw = struct.pack(">4I", *dwords)
            return socket.inet_ntop(socket.AF_INET6, be_raw)
    except Exception:
        pass
    return hex_str


@dataclass
class TCPInfoSample:
    """Bitta vaqt lahzasidagi TCP soket telemetriyasi."""

    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    state: str = "ESTABLISHED"
    snd_cwnd: int = 10
    snd_ssthresh: Optional[int] = None
    rtt_ms: float = 1.0
    rttvar_ms: float = 0.5
    retrans: int = 0
    total_retrans: int = 0
    bytes_acked: int = 0
    bytes_received: int = 0
    bytes_sent: int = 0
    delivery_rate_bps: float = 0.0
    pacing_rate_bps: float = 0.0
    send_rate_bps: float = 0.0
    ca_state: str = "open"  # open, disorder, cwr, recovery, loss
    congestion_algo: str = "cubic"
    mss: int = 1460
    rcv_space: int = 65535
    snd_wnd: int = 65535
    rcv_wnd: int = 65535
    min_rtt_ms: Optional[float] = None
    node: str = "localhost"
    timestamp: float = field(default_factory=time.time)

    @property
    def stream_key(self) -> str:
        return f"{self.src_ip}:{self.src_port}->{self.dst_ip}:{self.dst_port}"

    @property
    def stream_id(self) -> str:
        return self.stream_key

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "stream_key": self.stream_key,
            "protocol": "tcp",
            "node": self.node,
            "state": self.state,
            "src_ip": self.src_ip,
            "src_port": self.src_port,
            "dst_ip": self.dst_ip,
            "dst_port": self.dst_port,
            "cwnd": self.snd_cwnd,
            "snd_cwnd": self.snd_cwnd,
            "ssthresh": self.snd_ssthresh,
            "snd_ssthresh": self.snd_ssthresh,
            "rtt_ms": self.rtt_ms,
            "rttvar_ms": self.rttvar_ms,
            "retrans": self.retrans,
            "retrans_total": self.total_retrans,
            "total_retrans": self.total_retrans,
            "bytes_acked": self.bytes_acked,
            "goodput_bytes": self.bytes_acked,
            "bytes_received": self.bytes_received,
            "bytes_sent": self.bytes_sent,
            "delivery_rate_bps": self.delivery_rate_bps,
            "pacing_rate_bps": self.pacing_rate_bps,
            "send_rate_bps": self.send_rate_bps,
            "ca_state": self.ca_state,
            "congestion_algo": self.congestion_algo,
            "mss": self.mss,
            "rcv_space": self.rcv_space,
            "snd_wnd": self.snd_wnd,
            "rcv_wnd": self.rcv_wnd,
            "min_rtt_ms": self.min_rtt_ms,
            "timestamp": self.timestamp,
        }


class TCPInfoCollector:
    """Linux tarmoq nom fazolaridagi (yoki xostdagi) TCP soketlarini skan qiladi
    va to'liq L4 transport parametrlarini chiqarib beradi."""

    def __init__(self, driver=None):
        self.driver = driver

    def parse_ss_output(self, text: str, now: Optional[float] = None, node: str = "localhost") -> List[TCPInfoSample]:
        """`ss -tiemOn` buyrug'i natijasini `TCPInfoSample` namunalariga aylantiradi."""
        lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
        samples: List[TCPInfoSample] = []
        ts = now if now is not None else time.time()

        i = 0
        while i < len(lines):
            line = lines[i]
            parts = line.split()
            if not parts or parts[0] in ("Netid", "State", "Recv-Q", "Send-Q"):
                i += 1
                continue

            state = parts[0].upper()
            known_states = (
                "ESTAB", "ESTABLISHED", "SYN-SENT", "SYN-RECV", "FIN-WAIT-1",
                "FIN-WAIT-2", "TIME-WAIT", "CLOSE", "CLOSE-WAIT", "LAST-ACK", "LISTEN", "CLOSING"
            )
            if state in known_states:
                # ss ustunlari:
                # Agar Netid bo'lsa: Netid State Recv-Q Send-Q Local Peer [Info...]
                # Agar Netid bo'lmasa: State Recv-Q Send-Q Local Peer [Info...]
                if len(parts) >= 5:
                    local_addr = parts[3]
                    peer_addr = parts[4]
                    line_remainder = parts[5:]
                elif len(parts) >= 4:
                    local_addr = parts[2]
                    peer_addr = parts[3]
                    line_remainder = parts[4:]
                else:
                    i += 1
                    continue

                src_ip, src_port = self._split_addr_port(local_addr)
                dst_ip, dst_port = self._split_addr_port(peer_addr)

                # Qo'shimcha satrlar (tab yoki probel bilan boshlangan satrlar)
                extra_lines = []
                while i + 1 < len(lines) and (lines[i + 1].startswith("\t") or lines[i + 1].startswith(" ")):
                    i += 1
                    extra_lines.append(lines[i].strip())

                info_blob = " ".join(line_remainder + extra_lines)
                sample = self._parse_blob_to_sample(
                    blob=info_blob,
                    node=node,
                    state=state,
                    src_ip=src_ip,
                    src_port=src_port,
                    dst_ip=dst_ip,
                    dst_port=dst_port,
                    timestamp=ts,
                )
                samples.append(sample)
            i += 1

        return samples

    def collect_from_output(self, text: str, node: str = "localhost") -> List[Dict[str, Any]]:
        """`ss` chiqishini lug'atlar (dict) shaklida qaytaradi."""
        samples = self.parse_ss_output(text, node=node)
        return [s.to_dict() for s in samples]

    @staticmethod
    def _split_addr_port(addr: str) -> tuple[str, int]:
        addr = addr.strip()
        if addr.startswith("[") and "]:" in addr:
            ip, port_s = addr[1:].split("]:", 1)
        elif ":" in addr:
            ip, port_s = addr.rsplit(":", 1)
        else:
            return addr, 0

        # Scope identifierni tozalash (masalan fe80::1%eth0)
        if "%" in ip:
            ip = ip.split("%", 1)[0]

        # Port raqamini o'qish
        if port_s == "*":
            port = 0
        else:
            try:
                port = int(port_s)
            except ValueError:
                # Agar servis nomi bo'lsa (masalan http, https)
                port = {"http": 80, "https": 443, "ssh": 22, "dns": 53}.get(port_s.lower(), 0)
        return ip, port

    @classmethod
    def _parse_blob_to_sample(
        cls,
        blob: str,
        node: str,
        state: str,
        src_ip: str,
        src_port: int,
        dst_ip: str,
        dst_port: int,
        timestamp: float,
    ) -> TCPInfoSample:
        sample = TCPInfoSample(
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            state=state,
            node=node,
            timestamp=timestamp,
        )

        if not blob:
            return sample

        # Congestion algorithm
        m_algo = re.search(r"\b(cubic|bbr|reno|vegas|bic|dctcp|cdg)\b", blob, re.IGNORECASE)
        if m_algo:
            sample.congestion_algo = m_algo.group(1).lower()

        # CWND
        m_cwnd = re.search(r"\bcwnd:(\d+)\b", blob)
        if m_cwnd:
            sample.snd_cwnd = int(m_cwnd.group(1))

        # SSTHRESH
        m_ssthresh = re.search(r"\bssthresh:(\d+)\b", blob)
        if m_ssthresh:
            sample.snd_ssthresh = int(m_ssthresh.group(1))

        # RTT / RTTVAR
        m_rtt = re.search(r"\brtt:([\d.]+)(?:/([\d.]+))?\b", blob)
        if m_rtt:
            sample.rtt_ms = float(m_rtt.group(1))
            if m_rtt.group(2):
                sample.rttvar_ms = float(m_rtt.group(2))

        # Min RTT (minrtt yoki BBR mrtt)
        m_minrtt = re.search(r"\b(?:minrtt|mrtt):([\d.]+)\b", blob)
        if m_minrtt:
            sample.min_rtt_ms = float(m_minrtt.group(1))

        # Retransmits
        m_ret = re.search(r"\bretrans:(\d+)(?:/(\d+))?\b", blob)
        if m_ret:
            sample.retrans = int(m_ret.group(1))
            if m_ret.group(2):
                sample.total_retrans = int(m_ret.group(2))
            else:
                sample.total_retrans = sample.retrans

        # Delivery rate (Goodput)
        m_rate = re.search(r"\bdelivery_rate(?:\s+|:)([\d.]+\s*[a-zA-Z/]*)\b", blob)
        if m_rate:
            sample.delivery_rate_bps = parse_rate_string(m_rate.group(1))
        else:
            # BBR bw parameter (masalan bbr:(bw:10Mbps,mrtt:12.5))
            m_bw = re.search(r"\bbw:([\d.]+\s*[a-zA-Z/]*)\b", blob)
            if m_bw:
                sample.delivery_rate_bps = parse_rate_string(m_bw.group(1))

        # Pacing rate
        m_pace = re.search(r"\bpacing_rate(?:\s+|:)([\d.]+\s*[a-zA-Z/]*)\b", blob)
        if m_pace:
            sample.pacing_rate_bps = parse_rate_string(m_pace.group(1))

        # Send rate
        m_send = re.search(r"\bsend(?:\s+|:)([\d.]+\s*[a-zA-Z/]*)\b", blob)
        if m_send:
            sample.send_rate_bps = parse_rate_string(m_send.group(1))
            if sample.delivery_rate_bps == 0.0:
                sample.delivery_rate_bps = sample.send_rate_bps

        # Bytes acked (Goodput bytes)
        m_bytes_acked = re.search(r"\bbytes_acked(?::|\s+|=)(\d+)\b", blob)
        if m_bytes_acked:
            sample.bytes_acked = int(m_bytes_acked.group(1))

        # Bytes received
        m_bytes_rcv = re.search(r"\bbytes_received(?::|\s+|=)(\d+)\b", blob)
        if m_bytes_rcv:
            sample.bytes_received = int(m_bytes_rcv.group(1))

        # Bytes sent
        m_bytes_sent = re.search(r"\bbytes_sent(?::|\s+|=)(\d+)\b", blob)
        if m_bytes_sent:
            sample.bytes_sent = int(m_bytes_sent.group(1))

        # MSS
        m_mss = re.search(r"\bmss:(\d+)\b", blob)
        if m_mss:
            sample.mss = int(m_mss.group(1))

        # RCV Space
        m_rcv = re.search(r"\brcvspace:(\d+)\b", blob)
        if m_rcv:
            sample.rcv_space = int(m_rcv.group(1))

        # SND_WND / RCV_WND
        m_snd_wnd = re.search(r"\bsnd_wnd:(\d+)\b", blob)
        if m_snd_wnd:
            sample.snd_wnd = int(m_snd_wnd.group(1))
        m_rcv_wnd = re.search(r"\brcv_wnd:(\d+)\b", blob)
        if m_rcv_wnd:
            sample.rcv_wnd = int(m_rcv_wnd.group(1))

        # Congestion State (Open, Disorder, CWR, Recovery, Loss)
        # Non-open holatlarni oldin qidiramiz
        ca_found = None
        for ca in ("disorder", "cwr", "recovery", "loss", "open"):
            pattern = rf"\b(?:ca_state[:=])?{ca}\b|\btcp_ca_{ca}\b"
            if re.search(pattern, blob, re.IGNORECASE):
                ca_found = ca.lower()
                break
        sample.ca_state = ca_found if ca_found else "open"

        return sample

    @classmethod
    def parse_proc_tcp_content(
        cls, content: str, node: str = "localhost", now: Optional[float] = None
    ) -> List[TCPInfoSample]:
        """/proc/net/tcp yoki /proc/net/tcp6 matnini tahlil qilib TCPInfoSample namunalarini qaytaradi."""
        samples: List[TCPInfoSample] = []
        lines = content.strip().splitlines()
        if not lines:
            return []
        ts = now if now is not None else time.time()
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 10:
                continue
            local_hex = parts[1]
            peer_hex = parts[2]
            state_hex = parts[3]

            state = TCP_STATES.get(state_hex, "UNKNOWN")
            if ":" not in local_hex or ":" not in peer_hex:
                continue

            lip_hex, lport_hex = local_hex.split(":", 1)
            pip_hex, pport_hex = peer_hex.split(":", 1)

            src_ip = hex_to_ip(lip_hex)
            try:
                src_port = int(lport_hex, 16)
            except ValueError:
                src_port = 0

            dst_ip = hex_to_ip(pip_hex)
            try:
                dst_port = int(pport_hex, 16)
            except ValueError:
                dst_port = 0

            # Retransmissions (parts[6])
            retrans = 0
            if len(parts) > 6:
                try:
                    retrans = int(parts[6], 16)
                except ValueError:
                    try:
                        retrans = int(parts[6])
                    except ValueError:
                        retrans = 0

            # RTO (parts[12])
            rto_ms = 200.0
            if len(parts) > 12:
                try:
                    rto_jiffies = int(parts[12])
                    rto_ms = float(rto_jiffies * 10.0) if rto_jiffies > 0 else 200.0
                except ValueError:
                    pass

            # CWND (parts[15])
            cwnd = 10
            if len(parts) > 15:
                try:
                    cwnd_val = int(parts[15])
                    if cwnd_val > 0:
                        cwnd = cwnd_val
                except ValueError:
                    pass

            # SSTHRESH (parts[16])
            ssthresh = None
            if len(parts) > 16:
                try:
                    ssthresh_val = int(parts[16])
                    if 0 < ssthresh_val < 0x7FFFFFFF:
                        ssthresh = ssthresh_val
                except ValueError:
                    pass

            sample = TCPInfoSample(
                src_ip=src_ip,
                src_port=src_port,
                dst_ip=dst_ip,
                dst_port=dst_port,
                state=state,
                snd_cwnd=cwnd,
                snd_ssthresh=ssthresh,
                retrans=retrans,
                total_retrans=retrans,
                rtt_ms=round(rto_ms / 10.0, 2),
                node=node,
                timestamp=ts,
            )
            samples.append(sample)
        return samples

    def collect_from_proc_tcp(self, path: str = "/proc/net/tcp", node: str = "localhost") -> List[Dict[str, Any]]:
        """`/proc/net/tcp` faylini o'qib TCP oqimlarini chiqaradi (fallback mexanizmi)."""
        samples: List[TCPInfoSample] = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    samples.extend(self.parse_proc_tcp_content(f.read(), node=node))
            except OSError:
                pass
        # IPv6 /proc/net/tcp6 ham mavjud bo'lsa, o'qish
        if path == "/proc/net/tcp" and os.path.exists("/proc/net/tcp6"):
            try:
                with open("/proc/net/tcp6", "r", encoding="utf-8") as f:
                    samples.extend(self.parse_proc_tcp_content(f.read(), node=node))
            except OSError:
                pass
        return [s.to_dict() for s in samples]

    def collect_live_node(self, node_name: str, node_obj: Any = None) -> List[Dict[str, Any]]:
        """Mininet tugunidan (namespace ichida) jonli `ss` natijalarini oladi."""
        cmd = "ss -tiemOn state connected"
        try:
            if node_obj is not None and hasattr(node_obj, "cmd"):
                out = node_obj.cmd(cmd)
            else:
                r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=2.0)
                out = r.stdout
            return self.collect_from_output(out, node=node_name)
        except Exception:
            return []

    @staticmethod
    def simulate_sample(
        src_ip: str = "10.0.1.10",
        src_port: int = 45678,
        dst_ip: str = "10.0.2.20",
        dst_port: int = 80,
        step: int = 0,
        has_loss: bool = False,
        base_rtt: float = 10.0,
        algo: str = "cubic",
        node: str = "localhost",
    ) -> TCPInfoSample:
        """Kuzatuv paneli va testlar uchun realistik TCP dinamikasini generatsiya qiladi."""
        now = time.time() + float(step)
        if has_loss:
            cwnd = 10
            ssthresh = 16
            ca_state = "recovery"
            retrans = 1
            total_retrans = 1
        elif step < 4:
            cwnd = int(min(64, 4 * math.pow(2.0, step)))
            ssthresh = 32
            ca_state = "open"
            retrans = 0
            total_retrans = 0
        else:
            cwnd = min(64, 16 + (step - 4) * 2)
            ssthresh = 32
            ca_state = "open"
            retrans = 0
            total_retrans = 0

        rtt = base_rtt + (cwnd / 20.0)
        delivery_rate = (cwnd * 1460 * 8) / (max(0.001, rtt / 1000.0))

        return TCPInfoSample(
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            state="ESTABLISHED",
            snd_cwnd=cwnd,
            snd_ssthresh=ssthresh,
            rtt_ms=round(rtt, 2),
            rttvar_ms=round(rtt * 0.1, 2),
            retrans=retrans,
            total_retrans=total_retrans,
            bytes_acked=step * 14600,
            bytes_received=step * 29200,
            bytes_sent=step * 14600 + 4000,
            delivery_rate_bps=round(delivery_rate, 1),
            pacing_rate_bps=round(delivery_rate * 1.2, 1),
            send_rate_bps=round(delivery_rate, 1),
            ca_state=ca_state,
            congestion_algo=algo,
            node=node,
            timestamp=now,
        )

    @staticmethod
    def generate_synthetic_stream(
        stream_id: str,
        t: float,
        src_ip: str = "10.0.0.1",
        src_port: int = 49152,
        dst_ip: str = "10.0.0.2",
        dst_port: int = 80,
        node: str = "h1",
        algo: str = "cubic",
        base_rtt_ms: float = 12.0,
    ) -> Dict[str, Any]:
        """Demo rejim va testlar uchun realistik TCP transport oqimi simulyatsiyasi."""
        cycle_t = t % 25.0
        if cycle_t < 6.0:
            cwnd = int(min(64, 4 * math.pow(1.5, cycle_t)))
            ca_state = "open"
            retrans = 0
        elif cycle_t < 20.0:
            phase_t = cycle_t - 6.0
            cwnd = int(min(128, 25 + phase_t * 3.5))
            ca_state = "open"
            retrans = 0
        elif cycle_t < 22.0:
            cwnd = max(10, int(70 * 0.5))
            ca_state = "recovery"
            retrans = random.randint(1, 3)
        else:
            cwnd = 35 + int((cycle_t - 22.0) * 2)
            ca_state = "open"
            retrans = 0

        rtt_jitter = random.gauss(0, base_rtt_ms * 0.08)
        current_rtt = max(1.0, base_rtt_ms + (cwnd / 40.0) + rtt_jitter)
        delivery_rate = (cwnd * 1460 * 8) / (max(0.001, current_rtt / 1000.0))

        sample = TCPInfoSample(
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            state="ESTABLISHED",
            snd_cwnd=cwnd,
            snd_ssthresh=64 if cycle_t >= 6.0 else None,
            rtt_ms=round(current_rtt, 3),
            rttvar_ms=round(abs(rtt_jitter) * 1.5, 3),
            retrans=retrans,
            total_retrans=int(t // 25) * 3 + retrans,
            bytes_acked=int(t * 125000),
            bytes_received=int(t * 850000),
            bytes_sent=int(t * 125000) + 2000,
            delivery_rate_bps=round(delivery_rate, 1),
            pacing_rate_bps=round(delivery_rate * 1.2, 1),
            send_rate_bps=round(delivery_rate, 1),
            ca_state=ca_state,
            congestion_algo=algo,
            node=node,
            timestamp=t,
        )
        data = sample.to_dict()
        data["stream_id"] = stream_id
        return data

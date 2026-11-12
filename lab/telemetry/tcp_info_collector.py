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
    "bps": 1.0,
    "kbps": 1e3,
    "kibps": 1024.0,
    "mbps": 1e6,
    "mibps": 1024.0 * 1024.0,
    "gbps": 1e9,
    "gibps": 1024.0 * 1024.0 * 1024.0,
}


def parse_rate_string(s: str) -> float:
    """Masalan '125Mbps', '10.5Kbps', '1.2Gbps' ni bit/sekundga o'tkazadi."""
    m = re.match(r"^([\d.]+)\s*([a-zA-Z]+)?$", s.strip())
    if not m:
        return 0.0
    val = float(m.group(1))
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
    delivery_rate_bps: float = 0.0
    pacing_rate_bps: float = 0.0
    ca_state: str = "open"
    congestion_algo: str = "cubic"
    mss: int = 1460
    rcv_space: int = 65535
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
            "bytes_received": self.bytes_received,
            "delivery_rate_bps": self.delivery_rate_bps,
            "pacing_rate_bps": self.pacing_rate_bps,
            "ca_state": self.ca_state,
            "congestion_algo": self.congestion_algo,
            "mss": self.mss,
            "rcv_space": self.rcv_space,
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
                if len(parts) >= 5:
                    local_addr = parts[3]
                    peer_addr = parts[4]
                elif len(parts) >= 4:
                    local_addr = parts[2]
                    peer_addr = parts[3]
                else:
                    i += 1
                    continue

                src_ip, src_port = self._split_addr_port(local_addr)
                dst_ip, dst_port = self._split_addr_port(peer_addr)

                extra_lines = []
                while i + 1 < len(lines) and (lines[i + 1].startswith("\t") or lines[i + 1].startswith(" ")):
                    i += 1
                    extra_lines.append(lines[i].strip())

                info_blob = " ".join(extra_lines)
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
        try:
            port = int(port_s)
        except ValueError:
            port = 0
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

        # Retransmits
        m_ret = re.search(r"\bretrans:(\d+)(?:/(\d+))?\b", blob)
        if m_ret:
            sample.retrans = int(m_ret.group(1))
            if m_ret.group(2):
                sample.total_retrans = int(m_ret.group(2))
            else:
                sample.total_retrans = sample.retrans

        # Delivery rate
        m_rate = re.search(r"\bdelivery_rate\s+([\d.]+[a-zA-Z]+)\b", blob)
        if m_rate:
            sample.delivery_rate_bps = parse_rate_string(m_rate.group(1))

        # Pacing rate
        m_pace = re.search(r"\bpacing_rate\s+([\d.]+[a-zA-Z]+)\b", blob)
        if m_pace:
            sample.pacing_rate_bps = parse_rate_string(m_pace.group(1))

        # Bytes acked
        m_bytes_acked = re.search(r"\bbytes_acked:(\d+)\b", blob)
        if m_bytes_acked:
            sample.bytes_acked = int(m_bytes_acked.group(1))

        # Bytes received
        m_bytes_rcv = re.search(r"\bbytes_received:(\d+)\b", blob)
        if m_bytes_rcv:
            sample.bytes_received = int(m_bytes_rcv.group(1))

        # MSS
        m_mss = re.search(r"\bmss:(\d+)\b", blob)
        if m_mss:
            sample.mss = int(m_mss.group(1))

        # RCV Space
        m_rcv = re.search(r"\brcvspace:(\d+)\b", blob)
        if m_rcv:
            sample.rcv_space = int(m_rcv.group(1))

        # CA State
        for ca in ("open", "disorder", "cwr", "recovery", "loss"):
            if re.search(rf"\b{ca}\b", blob, re.IGNORECASE):
                sample.ca_state = ca.lower()
                break

        return sample

    def collect_from_proc_tcp(self, path: str = "/proc/net/tcp", node: str = "localhost") -> List[Dict[str, Any]]:
        """`/proc/net/tcp` faylini o'qib TCP oqimlarini chiqaradi (fallback mexanizmi)."""
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return []

        streams: List[Dict[str, Any]] = []
        lines = content.strip().splitlines()
        now = time.time()
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

            lip_hex, lport_hex = local_hex.split(":")
            pip_hex, pport_hex = peer_hex.split(":")

            src_ip = hex_to_ip(lip_hex)
            src_port = int(lport_hex, 16)
            dst_ip = hex_to_ip(pip_hex)
            dst_port = int(pport_hex, 16)

            sample = TCPInfoSample(
                src_ip=src_ip,
                src_port=src_port,
                dst_ip=dst_ip,
                dst_port=dst_port,
                state=state,
                node=node,
                timestamp=now,
            )
            streams.append(sample.to_dict())
        return streams

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
            delivery_rate_bps=round(delivery_rate, 1),
            pacing_rate_bps=round(delivery_rate * 1.2, 1),
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
        delivery_rate = (cwnd * 1460 * 8) / (current_rtt / 1000.0)

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
            delivery_rate_bps=round(delivery_rate, 1),
            pacing_rate_bps=round(delivery_rate * 1.2, 1),
            ca_state=ca_state,
            congestion_algo=algo,
            node=node,
            timestamp=t,
        )
        data = sample.to_dict()
        data["stream_id"] = stream_id
        return data

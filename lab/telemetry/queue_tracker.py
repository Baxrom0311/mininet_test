"""Linux Traffic Control (tc) Queue Telemetry & Bufferbloat Tracker:
Interfeys navbatlari (Queue occupancy, backlog bytes, packet drops) va
Bufferbloat holatini kuzatuvchi hamda tahlil qiluvchi modul.

Manbalar:
- `tc -s qdisc show dev <interface>`
- `tc -s class show dev <interface>`
- Qo'llab-quvvatlanadigan qdisc turlari: FIFO (pfifo, bfifo, pfifo_fast), RED, CoDel, fq_codel, TBF, HTB, Netem
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import os
import re
import subprocess
import time
from typing import Any, Deque, Dict, List, Optional, Tuple


RATE_MULTIPLIERS = {
    "bps": 1.0,
    "b/s": 1.0,
    "kbps": 8000.0,
    "kbit": 1000.0,
    "kibps": 8192.0,
    "mbps": 8000000.0,
    "mbit": 1000000.0,
    "mibps": 8388608.0,
    "gbps": 8000000000.0,
    "gbit": 1000000000.0,
}

SIZE_MULTIPLIERS = {
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "kb": 1024,
    "k": 1024,
    "kib": 1024,
    "mb": 1024 * 1024,
    "m": 1024 * 1024,
    "mib": 1024 * 1024,
    "gb": 1024 * 1024 * 1024,
    "g": 1024 * 1024 * 1024,
    "p": 1,
    "pkt": 1,
    "pkts": 1,
}


def parse_rate_bps(val_str: str) -> float:
    """Masalan '10Mbit', '100Kbps', '1Gbit', '500bps' ni bit/sekundga o'tkazadi."""
    if not val_str:
        return 0.0
    m = re.match(r"^([\d.]+)\s*([a-zA-Z/]+)?$", val_str.strip())
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = (m.group(2) or "bps").lower()
    return val * RATE_MULTIPLIERS.get(unit, 1.0)


def parse_size_bytes(val_str: str) -> int:
    """Masalan '1500b', '60Kb', '32Mb', '1000p' ni son qiymatiga o'tkazadi."""
    if not val_str:
        return 0
    m = re.match(r"^([\d.]+)\s*([a-zA-Z]+)?$", val_str.strip())
    if not m:
        return 0
    val = float(m.group(1))
    unit = (m.group(2) or "b").lower()
    mult = SIZE_MULTIPLIERS.get(unit, 1)
    return int(val * mult)


def parse_time_ms(val_str: str) -> float:
    """Masalan '5ms', '5.0ms', '100us', '1s' ni millisekundga o'tkazadi."""
    if not val_str:
        return 0.0
    m = re.match(r"^([\d.]+)\s*([a-zA-Z]+)?$", val_str.strip())
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = (m.group(2) or "ms").lower()
    if unit in ("s", "sec"):
        return val * 1000.0
    elif unit in ("us", "usec"):
        return val / 1000.0
    return val


@dataclass
class BufferbloatAssessment:
    """Bufferbloat holati va uning chuqur tahlili."""

    dev: str
    qdisc_kind: str
    detected: bool
    severity: str  # "normal", "low", "warning", "high", "critical"
    score: float  # 0.0 - 100.0
    queue_delay_ms: float
    occupancy_pct: float
    backlog_bytes: int
    backlog_packets: int
    dropped_packets: int
    early_drops: int = 0
    ecn_marks: int = 0
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dev": self.dev,
            "qdisc_kind": self.qdisc_kind,
            "detected": self.detected,
            "severity": self.severity,
            "score": round(self.score, 1),
            "queue_delay_ms": round(self.queue_delay_ms, 2),
            "occupancy_pct": round(self.occupancy_pct, 2),
            "backlog_bytes": self.backlog_bytes,
            "backlog_packets": self.backlog_packets,
            "dropped_packets": self.dropped_packets,
            "early_drops": self.early_drops,
            "ecn_marks": self.ecn_marks,
            "recommendation": self.recommendation,
        }


@dataclass
class QueueStats:
    """Bitta qdisc yoki class telemetriya namunasi."""

    dev: str
    node: str = "localhost"
    kind: str = "pfifo"  # fifo, pfifo, bfifo, pfifo_fast, red, codel, fq_codel, tbf, htb, etc.
    handle: str = "0:"
    parent: str = "root"
    bytes_sent: int = 0
    packets_sent: int = 0
    dropped: int = 0
    overlimits: int = 0
    requeues: int = 0
    backlog_bytes: int = 0
    backlog_packets: int = 0
    qlen: int = 0
    limit: Optional[int] = None
    limit_unit: Optional[str] = None  # "p" (packets) yoki "b" (bytes)
    occupancy_pct: float = 0.0
    estimated_queue_delay_ms: float = 0.0
    bufferbloat_detected: bool = False
    bufferbloat_severity: str = "normal"  # "normal", "low", "warning", "high", "critical"
    bloat_score: float = 0.0  # 0 - 100
    rate_bps: Optional[float] = None
    target_ms: Optional[float] = None
    interval_ms: Optional[float] = None
    ldelay_ms: Optional[float] = None
    ecn: bool = False
    ecn_marks: int = 0
    early_drops: int = 0
    dropping: bool = False
    params: Dict[str, Any] = field(default_factory=dict)
    extra_stats: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    is_class: bool = False

    @property
    def queue_id(self) -> str:
        return f"{self.node}:{self.dev}:{self.handle}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queue_id": self.queue_id,
            "dev": self.dev,
            "node": self.node,
            "kind": self.kind,
            "handle": self.handle,
            "parent": self.parent,
            "bytes_sent": self.bytes_sent,
            "packets_sent": self.packets_sent,
            "dropped": self.dropped,
            "overlimits": self.overlimits,
            "requeues": self.requeues,
            "backlog_bytes": self.backlog_bytes,
            "backlog_packets": self.backlog_packets,
            "qlen": self.qlen,
            "limit": self.limit,
            "limit_unit": self.limit_unit,
            "occupancy_pct": round(self.occupancy_pct, 2),
            "estimated_queue_delay_ms": round(self.estimated_queue_delay_ms, 2),
            "bufferbloat_detected": self.bufferbloat_detected,
            "bufferbloat_severity": self.bufferbloat_severity,
            "bloat_score": round(self.bloat_score, 1),
            "rate_bps": self.rate_bps,
            "target_ms": self.target_ms,
            "interval_ms": self.interval_ms,
            "ldelay_ms": round(self.ldelay_ms, 2) if self.ldelay_ms is not None else None,
            "ecn": self.ecn,
            "ecn_marks": self.ecn_marks,
            "early_drops": self.early_drops,
            "dropping": self.dropping,
            "params": self.params,
            "extra_stats": self.extra_stats,
            "timestamp": self.timestamp,
            "is_class": self.is_class,
        }


class QueueTracker:
    """Linux `tc` buyruqlari chiqishini tahlil qiluvchi va Bufferbloat metrikalarini kuzatuvchi sinf."""

    DEFAULT_ASSUMED_BW_BPS = 10_000_000.0  # 10 Mbps (agar qdisc parametrlarida rate noma'lum bo'lsa)
    DEFAULT_PACKET_LIMIT = 1000

    def __init__(self, driver=None, max_history_len: int = 200):
        self.driver = driver
        self.max_history_len = max_history_len
        self._history: Dict[str, Deque[QueueStats]] = {}
        self._latest: Dict[str, QueueStats] = {}

    def reset(self) -> None:
        self._history.clear()
        self._latest.clear()

    @staticmethod
    def _evaluate_bufferbloat(
        dev: str,
        kind: str,
        backlog_bytes: int,
        backlog_packets: int,
        limit: Optional[int],
        limit_unit: Optional[str],
        rate_bps: Optional[float],
        ldelay_ms: Optional[float],
        dropped: int,
        early_drops: int,
        ecn_marks: int,
        dropping: bool,
    ) -> Tuple[float, float, bool, str, float, str]:
        """Bufferbloat tahlili:
        Qaytaradi:
            (occupancy_pct, estimated_queue_delay_ms, bufferbloat_detected, severity, bloat_score, recommendation)
        """
        # 1. Occupancy hisoblash
        if limit and limit > 0:
            if limit_unit == "b":
                occ_pct = (backlog_bytes / float(limit)) * 100.0
            else:
                occ_pct = (backlog_packets / float(limit)) * 100.0
        else:
            occ_pct = (backlog_packets / float(QueueTracker.DEFAULT_PACKET_LIMIT)) * 100.0
        occ_pct = min(100.0, max(0.0, occ_pct))

        # 2. Queue delay (sojourn time) hisoblash
        if ldelay_ms is not None:
            delay_ms = ldelay_ms
        else:
            eff_rate = rate_bps if (rate_bps and rate_bps > 0) else QueueTracker.DEFAULT_ASSUMED_BW_BPS
            delay_ms = (backlog_bytes * 8.0 / eff_rate) * 1000.0

        # 3. Bloat score (0 - 100)
        # Latency (0..80ms -> 0..65 ball)
        delay_component = min(65.0, (delay_ms / 80.0) * 65.0)
        # Occupancy (0..100% -> 0..25 ball)
        occ_component = min(25.0, (occ_pct / 100.0) * 25.0)
        # Qo'shimcha faol tashlashlar/dropping holati (0..10 ball)
        active_penalty = 10.0 if (dropping or early_drops > 0) else 0.0
        bloat_score = min(100.0, max(0.0, delay_component + occ_component + active_penalty))

        # 4. Severity aniqlash
        if bloat_score >= 85.0 or delay_ms >= 100.0:
            severity = "critical"
        elif bloat_score >= 65.0 or delay_ms >= 50.0:
            severity = "high"
        elif bloat_score >= 45.0 or delay_ms >= 20.0:
            severity = "warning"
        elif bloat_score >= 20.0 or delay_ms >= 5.0:
            severity = "low"
        else:
            severity = "normal"

        # 5. Aniqlanganlik (Detected)
        detected = (delay_ms >= 15.0 or occ_pct >= 50.0 or severity in ("warning", "high", "critical"))

        # 6. Tavsiya shakllantirish
        clean_kind = kind.lower()
        if detected:
            if clean_kind in ("fifo", "pfifo", "bfifo", "pfifo_fast", "pfifo_head_drop"):
                recommendation = (
                    f"FIFO navbati ({dev}) da yuqori Bufferbloat aniqlandi ({delay_ms:.1f}ms kechikish). "
                    "FIFO rejimida AQM yo'qligi sababli navbat to'lib qolmoqda. "
                    "fq_codel yoki CoDel faol navbat boshqaruviga (AQM) o'tish tavsiya etiladi."
                )
            elif clean_kind == "red":
                recommendation = (
                    f"RED navbati ({dev}) bo'sag'alari oshib ketdi. "
                    "Min/Max limitlarini BDP ga moslab sozlang yoki ECN belgilashni faollashtiring."
                )
            elif clean_kind == "tbf":
                recommendation = (
                    f"TBF token cheklovchisi ({dev}) navbatida tiqilinch yuzaga keldi. "
                    "TBF latency parametrini pasaytiring yoki o'tkazuvchanlik chegarasini (rate) kengaytiring."
                )
            elif clean_kind in ("codel", "fq_codel"):
                if dropping:
                    recommendation = (
                        f"{clean_kind.upper()} ({dev}) da AQM algoritmi kechikishni jilovlash uchun "
                        "paketlarni tashlamoqda/ECN bilan belgilamoqda. Bu kutilgan normal reaksiyadir."
                    )
                else:
                    recommendation = f"{clean_kind.upper()} ({dev}) kechikishni me'yorida ushlab turmoqda."
            elif clean_kind == "cake":
                if dropping or early_drops > 0:
                    recommendation = (
                        f"CAKE ({dev}) da Cobalt AQM algoritmi kechikishni jilovlash uchun "
                        f"paketlarni nazorat ostida tashlamoqda/ECN bilan belgilamoqda ({delay_ms:.1f}ms kechikish). Bu kutilgan normal reaksiyadir."
                    )
                else:
                    recommendation = f"CAKE ({dev}) minimal kechikish ({delay_ms:.1f}ms) va adolatli taqsimot (Fair Queueing) bilan optimal ishlamoqda."
            else:
                recommendation = f"Interfeys {dev} navbatida yuqori kechikish ({delay_ms:.1f}ms). Parametrlarni optimallashtiring."
        else:
            if clean_kind in ("codel", "fq_codel"):
                recommendation = f"{clean_kind.upper()} ({dev}) minimal kechikish bilan optimal ishlamoqda."
            elif clean_kind == "cake":
                if dropping or early_drops > 0:
                    recommendation = (
                        f"CAKE ({dev}) da Cobalt AQM faol boshqaruvda ({early_drops} unforced drops, "
                        f"{delay_ms:.1f}ms kechikish) va adolatli taqsimot (Fair Queueing) bilan optimal ishlamoqda."
                    )
                else:
                    recommendation = f"CAKE ({dev}) minimal kechikish ({delay_ms:.1f}ms) va adolatli taqsimot (Fair Queueing) bilan optimal ishlamoqda."
            else:
                recommendation = f"Navbat holati me'yorida ({delay_ms:.1f}ms kechikish)."

        return occ_pct, delay_ms, detected, severity, bloat_score, recommendation

    def parse_tc_qdisc_output(
        self,
        output: str,
        dev: Optional[str] = None,
        node: str = "localhost",
        timestamp: Optional[float] = None,
    ) -> List[QueueStats]:
        """`tc -s qdisc show dev <intf>` yoki umumiy `tc -s qdisc show` chiqishini tahlil qiladi."""
        return self._parse_tc_blocks(output, is_class=False, override_dev=dev, node=node, timestamp=timestamp)

    def parse_tc_class_output(
        self,
        output: str,
        dev: Optional[str] = None,
        node: str = "localhost",
        timestamp: Optional[float] = None,
    ) -> List[QueueStats]:
        """`tc -s class show dev <intf>` chiqishini tahlil qiladi."""
        return self._parse_tc_blocks(output, is_class=True, override_dev=dev, node=node, timestamp=timestamp)

    def _parse_tc_blocks(
        self,
        text: str,
        is_class: bool = False,
        override_dev: Optional[str] = None,
        node: str = "localhost",
        timestamp: Optional[float] = None,
    ) -> List[QueueStats]:
        now = timestamp if timestamp is not None else time.time()
        blocks: List[List[str]] = []
        current: List[str] = []

        prefix = "class " if is_class else "qdisc "
        for line in text.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if line.startswith(prefix) or (not line.startswith(" ") and (line.startswith("qdisc ") or line.startswith("class "))):
                if current:
                    blocks.append(current)
                    current = []
            current.append(line)
        if current:
            blocks.append(current)

        results: List[QueueStats] = []
        for blk in blocks:
            qs = self._parse_single_block(blk, is_class=is_class, override_dev=override_dev, node=node, timestamp=now)
            if qs:
                results.append(qs)

        return results

    def _parse_single_block(
        self,
        block_lines: List[str],
        is_class: bool,
        override_dev: Optional[str],
        node: str,
        timestamp: float,
    ) -> Optional[QueueStats]:
        if not block_lines:
            return None

        header = block_lines[0].strip()
        tokens = header.split()
        if len(tokens) < 3:
            return None

        kind = tokens[1]
        handle = tokens[2]

        parsed_dev = override_dev
        parent = "root" if "root" in tokens else "0:"

        for i, t in enumerate(tokens):
            if t == "dev" and i + 1 < len(tokens):
                parsed_dev = tokens[i + 1]
            elif t == "parent" and i + 1 < len(tokens):
                parent = tokens[i + 1]

        target_dev = override_dev or parsed_dev or "unknown"

        params: Dict[str, Any] = {}

        # Limit
        limit_val: Optional[int] = None
        limit_unit: Optional[str] = None
        m_limit = re.search(r"\blimit\s+([\d.]+)([a-zA-Z]*)", header)
        if m_limit:
            lim_num = float(m_limit.group(1))
            raw_unit = m_limit.group(2).lower()
            if raw_unit in ("p", "pkt", "pkts", ""):
                limit_unit = "p"
                limit_val = int(lim_num)
            else:
                limit_unit = "b"
                limit_val = parse_size_bytes(m_limit.group(1) + raw_unit)
            params["limit"] = limit_val
            params["limit_unit"] = limit_unit

        # CoDel / fq_codel parametrlari
        m_target = re.search(r"\btarget\s+([\d.]+[a-zA-Z]*)", header)
        target_ms = parse_time_ms(m_target.group(1)) if m_target else None
        if target_ms is not None:
            params["target_ms"] = target_ms

        m_interval = re.search(r"\binterval\s+([\d.]+[a-zA-Z]*)", header)
        interval_ms = parse_time_ms(m_interval.group(1)) if m_interval else None
        if interval_ms is not None:
            params["interval_ms"] = interval_ms

        m_flows = re.search(r"\bflows\s+(\d+)", header)
        if m_flows:
            params["flows"] = int(m_flows.group(1))

        m_quantum = re.search(r"\bquantum\s+(\d+)", header)
        if m_quantum:
            params["quantum"] = int(m_quantum.group(1))

        m_mem_lim = re.search(r"\bmemory_limit\s+([\d.]+[a-zA-Z]*)", header)
        if m_mem_lim:
            params["memory_limit"] = m_mem_lim.group(1)

        # RED parametrlari
        m_min = re.search(r"\bmin\s+([\d.]+[a-zA-Z]*)", header)
        if m_min:
            params["min_bytes"] = parse_size_bytes(m_min.group(1))
        m_max = re.search(r"\bmax\s+([\d.]+[a-zA-Z]*)", header)
        if m_max:
            params["max_bytes"] = parse_size_bytes(m_max.group(1))
        m_avpkt = re.search(r"\bavpkt\s+(\d+)", header)
        if m_avpkt:
            params["avpkt"] = int(m_avpkt.group(1))
        m_burst = re.search(r"\bburst\s+([\d.]+[a-zA-Z]*)", header)
        if m_burst:
            params["burst"] = m_burst.group(1)
        m_prob = re.search(r"\bprobability\s+([\d.]+)", header)
        if m_prob:
            params["probability"] = float(m_prob.group(1))

        # TBF / CAKE / Rate parametrlari
        m_rate = re.search(r"\b(?:bandwidth|rate)\s+([\d.]+[a-zA-Z/]+)", header)
        rate_bps = parse_rate_bps(m_rate.group(1)) if m_rate else None
        if rate_bps is not None:
            params["rate_bps"] = rate_bps

        # CAKE rtt va boshqaruv parametrlari
        m_cake_rtt = re.search(r"\brtt\s+([\d.]+[a-zA-Z]*)", header)
        if m_cake_rtt:
            cake_rtt_ms = parse_time_ms(m_cake_rtt.group(1))
            params["target_rtt_ms"] = cake_rtt_ms
            if target_ms is None and kind.lower() == "cake":
                target_ms = cake_rtt_ms

        for cake_flag in (
            "diffserv3", "diffserv4", "besteffort", "triple-isolate",
            "dual-srchost", "dual-dsthost", "hostpair", "nat", "nonat",
            "ack-filter", "no-ack-filter", "split-gso", "no-split-gso", "raw"
        ):
            if cake_flag in tokens:
                params[cake_flag] = True

        m_ceil = re.search(r"\bceil\s+([\d.]+[a-zA-Z/]+)", header)
        if m_ceil:
            params["ceil_bps"] = parse_rate_bps(m_ceil.group(1))

        m_lat = re.search(r"\b(?:lat|latency)\s+([\d.]+[a-zA-Z]*)", header)
        if m_lat:
            params["latency_ms"] = parse_time_ms(m_lat.group(1))

        # FIFO priomap / bands
        m_bands = re.search(r"\bbands\s+(\d+)", header)
        if m_bands:
            params["bands"] = int(m_bands.group(1))
        m_priomap = re.search(r"\bpriomap\s+([0-9 ]+)", header)
        if m_priomap:
            params["priomap"] = [int(x) for x in m_priomap.group(1).split()]

        # ECN flag
        ecn_enabled = "ecn" in tokens
        if ecn_enabled:
            params["ecn"] = True

        # Qolgan statistika qatorlarini tahlil qilish
        bytes_sent = 0
        packets_sent = 0
        dropped = 0
        overlimits = 0
        requeues = 0
        backlog_bytes = 0
        backlog_packets = 0
        qlen = 0
        ldelay_ms: Optional[float] = None
        dropping = False
        ecn_marks = 0
        early_drops = 0
        extra_stats: Dict[str, Any] = {}

        for line in block_lines[1:]:
            l_str = line.strip()

            # Sent satri
            m_sent = re.search(
                r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt\s*\(dropped\s+(\d+),\s*overlimits\s+(\d+)(?:,\s*requeues\s+(\d+))?",
                l_str,
            )
            if m_sent:
                bytes_sent = int(m_sent.group(1))
                packets_sent = int(m_sent.group(2))
                dropped = int(m_sent.group(3))
                overlimits = int(m_sent.group(4))
                if m_sent.group(5):
                    requeues = int(m_sent.group(5))

            # Backlog satri
            m_backlog = re.search(
                r"backlog\s+(\d+)[bB]\s+(\d+)[pP](?:\s+requeues\s+(\d+))?",
                l_str,
            )
            if m_backlog:
                backlog_bytes = int(m_backlog.group(1))
                backlog_packets = int(m_backlog.group(2))
                qlen = backlog_packets
                if m_backlog.group(3) and not requeues:
                    requeues = int(m_backlog.group(3))

            # RED statistikasi
            m_red = re.search(
                r"marked\s+(\d+)\s+early\s+(\d+)\s+pdrop\s+(\d+)\s+other\s+(\d+)",
                l_str,
            )
            if m_red:
                ecn_marks = int(m_red.group(1))
                early_drops = int(m_red.group(2))
                extra_stats["marked"] = ecn_marks
                extra_stats["early"] = early_drops
                extra_stats["pdrop"] = int(m_red.group(3))
                extra_stats["other"] = int(m_red.group(4))

            # CoDel / fq_codel statistikasi
            m_ldelay = re.search(r"ldelay\s+([\d.]+[a-zA-Z]*)", l_str)
            if m_ldelay:
                ldelay_ms = parse_time_ms(m_ldelay.group(1))
                extra_stats["ldelay_ms"] = ldelay_ms

            if "dropping" in l_str.split():
                dropping = True
                extra_stats["dropping"] = True

            m_count = re.search(r"\bcount\s+(\d+)", l_str)
            if m_count:
                extra_stats["count"] = int(m_count.group(1))

            m_lastcount = re.search(r"\blastcount\s+(\d+)", l_str)
            if m_lastcount:
                extra_stats["lastcount"] = int(m_lastcount.group(1))

            m_ecn_m = re.search(r"\becn_mark\s+(\d+)", l_str)
            if m_ecn_m:
                ecn_marks += int(m_ecn_m.group(1))
                extra_stats["ecn_mark"] = ecn_marks

            m_ce_m = re.search(r"\bce_mark\s+(\d+)", l_str)
            if m_ce_m:
                extra_stats["ce_mark"] = int(m_ce_m.group(1))

            m_dov = re.search(r"\bdrop_overlimit\s+(\d+)", l_str)
            if m_dov:
                extra_stats["drop_overlimit"] = int(m_dov.group(1))

            m_nfc = re.search(r"\bnew_flow_count\s+(\d+)", l_str)
            if m_nfc:
                extra_stats["new_flow_count"] = int(m_nfc.group(1))

            m_maxpkt = re.search(r"\bmaxpacket\s+(\d+)", l_str)
            if m_maxpkt:
                extra_stats["maxpacket"] = int(m_maxpkt.group(1))

            m_mem_used = re.search(r"\bmemory_used\s+(\d+)", l_str)
            if m_mem_used:
                extra_stats["memory_used"] = int(m_mem_used.group(1))

            m_new_flows = re.search(r"\bnew_flows_len\s+(\d+)", l_str)
            if m_new_flows:
                extra_stats["new_flows_len"] = int(m_new_flows.group(1))

            m_old_flows = re.search(r"\bold_flows_len\s+(\d+)", l_str)
            if m_old_flows:
                extra_stats["old_flows_len"] = int(m_old_flows.group(1))

            # HTB class statistikasi
            m_lended = re.search(r"lended:\s*(\d+)\s+borrowed:\s*(\d+)\s+giants:\s*(\d+)", l_str)
            if m_lended:
                extra_stats["lended"] = int(m_lended.group(1))
                extra_stats["borrowed"] = int(m_lended.group(2))
                extra_stats["giants"] = int(m_lended.group(3))

            m_tokens = re.search(r"tokens:\s*(-?\d+)\s+ctokens:\s*(-?\d+)", l_str)
            if m_tokens:
                extra_stats["tokens"] = int(m_tokens.group(1))
                extra_stats["ctokens"] = int(m_tokens.group(2))

            # CAKE drop va kechikish statistikasi
            # unforced drops (AQM drop tracking)
            m_unforced = re.findall(r"(?:min\s+|max\s+)?unforced dropped:?\s*(\d+)", l_str)
            if m_unforced:
                u_sum = sum(int(x) for x in m_unforced)
                early_drops += u_sum
                extra_stats["cake_unforced_drops"] = extra_stats.get("cake_unforced_drops", 0) + u_sum

            # forced drops (buffer limit drop tracking)
            m_forced = re.findall(r"(?:min\s+|max\s+)?(?<!un)forced dropped:?\s*(\d+)", l_str)
            if m_forced:
                f_sum = sum(int(x) for x in m_forced)
                extra_stats["cake_forced_drops"] = extra_stats.get("cake_forced_drops", 0) + f_sum

            # tail drops
            m_tail = re.findall(r"(?:min\s+|max\s+)?tail dropped:?\s*(\d+)", l_str)
            if m_tail:
                t_sum = sum(int(x) for x in m_tail)
                extra_stats["cake_tail_drops"] = extra_stats.get("cake_tail_drops", 0) + t_sum

            # ack drops
            m_ack_d = re.findall(r"(?:min\s+|max\s+)?ack dropped:?\s*(\d+)", l_str)
            if m_ack_d:
                a_sum = sum(int(x) for x in m_ack_d)
                extra_stats["cake_ack_drops"] = extra_stats.get("cake_ack_drops", 0) + a_sum

            # ecn marks
            m_cake_ecn = re.findall(r"(?:min\s+|max\s+)?ecn marked:?\s*(\d+)", l_str)
            if m_cake_ecn:
                e_sum = sum(int(x) for x in m_cake_ecn)
                ecn_marks += e_sum
                extra_stats["cake_ecn_marks"] = extra_stats.get("cake_ecn_marks", 0) + e_sum

            # av_delay / peak_delay
            m_av = re.search(r"av_delay\s+([\d.]+[a-zA-Z]*)", l_str)
            if m_av:
                av_d = parse_time_ms(m_av.group(1))
                extra_stats["av_delay_ms"] = av_d
                if ldelay_ms is None:
                    ldelay_ms = av_d

            m_peak = re.search(r"peak_delay\s+([\d.]+[a-zA-Z]*)", l_str)
            if m_peak:
                pk_d = parse_time_ms(m_peak.group(1))
                extra_stats["peak_delay_ms"] = pk_d
                if ldelay_ms is None:
                    ldelay_ms = pk_d

            # capacity estimate
            m_cap = re.search(r"capacity estimate:\s*([\d.]+[a-zA-Z/]+)", l_str)
            if m_cap:
                cap_rate = parse_rate_bps(m_cap.group(1))
                extra_stats["capacity_estimate_bps"] = cap_rate
                if rate_bps is None:
                    rate_bps = cap_rate

            # memory used
            m_cake_mem = re.search(r"memory used:\s*([\d.]+[a-zA-Z]*)\s+of\s+([\d.]+[a-zA-Z]*)", l_str)
            if m_cake_mem:
                extra_stats["cake_memory_used"] = m_cake_mem.group(1)
                extra_stats["cake_memory_limit"] = m_cake_mem.group(2)

        # Agar CAKE unforced drops yoki faol AQM tashlash bo'lsa dropping = True
        if extra_stats.get("cake_unforced_drops", 0) > 0:
            dropping = True
            extra_stats["dropping"] = True

        # Bufferbloat metrikalarini hisoblash
        occ_pct, est_delay, bloat_det, severity, score, rec = self._evaluate_bufferbloat(
            dev=target_dev,
            kind=kind,
            backlog_bytes=backlog_bytes,
            backlog_packets=backlog_packets,
            limit=limit_val,
            limit_unit=limit_unit,
            rate_bps=rate_bps,
            ldelay_ms=ldelay_ms,
            dropped=dropped,
            early_drops=early_drops,
            ecn_marks=ecn_marks,
            dropping=dropping,
        )

        return QueueStats(
            dev=target_dev,
            node=node,
            kind=kind,
            handle=handle,
            parent=parent,
            bytes_sent=bytes_sent,
            packets_sent=packets_sent,
            dropped=dropped,
            overlimits=overlimits,
            requeues=requeues,
            backlog_bytes=backlog_bytes,
            backlog_packets=backlog_packets,
            qlen=qlen,
            limit=limit_val,
            limit_unit=limit_unit,
            occupancy_pct=occ_pct,
            estimated_queue_delay_ms=est_delay,
            bufferbloat_detected=bloat_det,
            bufferbloat_severity=severity,
            bloat_score=score,
            rate_bps=rate_bps,
            target_ms=target_ms,
            interval_ms=interval_ms,
            ldelay_ms=ldelay_ms,
            ecn=ecn_enabled,
            ecn_marks=ecn_marks,
            early_drops=early_drops,
            dropping=dropping,
            params=params,
            extra_stats=extra_stats,
            timestamp=timestamp,
            is_class=is_class,
        )

    def assess_bufferbloat(self, stats: QueueStats) -> BufferbloatAssessment:
        """Berilgan QueueStats bo'yicha to'liq Bufferbloat baholashini yaratadi."""
        occ_pct, est_delay, bloat_det, severity, score, rec = self._evaluate_bufferbloat(
            dev=stats.dev,
            kind=stats.kind,
            backlog_bytes=stats.backlog_bytes,
            backlog_packets=stats.backlog_packets,
            limit=stats.limit,
            limit_unit=stats.limit_unit,
            rate_bps=stats.rate_bps,
            ldelay_ms=stats.ldelay_ms,
            dropped=stats.dropped,
            early_drops=stats.early_drops,
            ecn_marks=stats.ecn_marks,
            dropping=stats.dropping,
        )
        return BufferbloatAssessment(
            dev=stats.dev,
            qdisc_kind=stats.kind,
            detected=bloat_det,
            severity=severity,
            score=score,
            queue_delay_ms=est_delay,
            occupancy_pct=occ_pct,
            backlog_bytes=stats.backlog_bytes,
            backlog_packets=stats.backlog_packets,
            dropped_packets=stats.dropped,
            early_drops=stats.early_drops,
            ecn_marks=stats.ecn_marks,
            recommendation=rec,
        )

    def ingest_sample(self, sample: QueueStats) -> None:
        """Yangi namunalarni xotiraga (vaqt ketma-ketligi va so'nggi holat) qo'shadi."""
        qid = sample.queue_id
        if qid not in self._history:
            self._history[qid] = deque(maxlen=self.max_history_len)
        self._history[qid].append(sample)
        self._latest[qid] = sample

    def collect_interface_queues(
        self, dev: str, node_obj: Any = None, node_name: str = "localhost"
    ) -> List[QueueStats]:
        """Bitta interfeys uchun `tc -s qdisc` va `tc -s class` buyruqlarini bajarib, natijalarni yig'adi."""
        results: List[QueueStats] = []
        try:
            cmd_qdisc = f"tc -s qdisc show dev {dev}"
            cmd_class = f"tc -s class show dev {dev}"
            if node_obj is not None and hasattr(node_obj, "cmd"):
                out_qdisc = node_obj.cmd(cmd_qdisc)
                out_class = node_obj.cmd(cmd_class)
            else:
                r1 = subprocess.run(cmd_qdisc.split(), capture_output=True, text=True, timeout=2.0)
                out_qdisc = r1.stdout
                r2 = subprocess.run(cmd_class.split(), capture_output=True, text=True, timeout=2.0)
                out_class = r2.stdout

            q_stats = self.parse_tc_qdisc_output(out_qdisc, dev=dev, node=node_name)
            c_stats = self.parse_tc_class_output(out_class, dev=dev, node=node_name)

            for s in q_stats:
                self.ingest_sample(s)
                results.append(s)
            for s in c_stats:
                self.ingest_sample(s)
                results.append(s)
        except Exception:
            pass
        return results

    def collect_live_node(self, node_name: str, node_obj: Any = None) -> List[QueueStats]:
        """Mininet tugunidagi barcha faol interfeyslar navbat telemetriyasini yig'adi."""
        results: List[QueueStats] = []
        try:
            intf_names: List[str] = []
            if node_obj is not None and hasattr(node_obj, "intfNames"):
                intf_names = list(node_obj.intfNames())
            elif node_obj is not None and hasattr(node_obj, "intfs"):
                intf_names = [intf.name for intf in node_obj.intfs.values() if hasattr(intf, "name")]

            if not intf_names:
                if node_obj is not None and hasattr(node_obj, "cmd"):
                    ip_out = node_obj.cmd("ip -o link show")
                else:
                    r = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True, timeout=2.0)
                    ip_out = r.stdout
                for line in ip_out.splitlines():
                    m = re.match(r"^\d+:\s+([^:@]+)", line.strip())
                    if m:
                        name = m.group(1).strip()
                        if name != "lo":
                            intf_names.append(name)

            for dev in intf_names:
                if dev == "lo":
                    continue
                results.extend(self.collect_interface_queues(dev, node_obj=node_obj, node_name=node_name))
        except Exception:
            pass
        return results

    def collect_live_net(self, net: Any) -> List[QueueStats]:
        """Butun Mininet tarmog'i bo'ylab barcha host, switch va router interfeyslarini skan qiladi."""
        results: List[QueueStats] = []
        if net is None:
            return results

        all_nodes: List[Any] = []
        if hasattr(net, "hosts"):
            all_nodes.extend(getattr(net, "hosts", []))
        if hasattr(net, "switches"):
            all_nodes.extend(getattr(net, "switches", []))
        if hasattr(net, "values") and callable(net.values):
            for n in net.values():
                if n not in all_nodes:
                    all_nodes.append(n)

        for node in all_nodes:
            node_name = getattr(node, "name", str(node))
            results.extend(self.collect_live_node(node_name, node))

        return results

    def simulate_sample(
        self,
        dev: str = "eth0",
        node: str = "h1",
        kind: str = "pfifo",
        handle: str = "1:",
        backlog_packets: int = 0,
        backlog_bytes: int = 0,
        limit: int = 1000,
        limit_unit: str = "p",
        dropped: int = 0,
        overlimits: int = 0,
        ldelay_ms: Optional[float] = None,
        rate_bps: Optional[float] = None,
        ecn: bool = False,
        ecn_marks: int = 0,
        early_drops: int = 0,
        dropping: bool = False,
        timestamp: Optional[float] = None,
    ) -> QueueStats:
        """Kuzatuv paneli va testlar uchun sintetik qdisc namunasini yaratadi."""
        now = timestamp if timestamp is not None else time.time()
        occ_pct, est_delay, bloat_det, severity, score, rec = self._evaluate_bufferbloat(
            dev=dev,
            kind=kind,
            backlog_bytes=backlog_bytes,
            backlog_packets=backlog_packets,
            limit=limit,
            limit_unit=limit_unit,
            rate_bps=rate_bps,
            ldelay_ms=ldelay_ms,
            dropped=dropped,
            early_drops=early_drops,
            ecn_marks=ecn_marks,
            dropping=dropping,
        )
        sample = QueueStats(
            dev=dev,
            node=node,
            kind=kind,
            handle=handle,
            parent="root",
            bytes_sent=102400 + backlog_bytes * 5,
            packets_sent=800 + backlog_packets * 5,
            dropped=dropped,
            overlimits=overlimits,
            requeues=0,
            backlog_bytes=backlog_bytes,
            backlog_packets=backlog_packets,
            qlen=backlog_packets,
            limit=limit,
            limit_unit=limit_unit,
            occupancy_pct=occ_pct,
            estimated_queue_delay_ms=est_delay,
            bufferbloat_detected=bloat_det,
            bufferbloat_severity=severity,
            bloat_score=score,
            rate_bps=rate_bps,
            target_ms=5.0 if "codel" in kind else None,
            interval_ms=100.0 if "codel" in kind else None,
            ldelay_ms=ldelay_ms,
            ecn=ecn,
            ecn_marks=ecn_marks,
            early_drops=early_drops,
            dropping=dropping,
            params={"limit": limit, "limit_unit": limit_unit},
            extra_stats={"dropping": dropping, "early_drops": early_drops, "ecn_marks": ecn_marks},
            timestamp=now,
        )
        self.ingest_sample(sample)
        return sample

    def simulate_cake_sample(
        self,
        dev: str = "r1-eth3",
        node: str = "r1",
        bandwidth_bps: float = 20_000_000.0,
        unforced_drops: int = 4,
        forced_drops: int = 0,
        ecn_marks: int = 3,
        av_delay_ms: float = 3.2,
        timestamp: Optional[float] = None,
    ) -> QueueStats:
        """Kuzatuv paneli va testlar uchun CAKE (sch_cake) namunasini yaratadi."""
        now = timestamp if timestamp is not None else time.time()
        sample = self.simulate_sample(
            dev=dev,
            node=node,
            kind="cake",
            handle="8002:",
            backlog_packets=2,
            backlog_bytes=2920,
            limit=1024,
            limit_unit="p",
            dropped=unforced_drops + forced_drops,
            rate_bps=bandwidth_bps,
            ldelay_ms=av_delay_ms,
            ecn=True,
            ecn_marks=ecn_marks,
            early_drops=unforced_drops,
            dropping=(unforced_drops > 0),
            timestamp=now,
        )
        sample.extra_stats["cake_unforced_drops"] = unforced_drops
        sample.extra_stats["cake_forced_drops"] = forced_drops
        sample.extra_stats["av_delay_ms"] = av_delay_ms
        return sample

    def simulate_step(self, elapsed: float = 0.0) -> List[QueueStats]:
        """Demo rejimida turli xil qdisc turlarining (FIFO, fq_codel, RED, TBF) dinamikasini yaratadi."""
        now = time.time()
        phase = math.sin(elapsed * 0.4)  # To'lqin shaklidagi yuklama

        # 1. FIFO: Yuklama oshganda navbat to'lib, katta bufferbloat hosil qiladi
        fifo_backlog_p = int(max(0, 15 + phase * 65))
        fifo_backlog_b = fifo_backlog_p * 1460
        fifo_drops = int(max(0, (phase - 0.5) * 40))
        s_fifo = self.simulate_sample(
            dev="h1-eth0",
            node="h1",
            kind="pfifo",
            handle="1:",
            backlog_packets=fifo_backlog_p,
            backlog_bytes=fifo_backlog_b,
            limit=100,
            limit_unit="p",
            dropped=fifo_drops,
            rate_bps=10_000_000.0,
            timestamp=now,
        )

        # 2. fq_codel: Xuddi shu yuklamada ham navbat kechikishini 5ms atrofida saqlaydi
        codel_delay = max(1.2, 4.2 + phase * 1.5)
        codel_dropping = codel_delay > 5.0
        codel_marks = 8 if codel_dropping else 2
        s_fq = self.simulate_sample(
            dev="r1-eth1",
            node="r1",
            kind="fq_codel",
            handle="8001:",
            backlog_packets=max(1, int(3 + phase * 3)),
            backlog_bytes=max(1500, int((3 + phase * 3) * 1460)),
            limit=10240,
            limit_unit="p",
            dropped=2 if codel_dropping else 0,
            ldelay_ms=codel_delay,
            ecn=True,
            ecn_marks=codel_marks,
            dropping=codel_dropping,
            rate_bps=10_000_000.0,
            timestamp=now,
        )

        # 3. RED: O'rtacha navbat hajmi bo'yicha ehtimolli erta tashlashlar (early drops)
        red_backlog_b = int(max(5000, 25000 + phase * 20000))
        red_early = int(max(0, (phase - 0.2) * 25))
        s_red = self.simulate_sample(
            dev="r1-eth2",
            node="r1",
            kind="red",
            handle="10:",
            backlog_packets=int(red_backlog_b / 1460),
            backlog_bytes=red_backlog_b,
            limit=60000,
            limit_unit="b",
            dropped=red_early,
            early_drops=red_early,
            ecn=True,
            ecn_marks=red_early // 2,
            rate_bps=10_000_000.0,
            timestamp=now,
        )

        # 4. TBF: Token Bucket Filter bilan o'tkazuvchanlikni chegaralash
        tbf_overlimits = int(max(0, 100 + phase * 200))
        s_tbf = self.simulate_sample(
            dev="s1-eth1",
            node="s1",
            kind="tbf",
            handle="1:",
            backlog_packets=max(0, int(5 + phase * 10)),
            backlog_bytes=max(0, int((5 + phase * 10) * 1460)),
            limit=10000,
            limit_unit="b",
            overlimits=tbf_overlimits,
            rate_bps=5_000_000.0,
            timestamp=now,
        )

        return [s_fifo, s_fq, s_red, s_tbf]

    def get_latest_stats(self, dev: Optional[str] = None) -> List[Dict[str, Any]]:
        """Eng so'nggi navbat telemetriyasini ro'yxat ko'rinishida qaytaradi."""
        results: List[Dict[str, Any]] = []
        for qid, qs in self._latest.items():
            if dev is not None and qs.dev != dev and dev not in qid:
                continue
            results.append(qs.to_dict())
        return results

    def get_history(self, dev: str, handle: Optional[str] = None) -> List[Dict[str, Any]]:
        """Bitta interfeys (va ixtiyoriy handle) uchun tarixiy telemetriya oqimini qaytaradi."""
        matched: List[QueueStats] = []
        for qid, deq in self._history.items():
            for qs in deq:
                if qs.dev == dev or dev in qid:
                    if handle is None or qs.handle == handle:
                        matched.append(qs)
        matched.sort(key=lambda x: x.timestamp)
        return [m.to_dict() for m in matched]

    def get_bufferbloat_report(self) -> Dict[str, Any]:
        """Tizimdagi barcha interfeyslar bo'yicha to'liq Bufferbloat tahlili hisobotini shakllantiradi."""
        assessments: List[BufferbloatAssessment] = []
        for qs in self._latest.values():
            assessments.append(self.assess_bufferbloat(qs))

        total_monitored = len(assessments)
        bloated = [a for a in assessments if a.detected]
        critical = [a for a in assessments if a.severity == "critical"]
        warning = [a for a in assessments if a.severity in ("warning", "high")]

        worst = max(assessments, key=lambda x: x.score) if assessments else None
        max_delay = max([a.queue_delay_ms for a in assessments], default=0.0)

        recs = [a.recommendation for a in assessments if a.recommendation]

        return {
            "summary": {
                "monitored_interfaces": total_monitored,
                "bloated_count": len(bloated),
                "critical_count": len(critical),
                "warning_count": len(warning),
                "highest_delay_ms": round(max_delay, 2),
                "worst_interface": worst.dev if worst else None,
                "overall_status": "critical" if critical else ("warning" if warning else "nominal"),
            },
            "assessments": [a.to_dict() for a in assessments],
            "recommendations": list(dict.fromkeys(recs)),  # unikal tavsiyalar
        }

    def get_summary(self) -> Dict[str, Any]:
        """Umumiy qisqacha statistika."""
        active = list(self._latest.values())
        if not active:
            return {
                "total_queues": 0,
                "total_backlog_bytes": 0,
                "total_backlog_packets": 0,
                "total_drops": 0,
                "total_overlimits": 0,
                "bufferbloat_detected_count": 0,
                "max_queue_delay_ms": 0.0,
                "timestamp": time.time(),
            }

        total_bytes = sum(q.backlog_bytes for q in active)
        total_pkts = sum(q.backlog_packets for q in active)
        total_drops = sum(q.dropped for q in active)
        total_overlimits = sum(q.overlimits for q in active)
        bloat_cnt = sum(1 for q in active if q.bufferbloat_detected)
        max_delay = max(q.estimated_queue_delay_ms for q in active)

        return {
            "total_queues": len(active),
            "total_backlog_bytes": total_bytes,
            "total_backlog_packets": total_pkts,
            "total_drops": total_drops,
            "total_overlimits": total_overlimits,
            "bufferbloat_detected_count": bloat_cnt,
            "max_queue_delay_ms": round(max_delay, 2),
            "timestamp": time.time(),
        }

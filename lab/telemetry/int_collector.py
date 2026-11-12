"""P4-16 In-Band Network Telemetry (INT v2.1) Engine & Hop-by-Hop Telemetry Collector:
- Hop-by-hop metadata collection (Switch ID, Ingress/Egress Ports, Hop Latency, Queue Occupancy)
- Microburst detection and bufferbloat pinpointing
- Binary INT Header Encoder & Decoder (P4 INT spec compliant)
- Realistic P4 Pipeline Simulation & Telemetry Collector with Hotspot/Bottleneck Analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct
import time
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class INTHopMetadata:
    """Bitta P4 switch (hop) tomonidan kiritilgan telemetriya ma'lumotlari."""

    switch_id: str
    ingress_port: int
    egress_port: int
    hop_latency_us: float
    queue_id: int = 0
    queue_occupancy_pkts: int = 0
    queue_occupancy_bytes: int = 0
    ingress_tstamp_ns: int = 0
    egress_tstamp_ns: int = 0
    tx_utilization_pct: float = 0.0
    drop_flag: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "switch_id": self.switch_id,
            "ingress_port": self.ingress_port,
            "egress_port": self.egress_port,
            "hop_latency_us": round(self.hop_latency_us, 2),
            "queue_id": self.queue_id,
            "queue_occupancy_pkts": self.queue_occupancy_pkts,
            "queue_occupancy_bytes": self.queue_occupancy_bytes,
            "ingress_tstamp_ns": self.ingress_tstamp_ns,
            "egress_tstamp_ns": self.egress_tstamp_ns,
            "tx_utilization_pct": round(self.tx_utilization_pct, 1),
            "drop_flag": self.drop_flag,
        }


@dataclass
class INTHeader:
    """P4 INT v2.1 Sarlavhasi (Shim + INT Metadata Header)."""

    version: int = 2
    d_flag: bool = False  # Discard flag
    q_flag: bool = False  # Queue congestion flag
    f_flag: bool = False  # Flow direction
    max_hop_count: int = 8
    instruction_mask: int = 0xAAAA  # Standart telemetriya instruksiyalari
    hops: List[INTHopMetadata] = field(default_factory=list)

    # Bayt formati (Binary Wire Protocol):
    # Shim Header (4B): [Ver(4b) | D(1b) | Q(1b) | F(1b) | Rsvd(9b) | HopML(8b) | RemainingHopCnt(8b)]
    # INT Header  (4B): [InsMask(16b) | TotalHops(16b)]
    # Per-Hop Metadata (16B): [SwitchID(2B) | InPort(1B) | OutPort(1B) | LatencyUS(4B) | QOccPkts(2B) | QOccBytes(4B) | Flags(2B)]

    def encode(self) -> bytes:
        """P4 INT sarlavhasini baytlarga o'raydi (Wire format)."""
        hop_ml = 4  # 16 bayt = 4 words
        remaining = max(0, self.max_hop_count - len(self.hops))
        flags_field = (
            ((self.version & 0xF) << 12)
            | ((1 if self.d_flag else 0) << 11)
            | ((1 if self.q_flag else 0) << 10)
            | ((1 if self.f_flag else 0) << 9)
        )
        shim = struct.pack("!HBB", flags_field, hop_ml, remaining)
        int_hdr = struct.pack("!HH", self.instruction_mask, len(self.hops))

        payload = bytearray(shim + int_hdr)
        for h in self.hops:
            # Switch nomidan ID raqamini ajratish (masalan "s1" -> 1)
            try:
                sid_num = int("".join(filter(str.isdigit, str(h.switch_id))) or "0")
            except Exception:
                sid_num = 0

            lat_us_int = int(h.hop_latency_us * 10)  # 0.1 us birligida
            q_flags = 1 if h.drop_flag else 0
            hop_bytes = struct.pack(
                "!HBB I H I H",
                sid_num & 0xFFFF,
                h.ingress_port & 0xFF,
                h.egress_port & 0xFF,
                lat_us_int & 0xFFFFFFFF,
                h.queue_occupancy_pkts & 0xFFFF,
                h.queue_occupancy_bytes & 0xFFFFFFFF,
                q_flags & 0xFFFF,
            )
            payload.extend(hop_bytes)

        return bytes(payload)

    @classmethod
    def decode(cls, raw: bytes) -> "INTHeader":
        """Binary baytlardan INT sarlavhasini ochadi."""
        if len(raw) < 8:
            raise ValueError(f"INT sarlavhasi kamida 8 bayt bo'lishi kerak, berildi: {len(raw)}")

        flags_field, hop_ml, remaining = struct.unpack("!HBB", raw[:4])
        ins_mask, total_hops = struct.unpack("!HH", raw[4:8])

        version = (flags_field >> 12) & 0xF
        d_flag = bool((flags_field >> 11) & 0x1)
        q_flag = bool((flags_field >> 10) & 0x1)
        f_flag = bool((flags_field >> 9) & 0x1)

        offset = 8
        hops: List[INTHopMetadata] = []
        hop_size = 16

        for _ in range(total_hops):
            if offset + hop_size > len(raw):
                break
            sid_num, in_p, out_p, lat_us_int, q_pkts, q_bytes, q_flags = struct.unpack(
                "!HBB I H I H", raw[offset : offset + hop_size]
            )
            offset += hop_size

            hops.append(
                INTHopMetadata(
                    switch_id=f"s{sid_num}",
                    ingress_port=in_p,
                    egress_port=out_p,
                    hop_latency_us=round(lat_us_int / 10.0, 2),
                    queue_occupancy_pkts=q_pkts,
                    queue_occupancy_bytes=q_bytes,
                    drop_flag=bool(q_flags & 0x1),
                )
            )

        return cls(
            version=version,
            d_flag=d_flag,
            q_flag=q_flag,
            f_flag=f_flag,
            max_hop_count=len(hops) + remaining,
            instruction_mask=ins_mask,
            hops=hops,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "d_flag": self.d_flag,
            "q_flag": self.q_flag,
            "f_flag": self.f_flag,
            "max_hop_count": self.max_hop_count,
            "instruction_mask": hex(self.instruction_mask),
            "hops_count": len(self.hops),
            "hops": [h.to_dict() for h in self.hops],
        }


@dataclass
class INTReport:
    """Tugallangan INT trassa hisoboti."""

    report_id: str
    flow_id: str
    timestamp: float
    total_hops: int
    path: List[str]
    end_to_end_latency_us: float
    max_queue_depth_pkts: int
    bottleneck_switch: Optional[str]
    microburst_detected: bool
    hops: List[INTHopMetadata]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "flow_id": self.flow_id,
            "timestamp": self.timestamp,
            "total_hops": self.total_hops,
            "path": self.path,
            "end_to_end_latency_us": round(self.end_to_end_latency_us, 2),
            "max_queue_depth_pkts": self.max_queue_depth_pkts,
            "bottleneck_switch": self.bottleneck_switch,
            "microburst_detected": self.microburst_detected,
            "hops": [h.to_dict() for h in self.hops],
        }


class INTTelemetryCollector:
    """Barcha INT hisobotlarini yig'uvchi va tarmoq bo'g'imlarini (bottleneck) tahlil qiluvchi servis."""

    def __init__(self, max_reports: int = 1000):
        self.max_reports = max_reports
        self._reports: List[INTReport] = []

    def add_report(self, report: INTReport) -> None:
        self._reports.append(report)
        if len(self._reports) > self.max_reports:
            self._reports.pop(0)

    def get_reports(
        self, limit: int = 50, flow_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        filtered = self._reports
        if flow_id:
            filtered = [r for r in filtered if r.flow_id == flow_id]
        return [r.to_dict() for r in reversed(filtered[-limit:])]

    def get_bottleneck_analysis(self) -> Dict[str, Any]:
        """Switchlar bo'yicha kechikish va navbat to'lishini statistik hisoblab beradi."""
        if not self._reports:
            return {"total_reports": 0, "switches": {}, "ranked_bottlenecks": []}

        switch_data: Dict[str, Dict[str, List[float]]] = {}
        switch_bursts: Dict[str, int] = {}

        for rep in self._reports:
            for h in rep.hops:
                sid = h.switch_id
                if sid not in switch_data:
                    switch_data[sid] = {"latencies": [], "queues": []}
                    switch_bursts[sid] = 0
                switch_data[sid]["latencies"].append(h.hop_latency_us)
                switch_data[sid]["queues"].append(float(h.queue_occupancy_pkts))
                if h.queue_occupancy_pkts > 40 or h.hop_latency_us > 80.0:
                    switch_bursts[sid] += 1

        summary_switches: Dict[str, Any] = {}
        ranked_list = []

        for sid, d in switch_data.items():
            lats = sorted(d["latencies"])
            queues = sorted(d["queues"])
            n = len(lats)
            avg_lat = sum(lats) / n if n else 0.0
            p95_idx = int(0.95 * n) if n else 0
            p95_lat = lats[min(p95_idx, n - 1)] if n else 0.0
            max_lat = lats[-1] if n else 0.0

            avg_q = sum(queues) / n if n else 0.0
            p95_q = queues[min(p95_idx, n - 1)] if n else 0.0
            max_q = int(queues[-1]) if n else 0

            burst_cnt = switch_bursts.get(sid, 0)
            is_critical = max_q >= 50 or p95_lat > 100.0 or burst_cnt > (n * 0.2)

            res = {
                "switch_id": sid,
                "sample_count": n,
                "avg_latency_us": round(avg_lat, 2),
                "p95_latency_us": round(p95_lat, 2),
                "max_latency_us": round(max_lat, 2),
                "avg_queue_depth": round(avg_q, 1),
                "p95_queue_depth": round(p95_q, 1),
                "max_queue_depth": max_q,
                "microburst_count": burst_cnt,
                "is_critical_bottleneck": is_critical,
            }
            summary_switches[sid] = res
            ranked_list.append(res)

        # Eng katta navbat va kechikishga ega bo'lgan switchlar birinchi chiqadi
        ranked_list.sort(key=lambda x: (x["max_queue_depth"], x["p95_latency_us"]), reverse=True)

        return {
            "total_reports": len(self._reports),
            "switches": summary_switches,
            "ranked_bottlenecks": ranked_list,
        }

    def clear(self) -> None:
        self._reports.clear()


class INTSimulator:
    """P4 Pipeline bo'ylab INT paket harakatini modellashtiruvchi dvigatel."""

    def __init__(self, collector: Optional[INTTelemetryCollector] = None):
        self.collector = collector

    def simulate_path(
        self,
        path: List[str],
        flow_id: str = "flow-tcp-1",
        base_hop_latency_us: float = 12.0,
        burst_switch: Optional[str] = None,
        burst_size_pkts: int = 0,
        traffic_load_pct: float = 30.0,
    ) -> Tuple[INTHeader, INTReport]:
        """Berilgan switch yo'nalishi bo'ylab INT paketini yuborish va har bir hopda P4 metrikalarini yig'ish."""
        if not path:
            path = ["s1", "s2"]

        int_header = INTHeader(max_hop_count=len(path) + 4)
        hops: List[INTHopMetadata] = []
        total_lat = 0.0
        max_q = 0
        bottleneck = None
        has_microburst = False

        now_ns = int(time.time() * 1_000_000_000)

        for i, sw in enumerate(path):
            in_port = 1 if i == 0 else 2
            out_port = 2 if i < len(path) - 1 else 1

            # Oddiy fon navbati (traffic load bo'yicha)
            base_q = int(traffic_load_pct * 0.2)
            # Mikro-portlash bormi?
            is_burst = (sw == burst_switch) and (burst_size_pkts > 0)
            if is_burst:
                q_occupancy = base_q + burst_size_pkts
                has_microburst = True
                int_header.q_flag = True
            else:
                q_occupancy = max(1, base_q)

            # Navbat kechikishi: har 10 ta paket navbatda ~15 us kechikish beradi
            queuing_delay_us = (q_occupancy * 1.5)
            # Switch P4 parsing & match-action quvur kechikishi (~8-15 us)
            pipeline_lat_us = base_hop_latency_us + (i * 0.5)
            hop_lat_us = pipeline_lat_us + queuing_delay_us

            if q_occupancy > max_q:
                max_q = q_occupancy
                bottleneck = sw

            total_lat += hop_lat_us
            ingress_t = now_ns + int(total_lat * 1000)
            egress_t = ingress_t + int(hop_lat_us * 1000)

            tx_util = min(100.0, traffic_load_pct + (40.0 if is_burst else 0.0))

            hop_meta = INTHopMetadata(
                switch_id=sw,
                ingress_port=in_port,
                egress_port=out_port,
                hop_latency_us=hop_lat_us,
                queue_id=0,
                queue_occupancy_pkts=q_occupancy,
                queue_occupancy_bytes=q_occupancy * 1500,
                ingress_tstamp_ns=ingress_t,
                egress_tstamp_ns=egress_t,
                tx_utilization_pct=tx_util,
                drop_flag=(q_occupancy > 150),
            )
            hops.append(hop_meta)

        int_header.hops = hops

        report = INTReport(
            report_id=f"rep_{int(time.time() * 1000)}_{len(path)}",
            flow_id=flow_id,
            timestamp=time.time(),
            total_hops=len(hops),
            path=path,
            end_to_end_latency_us=total_lat,
            max_queue_depth_pkts=max_q,
            bottleneck_switch=bottleneck,
            microburst_detected=has_microburst,
            hops=hops,
        )

        if self.collector:
            self.collector.add_report(report)

        return int_header, report


# Global umumiy instansiya
int_telemetry_collector = INTTelemetryCollector()
int_simulator = INTSimulator(collector=int_telemetry_collector)

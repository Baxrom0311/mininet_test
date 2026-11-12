"""OPNET-Grade Advanced Workloads & QoE Engine:
1. VoIPQualityEvaluator: ITU-T G.107 E-model bo'yicha VoIP MOS va R-Factor hisobi.
2. AdaptiveVideoEngine: DASH/HLS adaptiv video oqimlari (bitrate adaptation, rebuffering, QoE).
3. QUICStreamSimulator: HTTP/3 / QUIC multipleksing va Head-of-Line blocking tahlili.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────
# 1. VoIP QoE: ITU-T G.107 E-Model
# ─────────────────────────────────────────────────────────────

CODEC_PARAMS: Dict[str, Dict[str, float]] = {
    "g711": {"ie": 0.0, "bpl": 4.3, "bitrate_kbps": 64.0, "sample_rate_hz": 8000},
    "g729": {"ie": 11.0, "bpl": 19.0, "bitrate_kbps": 8.0, "sample_rate_hz": 8000},
    "opus": {"ie": 5.0, "bpl": 25.0, "bitrate_kbps": 24.0, "sample_rate_hz": 48000},
    "g722": {"ie": 7.0, "bpl": 14.0, "bitrate_kbps": 64.0, "sample_rate_hz": 16000},
}


@dataclass
class VoIPMetrics:
    r_factor: float
    mos: float
    quality_category: str
    delay_impairment: float
    equipment_impairment: float
    one_way_delay_ms: float
    packet_loss_pct: float
    codec: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "r_factor": round(self.r_factor, 2),
            "mos": round(self.mos, 2),
            "quality_category": self.quality_category,
            "delay_impairment": round(self.delay_impairment, 2),
            "equipment_impairment": round(self.equipment_impairment, 2),
            "one_way_delay_ms": round(self.one_way_delay_ms, 2),
            "packet_loss_pct": round(self.packet_loss_pct, 2),
            "codec": self.codec,
        }


class VoIPQualityEvaluator:
    """ITU-T G.107 tavsiyasi bo'yicha E-Model hisoblagichi.
    Kechikish (latency), tebranish (jitter) va yo'qotish (loss) asosida
    R-Factor va MOS (Mean Opinion Score) ni hisoblaydi."""

    def __init__(self, r0: float = 93.2, advantage_factor: float = 0.0):
        self.r0 = r0  # Baza ovoz sifati koeffitsienti
        self.a_factor = advantage_factor

    def evaluate(
        self,
        latency_ms: float,
        jitter_ms: float = 0.0,
        packet_loss_pct: float = 0.0,
        codec: str = "g711",
    ) -> VoIPMetrics:
        codec_key = codec.lower().replace(".", "").replace("-", "")
        c_param = CODEC_PARAMS.get(codec_key, CODEC_PARAMS["g711"])

        # Bir tomonlama umumiy kechikish: D = Latency + 2 * Jitter
        one_way_delay = max(0.0, latency_ms) + 2.0 * max(0.0, jitter_ms)
        loss = min(100.0, max(0.0, packet_loss_pct))

        # 1. Kechikish tufayli yo'qotish (Delay Impairment - Id)
        id_val = 0.0
        if one_way_delay > 100.0:
            # ITU-T G.107 logarifmik formula
            x = math.log(one_way_delay / 100.0) / math.log(2.0)
            id_val = 25.0 * (
                math.pow(1.0 + math.pow(x, 6.0), 1.0 / 6.0)
                - 3.0 * math.pow(1.0 + math.pow(x / 3.0, 6.0), 1.0 / 6.0)
                + 2.0
            )

        # 2. Qurilma va paket yo'qotish buzilishi (Equipment Impairment - Ie,eff)
        ie = c_param["ie"]
        bpl = c_param["bpl"]
        ie_eff = ie + (95.0 - ie) * (loss / (loss + bpl))

        # 3. R-Factor hisobi
        r = self.r0 - id_val - ie_eff + self.a_factor
        r = max(0.0, min(100.0, r))

        # 4. R-Factordan MOS (Mean Opinion Score: 1.0 - 4.5) ga o'tish
        if r <= 0:
            mos = 1.0
        elif r >= 100:
            mos = 4.5
        else:
            mos = 1.0 + 0.035 * r + r * (r - 60.0) * (100.0 - r) * 7e-6
            mos = max(1.0, min(4.5, mos))

        # 5. Sifat toifasi
        if r >= 90.0:
            cat = "A'lo (Best)"
        elif r >= 80.0:
            cat = "Yaxshi (High)"
        elif r >= 70.0:
            cat = "O'rtacha (Medium)"
        elif r >= 60.0:
            cat = "Qoniqarli (Low)"
        else:
            cat = "Yaroqsiz (Poor)"

        return VoIPMetrics(
            r_factor=r,
            mos=mos,
            quality_category=cat,
            delay_impairment=id_val,
            equipment_impairment=ie_eff,
            one_way_delay_ms=one_way_delay,
            packet_loss_pct=loss,
            codec=codec,
        )


# ─────────────────────────────────────────────────────────────
# 2. DASH / HLS Adaptive Video Streaming Engine
# ─────────────────────────────────────────────────────────────

VIDEO_PROFILES: List[Dict[str, Any]] = [
    {"label": "240p", "bitrate_kbps": 350, "resolution": "426x240", "fps": 24},
    {"label": "360p", "bitrate_kbps": 700, "resolution": "640x360", "fps": 24},
    {"label": "480p", "bitrate_kbps": 1400, "resolution": "854x480", "fps": 30},
    {"label": "720p", "bitrate_kbps": 2800, "resolution": "1280x720", "fps": 30},
    {"label": "1080p", "bitrate_kbps": 5500, "resolution": "1920x1080", "fps": 60},
    {"label": "4K", "bitrate_kbps": 15000, "resolution": "3840x2160", "fps": 60},
]


@dataclass
class VideoChunkReport:
    chunk_index: int
    resolution: str
    bitrate_kbps: int
    download_time_s: float
    buffer_level_s: float
    stalled_s: float


@dataclass
class VideoSessionSummary:
    total_chunks: int
    total_play_time_s: float
    total_stall_time_s: float
    stall_events: int
    quality_switches: int
    avg_bitrate_kbps: float
    qoe_score: float  # ITU-T P.1203 uslubidagi 1.0 - 5.0 ball
    resolution_distribution: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_chunks": self.total_chunks,
            "total_play_time_s": round(self.total_play_time_s, 2),
            "total_stall_time_s": round(self.total_stall_time_s, 2),
            "stall_events": self.stall_events,
            "quality_switches": self.quality_switches,
            "avg_bitrate_kbps": round(self.avg_bitrate_kbps, 1),
            "qoe_score": round(self.qoe_score, 2),
            "resolution_distribution": {
                k: round(v, 3) for k, v in self.resolution_distribution.items()
            },
        }


class AdaptiveVideoEngine:
    """BBA (Buffer-Based Adaptation) va Rate-Based gibrid algoritmi asosida
    DASH/HLS video oqimini simulyatsiya qiluvchi dvigatel."""

    def __init__(
        self,
        chunk_duration_s: float = 2.0,
        max_buffer_s: float = 30.0,
        min_buffer_s: float = 4.0,
    ):
        self.chunk_duration = chunk_duration_s
        self.max_buffer = max_buffer_s
        self.min_buffer = min_buffer_s
        self.profiles = sorted(VIDEO_PROFILES, key=lambda p: p["bitrate_kbps"])

    def simulate_stream(
        self,
        bandwidth_trace_kbps: List[float],
        initial_profile_idx: int = 1,
    ) -> Tuple[VideoSessionSummary, List[VideoChunkReport]]:
        """Vaqt bo'yicha berilgan tarmoq tezliklari asosida to'liq video sessiyani hisoblaydi."""
        if not bandwidth_trace_kbps:
            bandwidth_trace_kbps = [2500.0] * 10

        curr_prof_idx = max(0, min(len(self.profiles) - 1, initial_profile_idx))
        buffer_level = 0.0
        total_stall_time = 0.0
        stall_events = 0
        switches = 0
        chunks: List[VideoChunkReport] = []
        bitrate_history: List[int] = []
        resolution_counts: Dict[str, int] = {p["label"]: 0 for p in self.profiles}

        for i, bw in enumerate(bandwidth_trace_kbps):
            bw = max(100.0, bw)
            target_profile = self._select_profile(bw, buffer_level, curr_prof_idx)

            if target_profile != curr_prof_idx:
                switches += 1
                curr_prof_idx = target_profile

            p = self.profiles[curr_prof_idx]
            resolution_counts[p["label"]] += 1
            bitrate_history.append(p["bitrate_kbps"])

            # Bo'lakni (chunk) yuklash vaqti: ChunkBits / Bandwidth
            chunk_kbits = p["bitrate_kbps"] * self.chunk_duration
            download_time = chunk_kbits / bw

            # Bufer hisobi
            stalled = 0.0
            if buffer_level < download_time:
                stalled = download_time - buffer_level
                total_stall_time += stalled
                stall_events += 1
                buffer_level = self.chunk_duration
            else:
                buffer_level = min(self.max_buffer, buffer_level - download_time + self.chunk_duration)

            chunks.append(
                VideoChunkReport(
                    chunk_index=i,
                    resolution=p["label"],
                    bitrate_kbps=p["bitrate_kbps"],
                    download_time_s=round(download_time, 3),
                    buffer_level_s=round(buffer_level, 3),
                    stalled_s=round(stalled, 3),
                )
            )

        total_chunks = len(chunks)
        total_play_time = total_chunks * self.chunk_duration
        avg_bitrate = sum(bitrate_history) / max(1, total_chunks)

        # ITU-T P.1203 uslubidagi QoE hisobi (1.0 - 5.0)
        # Sifat bali: log(bitrate) bo'yicha (maks 4.5)
        base_qoe = 1.0 + 3.5 * (math.log(avg_bitrate / 300.0) / math.log(15000.0 / 300.0))
        base_qoe = max(1.0, min(4.5, base_qoe))

        # Qotib qolish (re-buffering) jarimasi
        stall_ratio = total_stall_time / max(1.0, total_play_time)
        stall_penalty = min(2.5, stall_ratio * 12.0 + stall_events * 0.15)

        # Sifat almashinuvi tebranishi jarimasi
        switch_penalty = min(0.6, (switches / max(1, total_chunks)) * 0.8)

        final_qoe = max(1.0, min(5.0, base_qoe - stall_penalty - switch_penalty))

        dist = {k: v / total_chunks for k, v in resolution_counts.items()}
        summary = VideoSessionSummary(
            total_chunks=total_chunks,
            total_play_time_s=total_play_time,
            total_stall_time_s=total_stall_time,
            stall_events=stall_events,
            quality_switches=switches,
            avg_bitrate_kbps=avg_bitrate,
            qoe_score=final_qoe,
            resolution_distribution=dist,
        )
        return summary, chunks

    def _select_profile(self, bw_kbps: float, buffer_s: float, current_idx: int) -> int:
        """BBA (Buffer-Based Adaptation) va xavfsizlik chegaralari bo'yicha profil tanlaydi."""
        # 1. Agar bufer o'ta xavfli past bo'lsa (< 3s) — darhol pasaytirish
        if buffer_s < self.min_buffer:
            return max(0, current_idx - 1)

        # 2. Xavfsiz o'tkazuvchanlik koeffitsienti: 0.85 * Bandwidth
        effective_bw = bw_kbps * 0.85

        # 3. Bufer yetarli darajada to'lgan bo'lsa (> 10s) va o'tkazuvchanlik ko'tarsa
        best_idx = 0
        for i, p in enumerate(self.profiles):
            if p["bitrate_kbps"] <= effective_bw:
                best_idx = i

        # Sifat sakrashini silliqlash (birdaniga 2 qadamdan ortiq sakramaslik)
        if best_idx > current_idx + 1:
            return current_idx + 1
        elif best_idx < current_idx - 1 and buffer_s > 6.0:
            return current_idx - 1
        return best_idx


# ─────────────────────────────────────────────────────────────
# 3. HTTP/3 & QUIC Stream Multiplexing Simulator
# ─────────────────────────────────────────────────────────────

@dataclass
class QUICStreamReport:
    stream_id: int
    size_bytes: int
    tcp_transfer_time_ms: float
    quic_transfer_time_ms: float
    hol_delay_avoided_ms: float
    loss_events: int


@dataclass
class QUICComparisonResult:
    total_streams: int
    rtt_ms: float
    packet_loss_pct: float
    total_tcp_time_ms: float
    total_quic_time_ms: float
    quic_speedup_pct: float
    total_hol_blocking_ms: float
    stream_reports: List[QUICStreamReport]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_streams": self.total_streams,
            "rtt_ms": round(self.rtt_ms, 2),
            "packet_loss_pct": round(self.packet_loss_pct, 2),
            "total_tcp_time_ms": round(self.total_tcp_time_ms, 2),
            "total_quic_time_ms": round(self.total_quic_time_ms, 2),
            "quic_speedup_pct": round(self.quic_speedup_pct, 1),
            "total_hol_blocking_ms": round(self.total_hol_blocking_ms, 2),
        }


class QUICStreamSimulator:
    """HTTP/2 (TCP) va HTTP/3 (QUIC/UDP) multipleksingini taqqoslovchi
    va Head-of-Line (HoL) blocking bartaraf etilishini modellashtiruvchi sinf."""

    def __init__(self, mss_bytes: int = 1460):
        self.mss = mss_bytes

    def compare_transfer(
        self,
        stream_sizes_bytes: List[int],
        rtt_ms: float = 30.0,
        bandwidth_mbps: float = 50.0,
        packet_loss_pct: float = 2.0,
        use_0rtt: bool = True,
    ) -> QUICComparisonResult:
        """Parallel oqimlarning yuklanishini hisoblab, QUIC va TCP farqini aniqlaydi."""
        bw_bps = bandwidth_mbps * 1e6
        total_streams = len(stream_sizes_bytes)
        reports: List[QUICStreamReport] = []

        # Bog'lanish o'rnatish vaqti (Handshake latency):
        # TCP + TLS 1.3 = 1 RTT (TCP SYN) + 1 RTT (TLS) = 2 RTT (60 ms)
        tcp_handshake_ms = 2.0 * rtt_ms
        # QUIC = 1 RTT (Initial) yoki 0-RTT (agar sessiya takrorlansa)
        quic_handshake_ms = 0.0 if use_0rtt else rtt_ms

        total_hol_delay = 0.0
        tcp_stream_ends = []
        quic_stream_ends = []

        for s_idx, size in enumerate(stream_sizes_bytes):
            pkts = max(1, math.ceil(size / self.mss))
            # Paket tushishi ehtimoli
            expected_losses = pkts * (packet_loss_pct / 100.0)

            # Transmissiya kechikishi
            tx_time_ms = (size * 8.0 / bw_bps) * 1000.0
            rtt_cycles = math.ceil(math.log2(max(2, pkts)))  # Slow start o'sishi
            base_time_ms = rtt_cycles * rtt_ms + tx_time_ms

            # TCP da Head-of-Line (HoL) Blocking:
            # Agar bitta paket yo'qolsa, TCP barcha oqimlarni RTO/FastRetransmit davomida kutdiradi
            # O'rtacha 1 ta yo'qotish uchun kutish = 1.5 * RTT
            hol_penalty_per_loss = 1.5 * rtt_ms
            tcp_hol_delay = expected_losses * hol_penalty_per_loss
            total_hol_delay += tcp_hol_delay

            tcp_total = tcp_handshake_ms + base_time_ms + tcp_hol_delay
            # QUIC da har bir oqim mustaqil: yo'qolgan paket faqat o'sha oqimni ushlaydi, boshqalarni emas
            quic_loss_delay = expected_losses * (1.0 * rtt_ms)
            quic_total = quic_handshake_ms + base_time_ms + quic_loss_delay

            tcp_stream_ends.append(tcp_total)
            quic_stream_ends.append(quic_total)

            reports.append(
                QUICStreamReport(
                    stream_id=s_idx + 1,
                    size_bytes=size,
                    tcp_transfer_time_ms=round(tcp_total, 2),
                    quic_transfer_time_ms=round(quic_total, 2),
                    hol_delay_avoided_ms=round(tcp_hol_delay, 2),
                    loss_events=int(round(expected_losses)),
                )
            )

        max_tcp = max(tcp_stream_ends) if tcp_stream_ends else 0.0
        max_quic = max(quic_stream_ends) if quic_stream_ends else 0.0
        speedup = ((max_tcp - max_quic) / max_tcp * 100.0) if max_tcp > 0 else 0.0

        return QUICComparisonResult(
            total_streams=total_streams,
            rtt_ms=rtt_ms,
            packet_loss_pct=packet_loss_pct,
            total_tcp_time_ms=max_tcp,
            total_quic_time_ms=max_quic,
            quic_speedup_pct=speedup,
            total_hol_blocking_ms=total_hol_delay,
            stream_reports=reports,
        )

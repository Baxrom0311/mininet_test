"""L4 Telemetry Manager:
Uchala dvigatelni (tcp_info, eBPF/drops, Streaming DPI) birlashtiruvchi,
vaqt ketma-ketligi (ring buffer time-series) xotirasini boshqaruvchi va
FastAPI hamda WebSockets uchun ma'lumot yetkazib beruvchi markaziy menejer.
"""

from __future__ import annotations

import asyncio
from collections import deque
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Set

from lab.telemetry.dpi_dissector import DPIDissector
from lab.telemetry.ebpf_tracker import EBPFEvent, EBPFTracker
from lab.telemetry.tcp_info_collector import TCPInfoCollector, TCPInfoSample


class StreamHistory:
    """Bitta L4 oqimining (masalan, Host1:45322 -> Server:80) vaqt bo'yicha dinamikasi (Ring Buffer)."""

    def __init__(self, stream_id: str, max_points: int = 200):
        self.stream_id = stream_id
        self.max_points = max_points
        self.samples: Deque[Dict[str, Any]] = deque(maxlen=max_points)
        self.last_update = time.time()
        self.created_at = time.time()
        self.latest_data: Dict[str, Any] = {}

    def append(self, sample_dict: Dict[str, Any]) -> None:
        self.latest_data = sample_dict
        self.last_update = sample_dict.get("timestamp", time.time())
        self.samples.append(sample_dict)

    def to_dict(self) -> Dict[str, Any]:
        samples_list = list(self.samples)
        rtts = [s.get("rtt_ms") for s in samples_list if s.get("rtt_ms") is not None]
        rates = [s.get("delivery_rate_bps") for s in samples_list if s.get("delivery_rate_bps") is not None]

        min_rtt = min(rtts) if rtts else 0.0
        max_rtt = max(rtts) if rtts else 0.0
        avg_rtt = sum(rtts) / len(rtts) if rtts else 0.0
        peak_rate = max(rates) if rates else 0.0

        return {
            "stream_id": self.stream_id,
            "stream_key": self.stream_id,
            **self.latest_data,
            "samples": samples_list,
            "history": samples_list,
            "points_count": len(samples_list),
            "created_at": self.created_at,
            "last_update": self.last_update,
            "stats": {
                "min_rtt_ms": round(min_rtt, 3),
                "max_rtt_ms": round(max_rtt, 3),
                "avg_rtt_ms": round(avg_rtt, 3),
                "peak_delivery_rate_bps": round(peak_rate, 2),
            },
        }


class TelemetryManager:
    """Transport qatlami (L4) ning barcha telemetriyasini boshqaruvchi markaziy klass."""

    def __init__(self, driver=None, bus=None, history_len: int = 200):
        self.driver = driver
        self.bus = bus
        self.history_len = history_len

        # Dvigatellar
        self.tcp_collector = TCPInfoCollector(driver)
        self.ebpf_tracker = EBPFTracker(driver)
        self.dpi = DPIDissector()

        # Queue / Bufferbloat Tracker
        try:
            from lab.telemetry.queue_tracker import QueueTracker
            self.queue_tracker = QueueTracker(driver)
        except Exception:
            self.queue_tracker = None

        # OPNET Workloads & SLA Monitor
        try:
            from lab.telemetry.opnet_workloads import (
                AdaptiveVideoSession,
                SLAMonitor,
                VoIPMOSCalculator,
                generate_latex_report,
            )
            self.sla_monitor = SLAMonitor()
            self.video_session = AdaptiveVideoSession("demo_stream")
            self.voip_calculator = VoIPMOSCalculator
            self._latex_generator = generate_latex_report
        except Exception:
            self.sla_monitor = None
            self.video_session = None
            self.voip_calculator = None
            self._latex_generator = None

        # Saqlash xotiralari
        self._streams: Dict[str, StreamHistory] = {}
        self._lock = threading.RLock()
        self._ws_lock = threading.Lock()
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._sim_start_time = time.time()

        # eBPF hodisalarini bus/ws ga uzatish
        self.ebpf_tracker.register_callback(self._on_ebpf_event)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Asyncio event loopni biriktiradi."""
        self._loop = loop

    def _on_ebpf_event(self, ev: EBPFEvent) -> None:
        payload = {"type": "l4_event", "event": ev.to_dict()}
        self._publish_ws(payload)

    def subscribe(self) -> asyncio.Queue:
        """WebSocket mijozlari uchun yangi navbat (queue) yaratadi."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._ws_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Mijoz uzilganda uning navbatini olib tashlaydi."""
        with self._ws_lock:
            self._subscribers.discard(q)

    @staticmethod
    def _safe_put(q: asyncio.Queue, msg: Any) -> None:
        try:
            q.put_nowait(msg)
        except (asyncio.QueueFull, Exception):
            pass

    def _publish_ws(self, msg: Any) -> None:
        with self._ws_lock:
            subscribers = list(self._subscribers)
        if not subscribers:
            return

        for q in subscribers:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._safe_put, q, msg)
                else:
                    self._safe_put(q, msg)
            except Exception:
                pass

    def ingest_tcp_sample(self, sample: Any) -> Dict[str, Any]:
        """Yangi TCP namunasini qabul qilib, oqimlar tarixiga qo'shadi va WebSocketga translyatsiya qiladi."""
        if hasattr(sample, "to_dict"):
            d = sample.to_dict()
            sid = getattr(sample, "stream_key", getattr(sample, "stream_id", None)) or d.get("stream_id")
        else:
            d = dict(sample)
            sid = (
                d.get("stream_key")
                or d.get("stream_id")
                or f"{d.get('src_ip')}:{d.get('src_port')}->{d.get('dst_ip')}:{d.get('dst_port')}"
            )

        if not sid:
            return d

        with self._lock:
            if sid not in self._streams:
                self._streams[sid] = StreamHistory(sid, max_points=self.history_len)
            self._streams[sid].append(d)

            # SLA va Workload baholash
            if self.sla_monitor and isinstance(d, dict):
                rtt = float(d.get("rtt_ms", 0.0) or 0.0)
                retrans = float(d.get("retrans_total", d.get("retrans", 0.0)) or 0.0)
                rate = float(d.get("delivery_rate_bps", 0.0) or 0.0)
                self.sla_monitor.evaluate_stream(
                    sid,
                    rtt_ms=rtt,
                    jitter_ms=round(rtt * 0.12, 2),
                    loss_pct=min(100.0, retrans * 2.5),
                    throughput_bps=rate,
                )
                if self.video_session and rate > 0:
                    self.video_session.update(rate)

        self._publish_ws({"type": "tcp_sample", "stream_id": sid, "sample": d})
        return d

    def start_polling(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._running = True
        self._sim_start_time = time.time()
        if loop is not None:
            self._loop = loop
        if self._loop and not self._poll_task:
            self._poll_task = self._loop.create_task(self._poll_loop())

    def stop_polling(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    def reset(self) -> None:
        with self._lock:
            self._streams.clear()
            self._sim_start_time = time.time()
        if self.queue_tracker:
            try:
                self.queue_tracker.reset()
            except Exception:
                pass

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                self.poll_once()
            except Exception:
                pass
            await asyncio.sleep(0.5)

    def poll_once(self) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        is_real_mininet = (
            self.driver is not None
            and hasattr(self.driver, "net")
            and getattr(self.driver, "net", None) is not None
        )

        if is_real_mininet:
            net = self.driver.net
            for node_name in getattr(net, "hosts", []):
                name = getattr(node_name, "name", str(node_name))
                node_obj = net.get(name) if hasattr(net, "get") else None
                s_list = self.tcp_collector.collect_live_node(name, node_obj)
                for s in s_list:
                    self.ingest_tcp_sample(s)
                    discovered.append(s)
                self.ebpf_tracker.collect_kernel_drops(node_obj, name)
            if self.queue_tracker:
                try:
                    q_samples = self.queue_tracker.collect_live_net(net)
                    for qs in q_samples:
                        discovered.append({"type": "queue", **qs.to_dict()})
                except Exception:
                    pass
        else:
            now = time.time()
            elapsed = now - self._sim_start_time
            s1 = self.tcp_collector.simulate_sample(
                "10.0.1.10", 45210, "10.0.2.20", 80, step=int(elapsed * 2) % 30, base_rtt=12.0
            )
            s2 = self.tcp_collector.simulate_sample(
                "10.0.1.20", 53488, "10.0.3.30", 443, step=(int(elapsed * 2) + 7) % 30, base_rtt=25.0
            )
            self.ingest_tcp_sample(s1)
            self.ingest_tcp_sample(s2)
            discovered.extend([s1.to_dict(), s2.to_dict()])
            if self.queue_tracker:
                try:
                    q_samples = self.queue_tracker.simulate_step(elapsed)
                    for qs in q_samples:
                        discovered.append({"type": "queue", **qs.to_dict()})
                except Exception:
                    pass

        return discovered

    def get_active_streams(self) -> List[Dict[str, Any]]:
        """Barcha faol oqimlarning eng so'nggi holatini qaytaradi."""
        with self._lock:
            return [
                {"stream_id": sid, **sh.latest_data, "points_count": len(sh.samples)}
                for sid, sh in self._streams.items()
                if sh.latest_data
            ]

    def get_stream_detail(self, stream_id: str) -> Optional[Dict[str, Any]]:
        """Bitta oqimning to'liq tarixi va parametrlarini qaytaradi."""
        with self._lock:
            sh = self._streams.get(stream_id)
            if sh:
                return sh.to_dict()
        return None

    def get_global_stats(self) -> Dict[str, Any]:
        with self._lock:
            active = [sh.latest_data for sh in self._streams.values() if sh.latest_data]
            total_history_points = sum(len(sh.samples) for sh in self._streams.values())

        q_summary = self.get_queue_summary()
        total_queues_count = q_summary.get("total_queues", 0)
        bufferbloat_detected_count = q_summary.get("bufferbloat_detected_count", 0)

        total = len(active)
        if total == 0:
            return {
                "active_streams_count": 0,
                "total_delivery_rate_bps": 0.0,
                "total_pacing_rate_bps": 0.0,
                "total_bytes_acked": 0,
                "avg_rtt_ms": 0.0,
                "min_rtt_ms": 0.0,
                "max_rtt_ms": 0.0,
                "total_retransmits": 0,
                "recent_drops_count": len(self.ebpf_tracker.get_recent_drops()),
                "total_history_points": 0,
                "total_queues_count": total_queues_count,
                "bufferbloat_detected_count": bufferbloat_detected_count,
            }

        total_rate = sum(s.get("delivery_rate_bps", 0.0) for s in active)
        total_pacing = sum(s.get("pacing_rate_bps", 0.0) for s in active)
        total_bytes = sum(s.get("bytes_acked", 0) for s in active)
        rtt_list = [s.get("rtt_ms", 0.0) for s in active if s.get("rtt_ms") is not None]
        avg_rtt = sum(rtt_list) / len(rtt_list) if rtt_list else 0.0
        min_rtt = min(rtt_list) if rtt_list else 0.0
        max_rtt = max(rtt_list) if rtt_list else 0.0
        total_ret = sum(s.get("total_retrans", s.get("retrans", 0)) for s in active)

        return {
            "active_streams_count": total,
            "total_delivery_rate_bps": round(total_rate, 2),
            "total_pacing_rate_bps": round(total_pacing, 2),
            "total_bytes_acked": total_bytes,
            "avg_rtt_ms": round(avg_rtt, 3),
            "min_rtt_ms": round(min_rtt, 3),
            "max_rtt_ms": round(max_rtt, 3),
            "total_retransmits": total_ret,
            "recent_drops_count": len(self.ebpf_tracker.get_recent_drops()),
            "total_history_points": total_history_points,
            "total_queues_count": total_queues_count,
            "bufferbloat_detected_count": bufferbloat_detected_count,
        }

    def get_recent_drops(self, limit: int = 50) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        return self.ebpf_tracker.get_recent_drops(limit=safe_limit)

    def dissect_raw(self, raw_bytes: bytes) -> Dict[str, Any]:
        return self.dpi.dissect_frame(raw_bytes)

    def inspect_pdu(
        self,
        packet_info: Dict[str, Any],
        device_name: str = "Router",
        device_type: str = "router",
    ) -> Dict[str, Any]:
        return self.dpi.build_cisco_pdu_inspection(packet_info, device_name=device_name, device_type=device_type)

    def get_queues(self, dev: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.queue_tracker:
            return self.queue_tracker.get_latest_stats(dev=dev)
        return []

    def get_queue_history(self, dev: str, handle: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.queue_tracker:
            return self.queue_tracker.get_history(dev=dev, handle=handle)
        return []

    def get_bufferbloat_report(self) -> Dict[str, Any]:
        if self.queue_tracker:
            return self.queue_tracker.get_bufferbloat_report()
        return {"devices": {}, "bloated_count": 0}

    def get_queue_summary(self) -> Dict[str, Any]:
        if self.queue_tracker:
            return self.queue_tracker.get_summary()
        return {}

    def get_sla_report(self) -> Dict[str, Any]:
        """SLA qoidalariga rioya qilinishi va buzilishlar hisobotini qaytaradi."""
        if self.sla_monitor:
            return self.sla_monitor.get_summary()
        return {
            "compliance_pct": 100.0,
            "total_evaluations": 0,
            "total_violations": 0,
            "recent_violations": [],
        }

    def evaluate_voip_call(
        self,
        delay_ms: float = 30.0,
        jitter_ms: float = 2.0,
        loss_pct: float = 0.0,
        codec: str = "G.711",
    ) -> Dict[str, Any]:
        """ITU-T G.107 E-Model bo'yicha VoIP qo'ng'irog'i sifatini (MOS) baholash."""
        if self.voip_calculator:
            return self.voip_calculator.calculate(
                delay_ms=delay_ms, jitter_ms=jitter_ms, loss_pct=loss_pct, codec=codec
            ).to_dict()
        return {}

    def get_video_streaming_stats(self) -> Dict[str, Any]:
        """Adaptiv video oqimi (HLS/DASH) holatini qaytaradi."""
        if self.video_session:
            curr = self.video_session.LADDER[self.video_session.current_rep_idx]
            qoe = self.video_session.get_qoe_score() if hasattr(self.video_session, "get_qoe_score") else 4.5
            return {
                "session_id": self.video_session.session_id,
                "current_quality": curr.label,
                "resolution": curr.resolution,
                "bitrate_bps": curr.bitrate_bps,
                "buffer_sec": round(self.video_session.buffer_sec, 2),
                "stalls": self.video_session.stall_count,
                "total_stall_sec": round(self.video_session.total_stall_sec, 2),
                "switches": self.video_session.quality_switches,
                "qoe_score": qoe,
                "history": self.video_session.history[-20:],
            }
        return {}

    def export_latex_report(self, title: str = "NetLab Research Evaluation Report") -> str:
        """Ilmiy maqolalar uchun professional LaTeX hisobotini shakllantiradi."""
        if self._latex_generator:
            return self._latex_generator(
                title=title,
                telemetry_summary=self.get_global_stats(),
                sla_summary=self.get_sla_report(),
                voip_summary=self.evaluate_voip_call(),
                video_summary=self.get_video_streaming_stats(),
            )
        return ""

    def compare_streams(
        self, stream_id_a: str, stream_id_b: str, label_a: str = "Stream A", label_b: str = "Stream B"
    ) -> Dict[str, Any]:
        """Ikkita faol L4 oqimining haqiqiy telemetriya ko'rsatkichlarini solishtirish."""
        from lab.telemetry.comparison import TelemetryComparator

        sa = self.get_stream_detail(stream_id_a) or {}
        sb = self.get_stream_detail(stream_id_b) or {}
        return TelemetryComparator.compare_streams(sa, sb, label_a=label_a, label_b=label_b)

    def compare_algorithms(
        self,
        algo_a: str = "cubic",
        algo_b: str = "bbr",
        bottleneck_bps: float = 20_000_000.0,
        base_rtt_ms: float = 30.0,
        loss_pct: float = 1.0,
        buffer_size_pkts: int = 200,
    ) -> Dict[str, Any]:
        """CUBIC vs BBR algoritmlarini turli tarmoq sharoitlarida solishtirish."""
        from lab.telemetry.comparison import TelemetryComparator

        return TelemetryComparator.compare_algorithms(
            algo_a=algo_a,
            algo_b=algo_b,
            bottleneck_bps=bottleneck_bps,
            base_rtt_ms=base_rtt_ms,
            loss_pct=loss_pct,
            buffer_size_pkts=buffer_size_pkts,
        )

    def compare_queuing(
        self,
        qdisc_a: str = "pfifo_fast",
        qdisc_b: str = "fq_codel",
        link_bps: float = 10_000_000.0,
        load_pct: float = 120.0,
    ) -> Dict[str, Any]:
        """FIFO va FQ-CoDel navbat algoritmlarini solishtirish."""
        from lab.telemetry.comparison import TelemetryComparator

        return TelemetryComparator.compare_queuing(
            qdisc_a=qdisc_a,
            qdisc_b=qdisc_b,
            link_bps=link_bps,
            load_pct=load_pct,
        )


# Global singleton instance
telemetry_manager = TelemetryManager()

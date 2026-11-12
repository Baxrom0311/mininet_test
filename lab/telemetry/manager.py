"""L4 Telemetry Manager:
Uchala dvigatelni (tcp_info, eBPF/drops, Streaming DPI) birlashtiruvchi,
vaqt ketma-ketligi (time-series) xotirasini boshqaruvchi va
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
    """Bitta L4 oqimining (masalan, Host1:45322 -> Server:80) vaqt bo'yicha dinamikasi."""

    def __init__(self, stream_id: str, max_points: int = 200):
        self.stream_id = stream_id
        self.max_points = max_points
        self.samples: Deque[Dict[str, Any]] = deque(maxlen=max_points)
        self.last_update = time.time()
        self.latest_data: Dict[str, Any] = {}

    def append(self, sample_dict: Dict[str, Any]) -> None:
        self.latest_data = sample_dict
        self.last_update = sample_dict.get("timestamp", time.time())
        self.samples.append(sample_dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            **self.latest_data,
            "samples": list(self.samples),
            "history": list(self.samples),
        }


class TelemetryManager:
    """Transport qatlami (L4) ning barcha telemetriyasini boshqaruvchi asosiy klass."""

    def __init__(self, driver=None, bus=None, history_len: int = 200):
        self.driver = driver
        self.bus = bus
        self.history_len = history_len

        # Dvigatellar
        self.tcp_collector = TCPInfoCollector(driver)
        self.ebpf_tracker = EBPFTracker(driver)
        self.dpi = DPIDissector()

        # Saqlash xotiralari
        self._streams: Dict[str, StreamHistory] = {}
        self._lock = threading.Lock()
        self._subscribers: Set[asyncio.Queue] = set()
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._sim_start_time = time.time()

        # eBPF hodisalarini bus/ws ga uzatish
        self.ebpf_tracker.register_callback(self._on_ebpf_event)

    def _on_ebpf_event(self, ev: EBPFEvent) -> None:
        payload = {"type": "l4_event", "event": ev.to_dict()}
        self._publish_ws(payload)

    def subscribe(self) -> asyncio.Queue:
        """WebSocket mijozlari uchun yangi navbat (queue) yaratadi."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Mijoz uzilganda uning navbatini olib tashlaydi."""
        self._subscribers.discard(q)

    def _publish_ws(self, msg: Any) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except (asyncio.QueueFull, Exception):
                pass

    def ingest_tcp_sample(self, sample: Any) -> None:
        """Yangi TCP namunasini qabul qilib, oqimlar tarixiga qo'shadi."""
        if hasattr(sample, "to_dict"):
            d = sample.to_dict()
            sid = getattr(sample, "stream_key", getattr(sample, "stream_id", None)) or d.get("stream_id")
        else:
            d = dict(sample)
            sid = d.get("stream_key") or d.get("stream_id") or f"{d.get('src_ip')}:{d.get('src_port')}->{d.get('dst_ip')}:{d.get('dst_port')}"

        if not sid:
            return

        with self._lock:
            if sid not in self._streams:
                self._streams[sid] = StreamHistory(sid, max_points=self.history_len)
            self._streams[sid].append(d)

        self._publish_ws({"type": "tcp_sample", "stream_id": sid, "sample": d})

    def start_polling(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._running = True
        self._sim_start_time = time.time()
        if loop is not None and not self._poll_task:
            self._poll_task = loop.create_task(self._poll_loop())

    def stop_polling(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    def reset(self) -> None:
        with self._lock:
            self._streams.clear()
            self._sim_start_time = time.time()

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

        return discovered

    def get_active_streams(self) -> List[Dict[str, Any]]:
        """Barcha faol oqimlarning eng so'nggi holatini qaytaradi."""
        with self._lock:
            return [
                {"stream_id": sid, **sh.latest_data}
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

        total = len(active)
        if total == 0:
            return {
                "active_streams_count": 0,
                "total_delivery_rate_bps": 0.0,
                "total_bytes_acked": 0,
                "avg_rtt_ms": 0.0,
                "total_retransmits": 0,
                "recent_drops_count": len(self.ebpf_tracker.get_recent_drops()),
            }

        total_rate = sum(s.get("delivery_rate_bps", 0.0) for s in active)
        total_bytes = sum(s.get("bytes_acked", 0) for s in active)
        rtt_list = [s.get("rtt_ms", 0.0) for s in active if s.get("rtt_ms") is not None]
        avg_rtt = sum(rtt_list) / len(rtt_list) if rtt_list else 0.0
        total_ret = sum(s.get("total_retrans", s.get("retrans", 0)) for s in active)

        return {
            "active_streams_count": total,
            "total_delivery_rate_bps": round(total_rate, 2),
            "total_bytes_acked": total_bytes,
            "avg_rtt_ms": round(avg_rtt, 3),
            "total_retransmits": total_ret,
            "recent_drops_count": len(self.ebpf_tracker.get_recent_drops()),
        }

    def get_recent_drops(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.ebpf_tracker.get_recent_drops(limit=limit)

    def dissect_raw(self, raw_bytes: bytes) -> Dict[str, Any]:
        return self.dpi.dissect_frame(raw_bytes)


# Global singleton instance
telemetry_manager = TelemetryManager()

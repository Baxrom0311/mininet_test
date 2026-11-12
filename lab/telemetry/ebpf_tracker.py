"""eBPF Tracker & Kernel Drop Monitor:
Yadro darajasida (Kernel level) paketlarning tushib qolishi (packet drop),
qayta yuborilishi (retransmission) va navbat (queue) tiqilinchini kuzatuvchi modul.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional


@dataclass
class EBPFEvent:
    """Yadro darajasida ushlangan L4 hodisasi."""

    event_type: str  # "tcp_drop", "tcp_retransmit", "tcp_probe"
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    timestamp: float = field(default_factory=time.time)
    drop_reason: Optional[str] = None
    cwnd: Optional[int] = None
    ssthresh: Optional[int] = None
    rtt_us: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "src_ip": self.src_ip,
            "src_port": self.src_port,
            "dst_ip": self.dst_ip,
            "dst_port": self.dst_port,
            "timestamp": self.timestamp,
            "drop_reason": self.drop_reason,
            "cwnd": self.cwnd,
            "ssthresh": self.ssthresh,
            "rtt_us": self.rtt_us,
        }


class EBPFTracker:
    """eBPF tracepoints/kprobes va yadro paket tushishlarini kuzatuvchi sinf."""

    def __init__(self, driver=None):
        self.driver = driver
        self.ebpf_available = self._check_ebpf_support()
        self._callbacks: List[Callable[[EBPFEvent], None]] = []
        self._recent_events: List[EBPFEvent] = []
        self._max_history = 500
        self._last_netstat: Dict[str, int] = {}

    def register_callback(self, cb: Callable[[EBPFEvent], None]) -> None:
        """Yangi hodisa yuz berganda chaqiriluvchi tinglovchini (listener) ro'yxatga oladi."""
        self._callbacks.append(cb)

    def _emit(self, event: EBPFEvent) -> None:
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_history:
            self._recent_events = self._recent_events[-self._max_history :]
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:
                pass

    def record_drop(
        self,
        src_ip: str,
        src_port: int,
        dst_ip: str,
        dst_port: int,
        reason: str = "KERNEL_DROP",
        ts: Optional[float] = None,
    ) -> EBPFEvent:
        """Paket tushib qolishini qo'lda yoki yadro drayveridan qayd qilish."""
        ev = EBPFEvent(
            event_type="tcp_drop",
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            drop_reason=reason,
            timestamp=ts if ts is not None else time.time(),
        )
        self._emit(ev)
        return ev

    def record_retransmit(
        self,
        src_ip: str,
        src_port: int,
        dst_ip: str,
        dst_port: int,
        cwnd: Optional[int] = None,
        ts: Optional[float] = None,
    ) -> EBPFEvent:
        """Qayta yuborish hodisasini qayd qilish."""
        ev = EBPFEvent(
            event_type="tcp_retransmit",
            src_ip=src_ip,
            src_port=src_port,
            dst_ip=dst_ip,
            dst_port=dst_port,
            cwnd=cwnd,
            timestamp=ts if ts is not None else time.time(),
        )
        self._emit(ev)
        return ev

    @staticmethod
    def _check_ebpf_support() -> bool:
        if os.path.exists("/sys/kernel/debug/tracing") and os.access("/sys/kernel/debug/tracing", os.R_OK):
            try:
                r = subprocess.run(["which", "bpftool"], capture_output=True, text=True, timeout=1.0)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    def collect_kernel_drops(self, node_obj: Any = None, node_name: str = "host") -> List[Dict[str, Any]]:
        """Linux /proc/net/netstat va tc orqali tushishlarni yig'adi."""
        drops: List[Dict[str, Any]] = []
        now = time.time()
        netstat = self._read_netstat(node_obj)
        if self._last_netstat and netstat:
            diff_timeouts = netstat.get("TCPTimeouts", 0) - self._last_netstat.get("TCPTimeouts", 0)
            if diff_timeouts > 0:
                ev = self.record_drop("0.0.0.0", 0, "0.0.0.0", 0, reason="RTO_TIMEOUT")
                drops.append(ev.to_dict())

            diff_fast_ret = netstat.get("TCPFastRetrans", 0) - self._last_netstat.get("TCPFastRetrans", 0)
            if diff_fast_ret > 0:
                ev = self.record_retransmit("0.0.0.0", 0, "0.0.0.0", 0)
                drops.append(ev.to_dict())

        if netstat:
            self._last_netstat = netstat
        return drops

    @staticmethod
    def _read_netstat(node_obj: Any = None) -> Dict[str, int]:
        content = ""
        try:
            if node_obj is not None and hasattr(node_obj, "cmd"):
                content = node_obj.cmd("cat /proc/net/netstat 2>/dev/null")
            elif os.path.exists("/proc/net/netstat"):
                with open("/proc/net/netstat", "r") as f:
                    content = f.read()
        except Exception:
            return {}

        res: Dict[str, int] = {}
        lines = content.strip().splitlines()
        for i in range(0, len(lines) - 1, 2):
            if lines[i].startswith("TcpExt:"):
                keys = lines[i].split()[1:]
                vals = lines[i + 1].split()[1:]
                for k, v in zip(keys, vals):
                    try:
                        res[k] = int(v)
                    except ValueError:
                        pass
        return res

    def get_recent_drops(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [ev.to_dict() for ev in self._recent_events[-limit:]]

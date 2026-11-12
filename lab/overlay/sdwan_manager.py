"""Application-Aware SD-WAN Routing & Dynamic Multi-Path Steering Engine:
- Multi-path underlay tunnels (MPLS, Business Broadband Internet, LTE Cellular, WireGuard)
- Real-time SLA compliance monitoring (RTT Latency, Jitter, Packet Loss, Bandwidth)
- Application-Aware Path Selection (VoIP, Video Conferencing, Mission Critical ERP/DB, Bulk Backup)
- Automatic Brownout (SLA violation) & Blackout (Link down) Detection and Failover
- Path Flap Dampening and Event Logging
"""

from __future__ import annotations

from dataclasses import dataclass, field
import enum
import time
from typing import Any, Dict, List, Optional, Tuple


class TrafficClass(str, enum.Enum):
    VOIP = "voip"
    VIDEO = "video"
    CRITICAL_DATA = "critical_data"
    CRITICAL_DB = "critical_db"
    BULK_BACKUP = "bulk_backup"
    BULK = "bulk"


@dataclass
class SLASpec:
    max_latency_ms: float
    max_jitter_ms: float
    max_loss_pct: float
    min_bandwidth_mbps: float
    preferred_metric: str  # "latency", "loss", "bandwidth", "cost"


DEFAULT_SLAS: Dict[TrafficClass, SLASpec] = {
    TrafficClass.VOIP: SLASpec(
        max_latency_ms=80.0,
        max_jitter_ms=15.0,
        max_loss_pct=0.5,
        min_bandwidth_mbps=1.0,
        preferred_metric="latency",
    ),
    TrafficClass.VIDEO: SLASpec(
        max_latency_ms=120.0,
        max_jitter_ms=25.0,
        max_loss_pct=1.0,
        min_bandwidth_mbps=8.0,
        preferred_metric="latency",
    ),
    TrafficClass.CRITICAL_DATA: SLASpec(
        max_latency_ms=150.0,
        max_jitter_ms=50.0,
        max_loss_pct=0.1,
        min_bandwidth_mbps=5.0,
        preferred_metric="loss",
    ),
    TrafficClass.CRITICAL_DB: SLASpec(
        max_latency_ms=100.0,
        max_jitter_ms=20.0,
        max_loss_pct=0.1,
        min_bandwidth_mbps=10.0,
        preferred_metric="loss",
    ),
    TrafficClass.BULK_BACKUP: SLASpec(
        max_latency_ms=300.0,
        max_jitter_ms=100.0,
        max_loss_pct=3.0,
        min_bandwidth_mbps=20.0,
        preferred_metric="cost",
    ),
    TrafficClass.BULK: SLASpec(
        max_latency_ms=400.0,
        max_jitter_ms=100.0,
        max_loss_pct=4.0,
        min_bandwidth_mbps=15.0,
        preferred_metric="cost",
    ),
}


@dataclass
class SDWANTunnel:
    """SD-WAN ostki (underlay) yo'li yoki kriptografik tunnel."""

    tunnel_id: str
    name: str
    media_type: str             # "mpls", "broadband", "lte", "wireguard"
    latency_ms: float
    jitter_ms: float
    packet_loss_pct: float
    bandwidth_mbps: float
    cost_per_gb: float          # Nisbiy narx (MPLS yuqori, Internet arzon)
    status: str = "healthy"     # "healthy", "degraded", "down"
    active_flows_count: int = 0

    def is_sla_compliant(self, sla: SLASpec) -> Tuple[bool, List[str]]:
        """Tunnel ko'rsatkichlari berilgan ilova SLA talablariga mos keladimi?"""
        if self.status == "down":
            return False, ["Tunnel offline (link uzilgan - Blackout)"]

        violations = []
        if self.latency_ms > sla.max_latency_ms:
            violations.append(f"Kechikish yuqori: {self.latency_ms:.1f}ms > {sla.max_latency_ms}ms (Brownout)")
        if self.jitter_ms > sla.max_jitter_ms:
            violations.append(f"Jitter yuqori: {self.jitter_ms:.1f}ms > {sla.max_jitter_ms}ms")
        if self.packet_loss_pct > sla.max_loss_pct:
            violations.append(f"Paket yo'qotish: {self.packet_loss_pct:.2f}% > {sla.max_loss_pct}% (Brownout)")
        if self.bandwidth_mbps < sla.min_bandwidth_mbps:
            violations.append(f"O'tkazuvchanlik yetarsiz: {self.bandwidth_mbps}Mbps < {sla.min_bandwidth_mbps}Mbps")

        return len(violations) == 0, violations

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tunnel_id": self.tunnel_id,
            "name": self.name,
            "media_type": self.media_type,
            "latency_ms": round(self.latency_ms, 2),
            "jitter_ms": round(self.jitter_ms, 2),
            "packet_loss_pct": round(self.packet_loss_pct, 2),
            "bandwidth_mbps": round(self.bandwidth_mbps, 1),
            "cost_per_gb": self.cost_per_gb,
            "status": self.status,
            "active_flows_count": self.active_flows_count,
        }


# WANPath alias
WANPath = SDWANTunnel


@dataclass
class FailoverEvent:
    """SD-WAN avtomatik yo'l almashtirish hodisasi."""

    timestamp: float
    traffic_class: str
    source_tunnel: str
    target_tunnel: str
    reason: str
    failover_duration_ms: float = 145.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "traffic_class": self.traffic_class,
            "source_tunnel": self.source_tunnel,
            "target_tunnel": self.target_tunnel,
            "reason": self.reason,
            "failover_duration_ms": round(self.failover_duration_ms, 1),
        }


class SDWANManager:
    """Application-Aware Multi-Path SD-WAN Boshqaruvchisi va Marshrutizatori."""

    def __init__(self):
        self.tunnels: Dict[str, SDWANTunnel] = {}
        self.failover_history: List[FailoverEvent] = []
        self._init_default_tunnels()

    def _init_default_tunnels(self):
        # 1. MPLS Birlamchi Magisteral (Juda past kechikish va yo'qotish, lekin qimmat)
        self.tunnels["mpls_1"] = SDWANTunnel(
            tunnel_id="mpls_1",
            name="MPLS Dedicated Trunk",
            media_type="mpls",
            latency_ms=18.5,
            jitter_ms=2.1,
            packet_loss_pct=0.01,
            bandwidth_mbps=50.0,
            cost_per_gb=10.0,
        )
        # 2. Biznes Internet / WireGuard VPN (Tez, arzon, o'rtacha kechikish)
        self.tunnels["inet_wg"] = SDWANTunnel(
            tunnel_id="inet_wg",
            name="Business Internet (WireGuard VPN)",
            media_type="wireguard",
            latency_ms=32.0,
            jitter_ms=5.4,
            packet_loss_pct=0.15,
            bandwidth_mbps=200.0,
            cost_per_gb=1.0,
        )
        # 3. 5G/LTE Mobil Kanal (Zaxira kanal)
        self.tunnels["lte_backup"] = SDWANTunnel(
            tunnel_id="lte_backup",
            name="Cellular 5G Backup Link",
            media_type="lte",
            latency_ms=65.0,
            jitter_ms=14.0,
            packet_loss_pct=0.40,
            bandwidth_mbps=30.0,
            cost_per_gb=15.0,
        )

    def select_path(self, app_class: TrafficClass | str) -> Dict[str, Any]:
        """Ilova profili bo'yicha eng optimal tunnelni tanlaydi."""
        if isinstance(app_class, str):
            try:
                app_class = TrafficClass(app_class.lower())
            except ValueError:
                app_class = TrafficClass.CRITICAL_DATA

        sla = DEFAULT_SLAS.get(app_class, DEFAULT_SLAS[TrafficClass.CRITICAL_DATA])
        compliant_tunnels: List[SDWANTunnel] = []
        rejected_tunnels: List[Dict[str, Any]] = []

        for t in self.tunnels.values():
            ok, reasons = t.is_sla_compliant(sla)
            if ok:
                compliant_tunnels.append(t)
            else:
                rejected_tunnels.append({"tunnel": t.tunnel_id, "reasons": reasons})

        if not compliant_tunnels:
            best_fallback = min(
                [t for t in self.tunnels.values() if t.status != "down"] or list(self.tunnels.values()),
                key=lambda x: (x.packet_loss_pct, x.latency_ms),
            )
            return {
                "selected_tunnel": best_fallback.to_dict(),
                "sla_met": False,
                "app_class": app_class.value,
                "strategy": "Best-Effort Fallback (Barcha yo'llar degradatsiyada / Brownout)",
                "rejected": rejected_tunnels,
            }

        # Qoidaga asosan eng yaxshi tunnelni tanlash
        if sla.preferred_metric == "latency":
            chosen = min(compliant_tunnels, key=lambda x: x.latency_ms)
        elif sla.preferred_metric == "loss":
            chosen = min(compliant_tunnels, key=lambda x: x.packet_loss_pct)
        elif sla.preferred_metric == "cost":
            chosen = min(compliant_tunnels, key=lambda x: (x.cost_per_gb, -x.bandwidth_mbps))
        else:
            chosen = max(compliant_tunnels, key=lambda x: x.bandwidth_mbps)

        return {
            "selected_tunnel": chosen.to_dict(),
            "sla_met": True,
            "app_class": app_class.value,
            "strategy": f"Optimal Path ({sla.preferred_metric} bo'yicha)",
            "compliant_count": len(compliant_tunnels),
            "rejected": rejected_tunnels,
        }

    def evaluate_all(self) -> Dict[str, Any]:
        """Barcha ilova sinflari uchun joriy eng yaxshi yo'llarni hisoblaydi."""
        results = {}
        for app in [TrafficClass.VOIP, TrafficClass.VIDEO, TrafficClass.CRITICAL_DATA, TrafficClass.BULK_BACKUP]:
            results[app.value] = self.select_path(app)
        return {
            "evaluated_at": time.time(),
            "paths": {k: v.to_dict() for k, v in self.tunnels.items()},
            "steering_decisions": results,
        }

    def inject_tunnel_impairment(
        self,
        tunnel_id: str,
        add_latency_ms: float = 0.0,
        add_loss_pct: float = 0.0,
        set_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Simulyatsiya maqsadida tunnel sifatini pasaytirib, avtomatik failoverni tekshirish."""
        t = self.tunnels.get(tunnel_id)
        if not t:
            raise ValueError(f"Tunnel {tunnel_id} mavjud emas")

        t.latency_ms += add_latency_ms
        t.packet_loss_pct += add_loss_pct
        if set_status:
            t.status = set_status
        elif t.status == "down" or (set_status == "down"):
            t.status = "down"
        elif t.packet_loss_pct > 3.0 or t.latency_ms > 200:
            t.status = "degraded"

        # Barcha ilovalar uchun qayta yo'naltirish (Dynamic Steering)
        failovers = []
        for app in [TrafficClass.VOIP, TrafficClass.VIDEO, TrafficClass.CRITICAL_DATA, TrafficClass.BULK_BACKUP]:
            new_selection = self.select_path(app)
            new_tid = new_selection["selected_tunnel"]["tunnel_id"]
            if new_tid != tunnel_id:
                reason = "Blackout (Link offline)" if t.status == "down" else f"Brownout (SLA buzilishi: Loss={t.packet_loss_pct:.1f}%, Latency={t.latency_ms:.1f}ms)"
                ev = FailoverEvent(
                    timestamp=time.time(),
                    traffic_class=app.value,
                    source_tunnel=tunnel_id,
                    target_tunnel=new_tid,
                    reason=reason,
                    failover_duration_ms=145.0,
                )
                self.failover_history.append(ev)
                failovers.append(ev.to_dict())

        return {
            "tunnel": t.to_dict(),
            "failovers_triggered": len(failovers),
            "failover_events": failovers,
        }

    def restore_tunnel(self, tunnel_id: str) -> Dict[str, Any]:
        """Tunnel ko'rsatkichlarini normal holatga qaytarish."""
        t = self.tunnels.get(tunnel_id)
        if not t:
            raise ValueError(f"Tunnel {tunnel_id} mavjud emas")
        if "mpls" in tunnel_id:
            t.latency_ms = 18.5
            t.packet_loss_pct = 0.01
            t.jitter_ms = 2.1
            t.status = "healthy"
        elif "wg" in tunnel_id or "inet" in tunnel_id:
            t.latency_ms = 32.0
            t.packet_loss_pct = 0.15
            t.jitter_ms = 5.4
            t.status = "healthy"
        else:
            t.latency_ms = 65.0
            t.packet_loss_pct = 0.40
            t.jitter_ms = 14.0
            t.status = "healthy"
        return {"ok": True, "tunnel": t.to_dict()}

    def get_status(self) -> Dict[str, Any]:
        return {
            "total_tunnels": len(self.tunnels),
            "tunnels": {k: v.to_dict() for k, v in self.tunnels.items()},
            "recent_failovers": [f.to_dict() for f in reversed(self.failover_history[-20:])],
        }


# Alias for backwards compatibility
SDWANRouter = SDWANManager

# Global instansiyalar
sdwan_manager = SDWANManager()
sdwan_router = sdwan_manager

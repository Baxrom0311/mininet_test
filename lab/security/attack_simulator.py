"""Kibernetik Hujumlar Simulyatori va Xavfsizlik Ogohlantirishlari (Security Alert / IDS) menejeri.

Modellashtiriladigan 4 ta asosiy tarmoq hujumi:
1. TCP SYN Flood (Backlog navbatining to'lishi, dropped SYNs, CPU yuklanishi, SYN Cookies himoyasi).
2. UDP Amplification / Reflection (DNS / NTP yordamida 50x-70x+ kuchaytirish, qurbon kanalining to'lishi, dropped traffic).
3. Slowloris HTTP DoS (Sekin headerlar, ulanishlar pulining to'lishi, javob kechikishi, server mavjudligi).
4. ARP Cache Poisoning / MitM (Gratuitous ARP soxtalashtirish, paketlarni ushlash, Dynamic ARP Inspection (DAI) himoyasi).
"""

from __future__ import annotations

import datetime
import math
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SecurityAlert:
    """Strukturalangan IDS / Xavfsizlik ogohlantirishi."""
    id: str
    timestamp: float
    iso_time: str
    severity: str  # "critical", "high", "medium", "low", "info"
    attack_type: str  # "syn_flood", "udp_reflection", "slowloris", "arp_spoof", "ids_anomaly"
    source: str
    target: str
    description: str
    mitigation: str
    status: str = "active"  # "active", "mitigated", "investigating"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SecurityAlertManager:
    """Tizimdagi xavfsizlik ogohlantirishlarini yaratish, filtrlash va boshqarish menejeri."""

    def __init__(self, max_alerts: int = 1000):
        self.max_alerts = max_alerts
        self._alerts: List[SecurityAlert] = []

    def create_alert(
        self,
        severity: str,
        attack_type: str,
        source: str,
        target: str,
        description: str,
        mitigation: str,
        details: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> SecurityAlert:
        """Yangi xavfsizlik ogohlantirishi yaratadi va saqlaydi."""
        ts = now if now is not None else time.time()
        iso = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
        alert_id = f"alert_{uuid.uuid4().hex[:8]}"

        alert = SecurityAlert(
            id=alert_id,
            timestamp=ts,
            iso_time=iso,
            severity=severity.lower(),
            attack_type=attack_type.lower(),
            source=source,
            target=target,
            description=description,
            mitigation=mitigation,
            status="active",
            details=details or {},
        )

        self._alerts.insert(0, alert)
        if len(self._alerts) > self.max_alerts:
            self._alerts.pop()

        return alert

    def get_alerts(
        self,
        severity: Optional[str] = None,
        attack_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Ogohlantirishlarni qidirish va filtrlash."""
        filtered = self._alerts
        if severity:
            s_low = severity.lower()
            filtered = [a for a in filtered if a.severity == s_low]
        if attack_type:
            at_low = attack_type.lower()
            filtered = [a for a in filtered if a.attack_type == at_low]
        if status:
            st_low = status.lower()
            filtered = [a for a in filtered if a.status == st_low]

        safe_limit = max(1, min(int(limit), self.max_alerts))
        return [a.to_dict() for a in filtered[:safe_limit]]

    def get_alert_by_id(self, alert_id: str) -> Optional[Dict[str, Any]]:
        for a in self._alerts:
            if a.id == alert_id:
                return a.to_dict()
        return None

    def update_alert_status(self, alert_id: str, new_status: str) -> bool:
        for a in self._alerts:
            if a.id == alert_id:
                a.status = new_status
                return True
        return False

    def clear_alerts(self) -> None:
        self._alerts.clear()

    def get_summary_stats(self) -> Dict[str, Any]:
        """Ogohlantirishlar statistikasi."""
        stats = {
            "total": len(self._alerts),
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "by_type": {},
            "active": 0,
            "mitigated": 0,
        }

        for a in self._alerts:
            sev = a.severity
            if sev in stats:
                stats[sev] += 1
            stats["by_type"][a.attack_type] = stats["by_type"].get(a.attack_type, 0) + 1
            if a.status == "active":
                stats["active"] += 1
            elif a.status == "mitigated":
                stats["mitigated"] += 1

        return stats


class AttackSimulator:
    """Tarmoq kibernetik hujumlarini modellashtirish va tahlil qilish dvigateli."""

    def __init__(self, alert_manager: Optional[SecurityAlertManager] = None):
        self.alert_manager = alert_manager or SecurityAlertManager()

    # ──────────────────────── 1. TCP SYN Flood ────────────────────────
    def simulate_syn_flood(
        self,
        target_ip: str,
        target_port: int = 80,
        pps: int = 10000,
        duration_s: float = 10.0,
        syn_cookies_enabled: bool = False,
        backlog_size: int = 1024,
    ) -> Dict[str, Any]:
        """TCP SYN Flood hujumini simulyatsiya qiladi.

        Parametrlar:
            target_ip: Nishon server IP manzili
            target_port: Nishon port (masalan 80, 443)
            pps: Sekundiga yuboriladigan soxta SYN paketlar tezligi
            duration_s: Hujum davomiyligi (sekund)
            syn_cookies_enabled: TCP SYN Cookies yoqilganmi
            backlog_size: Nishon kernelining SYN backlog navbati sig'imi (masalan 1024)
        """
        pps = max(10, int(pps))
        duration_s = max(1.0, float(duration_s))
        backlog_size = max(128, int(backlog_size))

        total_syn_packets = int(pps * duration_s)

        # Vaqt seriyasi tahlili
        steps = min(20, int(duration_s))
        time_series = []
        dt = duration_s / max(1, steps)

        current_backlog_used = 0
        total_dropped_syns = 0

        for step in range(steps + 1):
            t = round(step * dt, 2)
            incoming_this_step = int(pps * dt)

            if syn_cookies_enabled:
                # SYN Cookies yoqilganda yarim ochiq TCB xotira band qilinmaydi,
                # sequence raqami kriptografik hisoblanadi.
                current_backlog_used = min(backlog_size, int(backlog_size * 0.12))
                dropped_this_step = 0
                cpu = min(68.0, 18.0 + (pps / 400.0) + (step * 0.5))
                avail = max(95.0, 99.0 - (pps / 50000.0))
            else:
                # SYN Cookies o'chirilgan bo'lsa navbat tezda to'ladi
                space_left = max(0, backlog_size - current_backlog_used)
                accepted = min(space_left, incoming_this_step)
                current_backlog_used += accepted
                dropped_this_step = incoming_this_step - accepted
                total_dropped_syns += dropped_this_step

                exhausted = current_backlog_used >= backlog_size
                cpu = min(99.0, 48.0 + (pps / 180.0) + (10.0 if exhausted else 0.0))
                avail = 0.0 if exhausted else max(5.0, 100.0 * (1.0 - (current_backlog_used / backlog_size)))

            time_series.append({
                "time_s": t,
                "incoming_pps": pps,
                "backlog_used": current_backlog_used,
                "dropped_syns": total_dropped_syns if not syn_cookies_enabled else 0,
                "cpu_utilization_pct": round(cpu, 1),
                "server_availability_pct": round(avail, 1),
            })

        backlog_exhausted = (not syn_cookies_enabled) and (total_syn_packets > backlog_size)
        dropped_syns = max(0, total_syn_packets - backlog_size) if not syn_cookies_enabled else 0
        cpu_impact = time_series[-1]["cpu_utilization_pct"]
        server_availability = time_series[-1]["server_availability_pct"]

        if syn_cookies_enabled:
            mitigation_stats = {
                "active": True,
                "mitigation_type": "TCP SYN Cookies (net.ipv4.tcp_syncookies = 1)",
                "cookies_generated": total_syn_packets,
                "backlog_preservation_pct": 100.0,
                "summary": "SYN Cookies faol: Half-open backlog to'lib ketmadi, qonuniy ulanishlar qabul qilindi.",
            }
            alert = self.alert_manager.create_alert(
                severity="medium",
                attack_type="syn_flood",
                source="Distributed SYN Botnet",
                target=f"{target_ip}:{target_port}",
                description=f"SYN Flood hujumi aniqlandi ({pps} PPS, {duration_s}s). TCP SYN Cookies faol bo'lganligi sababli xizmat uzilishsiz ishladi.",
                mitigation="SYN Cookies faol. Perimetrda XDP SYNPROXY yoki iptables rate-limiting joriy qilish orqali CPU sarfini kamaytirish tavsiya etiladi.",
                details={"pps": pps, "cookies_generated": total_syn_packets, "cpu_pct": cpu_impact},
            )
        else:
            mitigation_stats = {
                "active": False,
                "mitigation_type": "None (SYN Backlog vulnerable)",
                "cookies_generated": 0,
                "backlog_preservation_pct": 0.0,
                "summary": "Backlog navbati to'ldi va yangi ulanishlar tashlab yuborildi (100% DoS).",
            }
            alert = self.alert_manager.create_alert(
                severity="critical",
                attack_type="syn_flood",
                source="Distributed SYN Botnet",
                target=f"{target_ip}:{target_port}",
                description=f"Kritik TCP SYN Flood hujumi: {target_ip}:{target_port} ga {pps} PPS oqimi. Backlog navbati ({backlog_size}) to'lib, {dropped_syns} paket tashlandi.",
                mitigation="Zudlik bilan 'sysctl -w net.ipv4.tcp_syncookies=1' ni yoqing, 'net.ipv4.tcp_max_syn_backlog' ni oshiring va iptables/nftables da SYN rate limiting joriy eting.",
                details={"pps": pps, "dropped_syns": dropped_syns, "cpu_pct": cpu_impact, "backlog_size": backlog_size},
            )

        return {
            "attack_type": "syn_flood",
            "target_ip": target_ip,
            "target_port": target_port,
            "pps": pps,
            "duration_s": duration_s,
            "total_packets": total_syn_packets,
            "backlog_size": backlog_size,
            "backlog_exhausted": backlog_exhausted,
            "dropped_syns": dropped_syns,
            "cpu_impact_pct": cpu_impact,
            "server_availability_pct": server_availability,
            "syn_cookies_enabled": syn_cookies_enabled,
            "mitigation_stats": mitigation_stats,
            "alert_generated": alert.to_dict(),
            "time_series": time_series,
        }

    # ──────────────────────── 2. UDP Reflection / Amplification ────────────────────────
    def simulate_udp_reflection(
        self,
        victim_ip: str,
        reflector_type: str = "dns",
        request_pps: int = 1000,
        duration_s: float = 10.0,
        pipe_capacity_mbps: float = 100.0,
    ) -> Dict[str, Any]:
        """UDP Amplification / Reflection hujumini simulyatsiya qiladi.

        Parametrlar:
            victim_ip: Jabrlanuvchi qurbon IP manzili
            reflector_type: Kuchaytiruvchi protokol ("dns", "ntp", "snmp", "ssdp")
            request_pps: Hujumchi tomonidan ochiq serverlarga jo'natiladigan soxta so'rovlar soni
            duration_s: Hujum davomiyligi (sekund)
            pipe_capacity_mbps: Qurbonning internet kanalining o'tkazuvchanlik quvvati (Mbps)
        """
        ref_type = reflector_type.lower()
        request_pps = max(10, int(request_pps))
        duration_s = max(1.0, float(duration_s))
        pipe_capacity_mbps = max(1.0, float(pipe_capacity_mbps))

        # Protokol bo'yicha kuchaytirish koeffitsienti va bayt hajmlari
        reflector_profiles = {
            "dns": {"req_size": 64, "resp_size": 3840, "ratio": 60.0, "port": 53, "query": "ANY / EDNS0"},
            "ntp": {"req_size": 48, "resp_size": 9600, "ratio": 200.0, "port": 123, "query": "monlist"},
            "snmp": {"req_size": 60, "resp_size": 1500, "ratio": 25.0, "port": 161, "query": "GetBulk"},
            "ssdp": {"req_size": 100, "resp_size": 3000, "ratio": 30.0, "port": 1900, "query": "M-SEARCH"},
        }

        profile = reflector_profiles.get(ref_type, reflector_profiles["dns"])
        amp_ratio = profile["ratio"]
        req_size = profile["req_size"]
        resp_size = profile["resp_size"]

        # Tezliklar hisobi
        outbound_request_mbps = (request_pps * req_size * 8.0) / 1_000_000.0
        inbound_reflected_mbps = outbound_request_mbps * amp_ratio
        pipe_saturated = inbound_reflected_mbps >= pipe_capacity_mbps
        dropped_traffic_mbps = max(0.0, inbound_reflected_mbps - pipe_capacity_mbps)
        victim_packet_loss_pct = (
            min(100.0, (dropped_traffic_mbps / inbound_reflected_mbps) * 100.0) if pipe_saturated else 0.0
        )

        steps = min(20, int(duration_s))
        dt = duration_s / max(1, steps)
        time_series = []

        for step in range(steps + 1):
            t = round(step * dt, 2)
            # Bir oz tabiiy jitter qo'shish
            jitter = 1.0 + (0.05 * math.sin(step))
            cur_inbound = inbound_reflected_mbps * jitter
            cur_dropped = max(0.0, cur_inbound - pipe_capacity_mbps)
            cur_loss = (cur_dropped / cur_inbound * 100.0) if cur_inbound > pipe_capacity_mbps else 0.0

            time_series.append({
                "time_s": t,
                "outbound_pps": request_pps,
                "inbound_mbps": round(cur_inbound, 2),
                "pipe_capacity_mbps": pipe_capacity_mbps,
                "dropped_mbps": round(cur_dropped, 2),
                "packet_loss_pct": round(cur_loss, 1),
            })

        severity = "critical" if pipe_saturated else "high"
        alert = self.alert_manager.create_alert(
            severity=severity,
            attack_type="udp_reflection",
            source=f"Reflected {ref_type.upper()} ({profile['query']}) via Port {profile['port']}",
            target=victim_ip,
            description=(
                f"UDP {ref_type.upper()} Reflection DDoS hujumi: Kuchaytirish koeffitsienti {amp_ratio}x. "
                f"Qurbon manziliga kiruvchi trafik {inbound_reflected_mbps:.1f} Mbps (Kanal: {pipe_capacity_mbps:.1f} Mbps). "
                f"Yo'qotish: {victim_packet_loss_pct:.1f}%."
            ),
            mitigation="Provayder darajasida BCP 38 / uRPF (Unicast Reverse Path Forwarding) ni talab qiling, UDP portiga Response Rate Limiting (RRL) qo'ying yoki BGP Flowspec orqali tozalashga yo'naltiring.",
            details={
                "amplification_ratio": amp_ratio,
                "inbound_mbps": round(inbound_reflected_mbps, 2),
                "pipe_capacity_mbps": pipe_capacity_mbps,
                "pipe_saturated": pipe_saturated,
            },
        )

        return {
            "attack_type": "udp_reflection",
            "victim_ip": victim_ip,
            "reflector_type": ref_type,
            "reflector_port": profile["port"],
            "amplification_ratio": amp_ratio,
            "request_pps": request_pps,
            "duration_s": duration_s,
            "outbound_request_mbps": round(outbound_request_mbps, 3),
            "inbound_reflected_mbps": round(inbound_reflected_mbps, 2),
            "pipe_capacity_mbps": pipe_capacity_mbps,
            "pipe_saturated": pipe_saturated,
            "dropped_traffic_mbps": round(dropped_traffic_mbps, 2),
            "victim_packet_loss_pct": round(victim_packet_loss_pct, 1),
            "alert_generated": alert.to_dict(),
            "time_series": time_series,
        }

    # ──────────────────────── 3. Slowloris HTTP DoS ────────────────────────
    def simulate_slowloris(
        self,
        target_ip: str,
        port: int = 80,
        connections: int = 200,
        header_interval_s: float = 10.0,
        server_max_connections: int = 150,
    ) -> Dict[str, Any]:
        """Slowloris sekin headerlar hujumini modellashtiradi.

        Parametrlar:
            target_ip: Nishon veb-server IP manzili
            port: Nishon port (masalan 80)
            connections: Ochiladigan bir vaqtdagi soxta sekin ulanishlar soni
            header_interval_s: Har bir noaniq header yuborish oralig'i (keep-alive)
            server_max_connections: Serverning maksimal bir vaqtdagi ulanishlar chegarasi (worker pool)
        """
        connections = max(1, int(connections))
        server_max_connections = max(10, int(server_max_connections))
        header_interval_s = max(1.0, float(header_interval_s))

        pool_exhausted = connections >= server_max_connections
        pool_utilization_pct = min(100.0, (connections / server_max_connections) * 100.0)

        if pool_exhausted:
            available_slots = 0
            response_delay_ms = 30000.0  # Server timeout
            server_availability_pct = 0.0
            sev = "critical"
        else:
            available_slots = server_max_connections - connections
            response_delay_ms = 25.0 + (connections * 7.5)
            server_availability_pct = round(((server_max_connections - connections) / server_max_connections) * 100.0, 1)
            sev = "high" if pool_utilization_pct > 75 else "medium"

        steps = 15
        time_series = []
        ramp_speed = connections / max(1, steps * 0.6)

        for step in range(steps + 1):
            active_c = min(connections, int(step * ramp_speed))
            util = min(100.0, (active_c / server_max_connections) * 100.0)
            if active_c >= server_max_connections:
                delay = 30000.0
                avail = 0.0
            else:
                delay = 25.0 + (active_c * 7.5)
                avail = max(0.0, 100.0 - util)

            time_series.append({
                "time_s": round(step * (header_interval_s / steps * 2), 1),
                "active_slow_connections": active_c,
                "pool_utilization_pct": round(util, 1),
                "response_delay_ms": round(delay, 1),
                "server_availability_pct": round(avail, 1),
            })

        alert = self.alert_manager.create_alert(
            severity=sev,
            attack_type="slowloris",
            source="Slowloris HTTP Client Pool",
            target=f"{target_ip}:{port}",
            description=(
                f"Slowloris DoS hujumi aniqlandi: {connections} ta ulanish har {header_interval_s}s da chala header jo'natmoqda. "
                f"Worker pool bandligi: {pool_utilization_pct:.1f}% ({connections}/{server_max_connections}). "
                f"Qonuniy so'rovlar kechikishi: {response_delay_ms:.0f} ms."
            ),
            mitigation="Veb-serverda mod_reqtimeout (RequestReadTimeout header=10-20,MinRate=500) ni sozlang, yoki hodisalarga asoslangan (event-driven) NGINX/HAProxy teskari proksi o'rnating.",
            details={
                "connections": connections,
                "server_max_connections": server_max_connections,
                "pool_exhausted": pool_exhausted,
                "response_delay_ms": response_delay_ms,
            },
        )

        return {
            "attack_type": "slowloris",
            "target_ip": target_ip,
            "port": port,
            "connections": connections,
            "header_interval_s": header_interval_s,
            "server_max_connections": server_max_connections,
            "pool_exhausted": pool_exhausted,
            "pool_utilization_pct": round(pool_utilization_pct, 1),
            "available_slots": available_slots,
            "response_delay_ms": response_delay_ms,
            "server_availability_pct": server_availability_pct,
            "alert_generated": alert.to_dict(),
            "time_series": time_series,
        }

    # ──────────────────────── 4. ARP Spoofing / MitM ────────────────────────
    def simulate_arp_spoof(
        self,
        target_ip: str,
        gateway_ip: str,
        attacker_ip: str,
        duration_s: float = 10.0,
        attacker_mac: str = "00:11:22:33:44:aa",
        dai_enabled: bool = False,
    ) -> Dict[str, Any]:
        """ARP Cache Poisoning va MitM (Man-in-the-Middle) hujumini modellashtiradi.

        Parametrlar:
            target_ip: Qurbon qurilma IP manzili
            gateway_ip: Shlyuz (Gateway) IP manzili
            attacker_ip: Hujumchi qurilma IP manzili
            duration_s: Hujum davomiyligi (sekund)
            attacker_mac: Hujumchining soxta MAC manzili
            dai_enabled: Dynamic ARP Inspection (DAI) va DHCP Snooping yoqilganmi
        """
        duration_s = max(1.0, float(duration_s))

        if dai_enabled:
            # DAI yoqilganda kommutator ARP javoblarini DHCP Snooping bazasi bilan tekshiradi.
            # Mismatch aniqlanadi va soxta paketlar tashlanadi.
            arp_cache_poisoned = False
            mitm_active = False
            intercepted_packets = 0
            dai_alert_generated = True
            dai_message = (
                f"DAI (Dynamic ARP Inspection) ALERT: Port Eth0/2 dan noqonuniy gratuitous ARP ushlandi. "
                f"MAC {attacker_mac} manzili {gateway_ip} IP manzilini da'vo qilmoqda (DHCP Snooping database mismatch). "
                f"Paket tashlab yuborildi."
            )
            alert = self.alert_manager.create_alert(
                severity="medium",
                attack_type="arp_spoof",
                source=f"{attacker_ip} ({attacker_mac})",
                target=f"{target_ip} / {gateway_ip}",
                description=f"ARP Spoofing urinishi DAI (Dynamic ARP Inspection) tomonidan bartaraf etildi. Kommutator soxta ARP paketlarni blokladi.",
                mitigation="DAI muvaffaqiyatli himoya qildi. Hujumchi ulangan Eth0/2 portini tekshiring va 802.1X port-security orqali qurilmani karantinga oling.",
                details={"dai_enabled": True, "dropped_mac": attacker_mac, "claimed_ip": gateway_ip},
            )
        else:
            # DAI yo'q bo'lsa qurbon va shlyuz keshiga hujumchining MAC manzili yoziladi.
            arp_cache_poisoned = True
            mitm_active = True
            intercepted_packets = int(duration_s * 42)
            dai_alert_generated = False
            dai_message = "DAI o'chirilgan: Soxta ARP xabarlari qurbon keshiga muvaffaqiyatli yozildi."

            alert = self.alert_manager.create_alert(
                severity="critical",
                attack_type="arp_spoof",
                source=f"{attacker_ip} ({attacker_mac})",
                target=f"{target_ip} <-> {gateway_ip}",
                description=(
                    f"Kritik ARP Cache Poisoning / MitM hujumi faol! Hujumchi {attacker_ip} ({attacker_mac}) "
                    f"o'zini {gateway_ip} sifatida ko'rsatib, barcha trafigini ({intercepted_packets} paket) ushlab olmoqda."
                ),
                mitigation="Kommutatorlarda Dynamic ARP Inspection (DAI) va DHCP Snooping funksiyalarini zudlik bilan yoqing yoki statik ARP jadvallarini qo'llang.",
                details={
                    "dai_enabled": False,
                    "target_ip": target_ip,
                    "gateway_ip": gateway_ip,
                    "intercepted_packets": intercepted_packets,
                },
            )

        steps = min(10, int(duration_s))
        dt = duration_s / max(1, steps)
        time_series = []

        for step in range(steps + 1):
            t = round(step * dt, 1)
            time_series.append({
                "time_s": t,
                "arp_cache_poisoned": arp_cache_poisoned,
                "mitm_active": mitm_active,
                "intercepted_packets_cumulative": int(step * (intercepted_packets / max(1, steps))) if mitm_active else 0,
                "dai_blocked_packets": int(step * 2) if dai_enabled else 0,
            })

        return {
            "attack_type": "arp_spoof",
            "target_ip": target_ip,
            "gateway_ip": gateway_ip,
            "attacker_ip": attacker_ip,
            "attacker_mac": attacker_mac,
            "duration_s": duration_s,
            "arp_cache_poisoned": arp_cache_poisoned,
            "mitm_active": mitm_active,
            "intercepted_packets": intercepted_packets,
            "dai_enabled": dai_enabled,
            "dai_alert_generated": dai_alert_generated,
            "dai_alert_message": dai_message,
            "alert_generated": alert.to_dict(),
            "time_series": time_series,
        }

    # ──────────────────────── Umumiy chaqiruv usuli (Dispatch) ────────────────────────
    def simulate(self, attack_type: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Istalgan turdagi hujumni parametrlar orqali ishga tushirish."""
        p = params or {}
        at = attack_type.lower()

        if at in ("syn_flood", "tcp_syn_flood"):
            return self.simulate_syn_flood(
                target_ip=p.get("target_ip", "10.0.2.10"),
                target_port=int(p.get("target_port", 80)),
                pps=int(p.get("pps", 10000)),
                duration_s=float(p.get("duration_s", 10.0)),
                syn_cookies_enabled=bool(p.get("syn_cookies_enabled", False)),
                backlog_size=int(p.get("backlog_size", 1024)),
            )

        elif at in ("udp_reflection", "amplification", "udp_amplification"):
            return self.simulate_udp_reflection(
                victim_ip=p.get("victim_ip", "10.0.2.10"),
                reflector_type=p.get("reflector_type", "dns"),
                request_pps=int(p.get("request_pps", 1000)),
                duration_s=float(p.get("duration_s", 10.0)),
                pipe_capacity_mbps=float(p.get("pipe_capacity_mbps", 100.0)),
            )

        elif at in ("slowloris", "slow_loris", "slow_http"):
            return self.simulate_slowloris(
                target_ip=p.get("target_ip", "10.0.2.10"),
                port=int(p.get("port", 80)),
                connections=int(p.get("connections", 200)),
                header_interval_s=float(p.get("header_interval_s", 10.0)),
                server_max_connections=int(p.get("server_max_connections", 150)),
            )

        elif at in ("arp_spoof", "arp_poison", "mitm"):
            return self.simulate_arp_spoof(
                target_ip=p.get("target_ip", "10.0.1.10"),
                gateway_ip=p.get("gateway_ip", "10.0.1.1"),
                attacker_ip=p.get("attacker_ip", "10.0.1.50"),
                duration_s=float(p.get("duration_s", 10.0)),
                attacker_mac=p.get("attacker_mac", "00:11:22:33:44:aa"),
                dai_enabled=bool(p.get("dai_enabled", False)),
            )

        else:
            raise ValueError(f"Noma'lum hujum turi '{attack_type}'. Qabul qilinadi: syn_flood, udp_reflection, slowloris, arp_spoof")

"""Connection Tracking (Conntrack) dvigateli va Stateful Firewall modeli.

Ushbu modul Netfilter / Linux conntrack xatti-harakatlarini modellashtiradi:
- TCP ulanish holatlari mashinasi (NEW, SYN_SENT, SYN_RECV, ESTABLISHED, FIN_WAIT, CLOSE_WAIT, LAST_ACK, TIME_WAIT, CLOSE, INVALID)
- UDP va ICMP tracking (UNREPLIED / ASSURED / REPLIED, ICMP xatoliklar RELATED holatida)
- Original va Reply tuple'lar, NAT (SNAT/DNAT/Masquerade) mappinglari
- Stateful firewall qoidalarini conntrack holati bo'yicha baholash
- Linux /proc/net/nf_conntrack va conntrack -L parseri hamda to'liq synthetic generator
"""

from __future__ import annotations

import copy
import ipaddress
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# TCP conntrack holatlari
TCP_STATE_NONE = "NONE"
TCP_STATE_SYN_SENT = "SYN_SENT"
TCP_STATE_SYN_RECV = "SYN_RECV"
TCP_STATE_ESTABLISHED = "ESTABLISHED"
TCP_STATE_FIN_WAIT = "FIN_WAIT"
TCP_STATE_CLOSE_WAIT = "CLOSE_WAIT"
TCP_STATE_LAST_ACK = "LAST_ACK"
TCP_STATE_TIME_WAIT = "TIME_WAIT"
TCP_STATE_CLOSE = "CLOSE"
TCP_STATE_INVALID = "INVALID"

# Netfilter yuqori darajadagi firewall holatlari
FW_STATE_NEW = "NEW"
FW_STATE_ESTABLISHED = "ESTABLISHED"
FW_STATE_RELATED = "RELATED"
FW_STATE_INVALID = "INVALID"

# Standart timeoutlar (sekundlarda)
DEFAULT_TIMEOUTS = {
    "tcp_syn_sent": 120,
    "tcp_syn_recv": 60,
    "tcp_established": 432000,
    "tcp_fin_wait": 120,
    "tcp_close_wait": 60,
    "tcp_last_ack": 30,
    "tcp_time_wait": 120,
    "tcp_close": 10,
    "udp_unreplied": 30,
    "udp_assured": 180,
    "icmp_unreplied": 30,
    "icmp_replied": 30,
    "generic": 600,
}


@dataclass(frozen=True)
class ConnectionTuple:
    """5-elementli ulanish tuple'i (src_ip, src_port, dst_ip, dst_port, proto)."""
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    proto: str

    def reversed(self) -> ConnectionTuple:
        """Teskari (reply) yo'nalishdagi tuple hosil qiladi."""
        return ConnectionTuple(
            src_ip=self.dst_ip,
            src_port=self.dst_port,
            dst_ip=self.src_ip,
            dst_port=self.src_port,
            proto=self.proto,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src_ip": self.src_ip,
            "src_port": self.src_port,
            "dst_ip": self.dst_ip,
            "dst_port": self.dst_port,
            "proto": self.proto,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConnectionTuple:
        return cls(
            src_ip=str(data.get("src_ip", "0.0.0.0")),
            src_port=int(data.get("src_port", 0)),
            dst_ip=str(data.get("dst_ip", "0.0.0.0")),
            dst_port=int(data.get("dst_port", 0)),
            proto=str(data.get("proto", "tcp")).lower(),
        )


@dataclass
class NATMapping:
    """NAT (SNAT / DNAT / Masquerade) tarjimasi ma'lumotlari."""
    enabled: bool = False
    nat_type: str = "none"  # "snat", "dnat", "masquerade"
    original_src: str = ""
    original_sport: int = 0
    original_dst: str = ""
    original_dport: int = 0
    translated_src: str = ""
    translated_sport: int = 0
    translated_dst: str = ""
    translated_dport: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "nat_type": self.nat_type,
            "original_src": self.original_src,
            "original_sport": self.original_sport,
            "original_dst": self.original_dst,
            "original_dport": self.original_dport,
            "translated_src": self.translated_src,
            "translated_sport": self.translated_sport,
            "translated_dst": self.translated_dst,
            "translated_dport": self.translated_dport,
        }


@dataclass
class ConntrackEntry:
    """Conntrack jadvalidagi alohida sessiya / ulanish yozuvi."""
    id: str
    orig_tuple: ConnectionTuple
    reply_tuple: ConnectionTuple
    proto: str
    tcp_state: Optional[str] = None
    fw_state: str = FW_STATE_NEW
    status: List[str] = field(default_factory=list)  # ["SEEN_REPLY", "ASSURED", ...]
    nat: Optional[NATMapping] = None
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    timeout_s: float = 300.0
    expires_at: float = field(default_factory=lambda: time.time() + 300.0)
    packets_orig: int = 0
    bytes_orig: int = 0
    packets_reply: int = 0
    bytes_reply: int = 0
    mark: int = 0
    use: int = 1

    @property
    def is_assured(self) -> bool:
        return "ASSURED" in self.status

    @property
    def seen_reply(self) -> bool:
        return "SEEN_REPLY" in self.status

    def remaining_ttl(self, now: Optional[float] = None) -> float:
        current = now if now is not None else time.time()
        return max(0.0, round(self.expires_at - current, 2))

    def to_dict(self, now: Optional[float] = None) -> Dict[str, Any]:
        return {
            "id": self.id,
            "proto": self.proto,
            "tcp_state": self.tcp_state,
            "fw_state": self.fw_state,
            "status": list(self.status),
            "orig": self.orig_tuple.to_dict(),
            "reply": self.reply_tuple.to_dict(),
            "nat": self.nat.to_dict() if self.nat else None,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "timeout_s": self.timeout_s,
            "remaining_ttl": self.remaining_ttl(now),
            "packets_orig": self.packets_orig,
            "bytes_orig": self.bytes_orig,
            "packets_reply": self.packets_reply,
            "bytes_reply": self.bytes_reply,
            "total_packets": self.packets_orig + self.packets_reply,
            "total_bytes": self.bytes_orig + self.bytes_reply,
            "mark": self.mark,
            "use": self.use,
        }


class ConntrackEngine:
    """Linux Netfilter modeliga mos Stateful Connection Tracking dvigateli."""

    def __init__(self, timeouts: Optional[Dict[str, float]] = None, strict_tcp: bool = True):
        self.timeouts = dict(DEFAULT_TIMEOUTS)
        if timeouts:
            self.timeouts.update(timeouts)
        self.strict_tcp = strict_tcp

        # Asosiy jadvallar: tuple -> ConntrackEntry
        self._orig_table: Dict[ConnectionTuple, ConntrackEntry] = {}
        self._reply_table: Dict[ConnectionTuple, ConntrackEntry] = {}
        self._entries_by_id: Dict[str, ConntrackEntry] = {}

    def clear(self) -> None:
        """Barcha ulanishlarni tozalash."""
        self._orig_table.clear()
        self._reply_table.clear()
        self._entries_by_id.clear()

    @property
    def count(self) -> int:
        return len(self._entries_by_id)

    # ──────────────────────── Paket tahlili va normalizatsiyasi ────────────────────────
    def _normalize_packet(self, pkt: Dict[str, Any]) -> Dict[str, Any]:
        proto = str(pkt.get("proto", "tcp")).lower()
        src_ip = str(pkt.get("src_ip", pkt.get("src", "0.0.0.0")))
        dst_ip = str(pkt.get("dst_ip", pkt.get("dst", "0.0.0.0")))
        src_port = int(pkt.get("src_port", pkt.get("sport", 0)) or 0)
        dst_port = int(pkt.get("dst_port", pkt.get("dport", 0)) or 0)

        # Bayroqlar (flags)
        flags = pkt.get("flags") or {}
        if isinstance(flags, str):
            flags_str = flags.upper()
            flags = {
                "syn": "SYN" in flags_str or "S" in flags_str,
                "ack": "ACK" in flags_str or "A" in flags_str,
                "fin": "FIN" in flags_str or "F" in flags_str,
                "rst": "RST" in flags_str or "R" in flags_str,
                "psh": "PSH" in flags_str or "P" in flags_str,
                "urg": "URG" in flags_str or "U" in flags_str,
            }

        length = int(pkt.get("length", pkt.get("bytes", pkt.get("payload_bytes", 64))) or 64)

        return {
            "proto": proto,
            "src_ip": src_ip,
            "src_port": src_port,
            "dst_ip": dst_ip,
            "dst_port": dst_port,
            "flags": flags,
            "length": length,
            "chain": pkt.get("chain", "forward"),
            "in_if": pkt.get("in_if", pkt.get("in")),
            "out_if": pkt.get("out_if", pkt.get("out")),
            "icmp_type": pkt.get("icmp_type"),
            "icmp_code": pkt.get("icmp_code"),
            "icmp_id": pkt.get("icmp_id", pkt.get("id", src_port or 1)),
            "inner_pkt": pkt.get("inner_pkt"),
            "nat_action": pkt.get("nat_action"),  # ixtiyoriy paket darajasidagi SNAT/DNAT
        }

    # ──────────────────────── Paketni qayta ishlash ────────────────────────
    def process_packet(
        self, pkt: Dict[str, Any], now: Optional[float] = None
    ) -> Tuple[bool, Optional[ConntrackEntry], str]:
        """Paketni qabul qilib, sessiya holatini yangilaydi.

        Qaytaradi:
            (is_valid: bool, entry: ConntrackEntry | None, fw_state: str)
            fw_state: "NEW", "ESTABLISHED", "RELATED", yoki "INVALID".
        """
        current_time = now if now is not None else time.time()
        p = self._normalize_packet(pkt)
        proto = p["proto"]

        # 1. ICMP Error tekshiruvi (RELATED holatini aniqlash)
        if proto == "icmp" and p.get("icmp_type") in (3, 11):  # Dest Unreachable, Time Exceeded
            inner = p.get("inner_pkt")
            if inner:
                inner_norm = self._normalize_packet(inner)
                inner_tuple = ConnectionTuple(
                    src_ip=inner_norm["src_ip"],
                    src_port=inner_norm["src_port"],
                    dst_ip=inner_norm["dst_ip"],
                    dst_port=inner_norm["dst_port"],
                    proto=inner_norm["proto"],
                )
                existing = self._orig_table.get(inner_tuple) or self._reply_table.get(inner_tuple)
                if existing:
                    existing.last_seen = current_time
                    return True, existing, FW_STATE_RELATED

        # 2. Ulanish tuple'ini tuzish
        pkt_tuple = ConnectionTuple(
            src_ip=p["src_ip"],
            src_port=p["src_port"],
            dst_ip=p["dst_ip"],
            dst_port=p["dst_port"],
            proto=proto,
        )

        # 3. Mavjud ulanishlarni qidirish (orig yoki reply)
        is_orig = False
        is_reply = False
        entry = self._orig_table.get(pkt_tuple)
        if entry:
            is_orig = True
        else:
            entry = self._reply_table.get(pkt_tuple)
            if entry:
                is_reply = True

        # 4. Agar ulanish mavjud bo'lmasa -> Yangi sessiya boshlash
        if not entry:
            return self._handle_new_connection(p, pkt_tuple, current_time)

        # 5. Mavjud ulanish bo'lsa -> Holat o'tishlari (State Transitions)
        return self._handle_existing_connection(entry, p, is_orig, is_reply, current_time)

    # ──────────────────────── Yangi ulanishni ochish ────────────────────────
    def _handle_new_connection(
        self, p: Dict[str, Any], pkt_tuple: ConnectionTuple, now: float
    ) -> Tuple[bool, Optional[ConntrackEntry], str]:
        proto = p["proto"]
        flags = p["flags"]

        # TCP uchun qat'iy tekshiruv: faqat SYN=True va ACK=False yangi ulanish ochishi mumkin
        if proto == "tcp":
            is_syn = flags.get("syn", False)
            is_ack = flags.get("ack", False)
            is_rst = flags.get("rst", False)
            is_fin = flags.get("fin", False)

            if not is_syn or is_ack or is_rst or is_fin:
                if self.strict_tcp:
                    return False, None, FW_STATE_INVALID

            tcp_state = TCP_STATE_SYN_SENT
            timeout = self.timeouts["tcp_syn_sent"]
            status = []

        elif proto == "udp":
            tcp_state = None
            timeout = self.timeouts["udp_unreplied"]
            status = ["UNREPLIED"]

        elif proto == "icmp":
            icmp_type = p.get("icmp_type")
            if icmp_type is not None and icmp_type not in (8, 0):  # Echo emas
                return False, None, FW_STATE_INVALID
            tcp_state = None
            timeout = self.timeouts["icmp_unreplied"]
            status = ["UNREPLIED"]

        else:
            tcp_state = None
            timeout = self.timeouts["generic"]
            status = ["UNREPLIED"]

        # Reply tuple hisoblash (NAT hisobga olingan holda)
        nat_mapping = self._create_nat_mapping(p, pkt_tuple)
        reply_tuple = self._calculate_reply_tuple(pkt_tuple, nat_mapping)

        entry_id = f"ct_{uuid.uuid4().hex[:8]}"
        entry = ConntrackEntry(
            id=entry_id,
            orig_tuple=pkt_tuple,
            reply_tuple=reply_tuple,
            proto=proto,
            tcp_state=tcp_state,
            fw_state=FW_STATE_NEW,
            status=status,
            nat=nat_mapping if nat_mapping.enabled else None,
            created_at=now,
            last_seen=now,
            timeout_s=timeout,
            expires_at=now + timeout,
            packets_orig=1,
            bytes_orig=p["length"],
            packets_reply=0,
            bytes_reply=0,
        )

        # Jadvallarga saqlash
        self._orig_table[pkt_tuple] = entry
        self._reply_table[reply_tuple] = entry
        self._entries_by_id[entry_id] = entry

        return True, entry, FW_STATE_NEW

    def _create_nat_mapping(self, p: Dict[str, Any], orig: ConnectionTuple) -> NATMapping:
        nat_act = p.get("nat_action") or {}
        if not nat_act:
            return NATMapping()

        nat_type = nat_act.get("type", "none").lower()
        if nat_type in ("snat", "masquerade"):
            trans_src = nat_act.get("to_src", orig.src_ip)
            trans_sport = int(nat_act.get("to_sport", orig.src_port))
            return NATMapping(
                enabled=True,
                nat_type=nat_type,
                original_src=orig.src_ip,
                original_sport=orig.src_port,
                original_dst=orig.dst_ip,
                original_dport=orig.dst_port,
                translated_src=trans_src,
                translated_sport=trans_sport,
                translated_dst=orig.dst_ip,
                translated_dport=orig.dst_port,
            )
        elif nat_type == "dnat":
            trans_dst = nat_act.get("to_dst", orig.dst_ip)
            trans_dport = int(nat_act.get("to_dport", orig.dst_port))
            return NATMapping(
                enabled=True,
                nat_type="dnat",
                original_src=orig.src_ip,
                original_sport=orig.src_port,
                original_dst=orig.dst_ip,
                original_dport=orig.dst_port,
                translated_src=orig.src_ip,
                translated_sport=orig.src_port,
                translated_dst=trans_dst,
                translated_dport=trans_dport,
            )
        return NATMapping()

    def _calculate_reply_tuple(self, orig: ConnectionTuple, nat: NATMapping) -> ConnectionTuple:
        if not nat.enabled:
            return orig.reversed()

        if nat.nat_type in ("snat", "masquerade"):
            # SNAT da server javobni translated_src ga qaytaradi
            return ConnectionTuple(
                src_ip=orig.dst_ip,
                src_port=orig.dst_port,
                dst_ip=nat.translated_src,
                dst_port=nat.translated_sport,
                proto=orig.proto,
            )
        elif nat.nat_type == "dnat":
            # DNAT da ichki server asl mijozga qaytaradi, javob src_ip translated_dst bo'ladi
            return ConnectionTuple(
                src_ip=nat.translated_dst,
                src_port=nat.translated_dport,
                dst_ip=orig.src_ip,
                dst_port=orig.src_port,
                proto=orig.proto,
            )
        return orig.reversed()

    # ──────────────────────── Mavjud ulanish holatini yangilash ────────────────────────
    def _handle_existing_connection(
        self,
        entry: ConntrackEntry,
        p: Dict[str, Any],
        is_orig: bool,
        is_reply: bool,
        now: float,
    ) -> Tuple[bool, ConntrackEntry, str]:
        proto = p["proto"]
        flags = p["flags"]
        entry.last_seen = now

        # Trafik hisoblagichlarini oshirish
        if is_orig:
            entry.packets_orig += 1
            entry.bytes_orig += p["length"]
        else:
            entry.packets_reply += 1
            entry.bytes_reply += p["length"]
            if "SEEN_REPLY" not in entry.status:
                entry.status.append("SEEN_REPLY")

        # Protokolga xos state machine
        if proto == "tcp":
            fw_state = self._transition_tcp(entry, flags, is_orig, is_reply, now)
        elif proto == "udp":
            fw_state = self._transition_udp(entry, is_reply, now)
        elif proto == "icmp":
            fw_state = self._transition_icmp(entry, p, is_reply, now)
        else:
            fw_state = FW_STATE_ESTABLISHED
            entry.expires_at = now + self.timeouts["generic"]

        entry.fw_state = fw_state
        return (fw_state != FW_STATE_INVALID), entry, fw_state

    def _transition_tcp(
        self,
        entry: ConntrackEntry,
        flags: Dict[str, bool],
        is_orig: bool,
        is_reply: bool,
        now: float,
    ) -> str:
        syn = flags.get("syn", False)
        ack = flags.get("ack", False)
        fin = flags.get("fin", False)
        rst = flags.get("rst", False)

        current_tcp = entry.tcp_state or TCP_STATE_NONE

        # RST kelganda ulanish tezda yopiladi
        if rst:
            entry.tcp_state = TCP_STATE_CLOSE
            entry.timeout_s = self.timeouts["tcp_close"]
            entry.expires_at = now + entry.timeout_s
            return FW_STATE_ESTABLISHED

        # Holatlar ketma-ketligi
        if current_tcp == TCP_STATE_SYN_SENT:
            if is_reply and syn and ack:
                entry.tcp_state = TCP_STATE_SYN_RECV
                entry.timeout_s = self.timeouts["tcp_syn_recv"]
                entry.expires_at = now + entry.timeout_s
                return FW_STATE_ESTABLISHED
            elif is_reply and syn and not ack:
                # Simmetrik (simultaneous) SYN
                entry.tcp_state = TCP_STATE_SYN_RECV
                entry.timeout_s = self.timeouts["tcp_syn_recv"]
                entry.expires_at = now + entry.timeout_s
                return FW_STATE_ESTABLISHED

        elif current_tcp == TCP_STATE_SYN_RECV:
            if is_orig and ack and not syn:
                entry.tcp_state = TCP_STATE_ESTABLISHED
                if "ASSURED" not in entry.status:
                    entry.status.append("ASSURED")
                entry.timeout_s = self.timeouts["tcp_established"]
                entry.expires_at = now + entry.timeout_s
                return FW_STATE_ESTABLISHED

        elif current_tcp == TCP_STATE_ESTABLISHED:
            if fin:
                # Bir tomon ulanishni tugatmoqda
                if is_orig:
                    entry.tcp_state = TCP_STATE_FIN_WAIT
                    entry.timeout_s = self.timeouts["tcp_fin_wait"]
                else:
                    entry.tcp_state = TCP_STATE_CLOSE_WAIT
                    entry.timeout_s = self.timeouts["tcp_close_wait"]
                entry.expires_at = now + entry.timeout_s
                return FW_STATE_ESTABLISHED
            else:
                # Ma'lumot almashinuvi
                entry.timeout_s = self.timeouts["tcp_established"]
                entry.expires_at = now + entry.timeout_s
                return FW_STATE_ESTABLISHED

        elif current_tcp == TCP_STATE_FIN_WAIT:
            if is_reply and fin:
                entry.tcp_state = TCP_STATE_TIME_WAIT
                entry.timeout_s = self.timeouts["tcp_time_wait"]
            elif is_reply and ack:
                # Boshqa tomon ack qaytardi
                entry.timeout_s = self.timeouts["tcp_fin_wait"]
            entry.expires_at = now + entry.timeout_s
            return FW_STATE_ESTABLISHED

        elif current_tcp == TCP_STATE_CLOSE_WAIT:
            if is_orig and fin:
                entry.tcp_state = TCP_STATE_LAST_ACK
                entry.timeout_s = self.timeouts["tcp_last_ack"]
                entry.expires_at = now + entry.timeout_s
            return FW_STATE_ESTABLISHED

        elif current_tcp == TCP_STATE_LAST_ACK:
            if is_reply and ack:
                entry.tcp_state = TCP_STATE_CLOSE
                entry.timeout_s = self.timeouts["tcp_close"]
                entry.expires_at = now + entry.timeout_s
            return FW_STATE_ESTABLISHED

        elif current_tcp == TCP_STATE_TIME_WAIT:
            entry.timeout_s = self.timeouts["tcp_time_wait"]
            entry.expires_at = now + entry.timeout_s
            return FW_STATE_ESTABLISHED

        # Boshqa noaniq holatda agar allaqachon bog'langan bo'lsa established qaytaradi
        entry.expires_at = now + entry.timeout_s
        return FW_STATE_ESTABLISHED

    def _transition_udp(self, entry: ConntrackEntry, is_reply: bool, now: float) -> str:
        if is_reply:
            if "ASSURED" not in entry.status:
                entry.status.append("ASSURED")
            if "UNREPLIED" in entry.status:
                entry.status.remove("UNREPLIED")
            entry.timeout_s = self.timeouts["udp_assured"]
            entry.expires_at = now + entry.timeout_s
            return FW_STATE_ESTABLISHED
        else:
            timeout = self.timeouts["udp_assured"] if entry.is_assured else self.timeouts["udp_unreplied"]
            entry.expires_at = now + timeout
            return FW_STATE_ESTABLISHED if entry.is_assured else FW_STATE_NEW

    def _transition_icmp(
        self, entry: ConntrackEntry, p: Dict[str, Any], is_reply: bool, now: float
    ) -> str:
        icmp_type = p.get("icmp_type")
        if is_reply or icmp_type == 0:  # Echo Reply
            if "REPLIED" not in entry.status:
                entry.status.append("REPLIED")
            if "ASSURED" not in entry.status:
                entry.status.append("ASSURED")
            if "UNREPLIED" in entry.status:
                entry.status.remove("UNREPLIED")
            entry.timeout_s = self.timeouts["icmp_replied"]
            entry.expires_at = now + entry.timeout_s
            return FW_STATE_ESTABLISHED
        else:
            entry.timeout_s = self.timeouts["icmp_unreplied"]
            entry.expires_at = now + entry.timeout_s
            return FW_STATE_NEW

    # ──────────────────────── Ulanishlarni olish va o'chirish ────────────────────────
    def get_active_connections(
        self, filter_dict: Optional[Dict[str, Any]] = None, now: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Aktiv ulanishlar ro'yxatini qaytaradi (filtrlar bilan)."""
        current_time = now if now is not None else time.time()
        # Avval eskirganlarini tozalash
        self.expire_connections(current_time)

        results = []
        f = filter_dict or {}
        f_proto = f.get("proto", "").lower()
        f_state = f.get("state", "").lower()
        f_search = str(f.get("search", "")).lower()

        for entry in self._entries_by_id.values():
            if f_proto and entry.proto != f_proto:
                continue

            if f_state:
                entry_states = [entry.fw_state.lower(), (entry.tcp_state or "").lower()]
                if not any(f_state == s for s in entry_states if s):
                    continue

            if f_search:
                match_src = f_search in entry.orig_tuple.src_ip or f_search in str(entry.orig_tuple.src_port)
                match_dst = f_search in entry.orig_tuple.dst_ip or f_search in str(entry.orig_tuple.dst_port)
                match_id = f_search in entry.id.lower()
                if not (match_src or match_dst or match_id):
                    continue

            results.append(entry.to_dict(current_time))

        return results

    def expire_connections(self, now: Optional[float] = None) -> int:
        """Eskirgan ulanishlarni jadvaldan o'chiradi. O'chirilganlar sonini qaytaradi."""
        current_time = now if now is not None else time.time()
        to_delete = []

        for entry_id, entry in self._entries_by_id.items():
            if entry.expires_at <= current_time:
                to_delete.append((entry_id, entry))

        for entry_id, entry in to_delete:
            self._entries_by_id.pop(entry_id, None)
            self._orig_table.pop(entry.orig_tuple, None)
            self._reply_table.pop(entry.reply_tuple, None)

        return len(to_delete)

    # ──────────────────────── Stateful Firewall Baholash ────────────────────────
    def evaluate_stateful_firewall(
        self,
        pkt: Dict[str, Any],
        rules: List[Dict[str, Any]],
        default_policy: str = "drop",
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Paketni stateful firewall qoidalari bo'yicha baholash.

        1. Conntrack orqali paket qayta ishlanadi va fw_state (NEW, ESTABLISHED, RELATED, INVALID) aniqlanadi.
        2. Qoidalar ketma-ket tekshiriladi:
           - state: "new" -> faqat NEW
           - state: "established" -> ESTABLISHED yoki RELATED
           - state: "related" -> faqat RELATED
           - state: "invalid" -> faqat INVALID
           - state ko'rsatilmagan bo'lsa -> istalgan holatda mos keladi.
        3. Mos kelgan birinchi qoida harakati ("accept", "drop", "reject") qaytariladi.
        """
        current_time = now if now is not None else time.time()
        is_valid, entry, fw_state = self.process_packet(pkt, current_time)
        norm_p = self._normalize_packet(pkt)

        matched_rule = None
        matched_index = None
        action = default_policy

        for idx, rule in enumerate(rules):
            if self._match_firewall_rule(rule, norm_p, fw_state):
                matched_rule = rule
                matched_index = idx
                action = rule.get("action", default_policy)
                break

        return {
            "action": action,
            "fw_state": fw_state,
            "tcp_state": entry.tcp_state if entry else None,
            "is_valid": is_valid,
            "matched_index": matched_index,
            "matched_rule": matched_rule,
            "entry_id": entry.id if entry else None,
            "packet": norm_p,
        }

    def _match_firewall_rule(self, rule: Dict[str, Any], p: Dict[str, Any], fw_state: str) -> bool:
        # Chain tekshiruvi
        r_chain = rule.get("chain")
        if r_chain and p.get("chain") and r_chain != p.get("chain"):
            return False

        # Protokol tekshiruvi
        r_proto = (rule.get("proto") or "any").lower()
        if r_proto != "any" and r_proto != p["proto"]:
            return False

        # Manba va manzil IP/CIDR tekshiruvi
        for r_key, p_key in (("src", "src_ip"), ("dst", "dst_ip")):
            rule_net = rule.get(r_key)
            if rule_net:
                try:
                    target_ip = ipaddress.ip_address(p[p_key])
                    net = ipaddress.ip_network(rule_net, strict=False)
                    if target_ip not in net:
                        return False
                except ValueError:
                    return False

        # Portlar tekshiruvi
        if rule.get("sport") not in (None, ""):
            if int(rule["sport"]) != p["src_port"]:
                return False

        if rule.get("dport") not in (None, ""):
            if int(rule["dport"]) != p["dst_port"]:
                return False

        # Interfeyslar tekshiruvi
        if rule.get("in") and rule.get("in") != p.get("in_if"):
            return False
        if rule.get("out") and rule.get("out") != p.get("out_if"):
            return False

        # Conntrack Holati (State) tekshiruvi
        r_state = (rule.get("state") or "").strip().lower()
        if r_state:
            allowed_states = set(s.strip() for s in r_state.split(","))
            fw_state_lower = fw_state.lower()

            # "established" iptables kabi RELATED ni ham qamrab oladi
            if "established" in allowed_states and fw_state_lower in ("established", "related"):
                return True
            if "new" in allowed_states and fw_state_lower == "new":
                return True
            if "related" in allowed_states and fw_state_lower == "related":
                return True
            if "invalid" in allowed_states and fw_state_lower == "invalid":
                return True

            if fw_state_lower not in allowed_states:
                return False

        return True

    # ──────────────────────── Linux /proc/net/nf_conntrack Parseri ────────────────────────
    @staticmethod
    def parse_proc_conntrack(content: str) -> List[ConntrackEntry]:
        """Linux `/proc/net/nf_conntrack` yoki `conntrack -L` chiqishini tahlil qiladi."""
        entries = []
        lines = [line.strip() for line in content.splitlines() if line.strip()]

        for idx, line in enumerate(lines):
            # Namuna qator:
            # ipv4 2 tcp 6 431999 ESTABLISHED src=192.168.1.10 dst=93.184.216.34 sport=49152 dport=80 src=93.184.216.34 dst=192.168.1.10 sport=80 dport=49152 [ASSURED] mark=0 use=2
            tokens = line.split()
            if not tokens:
                continue

            proto = "tcp"
            for p in ("tcp", "udp", "icmp", "generic"):
                if p in tokens:
                    proto = p
                    break

            # Holat
            tcp_state = None
            for st in ("ESTABLISHED", "SYN_SENT", "SYN_RECV", "FIN_WAIT", "CLOSE_WAIT", "LAST_ACK", "TIME_WAIT", "CLOSE"):
                if st in tokens:
                    tcp_state = st
                    break

            status = []
            if "[ASSURED]" in tokens:
                status.append("ASSURED")
            if "[UNREPLIED]" in tokens:
                status.append("UNREPLIED")

            # src/dst/sport/dport juftliklarini yig'ish (ikkita yo'nalish)
            src_list = re.findall(r"src=([0-9a-fA-F\.\:]+)", line)
            dst_list = re.findall(r"dst=([0-9a-fA-F\.\:]+)", line)
            sport_list = [int(p) for p in re.findall(r"sport=(\d+)", line)]
            dport_list = [int(p) for p in re.findall(r"dport=(\d+)", line)]

            # Kamida bitta src va dst bo'lishi kerak
            if not src_list or not dst_list:
                continue

            orig_src = src_list[0]
            orig_dst = dst_list[0]
            orig_sport = sport_list[0] if sport_list else 0
            orig_dport = dport_list[0] if dport_list else 0

            reply_src = src_list[1] if len(src_list) > 1 else orig_dst
            reply_dst = dst_list[1] if len(dst_list) > 1 else orig_src
            reply_sport = sport_list[1] if len(sport_list) > 1 else orig_dport
            reply_dport = dport_list[1] if len(dport_list) > 1 else orig_sport

            orig_tuple = ConnectionTuple(orig_src, orig_sport, orig_dst, orig_dport, proto)
            reply_tuple = ConnectionTuple(reply_src, reply_sport, reply_dst, reply_dport, proto)

            # NAT tekshiruvi
            nat = None
            if orig_src != reply_dst or orig_dst != reply_src or orig_sport != reply_dport or orig_dport != reply_sport:
                nat = NATMapping(
                    enabled=True,
                    nat_type="snat" if orig_src != reply_dst else "dnat",
                    original_src=orig_src,
                    original_sport=orig_sport,
                    original_dst=orig_dst,
                    original_dport=orig_dport,
                    translated_src=reply_dst,
                    translated_sport=reply_dport,
                    translated_dst=reply_src,
                    translated_dport=reply_sport,
                )

            # Timeout
            timeout_match = re.search(r"\b(\d+)\b", line)
            timeout = int(timeout_match.group(1)) if timeout_match else 300

            fw_state = FW_STATE_ESTABLISHED if "ASSURED" in status or tcp_state == "ESTABLISHED" else FW_STATE_NEW

            entry = ConntrackEntry(
                id=f"nf_{idx+1}",
                orig_tuple=orig_tuple,
                reply_tuple=reply_tuple,
                proto=proto,
                tcp_state=tcp_state,
                fw_state=fw_state,
                status=status,
                nat=nat,
                timeout_s=float(timeout),
                expires_at=time.time() + float(timeout),
                packets_orig=random.randint(10, 150),
                bytes_orig=random.randint(500, 80000),
                packets_reply=random.randint(5, 120),
                bytes_reply=random.randint(400, 120000),
            )
            entries.append(entry)

        return entries

    # ──────────────────────── Synthetic Generator ────────────────────────
    def generate_synthetic_entries(self, count: int = 15) -> List[ConntrackEntry]:
        """Unprivileged / demo holatlar uchun realistik sintetik conntrack yozuvlarini yaratadi."""
        templates = [
            # (proto, sport, dport, tcp_state, fw_state, status, nat_type)
            ("tcp", 51234, 443, TCP_STATE_ESTABLISHED, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "snat"),
            ("tcp", 51235, 80, TCP_STATE_ESTABLISHED, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "none"),
            ("tcp", 51236, 22, TCP_STATE_ESTABLISHED, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "none"),
            ("tcp", 49871, 8080, TCP_STATE_SYN_SENT, FW_STATE_NEW, ["UNREPLIED"], "none"),
            ("tcp", 49872, 80, TCP_STATE_SYN_RECV, FW_STATE_ESTABLISHED, ["SEEN_REPLY"], "none"),
            ("tcp", 49873, 443, TCP_STATE_FIN_WAIT, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "none"),
            ("tcp", 49874, 80, TCP_STATE_TIME_WAIT, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "none"),
            ("udp", 53531, 53, None, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "none"),
            ("udp", 53532, 53, None, FW_STATE_NEW, ["UNREPLIED"], "none"),
            ("udp", 5060, 5060, None, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "none"),  # SIP
            ("udp", 16384, 16384, None, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "none"),  # RTP
            ("icmp", 1, 1, None, FW_STATE_ESTABLISHED, ["REPLIED", "ASSURED", "SEEN_REPLY"], "none"),
            ("icmp", 2, 2, None, FW_STATE_NEW, ["UNREPLIED"], "none"),
            ("tcp", 55432, 3306, TCP_STATE_ESTABLISHED, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "dnat"),
            ("tcp", 55433, 5432, TCP_STATE_ESTABLISHED, FW_STATE_ESTABLISHED, ["ASSURED", "SEEN_REPLY"], "none"),
        ]

        now = time.time()
        self.clear()
        entries = []

        subnets = ["10.0.1.", "10.0.2.", "192.168.1.", "172.16.0."]
        wan_ips = ["198.51.100.10", "203.0.113.25", "8.8.8.8", "1.1.1.1", "93.184.216.34"]

        for i in range(min(count, len(templates))):
            proto, sp, dp, tcp_st, fw_st, st_list, nat_mode = templates[i]
            src_ip = f"{random.choice(subnets)}{random.randint(10, 100)}"
            dst_ip = random.choice(wan_ips)

            orig_t = ConnectionTuple(src_ip, sp, dst_ip, dp, proto)

            nat_mapping = None
            if nat_mode == "snat":
                trans_ip = "198.51.100.1"
                nat_mapping = NATMapping(
                    enabled=True,
                    nat_type="snat",
                    original_src=src_ip,
                    original_sport=sp,
                    original_dst=dst_ip,
                    original_dport=dp,
                    translated_src=trans_ip,
                    translated_sport=sp + 1000,
                    translated_dst=dst_ip,
                    translated_dport=dp,
                )
                reply_t = ConnectionTuple(dst_ip, dp, trans_ip, sp + 1000, proto)
            elif nat_mode == "dnat":
                trans_ip = "10.0.2.200"
                nat_mapping = NATMapping(
                    enabled=True,
                    nat_type="dnat",
                    original_src=src_ip,
                    original_sport=sp,
                    original_dst=dst_ip,
                    original_dport=dp,
                    translated_src=src_ip,
                    translated_sport=sp,
                    translated_dst=trans_ip,
                    translated_dport=dp,
                )
                reply_t = ConnectionTuple(trans_ip, dp, src_ip, sp, proto)
            else:
                reply_t = orig_t.reversed()

            entry_id = f"ct_{i+1:03d}"
            timeout = random.randint(60, 3600)
            entry = ConntrackEntry(
                id=entry_id,
                orig_tuple=orig_t,
                reply_tuple=reply_t,
                proto=proto,
                tcp_state=tcp_st,
                fw_state=fw_st,
                status=list(st_list),
                nat=nat_mapping,
                created_at=now - random.randint(5, 300),
                last_seen=now - random.randint(0, 10),
                timeout_s=float(timeout),
                expires_at=now + float(timeout),
                packets_orig=random.randint(5, 250),
                bytes_orig=random.randint(300, 150000),
                packets_reply=random.randint(3, 200),
                bytes_reply=random.randint(200, 250000),
            )

            self._orig_table[orig_t] = entry
            self._reply_table[reply_t] = entry
            self._entries_by_id[entry_id] = entry
            entries.append(entry)

        return entries

    def get_summary_stats(self) -> Dict[str, Any]:
        """Conntrack statistikasi."""
        tcp_est = 0
        tcp_syn = 0
        udp_count = 0
        icmp_count = 0
        nat_count = 0

        for e in self._entries_by_id.values():
            if e.proto == "tcp":
                if e.tcp_state == TCP_STATE_ESTABLISHED:
                    tcp_est += 1
                elif e.tcp_state in (TCP_STATE_SYN_SENT, TCP_STATE_SYN_RECV):
                    tcp_syn += 1
            elif e.proto == "udp":
                udp_count += 1
            elif e.proto == "icmp":
                icmp_count += 1

            if e.nat and e.nat.enabled:
                nat_count += 1

        return {
            "total_connections": len(self._entries_by_id),
            "tcp_established": tcp_est,
            "tcp_syn_half_open": tcp_syn,
            "udp_active": udp_count,
            "icmp_active": icmp_count,
            "nat_translations": nat_count,
        }

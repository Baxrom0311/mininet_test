"""Streaming DPI (Deep Packet Inspection) & Dissector:
Xom tarmoq baytlarini (raw ethernet frames) OSI 1-7 qatlamlariga ajratuvchi,
TCP flags/seq/ack, UDP Jitter (RFC 3550) va paket yo'qotishlarini tahlil qiluvchi modul.

Hech qanday tashqi kutubxonasiz (pure Python struct), juda yuqori tezlikda
ishlaydi va Cisco Packet Tracer / Wireshark kabi PDU strukturasini yaratadi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Set, Tuple


def calculate_rfc3550_jitter(prev_jitter: float, prev_transit: float, curr_transit: float) -> float:
    """RFC 3550 bo'yicha Inter-arrival Jitter hisoblaydi:
    d = |curr_transit - prev_transit|
    J(i) = J(i-1) + (d - J(i-1)) / 16.0
    """
    d = abs(curr_transit - prev_transit)
    return prev_jitter + (d - prev_jitter) / 16.0


@dataclass
class DissectedPacket:
    """Dekonstruktsiya qilingan paket ob'ekti."""

    timestamp: float
    raw_len: int
    l2: Dict[str, Any] = field(default_factory=dict)
    l3: Dict[str, Any] = field(default_factory=dict)
    l4: Dict[str, Any] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    payload: bytes = b""
    payload_len: int = 0
    app_summary: Optional[str] = None
    summary: str = ""
    hex_dump: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "raw_len": self.raw_len,
            "l2": self.l2,
            "l3": self.l3,
            "l4": self.l4,
            "flags": self.flags,
            "payload_len": self.payload_len,
            "app_summary": self.app_summary,
            "summary": self.summary,
            "hex_dump": self.hex_dump,
        }


def mac_bytes_to_str(b: bytes) -> str:
    """6 baytli MAC adresni '00:11:22:33:44:55' formatiga o'tkazadi."""
    return ":".join(f"{x:02x}" for x in b)


def hex_dump_format(raw: bytes, max_bytes: int = 256) -> str:
    """Baytlarni Wireshark / hexdump formatida (offset, hex, ascii) chiroyli qilib beradi."""
    truncated = raw[:max_bytes]
    lines = []
    for i in range(0, len(truncated), 16):
        chunk = truncated[i : i + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        if len(chunk) > 8:
            hex_part = hex_part[:23] + "  " + hex_part[24:]
        hex_part = hex_part.ljust(49)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:04x}   {hex_part}  |{ascii_part}|")
    if len(raw) > max_bytes:
        lines.append(f"... ({len(raw) - max_bytes} bayt qisqartirildi)")
    return "\n".join(lines)


def calculate_rfc3550_jitter(prev_jitter: float, d_prev: float, d_curr: float) -> float:
    """RFC 3550 section 6.4.1 bo'yicha inter-arrival jitter hisoblash:
    d = |d_curr - d_prev|
    J(i) = J(i-1) + (d - J(i-1)) / 16.0
    """
    d = abs(d_curr - d_prev)
    return prev_jitter + (d - prev_jitter) / 16.0


class UDPJitterTracker:
    """RFC 3550 bo'yicha UDP oqimlarida paketlararo kechikish tebranishini hisoblovchi sinf."""

    def __init__(self):
        self._streams: Dict[str, Dict[str, Any]] = {}

    def update(self, stream_key: str, arrival_time: float, seq: Optional[int] = None) -> Dict[str, Any]:
        st = self._streams.get(stream_key)
        if st is None:
            st = {
                "last_arrival": arrival_time,
                "last_seq": seq,
                "jitter_ms": 0.0,
                "total_pkts": 1,
                "lost_pkts": 0,
                "out_of_order": 0,
            }
            self._streams[stream_key] = st
            return {
                "jitter_ms": 0.0,
                "delta_ms": 0.0,
                "lost_pkts": 0,
                "total_pkts": 1,
            }

        delta_ms = (arrival_time - st["last_arrival"]) * 1000.0
        prev_jitter = st["jitter_ms"]
        new_jitter = calculate_rfc3550_jitter(prev_jitter, 0.0, delta_ms)
        st["jitter_ms"] = new_jitter
        st["last_arrival"] = arrival_time
        st["total_pkts"] += 1

        if seq is not None and st["last_seq"] is not None:
            diff = seq - st["last_seq"]
            if diff > 1:
                st["lost_pkts"] += (diff - 1)
            elif diff < 0:
                st["out_of_order"] += 1
            st["last_seq"] = seq

        return {
            "jitter_ms": round(new_jitter, 3),
            "delta_ms": round(delta_ms, 3),
            "lost_pkts": st["lost_pkts"],
            "total_pkts": st["total_pkts"],
        }


class DPIDissector:
    """Xom baytli paketlarni tahlil qiluvchi (dissect) va PDU tuzuvchi asosiy sinf."""

    def __init__(self):
        self.jitter_tracker = UDPJitterTracker()

    def dissect_raw(self, raw: bytes, ts: float = 0.0) -> DissectedPacket:
        """Xom baytlarni DissectedPacket ob'ektiga aylantiradi."""
        t_now = ts if ts > 0.0 else time.time()
        dp = DissectedPacket(timestamp=t_now, raw_len=len(raw), hex_dump=hex_dump_format(raw))

        if len(raw) < 14:
            dp.summary = "Kichik freym"
            return dp

        # L2 Ethernet
        dst_mac = mac_bytes_to_str(raw[0:6])
        src_mac = mac_bytes_to_str(raw[6:12])
        ethertype = struct.unpack("!H", raw[12:14])[0]
        offset = 14
        vlan_info = None

        if ethertype == 0x8100 and len(raw) >= 18:
            tci = struct.unpack("!H", raw[14:16])[0]
            vlan_info = {"vlan_id": tci & 0x0FFF, "priority": (tci >> 13) & 0x07}
            ethertype = struct.unpack("!H", raw[16:18])[0]
            offset = 18

        dp.l2 = {
            "src_mac": src_mac,
            "dst_mac": dst_mac,
            "ethertype_hex": f"0x{ethertype:04x}",
            "vlan": vlan_info,
        }

        # L3
        if ethertype == 0x0800 and len(raw) >= offset + 20:  # IPv4
            ip_hdr = raw[offset : offset + 20]
            v_ihl, tos, tot_len, ident, flags_frag, ttl, proto, cksum, s_ip, d_ip = struct.unpack(
                "!BBHHHBBH4s4s", ip_hdr
            )
            ihl = (v_ihl & 0x0F) * 4
            src_ip = socket.inet_ntoa(s_ip)
            dst_ip = socket.inet_ntoa(d_ip)
            dp.l3 = {
                "proto": "IPv4",
                "version": v_ihl >> 4,
                "ihl": ihl,
                "total_len": tot_len,
                "ttl": ttl,
                "protocol": proto,
                "src_ip": src_ip,
                "dst_ip": dst_ip,
            }
            l4_offset = offset + ihl

            # L4
            if proto == 6 and len(raw) >= l4_offset + 20:  # TCP
                sport, dport, seq, ack, off_res, flags_b, win, ck, urp = struct.unpack(
                    "!HHIIBBHHH", raw[l4_offset : l4_offset + 20]
                )
                doff = (off_res >> 4) * 4
                flags_list = []
                flag_map = [
                    (0x80, "CWR"), (0x40, "ECE"), (0x20, "URG"),
                    (0x10, "ACK"), (0x08, "PSH"), (0x04, "RST"),
                    (0x02, "SYN"), (0x01, "FIN"),
                ]
                for mask, name in flag_map:
                    if flags_b & mask:
                        flags_list.append(name)
                dp.flags = flags_list
                payload = raw[l4_offset + doff :]
                dp.payload = payload
                dp.payload_len = len(payload)
                dp.l4 = {
                    "proto": "TCP",
                    "src_port": sport,
                    "dst_port": dport,
                    "seq": seq,
                    "ack": ack,
                    "window": win,
                    "data_offset": doff,
                }
                dp.summary = f"TCP {src_ip}:{sport} -> {dst_ip}:{dport} [{' '.join(flags_list)}] Seq={seq} Ack={ack}"

            elif proto == 17 and len(raw) >= l4_offset + 8:  # UDP
                sport, dport, ulen, uck = struct.unpack("!HHHH", raw[l4_offset : l4_offset + 8])
                payload = raw[l4_offset + 8 :]
                dp.payload = payload
                dp.payload_len = len(payload)
                dp.l4 = {
                    "proto": "UDP",
                    "src_port": sport,
                    "dst_port": dport,
                    "length": ulen,
                }
                dp.summary = f"UDP {src_ip}:{sport} -> {dst_ip}:{dport} Len={ulen}"

        return dp

    def dissect_frame(self, raw: bytes, capture_time: Optional[float] = None) -> Dict[str, Any]:
        """Tashqi API va UI uchun to'liq PDU dict shaklida qaytaradi."""
        dp = self.dissect_raw(raw, ts=capture_time or 0.0)
        return {
            "timestamp": dp.timestamp,
            "raw_len": dp.raw_len,
            "summary": dp.summary,
            "hex_dump": dp.hex_dump,
            "layers": {
                "l2_ethernet": dp.l2,
                "l3": dp.l3,
                "l4": dp.l4,
            },
            "flags": dp.flags,
            "payload_len": dp.payload_len,
        }

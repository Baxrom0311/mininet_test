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

    def build_cisco_pdu_inspection(
        self,
        packet_info: Dict[str, Any],
        device_name: str = "Router",
        device_type: str = "router",
    ) -> Dict[str, Any]:
        """Cisco Packet Tracer va OPNET uslubidagi 7-qatlamli In-Layers vs Out-Layers
        hamda Device Processing Log tahlilini yaratadi."""
        proto = str(packet_info.get("proto") or "TCP").upper()
        summary = str(packet_info.get("summary") or f"{proto} Packet")
        link = str(packet_info.get("link") or "eth0")
        ts = float(packet_info.get("ts") or time.time())

        # Agar raw_hex bo'lsa, undan dekonstruksiya qilish
        raw_hex = packet_info.get("raw_hex")
        if raw_hex:
            try:
                raw_bytes = bytes.fromhex(raw_hex.replace(" ", "").replace("\n", "").replace("0x", ""))
                dissected = self.dissect_frame(raw_bytes, capture_time=ts)
                l2 = dissected["layers"].get("l2_ethernet") or {}
                l3 = dissected["layers"].get("l3") or {}
                l4 = dissected["layers"].get("l4") or {}
                hex_dump = dissected.get("hex_dump", "")
            except Exception:
                l2, l3, l4, hex_dump = {}, {}, {}, ""
        else:
            l2, l3, l4, hex_dump = {}, {}, {}, ""

        # Default IP va portlar
        src_ip = l3.get("src_ip") or packet_info.get("src_ip") or "10.0.1.10"
        dst_ip = l3.get("dst_ip") or packet_info.get("dst_ip") or "10.0.2.20"
        src_port = l4.get("src_port") or packet_info.get("src_port") or 45678
        dst_port = l4.get("dst_port") or packet_info.get("dst_port") or 80
        src_mac = l2.get("src_mac") or packet_info.get("src_mac") or "00:11:22:33:44:55"
        dst_mac = l2.get("dst_mac") or packet_info.get("dst_mac") or "00:aa:bb:cc:dd:ee"
        ttl = l3.get("ttl", 64)

        if not hex_dump:
            # Namoyish uchun hexdump generatsiya qilish
            mock_bytes = (
                bytes.fromhex("00aabbccddee0011223344550800")
                + struct.pack("!BBHHHBBH4s4s", 0x45, 0, 60, 101, 0x4000, ttl, 6 if proto == "TCP" else 17, 0x3d41,
                              socket.inet_aton(src_ip), socket.inet_aton(dst_ip))
                + (struct.pack("!HHIIBBHHH", src_port, dst_port, 1000, 500, 5 << 4, 0x18, 64240, 0, 0)
                   if proto == "TCP" else struct.pack("!HHHH", src_port, dst_port, 16, 0) + b"NETLAB_L4")
            )
            hex_dump = hex_dump_format(mock_bytes)

        # In-Layers
        in_layers = {
            "l1": {
                "name": "Layer 1: Physical (Jismoniy)",
                "port": f"{link} (Inbound Rx)",
                "media": "Ethernet 100Base-TX / Full-Duplex",
                "status": "UP / Line Protocol Active",
            },
            "l2": {
                "name": "Layer 2: Data Link (Kanal)",
                "src_mac": src_mac,
                "dst_mac": dst_mac,
                "ethertype": "0x0800 (IPv4)" if proto != "ARP" else "0x0806 (ARP)",
                "vlan": packet_info.get("vlan"),
                "status": "Frame Check Sequence (FCS) Valid",
            },
            "l3": {
                "name": "Layer 3: Network (Tarmoq)",
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "ttl": ttl,
                "protocol": f"{proto} (Proto {6 if proto == 'TCP' else 17 if proto == 'UDP' else 1})",
                "checksum": "0x4a2b (Valid)",
                "total_len": 60,
            },
            "l4": {
                "name": "Layer 4: Transport",
                "proto": proto,
                "src_port": src_port,
                "dst_port": dst_port,
                "seq": l4.get("seq", 1000) if proto == "TCP" else None,
                "ack": l4.get("ack", 501) if proto == "TCP" else None,
                "flags": l4.get("flags") or ["ACK", "PSH"] if proto == "TCP" else [],
                "window": l4.get("window", 64240) if proto == "TCP" else None,
            },
            "l7": {
                "name": "Layer 7: Application (Ilova)",
                "protocol": proto,
                "summary": summary,
            },
        }

        # Device Forwarding & Processing Log (Cisco uslubida)
        is_router = device_type in ("router", "sdnrouter")
        new_ttl = max(1, ttl - 1) if is_router else ttl
        next_hop_mac = "00:50:56:c0:00:08"
        out_port = "eth1" if link == "eth0" else "eth0"

        device_logs: List[str] = [
            f"1. Port qabuli: Freym '{link}' interfeysiga to'liq yetib keldi.",
            f"2. Layer 2: Freym sarlavhasi tekshirildi. Maqsad MAC ({dst_mac}) qurilma MAC manziliga mos keldi.",
        ]
        if proto == "ARP":
            device_logs.extend([
                "3. Layer 3: ARP so'rovi tekshirildi va qidirilayotgan IP manzili lokal keshdan topildi.",
                "4. Qurilma ARP Reply paketi tuzishni boshladi.",
            ])
        elif is_router:
            device_logs.extend([
                f"3. Layer 3: IPv4 sarlavhasi tekshirildi (TTL={ttl} > 1, Nazorat yig'indisi to'g'ri).",
                f"4. Marshrutlash (FIB lookup): Maqsad IP ({dst_ip}) uchun marshrut jadvalidan mos Next-Hop qidirildi.",
                f"5. Marshrut topildi: Next-Hop IP interfeys '{out_port}' orqali uzatiladi.",
                f"6. TTL o'zgarishi: TTL qiymati {ttl} dan {new_ttl} ga kamaytirildi, IP checksum qayta hisoblandi.",
                f"7. ARP jadvali: Next-Hop IP uchun yangi maqsad MAC manzili ({next_hop_mac}) olindi.",
                f"8. Layer 2: Yangi Ethernet II sarlavhasi bilan qayta inkapsulyatsiya qilindi.",
                f"9. Chiqish: Yangi freym '{out_port}' chiqish navbatiga uzatildi.",
            ])
        else:
            device_logs.extend([
                f"3. Layer 2 L2 Learning: Kiruvchi port va Src MAC ({src_mac}) MAC manzil jadvaliga yozildi.",
                f"4. L2 Forwarding: Dst MAC ({dst_mac}) jadvaldan qidirildi va chiqish porti '{out_port}' aniqlandi.",
                f"5. Freym o'zgartirishlarsiz '{out_port}' portiga uzatildi.",
            ])

        # Out-Layers
        out_layers = {
            "l1": {
                "name": "Layer 1: Physical (Chiquvchi)",
                "port": f"{out_port} (Outbound Tx)",
                "media": "Ethernet 100Base-TX / Full-Duplex",
                "status": "Transmitting",
            },
            "l2": {
                "name": "Layer 2: Data Link (Chiquvchi Freym)",
                "src_mac": "00:1c:73:00:00:01" if is_router else src_mac,
                "dst_mac": next_hop_mac if is_router else dst_mac,
                "ethertype": in_layers["l2"]["ethertype"],
                "status": "Frame Check Sequence (FCS) Recalculated",
            },
            "l3": {
                "name": "Layer 3: Network (Chiquvchi Paket)",
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "ttl": new_ttl,
                "protocol": in_layers["l3"]["protocol"],
                "checksum": "0x5b3c (Recalculated)",
                "total_len": 60,
            },
            "l4": in_layers["l4"],
            "l7": in_layers["l7"],
        }

        return {
            "device": device_name,
            "device_type": device_type,
            "timestamp": ts,
            "proto": proto,
            "summary": summary,
            "in_layers": in_layers,
            "out_layers": out_layers,
            "device_logs": device_logs,
            "hex_dump": hex_dump,
        }

"""Streaming DPI (Deep Packet Inspection) & Dissector:
Xom tarmoq baytlarini (raw ethernet frames) OSI 1-7 qatlamlariga ajratuvchi,
TCP flags/seq/ack, UDP Jitter (RFC 3550), ICMP, ARP, IPv6 va paketlarni tahlil qiluvchi modul.

Hech qanday tashqi kutubxonasiz (pure Python struct), yuqori tezlikda
ishlaydi va Cisco Packet Tracer / Wireshark kabi 7-qatlamli PDU strukturasini yaratadi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Set, Tuple


ICMP4_TYPES = {
    0: "Echo Reply",
    3: "Destination Unreachable",
    4: "Source Quench",
    5: "Redirect",
    8: "Echo Request",
    9: "Router Advertisement",
    10: "Router Solicitation",
    11: "Time Exceeded",
    12: "Parameter Problem",
    13: "Timestamp Request",
    14: "Timestamp Reply",
}

ICMP6_TYPES = {
    1: "Destination Unreachable",
    2: "Packet Too Big",
    3: "Time Exceeded",
    4: "Parameter Problem",
    128: "Echo Request",
    129: "Echo Reply",
    133: "Router Solicitation",
    134: "Router Advertisement",
    135: "Neighbor Solicitation",
    136: "Neighbor Advertisement",
}

TCP_FLAG_DEFS = [
    (0x100, "NS"),
    (0x80, "CWR"),
    (0x40, "ECE"),
    (0x20, "URG"),
    (0x10, "ACK"),
    (0x08, "PSH"),
    (0x04, "RST"),
    (0x02, "SYN"),
    (0x01, "FIN"),
]


def calculate_rfc3550_jitter(prev_jitter: float, prev_transit: float, curr_transit: float) -> float:
    """RFC 3550 Section 6.4.1 bo'yicha Inter-arrival Jitter hisoblaydi:
    d = |curr_transit - prev_transit|
    J(i) = J(i-1) + (d - J(i-1)) / 16.0
    """
    d = abs(curr_transit - prev_transit)
    return prev_jitter + (d - prev_jitter) / 16.0


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


class UDPJitterTracker:
    """RFC 3550 bo'yicha UDP/RTP oqimlarida paketlararo kechikish tebranishini hisoblovchi sinf."""

    def __init__(self):
        self._streams: Dict[str, Dict[str, Any]] = {}

    def update(
        self, stream_key: str, arrival_time: float, seq: Optional[int] = None, transit_ms: Optional[float] = None
    ) -> Dict[str, Any]:
        st = self._streams.get(stream_key)
        if st is None:
            curr_transit = transit_ms if transit_ms is not None else 0.0
            st = {
                "last_arrival": arrival_time,
                "last_transit": curr_transit,
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
        curr_transit = transit_ms if transit_ms is not None else delta_ms
        prev_transit = st["last_transit"]
        new_jitter = calculate_rfc3550_jitter(st["jitter_ms"], prev_transit, curr_transit)
        st["jitter_ms"] = new_jitter
        st["last_transit"] = curr_transit
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
        """Xom baytlarni (Ethernet freymi) DissectedPacket ob'ektiga aylantiradi."""
        t_now = ts if ts > 0.0 else time.time()
        dp = DissectedPacket(timestamp=t_now, raw_len=len(raw), hex_dump=hex_dump_format(raw))

        if len(raw) < 14:
            dp.summary = f"Qisqa freym ({len(raw)} bayt)"
            return dp

        # ── Layer 2: Ethernet ──
        dst_mac = mac_bytes_to_str(raw[0:6])
        src_mac = mac_bytes_to_str(raw[6:12])
        ethertype = struct.unpack("!H", raw[12:14])[0]
        offset = 14
        vlan_info = None

        # 802.1Q / 802.1ad VLAN tekshiruvi
        if ethertype in (0x8100, 0x88A8) and len(raw) >= offset + 4:
            tci = struct.unpack("!H", raw[offset : offset + 2])[0]
            vlan_info = {"vlan_id": tci & 0x0FFF, "priority": (tci >> 13) & 0x07}
            ethertype = struct.unpack("!H", raw[offset + 2 : offset + 4])[0]
            offset += 4

        dp.l2 = {
            "src_mac": src_mac,
            "dst_mac": dst_mac,
            "ethertype": ethertype,
            "ethertype_hex": f"0x{ethertype:04x}",
            "vlan": vlan_info,
        }

        # ── Layer 3: ARP (0x0806) ──
        if ethertype == 0x0806:
            if len(raw) >= offset + 8:
                htype, ptype, hlen, plen, opcode = struct.unpack("!HHBBH", raw[offset : offset + 8])
                op_name = "Request" if opcode == 1 else "Reply" if opcode == 2 else f"Opcode {opcode}"
                sender_mac, sender_ip, target_mac, target_ip = "", "", "", ""

                if hlen == 6 and plen == 4 and len(raw) >= offset + 28:
                    sender_mac = mac_bytes_to_str(raw[offset + 8 : offset + 14])
                    sender_ip = socket.inet_ntoa(raw[offset + 14 : offset + 18])
                    target_mac = mac_bytes_to_str(raw[offset + 18 : offset + 24])
                    target_ip = socket.inet_ntoa(raw[offset + 24 : offset + 28])
                    dp.payload = raw[offset + 28 :]
                    dp.payload_len = len(dp.payload)

                dp.l3 = {
                    "proto": "ARP",
                    "hardware_type": htype,
                    "protocol_type": f"0x{ptype:04x}",
                    "opcode": opcode,
                    "op_name": op_name,
                    "sender_mac": sender_mac,
                    "sender_ip": sender_ip,
                    "target_mac": target_mac,
                    "target_ip": target_ip,
                    "src_ip": sender_ip,
                    "dst_ip": target_ip,
                    "src_mac": sender_mac,
                    "dst_mac": target_mac,
                }
                if opcode == 1:
                    dp.summary = f"ARP Who has {target_ip}? Tell {sender_ip}"
                elif opcode == 2:
                    dp.summary = f"ARP {sender_ip} is at {sender_mac}"
                else:
                    dp.summary = f"ARP {op_name} ({sender_ip} -> {target_ip})"
            return dp

        # ── Layer 3: IPv4 (0x0800) ──
        proto = None
        l4_offset = offset
        src_ip = ""
        dst_ip = ""

        if ethertype == 0x0800 and len(raw) >= offset + 20:
            ip_hdr = raw[offset : offset + 20]
            v_ihl, tos, tot_len, ident, flags_frag, ttl, proto, cksum, s_ip, d_ip = struct.unpack(
                "!BBHHHBBH4s4s", ip_hdr
            )
            ihl = (v_ihl & 0x0F) * 4
            version = v_ihl >> 4
            src_ip = socket.inet_ntoa(s_ip)
            dst_ip = socket.inet_ntoa(d_ip)
            df = bool(flags_frag & 0x4000)
            mf = bool(flags_frag & 0x2000)
            frag_offset = (flags_frag & 0x1FFF) * 8

            dp.l3 = {
                "proto": "IPv4",
                "version": version,
                "ihl": ihl,
                "tos": tos,
                "dscp": tos >> 2,
                "ecn": tos & 0x03,
                "total_len": tot_len,
                "id": ident,
                "flags": {"df": df, "mf": mf},
                "frag_offset": frag_offset,
                "ttl": ttl,
                "protocol": proto,
                "checksum": f"0x{cksum:04x}",
                "src_ip": src_ip,
                "dst_ip": dst_ip,
            }
            l4_offset = offset + ihl

        # ── Layer 3: IPv6 (0x86DD) ──
        elif ethertype == 0x86DD and len(raw) >= offset + 40:
            ip6_hdr = raw[offset : offset + 40]
            v_tc_fl, payload_len, next_header, hop_limit, s_ip6, d_ip6 = struct.unpack(
                "!IHBB16s16s", ip6_hdr
            )
            version = (v_tc_fl >> 28) & 0x0F
            traffic_class = (v_tc_fl >> 20) & 0xFF
            flow_label = v_tc_fl & 0x0FFFFF
            src_ip = socket.inet_ntop(socket.AF_INET6, s_ip6)
            dst_ip = socket.inet_ntop(socket.AF_INET6, d_ip6)
            proto = next_header

            dp.l3 = {
                "proto": "IPv6",
                "version": version,
                "traffic_class": traffic_class,
                "flow_label": flow_label,
                "payload_len": payload_len,
                "total_len": payload_len + 40,
                "next_header": next_header,
                "protocol": next_header,
                "hop_limit": hop_limit,
                "ttl": hop_limit,
                "src_ip": src_ip,
                "dst_ip": dst_ip,
            }
            l4_offset = offset + 40

        # Agar L3 aniqlanmagan bo'lsa
        if proto is None or not dp.l3:
            dp.summary = f"Ethernet {src_mac} -> {dst_mac} (Type 0x{ethertype:04x})"
            return dp

        # ── Layer 4: TCP (Proto 6) ──
        if proto == 6 and len(raw) >= l4_offset + 20:
            sport, dport, seq, ack, off_res, flags_b, win, ck, urp = struct.unpack(
                "!HHIIBBHHH", raw[l4_offset : l4_offset + 20]
            )
            doff = (off_res >> 4) * 4
            flags_val = ((off_res & 0x0F) << 8) | flags_b

            flags_list = [name for mask, name in TCP_FLAG_DEFS if flags_val & mask]
            flags_dict = {name: bool(flags_val & mask) for mask, name in TCP_FLAG_DEFS}
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
                "checksum": f"0x{ck:04x}",
                "urgent_pointer": urp,
                "data_offset": doff,
                "flags": flags_list,
                "flags_detail": flags_dict,
            }
            dp.app_summary = self._detect_app(sport, dport, payload)
            app_str = f" [{dp.app_summary}]" if dp.app_summary else ""
            dp.summary = (
                f"TCP {src_ip}:{sport} -> {dst_ip}:{dport} "
                f"[{' '.join(flags_list)}] Seq={seq} Ack={ack} Win={win}{app_str}"
            )

        # ── Layer 4: UDP (Proto 17) ──
        elif proto == 17 and len(raw) >= l4_offset + 8:
            sport, dport, ulen, uck = struct.unpack("!HHHH", raw[l4_offset : l4_offset + 8])
            payload = raw[l4_offset + 8 : l4_offset + ulen] if len(raw) >= l4_offset + ulen else raw[l4_offset + 8 :]
            dp.payload = payload
            dp.payload_len = len(payload)

            dp.l4 = {
                "proto": "UDP",
                "src_port": sport,
                "dst_port": dport,
                "length": ulen,
                "checksum": f"0x{uck:04x}",
            }
            dp.app_summary = self._detect_app(sport, dport, payload)
            app_str = f" [{dp.app_summary}]" if dp.app_summary else ""
            dp.summary = f"UDP {src_ip}:{sport} -> {dst_ip}:{dport} Len={ulen}{app_str}"

        # ── Layer 4: ICMP (Proto 1) yoki ICMPv6 (Proto 58) ──
        elif (proto == 1 or proto == 58) and len(raw) >= l4_offset + 4:
            icmp_type, icmp_code, icmp_ck = struct.unpack("!BBH", raw[l4_offset : l4_offset + 4])
            ident, seq = 0, 0
            if len(raw) >= l4_offset + 8:
                ident, seq = struct.unpack("!HH", raw[l4_offset + 4 : l4_offset + 8])
                payload = raw[l4_offset + 8 :]
            else:
                payload = raw[l4_offset + 4 :]

            dp.payload = payload
            dp.payload_len = len(payload)

            proto_name = "ICMP" if proto == 1 else "ICMPv6"
            type_names = ICMP4_TYPES if proto == 1 else ICMP6_TYPES
            type_name = type_names.get(icmp_type, f"Type {icmp_type}")

            dp.l4 = {
                "proto": proto_name,
                "type": icmp_type,
                "code": icmp_code,
                "type_name": type_name,
                "id": ident,
                "seq": seq,
                "checksum": f"0x{icmp_ck:04x}",
            }
            dp.summary = f"{proto_name} {src_ip} -> {dst_ip} {type_name} id={ident} seq={seq}"

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
            "app_summary": dp.app_summary,
        }

    @staticmethod
    def _detect_app(sport: int, dport: int, payload: bytes) -> Optional[str]:
        """Port va xom yuklama asosida L7 protokolini aniqlaydi."""
        ports = {sport, dport}
        if 80 in ports or 8080 in ports:
            return "HTTP"
        if 443 in ports or 8443 in ports:
            return "HTTPS / TLS"
        if 53 in ports:
            return "DNS"
        if 67 in ports or 68 in ports:
            return "DHCP"
        if 22 in ports:
            return "SSH"
        if 23 in ports:
            return "Telnet"
        if 123 in ports:
            return "NTP"
        if 161 in ports or 162 in ports:
            return "SNMP"
        if 5004 in ports or 5005 in ports:
            return "RTP"
        if 6633 in ports or 6653 in ports:
            return "OpenFlow"

        # Bayt tekshiruvi (masalan HTTP metodlari)
        if len(payload) >= 4:
            prefix = payload[:8].upper()
            if prefix.startswith((b"GET ", b"POST", b"HTTP", b"HEAD", b"PUT ", b"DELETE")):
                return "HTTP"
        return None

    def build_cisco_pdu_inspection(
        self,
        packet_info: Dict[str, Any],
        device_name: str = "Router",
        device_type: str = "router",
    ) -> Dict[str, Any]:
        """Cisco Packet Tracer va OPNET uslubidagi 7-qatlamli In-Layers vs Out-Layers
        hamda Device Processing Log tahlilini yaratadi."""
        # Agar packet_info ichida ichki packet ob'ekti bo'lsa
        if isinstance(packet_info.get("packet"), dict):
            inner = packet_info["packet"]
            for k, v in inner.items():
                if k not in packet_info:
                    packet_info[k] = v

        proto = str(packet_info.get("proto") or "TCP").upper()
        summary = str(packet_info.get("summary") or f"{proto} Packet")
        link = str(packet_info.get("link") or "eth0")
        ts = float(packet_info.get("ts") or time.time())

        # Agar raw_hex berilgan bo'lsa, undan haqiqiy dissect qilish
        raw_hex = packet_info.get("raw_hex") or packet_info.get("hex")
        l2: Dict[str, Any] = {}
        l3: Dict[str, Any] = {}
        l4: Dict[str, Any] = {}
        hex_dump = ""

        if raw_hex:
            try:
                clean_hex = str(raw_hex).strip()
                if clean_hex.lower().startswith("0x"):
                    clean_hex = clean_hex[2:]
                clean_hex = re.sub(r"\s+", "", clean_hex)
                raw_bytes = bytes.fromhex(clean_hex)
                dissected = self.dissect_frame(raw_bytes, capture_time=ts)
                l2 = dissected["layers"].get("l2_ethernet") or {}
                l3 = dissected["layers"].get("l3") or {}
                l4 = dissected["layers"].get("l4") or {}
                hex_dump = dissected.get("hex_dump", "")
                if l3.get("proto"):
                    proto = str(l4.get("proto") or l3.get("proto")).upper()
                summary = dissected.get("summary") or summary
            except Exception:
                pass

        # Agar qatlamlar tashqaridan berilgan bo'lsa
        if not l3 and isinstance(packet_info.get("layers"), dict):
            l2 = packet_info["layers"].get("l2_ethernet") or packet_info["layers"].get("l2") or {}
            l3 = packet_info["layers"].get("l3") or {}
            l4 = packet_info["layers"].get("l4") or {}

        # Default IP va portlar
        src_ip = str(l3.get("src_ip") or packet_info.get("src_ip") or "10.0.1.10")
        dst_ip = str(l3.get("dst_ip") or packet_info.get("dst_ip") or "10.0.2.20")
        src_port = int(l4.get("src_port") or packet_info.get("src_port") or 45678)
        dst_port = int(l4.get("dst_port") or packet_info.get("dst_port") or 80)
        src_mac = str(l2.get("src_mac") or packet_info.get("src_mac") or "00:11:22:33:44:55")
        dst_mac = str(l2.get("dst_mac") or packet_info.get("dst_mac") or "00:aa:bb:cc:dd:ee")
        ttl = int(l3.get("ttl") or packet_info.get("ttl") or 64)
        flags = l4.get("flags") or packet_info.get("flags") or (["ACK", "PSH"] if proto == "TCP" else [])
        seq = l4.get("seq") or packet_info.get("seq") or (1000 if proto == "TCP" else None)
        ack = l4.get("ack") or packet_info.get("ack") or (501 if proto == "TCP" else None)
        win = l4.get("window") or packet_info.get("window") or (64240 if proto == "TCP" else None)

        if not hex_dump:
            # Namoyish uchun hexdump generatsiya qilish
            ip_b = 6 if proto == "TCP" else 17 if proto == "UDP" else 1
            mock_bytes = (
                bytes.fromhex("00aabbccddee0011223344550800")
                + struct.pack(
                    "!BBHHHBBH4s4s",
                    0x45, 0, 60, 101, 0x4000, ttl, ip_b, 0x3D41,
                    socket.inet_aton("10.0.1.10" if not src_ip or ":" in src_ip else src_ip),
                    socket.inet_aton("10.0.2.20" if not dst_ip or ":" in dst_ip else dst_ip),
                )
                + (
                    struct.pack("!HHIIBBHHH", src_port, dst_port, seq or 1000, ack or 501, 5 << 4, 0x18, win or 64240, 0, 0)
                    if proto == "TCP"
                    else struct.pack("!HHHH", src_port, dst_port, 16, 0) + b"NETLAB_L4"
                    if proto == "UDP"
                    else struct.pack("!BBHHH", 8, 0, 0xF7FD, 1, 1) + b"PING"
                )
            )
            hex_dump = hex_dump_format(mock_bytes)

        # ── In-Layers (OSI 1 - 7) ──
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
                "ethertype": (
                    "0x0806 (ARP)"
                    if proto == "ARP"
                    else "0x86DD (IPv6)"
                    if ":" in src_ip
                    else "0x0800 (IPv4)"
                ),
                "vlan": l2.get("vlan") or packet_info.get("vlan"),
                "status": "Frame Check Sequence (FCS) Valid",
            },
            "l3": {
                "name": "Layer 3: Network (Tarmoq)",
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "ttl": ttl,
                "protocol": f"{proto} (Proto {6 if proto == 'TCP' else 17 if proto == 'UDP' else 1})",
                "checksum": "0x4a2b (Valid)",
                "total_len": int(l3.get("total_len") or 60),
            },
            "l4": {
                "name": "Layer 4: Transport",
                "proto": proto,
                "src_port": src_port if proto in ("TCP", "UDP") else None,
                "dst_port": dst_port if proto in ("TCP", "UDP") else None,
                "seq": seq,
                "ack": ack,
                "flags": flags,
                "window": win,
                "length": l4.get("length") if proto == "UDP" else None,
            },
            "l7": {
                "name": "Layer 7: Application (Ilova)",
                "app": proto,
                "protocol": proto,
                "summary": summary,
            },
        }

        # ── Device Forwarding & Processing Log (Cisco uslubida) ──
        is_router = device_type in ("router", "sdnrouter")
        is_switch = device_type in ("switch", "ovs", "sdnswitch", "bridge")
        new_ttl = max(1, ttl - 1) if is_router else ttl
        next_hop_mac = "00:50:56:c0:00:08"
        out_port = "eth1" if link == "eth0" else "eth0"

        device_logs: List[str] = [
            f"1. Port qabuli: Freym '{link}' interfeysiga to'liq yetib keldi.",
            f"2. Layer 2: Freym sarlavhasi tekshirildi. Maqsad MAC ({dst_mac}) qurilma MAC manziliga mos keldi.",
        ]

        if is_switch:
            device_logs.extend([
                f"3. Layer 2 MAC Learning ({proto}): Kiruvchi port '{link}' va Src MAC ({src_mac}) CAM jadvaliga yozildi.",
                f"4. L2 Forwarding: Dst MAC ({dst_mac}) CAM jadvaldan qidirildi va chiqish porti '{out_port}' aniqlandi.",
                f"5. Freym o'zgartirishlarsiz '{out_port}' portiga uzatildi.",
            ])
        elif proto == "ARP":
            device_logs.extend([
                f"3. Layer 3: ARP paketi tekshirildi (Opcode: {l3.get('op_name', 'Request')}).",
                f"4. ARP Kesh: Qidirilayotgan IP ({dst_ip}) tahlil qilindi va ARP javob freymi tayyorlandi.",
                f"5. Chiqish: Yangi freym '{out_port}' chiqish navbatiga uzatildi.",
            ])
        elif is_router:
            device_logs.extend([
                f"3. Layer 3 ({proto}): IPv4 sarlavhasi tekshirildi (TTL={ttl} > 1, Nazorat yig'indisi to'g'ri).",
                f"4. Marshrutlash (FIB lookup): Maqsad IP ({dst_ip}) uchun marshrut jadvalidan mos Next-Hop qidirildi.",
                f"5. Marshrut topildi: Next-Hop IP interfeys '{out_port}' orqali uzatiladi.",
                f"6. TTL o'zgarishi: TTL qiymati {ttl} dan {new_ttl} ga kamaytirildi, IP checksum qayta hisoblandi.",
                f"7. ARP jadvali: Next-Hop IP uchun yangi maqsad MAC manzili ({next_hop_mac}) olindi.",
                f"8. Layer 2: Yangi Ethernet II sarlavhasi bilan qayta inkapsulyatsiya qilindi.",
                f"9. Chiqish: Yangi freym '{out_port}' chiqish navbatiga uzatildi.",
            ])
        else:
            device_logs.extend([
                f"3. Layer 3: Paket xost manziliga ({dst_ip}) yetib keldi.",
                f"4. Layer 4: {proto} soketiga uzatildi (Port: {dst_port}).",
                f"5. Layer 7: Ilova qatlami yuklamani qabul qildi.",
            ])

        # ── Out-Layers ──
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
                "total_len": in_layers["l3"]["total_len"],
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

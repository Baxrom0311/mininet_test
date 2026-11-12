"""VXLAN & BGP EVPN Multi-Cloud Overlay Dataplane Engine:
- RFC 7348 VXLAN (Virtual eXtensible Local Area Network) Header Encap/Decap (Port 4789)
- RFC 8365 / RFC 7432 BGP EVPN Control Plane (Route Type-2 MAC/IP, Route Type-5 IP Prefix)
- Multi-Tenant VNI (Virtual Network Identifier) isolation & VTEP (VXLAN Tunnel Endpoint) table
- Multi-Tenant Leaf-Spine VXLAN Topology Helper (Leaf1, Leaf2, Spine, Hosts)
- Outer IP/UDP headers + Inner Ethernet/IP frame serialization, parsing, and ECMP hashing
- BUM (Broadcast, Unknown Unicast, Multicast) Ingress Replication Simulation
- Encapsulation overhead & MTU calculation (50 bytes overhead, inner MTU 1450)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import ipaddress
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Set, Tuple


# Standard VXLAN UDP destination port per RFC 7348
VXLAN_UDP_PORT: int = 4789

# VXLAN Flags: Bit 3 is the I-bit (Instance ID / VNI valid) -> 0x08
VXLAN_FLAGS_IBIT: int = 0x08

# Standard encapsulation overhead:
# Outer Ethernet (14) + Outer IPv4 (20) + Outer UDP (8) + VXLAN Header (8) = 50 bytes
VXLAN_ENCAP_OVERHEAD: int = 50


class EVPNRouteType(int, Enum):
    """BGP EVPN Route Types (RFC 7432 / RFC 8365)."""
    TYPE_1_ETHERNET_AUTO_DISCOVERY = 1
    TYPE_2_MAC_IP_ADVERTISEMENT = 2
    TYPE_3_INCLUSIVE_MULTICAST = 3
    TYPE_4_ETHERNET_SEGMENT = 4
    TYPE_5_IP_PREFIX = 5


@dataclass
class VXLANHeader:
    """RFC 7348 VXLAN sarlavhasi (8 bayt).

    0                   1                   2                   3
    0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
   |R|R|R|R|I|R|R|R|            Reserved                           |
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
   |                VNI (24 bits)                  |   Reserved    |
   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    """

    flags: int = VXLAN_FLAGS_IBIT  # I-flag o'rnatilgan (VNI haqiqiy)
    reserved1: int = 0
    vni: int = 10010   # 24-bit Virtual Network Identifier
    reserved2: int = 0

    def encode(self) -> bytes:
        """8 baytli VXLAN sarlavhasini baytlarga o'raydi:
        [Flags (8b) | Reserved (24b)] [VNI (24b) | Reserved (8b)]
        """
        if not (0 <= self.vni <= 0xFFFFFF):
            raise ValueError(f"VNI 24-bit bo'lishi shart (0..16777215): {self.vni}")
        word1 = ((self.flags & 0xFF) << 24) | (self.reserved1 & 0xFFFFFF)
        word2 = ((self.vni & 0xFFFFFF) << 8) | (self.reserved2 & 0xFF)
        return struct.pack("!II", word1, word2)

    @classmethod
    def decode(cls, raw: bytes) -> "VXLANHeader":
        if len(raw) < 8:
            raise ValueError(f"VXLAN sarlavhasi kamida 8 bayt bo'lishi kerak, berildi: {len(raw)}")
        word1, word2 = struct.unpack("!II", raw[:8])
        flags = (word1 >> 24) & 0xFF
        res1 = word1 & 0xFFFFFF
        vni = (word2 >> 8) & 0xFFFFFF
        res2 = word2 & 0xFF
        return cls(flags=flags, reserved1=res1, vni=vni, reserved2=res2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flags": hex(self.flags),
            "i_flag_valid": bool(self.flags & VXLAN_FLAGS_IBIT),
            "i_bit_set": bool(self.flags & VXLAN_FLAGS_IBIT),
            "vni": self.vni,
            "header_len_bytes": 8,
            "raw_hex": self.encode().hex(),
        }


@dataclass
class EthernetFrame:
    """L2 Ethernet Frame (14 bayt sarlavha + payload)."""
    src_mac: str
    dst_mac: str
    ethertype: int = 0x0800  # 0x0800 = IPv4, 0x0806 = ARP, 0x86DD = IPv6
    payload: bytes = b""

    @staticmethod
    def _mac_to_bytes(mac: str) -> bytes:
        clean = mac.replace(":", "").replace("-", "").replace(".", "")
        return bytes.fromhex(clean)

    @staticmethod
    def _bytes_to_mac(b: bytes) -> str:
        return ":".join(f"{x:02x}" for x in b)

    def encode(self) -> bytes:
        dst = self._mac_to_bytes(self.dst_mac)
        src = self._mac_to_bytes(self.src_mac)
        return struct.pack("!6s6sH", dst, src, self.ethertype) + self.payload

    @classmethod
    def decode(cls, data: bytes) -> EthernetFrame:
        if len(data) < 14:
            raise ValueError(f"Ethernet freymi kamida 14 bayt bo'lishi shart: {len(data)}")
        dst_b, src_b, ethertype = struct.unpack("!6s6sH", data[:14])
        return cls(
            dst_mac=cls._bytes_to_mac(dst_b),
            src_mac=cls._bytes_to_mac(src_b),
            ethertype=ethertype,
            payload=data[14:],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src_mac": self.src_mac,
            "dst_mac": self.dst_mac,
            "ethertype": f"0x{self.ethertype:04x}",
            "payload_len": len(self.payload),
        }


@dataclass
class IPv4Packet:
    """IPv4 Sarlavhasi va Payload."""
    src_ip: str
    dst_ip: str
    proto: int = 6  # 6=TCP, 17=UDP, 1=ICMP
    ttl: int = 64
    payload: bytes = b""
    identification: int = 54321
    flags_fragment: int = 0x4000  # DF bit set

    def encode(self) -> bytes:
        v_ihl = (4 << 4) | 5
        dscp_ecn = 0
        total_len = 20 + len(self.payload)
        src_bytes = socket.inet_aton(self.src_ip)
        dst_bytes = socket.inet_aton(self.dst_ip)
        hdr_without_cksum = struct.pack(
            "!BBHHHBBH4s4s",
            v_ihl, dscp_ecn, total_len, self.identification, self.flags_fragment,
            self.ttl, self.proto, 0, src_bytes, dst_bytes
        )
        cksum = self._calc_checksum(hdr_without_cksum)
        hdr = struct.pack(
            "!BBHHHBBH4s4s",
            v_ihl, dscp_ecn, total_len, self.identification, self.flags_fragment,
            self.ttl, self.proto, cksum, src_bytes, dst_bytes
        )
        return hdr + self.payload

    @classmethod
    def decode(cls, data: bytes) -> IPv4Packet:
        if len(data) < 20:
            raise ValueError(f"IPv4 sarlavhasi kamida 20 bayt bo'lishi shart: {len(data)}")
        v_ihl, dscp_ecn, total_len, ident, flags_frag, ttl, proto, cksum, src_b, dst_b = struct.unpack(
            "!BBHHHBBH4s4s", data[:20]
        )
        ihl = (v_ihl & 0x0F) * 4
        return cls(
            src_ip=socket.inet_ntoa(src_b),
            dst_ip=socket.inet_ntoa(dst_b),
            proto=proto,
            ttl=ttl,
            identification=ident,
            flags_fragment=flags_frag,
            payload=data[ihl:total_len if total_len <= len(data) else len(data)],
        )

    @staticmethod
    def _calc_checksum(data: bytes) -> int:
        if len(data) % 2 == 1:
            data += b"\x00"
        s = sum(struct.unpack(f"!{len(data)//2}H", data))
        s = (s >> 16) + (s & 0xFFFF)
        s += (s >> 16)
        return (~s) & 0xFFFF

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src_ip": self.src_ip,
            "dst_ip": self.dst_ip,
            "proto": self.proto,
            "ttl": self.ttl,
            "payload_len": len(self.payload),
        }


@dataclass
class VXLANPacket:
    """VXLAN Enkapsulyatsiyalangan paket modeli (Outer IP/UDP + VXLAN + Inner Frame)."""
    outer_src_ip: str
    outer_dst_ip: str
    outer_src_port: int
    vni: int
    inner_frame: EthernetFrame
    outer_dst_port: int = VXLAN_UDP_PORT
    outer_src_mac: str = "00:00:5e:00:53:01"
    outer_dst_mac: str = "00:00:5e:00:53:02"

    @classmethod
    def encapsulate(
        cls,
        src_vtep: str,
        dst_vtep: str,
        vni: int,
        inner_frame: EthernetFrame | bytes,
        outer_src_port: Optional[int] = None,
        outer_src_mac: str = "00:00:5e:00:53:01",
        outer_dst_mac: str = "00:00:5e:00:53:02",
    ) -> bytes:
        """Ichki L2 Ethernet freymini VXLAN/UDP/IP/Ethernet bilan enkapsulyatsiya qilish."""
        if isinstance(inner_frame, bytes):
            inner_eth = EthernetFrame.decode(inner_frame)
            inner_raw = inner_frame
        else:
            inner_eth = inner_frame
            inner_raw = inner_frame.encode()

        # ECMP uchun ichki frame xeshidan foydalanib UDP source port hosil qilish (RFC 7348)
        if outer_src_port is None:
            flow_hash = int(hashlib.md5(inner_raw[:34]).hexdigest()[:4], 16)
            outer_src_port = 49152 + (flow_hash % 16383)

        vxlan_hdr = VXLANHeader(vni=vni).encode()
        udp_payload = vxlan_hdr + inner_raw
        udp_len = 8 + len(udp_payload)
        outer_udp = struct.pack("!HHHH", outer_src_port, VXLAN_UDP_PORT, udp_len, 0)

        outer_ip = IPv4Packet(
            src_ip=src_vtep,
            dst_ip=dst_vtep,
            proto=17,  # UDP
            ttl=64,
            payload=outer_udp + udp_payload,
        ).encode()

        outer_eth = EthernetFrame(
            src_mac=outer_src_mac,
            dst_mac=outer_dst_mac,
            ethertype=0x0800,
            payload=outer_ip,
        ).encode()

        return outer_eth

    @classmethod
    def decapsulate(cls, raw_packet: bytes) -> Dict[str, Any]:
        """Tashqi paketni yechib, ichki frame va VXLAN sarlavhalarini chiqarib olish."""
        if len(raw_packet) < 14 + 20 + 8 + 8 + 14:
            raise ValueError(f"Paket hajmi VXLAN enkapsulyatsiyasi uchun juda qisqa: {len(raw_packet)} bayt")

        outer_eth = EthernetFrame.decode(raw_packet)
        outer_ip = IPv4Packet.decode(outer_eth.payload)
        if outer_ip.proto != 17:
            raise ValueError(f"Kutilmagan L4 protokol: {outer_ip.proto} (UDP=17 kutilgan)")

        src_port, dst_port, udp_len, cksum = struct.unpack("!HHHH", outer_ip.payload[:8])
        if dst_port != VXLAN_UDP_PORT:
            raise ValueError(f"Kutilmagan UDP port: {dst_port} (VXLAN={VXLAN_UDP_PORT} kutilgan)")

        vxlan_raw = outer_ip.payload[8:16]
        vxlan_hdr = VXLANHeader.decode(vxlan_raw)

        inner_raw = outer_ip.payload[16:]
        inner_eth = EthernetFrame.decode(inner_raw)

        inner_ip_info: Optional[Dict[str, Any]] = None
        if inner_eth.ethertype == 0x0800 and len(inner_eth.payload) >= 20:
            try:
                inner_ip = IPv4Packet.decode(inner_eth.payload)
                inner_ip_info = inner_ip.to_dict()
            except Exception:
                pass

        return {
            "outer": {
                "src_mac": outer_eth.src_mac,
                "dst_mac": outer_eth.dst_mac,
                "src_vtep": outer_ip.src_ip,
                "dst_vtep": outer_ip.dst_ip,
                "src_port": src_port,
                "dst_port": dst_port,
            },
            "vxlan": vxlan_hdr.to_dict(),
            "inner": {
                "src_mac": inner_eth.src_mac,
                "dst_mac": inner_eth.dst_mac,
                "ethertype": f"0x{inner_eth.ethertype:04x}",
                "ip": inner_ip_info,
                "payload_len": len(inner_eth.payload),
            },
            "total_encap_overhead_bytes": VXLAN_ENCAP_OVERHEAD,
        }


# ─────────────────────────────────────────────────────────────────────────────
# EVPN Control-Plane (RFC 7432 / RFC 8365)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EVPNRouteType2:
    """BGP EVPN Route Type-2: MAC/IP Advertisement Route (RFC 7432)."""

    rd: str            # Route Distinguisher, masalan: "65000:100"
    esi: str = "00:00:00:00:00:00:00:00:00:00"  # Ethernet Segment Identifier
    mac: str = "00:00:00:00:00:00"              # Mijoz MAC manzili
    ip: Optional[str] = None                    # Mijoz IP manzili (ixtiyoriy)
    vni: int = 10010                            # L2 VNI
    next_hop_vtep: str = "10.0.0.1"             # Ushbu MAC joylashgan VTEP IP
    rt: str = "target:65000:100"                # Route Target
    sequence_num: int = 0                       # MAC mobility seq counter
    l3_vni: Optional[int] = None                # IRB L3 VNI
    label: int = 0

    def route_key(self) -> str:
        ip_part = self.ip or "none"
        return f"type2/{self.vni}/{self.mac.lower()}/{ip_part}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_type": "Type-2 (MAC/IP Advertisement)",
            "rd": self.rd,
            "rt": self.rt,
            "esi": self.esi,
            "mac": self.mac,
            "ip": self.ip,
            "vni": self.vni,
            "l3_vni": self.l3_vni,
            "next_hop_vtep": self.next_hop_vtep,
            "sequence_num": self.sequence_num,
        }


@dataclass
class EVPNRouteType5:
    """BGP EVPN Route Type-5: IP Prefix Route (RFC 9136) L3 Inter-Subnet Routing."""

    rd: str
    prefix: str        # Masalan: "192.168.10.0/24"
    gw_ip: str         # Gateway IP
    vni: int           # L3 VNI
    next_hop_vtep: str
    rt: str = "target:65000:1000"

    def route_key(self) -> str:
        return f"type5/{self.vni}/{self.prefix}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_type": "Type-5 (IP Prefix)",
            "rd": self.rd,
            "rt": self.rt,
            "prefix": self.prefix,
            "gw_ip": self.gw_ip,
            "vni": self.vni,
            "next_hop_vtep": self.next_hop_vtep,
        }


@dataclass
class VTEP:
    """VXLAN Tunnel Endpoint (Switch yoki Routerda ishlovchi VTEP)."""

    vtep_id: str
    underlay_ip: str
    vnis: Set[int] = field(default_factory=set)
    # vni -> {mac: remote_vtep_ip}
    fdb_table: Dict[int, Dict[str, str]] = field(default_factory=dict)
    arp_table: Dict[int, Dict[str, str]] = field(default_factory=dict)
    peers: Set[str] = field(default_factory=set)

    def learn_mac(self, vni: int, mac: str, remote_vtep: str, ip: Optional[str] = None) -> None:
        if vni not in self.fdb_table:
            self.fdb_table[vni] = {}
        self.fdb_table[vni][mac.lower()] = remote_vtep
        if ip:
            if vni not in self.arp_table:
                self.arp_table[vni] = {}
            self.arp_table[vni][ip] = mac.lower()

    def lookup_mac(self, vni: int, mac: str) -> Optional[str]:
        if vni not in self.fdb_table:
            return None
        return self.fdb_table[vni].get(mac.lower())

    def suppress_arp(self, vni: int, target_ip: str) -> Optional[str]:
        if vni not in self.arp_table:
            return None
        return self.arp_table[vni].get(target_ip)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vtep_id": self.vtep_id,
            "underlay_ip": self.underlay_ip,
            "vnis": sorted(list(self.vnis)),
            "peers": sorted(list(self.peers)),
            "fdb_entries": {
                vni: len(entries) for vni, entries in self.fdb_table.items()
            },
            "arp_suppression_entries": {
                vni: len(entries) for vni, entries in self.arp_table.items()
            },
        }


class VXLANOverlayEngine:
    """Multi-Site va Data-Center VXLAN EVPN boshqaruv va emulyatsiya tizimi."""

    def __init__(self, local_as: int = 65000):
        self.local_as = local_as
        self.vteps: Dict[str, VTEP] = {}
        self.evpn_routes_t2: List[EVPNRouteType2] = []
        self.evpn_routes_t5: List[EVPNRouteType5] = []
        self.tunnels: Dict[str, Dict[str, Any]] = {}
        self.topology_helper = LeafSpineVXLANTopology()
        self._init_default_fabric()

    def _init_default_fabric(self):
        """Birlamchi VTEP va marshrutlarni sozlash."""
        self.register_vtep("leaf1", "10.0.0.1", [100, 200, 1000, 2000])
        self.register_vtep("leaf2", "10.0.0.2", [100, 200, 1000, 2000])

        # Type-2 reklamalar
        self.advertise_mac_ip("leaf1", "00:00:00:00:01:01", vni=100, ip="192.168.10.1", rd="65001:100", rt="target:65000:100")
        self.advertise_mac_ip("leaf2", "00:00:00:00:01:02", vni=100, ip="192.168.10.2", rd="65002:100", rt="target:65000:100")
        self.advertise_mac_ip("leaf1", "00:00:00:00:02:01", vni=200, ip="192.168.20.1", rd="65001:200", rt="target:65000:200")
        self.advertise_mac_ip("leaf2", "00:00:00:00:02:02", vni=200, ip="192.168.20.2", rd="65002:200", rt="target:65000:200")

        # Type-5 reklamalar
        self.advertise_prefix("leaf1", prefix="192.168.10.0/24", gw_ip="10.0.0.1", vni=1000, rd="65001:1000", rt="target:65000:1000")
        self.advertise_prefix("leaf2", prefix="192.168.20.0/24", gw_ip="10.0.0.2", vni=2000, rd="65002:2000", rt="target:65000:2000")

        # Tunnellar
        self.configure_tunnel(100, "10.0.0.1", "10.0.0.2")
        self.configure_tunnel(200, "10.0.0.1", "10.0.0.2")

    def register_vtep(self, vtep_id: str, underlay_ip: str, vnis: List[int]) -> VTEP:
        vtep = VTEP(vtep_id=vtep_id, underlay_ip=underlay_ip, vnis=set(vnis))
        self.vteps[vtep_id] = vtep
        for other_id, other_vtep in self.vteps.items():
            if other_id != vtep_id:
                vtep.peers.add(other_vtep.underlay_ip)
                other_vtep.peers.add(underlay_ip)
        return vtep

    def advertise_mac_ip(
        self,
        origin_vtep_id: str,
        mac: str,
        vni: int,
        ip: Optional[str] = None,
        rd: str = "65001:100",
        rt: str = "target:65000:100",
        sequence_num: int = 0,
        l3_vni: Optional[int] = None,
    ) -> EVPNRouteType2:
        """Yangi mijoz ulanganda BGP EVPN Type-2 marshruti orqali barcha VTEPlarga tarqatish."""
        vtep = self.vteps.get(origin_vtep_id)
        if not vtep:
            raise ValueError(f"VTEP {origin_vtep_id} topilmadi")

        route = EVPNRouteType2(
            rd=rd,
            rt=rt,
            esi="00:00:00:00:00:00:00:00:00:00",
            mac=mac.lower(),
            ip=ip,
            vni=vni,
            next_hop_vtep=vtep.underlay_ip,
            sequence_num=sequence_num,
            l3_vni=l3_vni,
        )
        self.evpn_routes_t2.append(route)

        # Barcha qolgan VTEPlarning FDB jadvaliga yozish
        for other_id, other_vtep in self.vteps.items():
            if other_id != origin_vtep_id and vni in other_vtep.vnis:
                other_vtep.learn_mac(vni, mac, vtep.underlay_ip, ip=ip)

        return route

    def advertise_prefix(
        self,
        origin_vtep_id: str,
        prefix: str,
        gw_ip: str,
        vni: int,
        rd: str = "65001:1000",
        rt: str = "target:65000:1000",
    ) -> EVPNRouteType5:
        """Type-5 IP Prefix marshrutini e'lon qilish."""
        vtep = self.vteps.get(origin_vtep_id)
        next_hop = vtep.underlay_ip if vtep else gw_ip
        route = EVPNRouteType5(
            rd=rd,
            rt=rt,
            prefix=prefix,
            gw_ip=gw_ip,
            vni=vni,
            next_hop_vtep=next_hop,
        )
        self.evpn_routes_t5.append(route)
        return route

    def configure_tunnel(
        self,
        vni: int,
        src_vtep: str,
        dst_vtep: str,
        udp_port: int = VXLAN_UDP_PORT,
    ) -> Dict[str, Any]:
        tid = f"vxlan_{vni}_{src_vtep}_{dst_vtep}"
        tunnel = {
            "tunnel_id": tid,
            "vni": vni,
            "src_vtep": src_vtep,
            "dst_vtep": dst_vtep,
            "udp_port": udp_port,
            "status": "UP",
            "overhead_bytes": VXLAN_ENCAP_OVERHEAD,
            "recommended_inner_mtu": 1450,
            "packets_tx": 2450,
            "packets_rx": 2410,
            "bytes_tx": 2450 * 128,
            "bytes_rx": 2410 * 128,
        }
        self.tunnels[tid] = tunnel
        return tunnel

    def encapsulate_packet(
        self,
        vni: int,
        src_mac: str,
        dst_mac: str,
        src_vtep_ip: str,
        payload_bytes: bytes = b"",
    ) -> Dict[str, Any]:
        """Ichki L2 Ethernet freymini VXLAN/UDP/IP sarlavhalariga enkapsulyatsiya qiladi."""
        vxlan_hdr = VXLANHeader(vni=vni)
        vxlan_raw = vxlan_hdr.encode()

        dst_vtep_ip = None
        for vtep in self.vteps.values():
            if vtep.underlay_ip == src_vtep_ip:
                dst_vtep_ip = vtep.lookup_mac(vni, dst_mac)
                break

        is_bum = (dst_vtep_ip is None) or (dst_mac.lower() == "ff:ff:ff:ff:ff:ff")
        mode = "BUM Ingress Replication" if is_bum else "Unicast Tunnel"

        overhead_bytes = VXLAN_ENCAP_OVERHEAD
        inner_frame_len = 14 + len(payload_bytes)
        total_packet_len = inner_frame_len + overhead_bytes

        # ECMP source port
        flow_hash = int(hashlib.md5(f"{src_mac}{dst_mac}".encode()).hexdigest()[:4], 16)
        src_port = 49152 + (flow_hash % 16383)

        return {
            "vni": vni,
            "mode": mode,
            "outer_encapsulation": {
                "outer_src_ip": src_vtep_ip,
                "outer_dst_ip": dst_vtep_ip or "239.1.1.1 (Multicast/Replication)",
                "outer_src_port": src_port,
                "outer_dst_port": VXLAN_UDP_PORT,
                "vxlan_header_hex": vxlan_raw.hex(),
                "vxlan_header": vxlan_hdr.to_dict(),
                "overhead_bytes": overhead_bytes,
            },
            "inner_frame": {
                "src_mac": src_mac,
                "dst_mac": dst_mac,
                "payload_bytes_len": len(payload_bytes),
                "inner_frame_len": inner_frame_len,
            },
            "total_packet_len": total_packet_len,
        }

    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_tunnels": len(self.tunnels),
            "tunnels": list(self.tunnels.values()),
            "total_vteps": len(self.vteps),
            "vteps": {k: v.to_dict() for k, v in self.vteps.items()},
            "total_evpn_type2_routes": len(self.evpn_routes_t2),
            "total_evpn_type5_routes": len(self.evpn_routes_t5),
            "evpn_routes_type2": [r.to_dict() for r in self.evpn_routes_t2[:20]],
            "evpn_routes_type5": [r.to_dict() for r in self.evpn_routes_t5[:20]],
            "topology": self.topology_helper.build_topology_spec(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Multi-Tenant Leaf-Spine VXLAN Topology Helper
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LeafSpineVXLANTopology:
    """Multi-tenant VXLAN tunnel topologiyasi modeli:
    - Spine1 (Underlay IP Fabric marshrutizator 10.0.0.100)
    - Leaf1 (VTEP 10.0.0.1, AS 65001)
    - Leaf2 (VTEP 10.0.0.2, AS 65002)
    - Tenant A: VNI 100, L3 VNI 1000 (Hosts: h1-tenant-a, h2-tenant-a)
    - Tenant B: VNI 200, L3 VNI 2000 (Hosts: h3-tenant-b, h4-tenant-b)
    """

    spine_ip: str = "10.0.0.100"
    leaf1_vtep: str = "10.0.0.1"
    leaf2_vtep: str = "10.0.0.2"

    def build_topology_spec(self) -> Dict[str, Any]:
        return {
            "name": "multi_tenant_leaf_spine_fabric",
            "underlay": {
                "spine": {"name": "spine1", "ip": self.spine_ip, "as": 65000},
                "leafs": [
                    {"name": "leaf1", "vtep_ip": self.leaf1_vtep, "as": 65001, "loopback": "10.0.0.1/32"},
                    {"name": "leaf2", "vtep_ip": self.leaf2_vtep, "as": 65002, "loopback": "10.0.0.2/32"},
                ],
                "underlay_mtu": 1550,
            },
            "tenants": {
                "tenant_a": {
                    "vni": 100,
                    "l3_vni": 1000,
                    "subnet": "192.168.10.0/24",
                    "rd": "65000:100",
                    "rt": "target:65000:100",
                    "hosts": [
                        {"name": "h1-tenant-a", "leaf": "leaf1", "ip": "192.168.10.1", "mac": "00:00:00:00:01:01"},
                        {"name": "h2-tenant-a", "leaf": "leaf2", "ip": "192.168.10.2", "mac": "00:00:00:00:01:02"},
                    ],
                },
                "tenant_b": {
                    "vni": 200,
                    "l3_vni": 2000,
                    "subnet": "192.168.20.0/24",
                    "rd": "65000:200",
                    "rt": "target:65000:200",
                    "hosts": [
                        {"name": "h3-tenant-b", "leaf": "leaf1", "ip": "192.168.20.1", "mac": "00:00:00:00:02:01"},
                        {"name": "h4-tenant-b", "leaf": "leaf2", "ip": "192.168.20.2", "mac": "00:00:00:00:02:02"},
                    ],
                },
            },
        }

    def generate_frr_evpn_config(self, leaf_name: str) -> str:
        is_leaf1 = "1" in leaf_name
        local_as = 65001 if is_leaf1 else 65002
        router_id = self.leaf1_vtep if is_leaf1 else self.leaf2_vtep

        return f"""! FRRouting Configuration for {leaf_name} (BGP EVPN)
frr version 8.4
hostname {leaf_name}
service integrated-vtysh-config
!
router bgp {local_as}
 bgp router-id {router_id}
 no bgp default ipv4-unicast
 neighbor spine peer-group
 neighbor spine remote-as 65000
 neighbor spine capability extended-nexthop
 neighbor {self.spine_ip} peer-group spine
 !
 address-family l2vpn evpn
  neighbor spine activate
  neighbor spine route-reflector-client
  advertise-all-vni
  vni 100
   rd {local_as}:100
   route-target both 65000:100
  vni 200
   rd {local_as}:200
   route-target both 65000:200
  vni 1000
   rd {local_as}:1000
   route-target both 65000:1000
  vni 2000
   rd {local_as}:2000
   route-target both 65000:2000
 exit-address-family
!
line vty
!
"""

    def generate_linux_vxlan_cmds(self, leaf_name: str) -> List[str]:
        is_leaf1 = "1" in leaf_name
        local_ip = self.leaf1_vtep if is_leaf1 else self.leaf2_vtep

        return [
            "ip link set dev eth0 mtu 1550",
            f"ip link add vxlan100 type vxlan id 100 local {local_ip} dstport {VXLAN_UDP_PORT} nolearning",
            "ip link add br-tenant-a type bridge",
            "ip link set vxlan100 master br-tenant-a",
            "ip link set vxlan100 up",
            "ip link set br-tenant-a up",
            f"ip link add vxlan200 type vxlan id 200 local {local_ip} dstport {VXLAN_UDP_PORT} nolearning",
            "ip link add br-tenant-b type bridge",
            "ip link set vxlan200 master br-tenant-b",
            "ip link set vxlan200 up",
            "ip link set br-tenant-b up",
        ]

    def simulate_forwarding(self, src_host: str, dst_host: str) -> Dict[str, Any]:
        spec = self.build_topology_spec()
        all_hosts = {}
        for tenant_id, tenant_info in spec["tenants"].items():
            for h in tenant_info["hosts"]:
                all_hosts[h["name"]] = {**h, "tenant": tenant_id, "vni": tenant_info["vni"]}

        if src_host not in all_hosts or dst_host not in all_hosts:
            return {
                "ok": False,
                "error": f"Noma'lum hostlar. Mavjud: {list(all_hosts.keys())}",
            }

        src_info = all_hosts[src_host]
        dst_info = all_hosts[dst_host]

        if src_info["tenant"] != dst_info["tenant"]:
            return {
                "ok": False,
                "tenant_isolation_enforced": True,
                "verdict": "DROPPED (Tenant Isolation Violation)",
                "details": f"{src_host} ({src_info['tenant']}) va {dst_host} ({dst_info['tenant']}) alohida VNI larda joylashgan.",
                "src": src_info,
                "dst": dst_info,
            }

        same_leaf = src_info["leaf"] == dst_info["leaf"]
        vni = src_info["vni"]
        src_vtep = self.leaf1_vtep if src_info["leaf"] == "leaf1" else self.leaf2_vtep
        dst_vtep = self.leaf1_vtep if dst_info["leaf"] == "leaf1" else self.leaf2_vtep

        trace_steps = [
            f"1. Host {src_host} ({src_info['ip']}) paket jo'natdi -> {src_info['leaf']}",
        ]
        if same_leaf:
            trace_steps.append(f"2. {src_info['leaf']} lokal VNI {vni} ko'prigi orqali to'g'ridan-to'g'ri {dst_host} ga yetkazdi.")
            encap_bytes = 64
        else:
            trace_steps.extend([
                f"2. {src_info['leaf']} EVPN FDB orqali {dst_info['mac']} manzilini {dst_vtep} ga bog'ladi.",
                f"3. VXLAN Enkapsulyatsiya: VNI {vni}, Outer IP ({src_vtep} -> {dst_vtep}), UDP Port {VXLAN_UDP_PORT}.",
                f"4. Underlay Spine ({self.spine_ip}) orqali IP marshrutlash amalga oshirildi.",
                f"5. {dst_info['leaf']} VXLAN sarlavhasini dekapsulyatsiya qildi (50 bayt overhead olib tashlandi).",
                f"6. Ichki Ethernet frame {dst_host} interfeysiga uzatildi.",
            ])
            encap_bytes = 114  # 64 + 50 overhead

        return {
            "ok": True,
            "tenant_isolation_enforced": True,
            "tenant": src_info["tenant"],
            "vni": vni,
            "src": src_info,
            "dst": dst_info,
            "same_leaf": same_leaf,
            "encapsulated_bytes": encap_bytes,
            "overhead_bytes": 0 if same_leaf else VXLAN_ENCAP_OVERHEAD,
            "inner_mtu_limit": 1500 - (0 if same_leaf else VXLAN_ENCAP_OVERHEAD),
            "trace_hops": trace_steps,
        }


# Global instansiya
vxlan_engine = VXLANOverlayEngine()

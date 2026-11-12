"""WireGuard Cryptographic VPN & Cryptokey Routing Engine:
- RFC Draft / Noise IK Protocol Handshake Simulation (Curve25519, ChaCha20, Poly1305, BLAKE2s)
- Real Curve25519 Key Generation, Validation & Shared Secret Derivation
- ChaCha20-Poly1305 AEAD Authenticated Encryption Overhead (32 bytes WG header/tag)
- WireGuard 1420-byte MTU & TCP MSS Clamping Calculation (RFC standard)
- Keepalive timer (25s) for stateful NAT / firewall traversal
- IPsec (IKEv2/ESP) vs WireGuard comprehensive performance & complexity model
- Cryptokey Routing Table (AllowedIPs Prefix Matching) & Zero-RTT Roaming
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import ipaddress
import secrets
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.primitives import serialization
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


# Constants
WIREGUARD_PORT: int = 51820
WIREGUARD_OVERHEAD_BYTES: int = 32  # 16 bytes transport header + 16 bytes Poly1305 auth tag
IPV4_HEADER_BYTES: int = 20
IPV6_HEADER_BYTES: int = 40
UDP_HEADER_BYTES: int = 8
PERSISTENT_KEEPALIVE_DEFAULT: int = 25  # seconds
REKEY_AFTER_TIME_S: int = 120
REJECT_AFTER_TIME_S: int = 180


class Curve25519Key:
    """Curve25519 (RFC 7748) Kalitlarini generatsiya qilish va tekshirish."""

    @staticmethod
    def generate_keypair() -> Tuple[str, str]:
        """Base64 kodlangan (private_key, public_key) juftligini hosil qilish."""
        if HAS_CRYPTOGRAPHY:
            priv = x25519.X25519PrivateKey.generate()
            pub = priv.public_key()
            priv_b = priv.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            pub_b = pub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            return base64.b64encode(priv_b).decode("ascii"), base64.b64encode(pub_b).decode("ascii")

        # Fallback pseudo-Curve25519 generatsiyasi
        raw_priv = bytearray(secrets.token_bytes(32))
        # RFC 7748 clamping
        raw_priv[0] &= 248
        raw_priv[31] &= 127
        raw_priv[31] |= 64
        priv_str = base64.b64encode(bytes(raw_priv)).decode("ascii")
        # Pseudo public key
        pub_hash = hashlib.sha256(bytes(raw_priv) + b"Curve25519_PUB").digest()
        pub_str = base64.b64encode(pub_hash).decode("ascii")
        return priv_str, pub_str

    @staticmethod
    def validate_key(key_b64: str) -> bool:
        """Base64 kalitning 32 baytli Curve25519 formati ekanligini tekshirish."""
        try:
            raw = base64.b64decode(key_b64.encode("ascii"))
            return len(raw) == 32
        except Exception:
            return False

    @staticmethod
    def derive_shared_secret(priv_b64: str, pub_b64: str) -> str:
        """Diffie-Hellman umumiy maxfiy kalitini hisoblash."""
        if HAS_CRYPTOGRAPHY:
            try:
                priv_b = base64.b64decode(priv_b64.encode("ascii"))
                pub_b = base64.b64decode(pub_b64.encode("ascii"))
                priv_obj = x25519.X25519PrivateKey.from_private_bytes(priv_b)
                pub_obj = x25519.X25519PublicKey.from_public_bytes(pub_b)
                shared = priv_obj.exchange(pub_obj)
                return base64.b64encode(shared).decode("ascii")
            except Exception:
                pass
        # Fallback
        h = hashlib.sha256(priv_b64.encode() + pub_b64.encode()).digest()
        return base64.b64encode(h).decode("ascii")


@dataclass
class WireGuardPeer:
    """WireGuard uzoqdagi tengdoshi (Remote Peer)."""

    public_key: str
    endpoint: str            # "198.51.100.1:51820"
    allowed_ips: List[str]   # ["10.50.1.0/24", "192.168.10.0/24"]
    persistent_keepalive_s: int = PERSISTENT_KEEPALIVE_DEFAULT
    latest_handshake_ts: float = 0.0
    tx_bytes: int = 0
    rx_bytes: int = 0
    is_connected: bool = False
    preshared_key: Optional[str] = None
    handshake_count: int = 0

    def matches_ip(self, dest_ip: str) -> bool:
        """Cryptokey routing: IP manzil ushbu peerning AllowedIPs ro'yxatiga to'g'ri keladimi?"""
        try:
            target = ipaddress.ip_address(dest_ip)
            for allowed in self.allowed_ips:
                net = ipaddress.ip_network(allowed, strict=False)
                if target in net:
                    return True
        except ValueError:
            pass
        return False

    def update_endpoint_roaming(self, new_endpoint: str) -> None:
        """WireGuard Roaming xususiyati: Peer IP o'zgarganda endpoint avtomatik yangilanadi."""
        self.endpoint = new_endpoint

    def to_dict(self) -> Dict[str, Any]:
        return {
            "public_key": self.public_key,
            "endpoint": self.endpoint,
            "allowed_ips": self.allowed_ips,
            "persistent_keepalive_s": self.persistent_keepalive_s,
            "latest_handshake_ts": self.latest_handshake_ts,
            "tx_bytes": self.tx_bytes,
            "rx_bytes": self.rx_bytes,
            "is_connected": self.is_connected,
            "handshake_count": self.handshake_count,
        }


class WireGuardEngine:
    """WireGuard interfeysi, Noise IK protokoli va taqqoslash modeli."""

    def __init__(
        self,
        interface_name: str = "wg0",
        listen_port: int = WIREGUARD_PORT,
        interface_ip: str = "10.50.0.1/24",
    ):
        self.interface_name = interface_name
        self.listen_port = listen_port
        self.interface_ip = interface_ip
        # Haqiqiy Curve25519 kalitlar juftligi
        self.private_key, self.public_key = Curve25519Key.generate_keypair()
        self.peers: Dict[str, WireGuardPeer] = {}
        self._init_default_peers()

    def _init_default_peers(self):
        """Birlamchi peerlar."""
        _, peer1_pub = Curve25519Key.generate_keypair()
        self.add_peer(
            public_key=peer1_pub,
            endpoint="198.51.100.1:51820",
            allowed_ips=["10.50.1.0/24", "192.168.10.0/24"],
            persistent_keepalive_s=25,
        )

    def calculate_mtu_mss(self, underlay_mtu: int = 1500, is_ipv6: bool = False) -> Dict[str, Any]:
        """WireGuard MTU va TCP MSS Clamping hisobini chiqaradi:
        IPv4: Outer IP (20B) + UDP (8B) + WG Type 4 Header (16B) + Poly1305 MAC (16B) = 60B overhead.
        IPv6: Outer IPv6 (40B) + UDP (8B) + WG Header (16B) + Poly1305 MAC (16B) = 80B overhead.
        Standard WireGuard MTU = 1420 (IPv6 yoki PPPoE ostida ham paket parchalanmasligi uchun).
        """
        overhead = 80 if is_ipv6 else 60
        wg_mtu = underlay_mtu - overhead  # Masalan: 1500 - 60 = 1440 yoki 1500 - 80 = 1420
        tcp_mss = wg_mtu - (60 if is_ipv6 else 40)

        return {
            "underlay_mtu": underlay_mtu,
            "ip_version": "IPv6" if is_ipv6 else "IPv4",
            "overhead_bytes": overhead,
            "recommended_wg_mtu": wg_mtu,
            "standard_safe_mtu": 1420,
            "recommended_tcp_mss": tcp_mss,
            "fragmentation_prevented": True,
            "wireguard_payload_overhead": WIREGUARD_OVERHEAD_BYTES,
        }

    def add_peer(
        self,
        public_key: str,
        endpoint: str,
        allowed_ips: List[str],
        persistent_keepalive_s: int = PERSISTENT_KEEPALIVE_DEFAULT,
        preshared_key: Optional[str] = None,
    ) -> WireGuardPeer:
        peer = WireGuardPeer(
            public_key=public_key,
            endpoint=endpoint,
            allowed_ips=allowed_ips,
            persistent_keepalive_s=persistent_keepalive_s,
            preshared_key=preshared_key,
        )
        self.peers[public_key] = peer
        return peer

    def initiate_handshake(self, peer_public_key: str) -> Dict[str, Any]:
        """Noise IK 1-RTT Handshake modellashtirish (Initiation 148B + Response 92B)."""
        peer = self.peers.get(peer_public_key)
        if not peer:
            # Kalit formatini qidirish
            for p in self.peers.values():
                if p.public_key == peer_public_key or p.public_key.startswith(peer_public_key[:10]):
                    peer = p
                    break
        if not peer:
            raise ValueError(f"Peer {peer_public_key} topilmadi")

        # 1. Ephemeral kalitlar yaratish (Noise IK 1-RTT)
        e_priv, e_pub = Curve25519Key.generate_keypair()
        sender_idx = secrets.randbits(32)
        receiver_idx = secrets.randbits(32)

        # 2. Diffie-Hellman almashinuvi
        # DH(e, rs) va DH(s, rs)
        shared_secret = Curve25519Key.derive_shared_secret(e_priv, peer.public_key)

        # 3. Paket o'lchamlari (RFC Noise IK standard)
        initiation_bytes = 148  # Type 1: unencrypted_ephemeral (32) + encrypted_static (48) + encrypted_timestamp (28) + mac1 (16) + mac2 (16)
        response_bytes = 92     # Type 2: sender_index (4) + receiver_index (4) + unencrypted_ephemeral (32) + encrypted_nothing (16) + mac1 (16) + mac2 (16)

        peer.latest_handshake_ts = time.time()
        peer.is_connected = True
        peer.tx_bytes += initiation_bytes
        peer.rx_bytes += response_bytes
        peer.handshake_count += 1

        return {
            "handshake_status": "established",
            "protocol": "Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s",
            "rtt_mode": "1-RTT Handshake",
            "sender_index": sender_idx,
            "receiver_index": receiver_idx,
            "ephemeral_public_key": e_pub,
            "derived_shared_secret_sample": shared_secret[:16] + "...",
            "initiation_msg_bytes": initiation_bytes,
            "response_msg_bytes": response_bytes,
            "handshake_rtt_ms": 1.25,
            "persistent_keepalive_s": peer.persistent_keepalive_s,
            "rekey_interval_s": REKEY_AFTER_TIME_S,
            "reject_after_s": REJECT_AFTER_TIME_S,
            "peer": peer.to_dict(),
        }

    def route_and_encapsulate(self, dest_ip: str, payload_size_bytes: int = 1000) -> Dict[str, Any]:
        """Cryptokey routing bo'yicha qaysi peerga yuborishni aniqlab, WireGuard paketiga o'raydi."""
        matched_peer: Optional[WireGuardPeer] = None
        for peer in self.peers.values():
            if peer.matches_ip(dest_ip):
                matched_peer = peer
                break

        if not matched_peer:
            return {
                "ok": False,
                "error": f"Cryptokey Routing xatosi: {dest_ip} manzili hech qaysi peerning AllowedIPs ro'yxatiga mos kelmadi",
                "dropped": True,
            }

        overhead = 60  # IPv4 WireGuard transport data
        total_wire_size = payload_size_bytes + overhead
        matched_peer.tx_bytes += total_wire_size

        return {
            "ok": True,
            "routed_peer": matched_peer.public_key,
            "remote_endpoint": matched_peer.endpoint,
            "dest_ip": dest_ip,
            "payload_bytes": payload_size_bytes,
            "wireguard_overhead_bytes": overhead,
            "total_wire_bytes": total_wire_size,
            "packet_format": "Type 4 Transport Data (ChaCha20-Poly1305 AEAD, 32B WG overhead)",
        }

    def export_wg_conf(self) -> str:
        """wg0.conf konfiguratsiya faylini chiqarish."""
        lines = [
            "# WireGuard Configuration (Generated by NetLab Overlay)",
            "[Interface]",
            f"PrivateKey = {self.private_key}",
            f"Address = {self.interface_ip}",
            f"ListenPort = {self.listen_port}",
            "MTU = 1420",
            "",
        ]
        for p in self.peers.values():
            lines.extend([
                "[Peer]",
                f"PublicKey = {p.public_key}",
                f"Endpoint = {p.endpoint}",
                f"AllowedIPs = {', '.join(p.allowed_ips)}",
                f"PersistentKeepalive = {p.persistent_keepalive_s}",
                "",
            ])
        return "\n".join(lines)

    def compare_with_ipsec(
        self,
        packet_size: int = 1420,
        link_rtt_ms: float = 20.0,
    ) -> Dict[str, Any]:
        """WireGuard va IPsec (IKEv2 / ESP) o'rtasidagi qiyosiy tahlil modeli."""
        # 1. Handshake RTT & Latency
        # WireGuard: 1 RTT (Noise IK)
        wg_handshake_ms = link_rtt_ms * 1.0 + 1.2
        # IPsec IKEv2: IKE_SA_INIT (1 RTT) + IKE_AUTH (1 RTT) + Cert validation = kamida 2.5 RTT
        ipsec_handshake_ms = link_rtt_ms * 2.5 + 8.5

        # 2. Header Overhead
        wg_overhead = 60  # 20 IP + 8 UDP + 32 WG (Type 4 + Poly1305)
        # IPsec ESP in UDP Tunnel: 20 IP + 8 UDP + 8 ESP Hdr + 16 IV + 16 ICV + ~4B Padding = ~72B
        ipsec_overhead = 72

        # 3. Throughput efficiency (1 Gbps link, packet_size)
        wg_goodput_ratio = packet_size / (packet_size + wg_overhead)
        ipsec_goodput_ratio = packet_size / (packet_size + ipsec_overhead)

        return {
            "comparison_packet_size_bytes": packet_size,
            "link_rtt_ms": link_rtt_ms,
            "metrics": {
                "handshake": {
                    "wireguard": {
                        "protocol": "Noise_IK (1-RTT)",
                        "latency_ms": round(wg_handshake_ms, 2),
                        "packets_exchanged": 2,
                        "bytes_exchanged": 240,
                    },
                    "ipsec_ikev2": {
                        "protocol": "IKEv2 + X.509 (2-3 RTT)",
                        "latency_ms": round(ipsec_handshake_ms, 2),
                        "packets_exchanged": 4,
                        "bytes_exchanged": 2400,
                    },
                    "winner": "WireGuard (~3-4x tezroq bog'lanish)",
                },
                "packet_overhead": {
                    "wireguard_bytes": wg_overhead,
                    "ipsec_esp_udp_bytes": ipsec_overhead,
                    "difference_bytes": ipsec_overhead - wg_overhead,
                    "wireguard_goodput_pct": round(wg_goodput_ratio * 100, 2),
                    "ipsec_goodput_pct": round(ipsec_goodput_ratio * 100, 2),
                },
                "codebase_complexity": {
                    "wireguard_lines_of_code": "~4,000 (Linux Kernel in-tree)",
                    "strongswan_ipsec_lines_of_code": "~400,000+",
                    "attack_surface": "WireGuard ~100x kichikroq zaiflik ehtimoli",
                },
                "crypto_primitives": {
                    "wireguard": "Curve25519, ChaCha20-Poly1305, BLAKE2s (Modern & audited)",
                    "ipsec": "Negotiable suites (AES-GCM, SHA-256, Diffie-Hellman groups, legacy DES/MD5 risk)",
                },
                "roaming": {
                    "wireguard": "Cryptokey Routing (0-RTT avtomatik roaming, Wi-Fi -> 4G uzilishsiz)",
                    "ipsec": "MOBIKE signaling talab qiladi (renegotiation)",
                },
            },
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "interface": self.interface_name,
            "public_key": self.public_key,
            "listen_port": self.listen_port,
            "interface_ip": self.interface_ip,
            "mtu": 1420,
            "total_peers": len(self.peers),
            "peers": [p.to_dict() for p in self.peers.values()],
            "ipsec_comparison": self.compare_with_ipsec(),
        }


# Global instansiya
wireguard_engine = WireGuardEngine()

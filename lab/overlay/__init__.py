"""Multi-Cloud Overlay & SD-WAN Subsystem:
- VXLAN (RFC 7348) & BGP EVPN (RFC 7432 / RFC 8365)
- WireGuard Cryptographic VPN (Noise IK, Curve25519, ChaCha20-Poly1305, MTU 1420)
- Application-Aware SD-WAN dynamic path selector (Brownout/Blackout auto-failover)
"""

from .vxlan_evpn import (
    VXLAN_UDP_PORT,
    VXLAN_ENCAP_OVERHEAD,
    EVPNRouteType,
    VXLANHeader,
    EthernetFrame,
    IPv4Packet,
    VXLANPacket,
    EVPNRouteType2,
    EVPNRouteType5,
    VTEP,
    VXLANOverlayEngine,
    LeafSpineVXLANTopology,
    vxlan_engine,
)
from .wireguard_vpn import (
    Curve25519Key,
    WireGuardPeer,
    WireGuardEngine,
    wireguard_engine,
)
from .sdwan_manager import (
    TrafficClass,
    SLASpec,
    SDWANTunnel,
    WANPath,
    FailoverEvent,
    SDWANRouter,
    SDWANManager,
    DEFAULT_SLAS,
    sdwan_manager,
    sdwan_router,
)

__all__ = [
    "VXLAN_UDP_PORT",
    "VXLAN_ENCAP_OVERHEAD",
    "EVPNRouteType",
    "VXLANHeader",
    "EthernetFrame",
    "IPv4Packet",
    "VXLANPacket",
    "EVPNRouteType2",
    "EVPNRouteType5",
    "VTEP",
    "VXLANOverlayEngine",
    "LeafSpineVXLANTopology",
    "vxlan_engine",
    "Curve25519Key",
    "WireGuardPeer",
    "WireGuardEngine",
    "wireguard_engine",
    "TrafficClass",
    "SLASpec",
    "SDWANTunnel",
    "WANPath",
    "FailoverEvent",
    "SDWANRouter",
    "SDWANManager",
    "DEFAULT_SLAS",
    "sdwan_manager",
    "sdwan_router",
]

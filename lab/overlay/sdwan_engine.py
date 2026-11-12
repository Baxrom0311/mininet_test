"""Re-export SD-WAN engine components for backwards compatibility."""

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

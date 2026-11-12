"""NetLab Xavfsizlik va Conntrack moduli.

Ushbu paket quyidagi dvigatellarni taqdim etadi:
- `ConntrackEngine`: Stateful connection tracking va firewall qoidalarini tekshirish.
- `AttackSimulator`: 4 ta asosiy kiber-hujumni (SYN Flood, UDP Reflection, Slowloris, ARP Spoofing) modellashtirish.
- `SecurityAlertManager`: IDS / Xavfsizlik hodisalarini qayd qilish va saqlash.
"""

from lab.security.conntrack_engine import (
    ConntrackEngine,
    ConntrackEntry,
    ConnectionTuple,
    NATMapping,
    FW_STATE_NEW,
    FW_STATE_ESTABLISHED,
    FW_STATE_RELATED,
    FW_STATE_INVALID,
    TCP_STATE_SYN_SENT,
    TCP_STATE_SYN_RECV,
    TCP_STATE_ESTABLISHED,
    TCP_STATE_FIN_WAIT,
    TCP_STATE_CLOSE_WAIT,
    TCP_STATE_LAST_ACK,
    TCP_STATE_TIME_WAIT,
    TCP_STATE_CLOSE,
    TCP_STATE_INVALID,
)
from lab.security.attack_simulator import (
    AttackSimulator,
    SecurityAlertManager,
    SecurityAlert,
)

# Global yagona instansiyalar (Server API va demo muhitlar uchun)
conntrack_engine = ConntrackEngine()
alert_manager = SecurityAlertManager()
attack_simulator = AttackSimulator(alert_manager=alert_manager)

__all__ = [
    "ConntrackEngine",
    "ConntrackEntry",
    "ConnectionTuple",
    "NATMapping",
    "FW_STATE_NEW",
    "FW_STATE_ESTABLISHED",
    "FW_STATE_RELATED",
    "FW_STATE_INVALID",
    "TCP_STATE_SYN_SENT",
    "TCP_STATE_SYN_RECV",
    "TCP_STATE_ESTABLISHED",
    "TCP_STATE_FIN_WAIT",
    "TCP_STATE_CLOSE_WAIT",
    "TCP_STATE_LAST_ACK",
    "TCP_STATE_TIME_WAIT",
    "TCP_STATE_CLOSE",
    "TCP_STATE_INVALID",
    "AttackSimulator",
    "SecurityAlertManager",
    "SecurityAlert",
    "conntrack_engine",
    "alert_manager",
    "attack_simulator",
]

"""NetLab Xavfsizlik va Conntrack moduli.

Ushbu paket quyidagi dvigatellarni taqdim etadi:
- `ConntrackEngine`: Stateful connection tracking va firewall qoidalarini tekshirish.
- `FirewallGenerator`: iptables va nftables sintaksisida stateful firewall, NAT va DMZ qoidalarini generatsiya qilish.
- `AttackSimulator`: 4 ta asosiy kiber-hujumni (SYN Flood, UDP Reflection, Slowloris, ARP Spoofing) modellashtirish.
- `IDSEngine`: Snort / Suricata qoidalar modeli, imzo mosligi va xavfsizlik tahlili.
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
from lab.security.firewall_generator import (
    FirewallGenerator,
    FirewallRule,
    PortForwardRule,
    NATRule,
)
from lab.security.attack_simulator import (
    AttackSimulator,
    SecurityAlertManager,
    SecurityAlert,
)
from lab.security.ids_engine import (
    IDSEngine,
    IDSRule,
    ids_engine,
)

# Global yagona instansiyalar (Server API va demo muhitlar uchun)
conntrack_engine = ConntrackEngine()
alert_manager = SecurityAlertManager()
attack_simulator = AttackSimulator(alert_manager=alert_manager)
ids_engine.alert_manager = alert_manager

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
    "FirewallGenerator",
    "FirewallRule",
    "PortForwardRule",
    "NATRule",
    "AttackSimulator",
    "SecurityAlertManager",
    "SecurityAlert",
    "IDSEngine",
    "IDSRule",
    "ids_engine",
    "conntrack_engine",
    "alert_manager",
    "attack_simulator",
]

"""Stateful Firewall Generator (iptables & nftables) va DMZ / NAT Arxitekturasi.

Ushbu modul quyidagilarni taqdim etadi:
- iptables va zamonaviy nftables sintaksisida stateful firewall qoidalarini generatsiya qilish
- Conntrack holatlari: NEW, ESTABLISHED, RELATED, INVALID
- NAT, Masquerade va Port Forwarding (DNAT)
- 3 zonali DMZ arxitekturasi (LAN, DMZ, WAN/Internet) va qat'iy izolatsiya qoidalari
- Xavfsizlik qoidalarini tekshirish (validation) va xatoliklarni aniqlash
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union


@dataclass
class FirewallRule:
    """Alohida firewall qoidasi."""
    chain: str = "forward"          # "input", "forward", "output"
    action: str = "accept"          # "accept", "drop", "reject"
    proto: str = "any"              # "any", "tcp", "udp", "icmp"
    src: Optional[str] = None       # CIDR yoki IP
    dst: Optional[str] = None       # CIDR yoki IP
    sport: Optional[Union[int, str]] = None
    dport: Optional[Union[int, str]] = None
    state: Optional[str] = None     # "new", "established", "related", "invalid", "established,related"
    in_if: Optional[str] = None     # Kirish interfeysi (masalan "eth0")
    out_if: Optional[str] = None    # Chiqish interfeysi (masalan "eth1")
    comment: Optional[str] = None
    log: bool = False
    reject_with: Optional[str] = None  # "icmp-host-prohibited", "tcp-reset"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain": self.chain,
            "action": self.action,
            "proto": self.proto,
            "src": self.src,
            "dst": self.dst,
            "sport": self.sport,
            "dport": self.dport,
            "state": self.state,
            "in_if": self.in_if,
            "out_if": self.out_if,
            "comment": self.comment,
            "log": self.log,
            "reject_with": self.reject_with,
        }


@dataclass
class PortForwardRule:
    """Port Forwarding (DNAT) qoidasi."""
    ext_port: int
    int_ip: str
    int_port: int
    proto: str = "tcp"
    ext_ip: Optional[str] = None
    in_if: Optional[str] = None
    comment: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ext_port": self.ext_port,
            "int_ip": self.int_ip,
            "int_port": self.int_port,
            "proto": self.proto,
            "ext_ip": self.ext_ip,
            "in_if": self.in_if,
            "comment": self.comment,
        }


@dataclass
class NATRule:
    """Masquerade / SNAT qoidasi."""
    out_if: str
    src_net: Optional[str] = None
    snat_ip: Optional[str] = None  # Agar SNAT bo'lsa IP, aks holda Masquerade
    comment: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "out_if": self.out_if,
            "src_net": self.src_net,
            "snat_ip": self.snat_ip,
            "comment": self.comment,
        }


class FirewallGenerator:
    """iptables va nftables sintaksisini yaratuvchi to'liq dvigatel."""

    def __init__(
        self,
        default_input: str = "drop",
        default_forward: str = "drop",
        default_output: str = "accept",
        allow_established_related: bool = True,
        drop_invalid: bool = True,
    ):
        self.default_policies = {
            "input": default_input.lower(),
            "forward": default_forward.lower(),
            "output": default_output.lower(),
        }
        self.allow_established_related = allow_established_related
        self.drop_invalid = drop_invalid
        self.rules: List[FirewallRule] = []
        self.port_forwards: List[PortForwardRule] = []
        self.nat_rules: List[NATRule] = []

    def add_rule(self, rule: Union[FirewallRule, Dict[str, Any]]) -> FirewallRule:
        if isinstance(rule, dict):
            rule_obj = FirewallRule(
                chain=str(rule.get("chain", "forward")).lower(),
                action=str(rule.get("action", "accept")).lower(),
                proto=str(rule.get("proto", "any")).lower(),
                src=rule.get("src"),
                dst=rule.get("dst"),
                sport=rule.get("sport"),
                dport=rule.get("dport"),
                state=str(rule.get("state")).lower() if rule.get("state") else None,
                in_if=rule.get("in") or rule.get("in_if"),
                out_if=rule.get("out") or rule.get("out_if"),
                comment=rule.get("comment"),
                log=bool(rule.get("log", False)),
                reject_with=rule.get("reject_with"),
            )
        else:
            rule_obj = rule
        self.rules.append(rule_obj)
        return rule_obj

    def add_port_forward(
        self,
        ext_port: int,
        int_ip: str,
        int_port: int,
        proto: str = "tcp",
        ext_ip: Optional[str] = None,
        in_if: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> PortForwardRule:
        pf = PortForwardRule(
            ext_port=int(ext_port),
            int_ip=str(int_ip),
            int_port=int(int_port),
            proto=str(proto).lower(),
            ext_ip=ext_ip,
            in_if=in_if,
            comment=comment,
        )
        self.port_forwards.append(pf)
        return pf

    def add_nat(
        self,
        out_if: str,
        src_net: Optional[str] = None,
        snat_ip: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> NATRule:
        nr = NATRule(
            out_if=str(out_if),
            src_net=src_net,
            snat_ip=snat_ip,
            comment=comment,
        )
        self.nat_rules.append(nr)
        return nr

    def clear(self) -> None:
        self.rules.clear()
        self.port_forwards.clear()
        self.nat_rules.clear()

    # ──────────────────────── iptables generator ────────────────────────
    def generate_iptables(self) -> List[str]:
        """Bash formatidagi iptables buyruqlar ketma-ketligini qaytaradi."""
        cmds: List[str] = [
            "# ==========================================",
            "# NetLab Generated iptables Stateful Ruleset",
            "# ==========================================",
            "# 1. Flush tables and set default policies",
            "iptables -F",
            "iptables -X",
            "iptables -t nat -F",
            "iptables -t nat -X",
            f"iptables -P INPUT {self.default_policies['input'].upper()}",
            f"iptables -P FORWARD {self.default_policies['forward'].upper()}",
            f"iptables -P OUTPUT {self.default_policies['output'].upper()}",
            "",
            "# 2. Loopback interfeysiga to'liq ruxsat",
            "iptables -A INPUT -i lo -j ACCEPT",
            "iptables -A OUTPUT -o lo -j ACCEPT",
        ]

        # Stateful asosiy qoidalar
        if self.allow_established_related:
            cmds.extend([
                "",
                "# 3. Stateful inspection: mavjud ulanishlar (ESTABLISHED,RELATED) avtomatik o'tadi",
                "iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
                "iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
                "iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
            ])

        if self.drop_invalid:
            cmds.extend([
                "",
                "# 4. Noto'g'ri (INVALID) paketlarni darhol tashlab yuborish",
                "iptables -A INPUT -m conntrack --ctstate INVALID -j DROP",
                "iptables -A FORWARD -m conntrack --ctstate INVALID -j DROP",
            ])

        # Port Forwarding (DNAT)
        if self.port_forwards:
            cmds.extend(["", "# 5. Port Forwarding (DNAT - PREROUTING)"])
            for pf in self.port_forwards:
                parts = ["iptables -t nat -A PREROUTING"]
                if pf.in_if:
                    parts.append(f"-i {pf.in_if}")
                if pf.ext_ip:
                    parts.append(f"-d {pf.ext_ip}")
                parts.append(f"-p {pf.proto}")
                parts.append(f"--dport {pf.ext_port}")
                parts.append(f"-j DNAT --to-destination {pf.int_ip}:{pf.int_port}")
                if pf.comment:
                    parts.append(f"-m comment --comment \"{pf.comment}\"")
                cmds.append(" ".join(parts))

                # FORWARD zanjirida ham ichki serverga ruxsat berish kerak
                fwd_parts = [
                    "iptables -A FORWARD",
                    f"-d {pf.int_ip}",
                    f"-p {pf.proto}",
                    f"--dport {pf.int_port}",
                    "-m conntrack --ctstate NEW",
                    "-j ACCEPT",
                ]
                if pf.comment:
                    fwd_parts.append(f"-m comment --comment \"Allow DNAT {pf.comment}\"")
                cmds.append(" ".join(fwd_parts))

        # NAT / Masquerade (POSTROUTING)
        if self.nat_rules:
            cmds.extend(["", "# 6. NAT Masquerade / SNAT (POSTROUTING)"])
            for nr in self.nat_rules:
                parts = ["iptables -t nat -A POSTROUTING"]
                parts.append(f"-o {nr.out_if}")
                if nr.src_net:
                    parts.append(f"-s {nr.src_net}")
                if nr.snat_ip:
                    parts.append(f"-j SNAT --to-source {nr.snat_ip}")
                else:
                    parts.append("-j MASQUERADE")
                if nr.comment:
                    parts.append(f"-m comment --comment \"{nr.comment}\"")
                cmds.append(" ".join(parts))

        # Foydalanuvchi qoidalari
        if self.rules:
            cmds.extend(["", "# 7. Filtr qoidalari (Filter Rules)"])
            for r in self.rules:
                chain = r.chain.upper()
                parts = [f"iptables -A {chain}"]

                if r.proto and r.proto != "any":
                    parts.append(f"-p {r.proto}")
                if r.src:
                    parts.append(f"-s {r.src}")
                if r.dst:
                    parts.append(f"-d {r.dst}")
                if r.in_if:
                    parts.append(f"-i {r.in_if}")
                if r.out_if:
                    parts.append(f"-o {r.out_if}")
                if r.sport not in (None, ""):
                    parts.append(f"--sport {r.sport}")
                if r.dport not in (None, ""):
                    parts.append(f"--dport {r.dport}")

                if r.state:
                    st_val = r.state.upper()
                    if st_val == "ESTABLISHED":
                        st_val = "ESTABLISHED,RELATED"
                    parts.append(f"-m conntrack --ctstate {st_val}")

                if r.log:
                    log_parts = list(parts)
                    log_parts.append(f"-j LOG --log-prefix \"[NETLAB-FW-{chain}] \"")
                    cmds.append(" ".join(log_parts))

                if r.action == "reject":
                    rej_target = r.reject_with or ("tcp-reset" if r.proto == "tcp" else "icmp-host-prohibited")
                    parts.append(f"-j REJECT --reject-with {rej_target}")
                else:
                    parts.append(f"-j {r.action.upper()}")

                if r.comment:
                    parts.append(f"-m comment --comment \"{r.comment}\"")

                cmds.append(" ".join(parts))

        return cmds

    # ──────────────────────── nftables generator ────────────────────────
    def generate_nftables(self) -> str:
        """Standart /etc/nftables.conf ko'rinishidagi nftables qoidalarini qaytaradi."""
        lines: List[str] = [
            "#!/usr/sbin/nft -f",
            "",
            "# NetLab Generated nftables Stateful Ruleset",
            "flush ruleset",
            "",
            "table inet filter {",
        ]

        for chain in ("input", "forward", "output"):
            hook_type = "filter"
            priority = "filter"
            default_pol = self.default_policies.get(chain, "accept")

            lines.append(f"    chain {chain} {{")
            lines.append(f"        type {hook_type} hook {chain} priority {priority}; policy {default_pol};")
            lines.append("")

            # Asosiy stateful himoyalar
            if chain == "input":
                lines.append('        iif "lo" accept')

            if self.allow_established_related:
                lines.append("        ct state established,related accept")

            if self.drop_invalid and chain in ("input", "forward"):
                lines.append("        ct state invalid drop")

            # Maxsus qoidalar
            chain_rules = [r for r in self.rules if r.chain == chain]
            for r in chain_rules:
                rule_str = self._format_nft_rule(r)
                lines.append(f"        {rule_str}")

            # DNAT forward ruxsatlari
            if chain == "forward" and self.port_forwards:
                for pf in self.port_forwards:
                    lines.append(f"        ip daddr {pf.int_ip} {pf.proto} dport {pf.int_port} ct state new accept")

            lines.append("    }")
            lines.append("")

        lines.append("}")

        # NAT jadvali (agar DNAT yoki SNAT/Masquerade bo'lsa)
        if self.port_forwards or self.nat_rules:
            lines.extend([
                "",
                "table ip nat {",
                "    chain prerouting {",
                "        type nat hook prerouting priority dstnat; policy accept;",
            ])

            for pf in self.port_forwards:
                dnat_line = "        "
                if pf.in_if:
                    dnat_line += f'iif "{pf.in_if}" '
                if pf.ext_ip:
                    dnat_line += f"ip daddr {pf.ext_ip} "
                dnat_line += f"{pf.proto} dport {pf.ext_port} dnat to {pf.int_ip}:{pf.int_port}"
                if pf.comment:
                    dnat_line += f' comment "{pf.comment}"'
                lines.append(dnat_line)

            lines.extend([
                "    }",
                "",
                "    chain postrouting {",
                "        type nat hook postrouting priority srcnat; policy accept;",
            ])

            for nr in self.nat_rules:
                snat_line = f'        oif "{nr.out_if}" '
                if nr.src_net:
                    snat_line += f"ip saddr {nr.src_net} "
                if nr.snat_ip:
                    snat_line += f"snat to {nr.snat_ip}"
                else:
                    snat_line += "masquerade"
                if nr.comment:
                    snat_line += f' comment "{nr.comment}"'
                lines.append(snat_line)

            lines.extend([
                "    }",
                "}",
            ])

        return "\n".join(lines) + "\n"

    def _format_nft_rule(self, r: FirewallRule) -> str:
        parts = []
        if r.in_if:
            parts.append(f'iif "{r.in_if}"')
        if r.out_if:
            parts.append(f'oif "{r.out_if}"')
        if r.src:
            parts.append(f"ip saddr {r.src}")
        if r.dst:
            parts.append(f"ip daddr {r.dst}")

        if r.proto and r.proto != "any":
            parts.append(r.proto)
            if r.sport not in (None, ""):
                parts.append(f"sport {r.sport}")
            if r.dport not in (None, ""):
                parts.append(f"dport {r.dport}")

        if r.state:
            st = r.state.lower()
            if st == "established":
                st = "established,related"
            parts.append(f"ct state {st}")

        if r.log:
            parts.append(f'log prefix "[NFT-{r.chain.upper()}] "')

        act = r.action.lower()
        if act == "reject":
            if r.reject_with:
                parts.append(f"reject with {r.reject_with}")
            elif r.proto == "tcp":
                parts.append("reject with tcp reset")
            else:
                parts.append("reject with icmp type host-prohibited")
        else:
            parts.append(act)

        if r.comment:
            parts.append(f'comment "{r.comment}"')

        return " ".join(parts)

    # ──────────────────────── 3 Zonali DMZ Arxitekturasi ────────────────────────
    @classmethod
    def create_dmz_preset(
        cls,
        lan_subnet: str = "10.0.1.0/24",
        lan_if: str = "eth1",
        dmz_subnet: str = "10.0.2.0/24",
        dmz_if: str = "eth2",
        wan_if: str = "eth0",
        wan_ip: Optional[str] = "203.0.113.1",
        published_services: Optional[List[Dict[str, Any]]] = None,
    ) -> "FirewallGenerator":
        """DMZ (Demilitarized Zone) xavfsizlik arxitekturasi uchun tayyor generator yaratadi.

        DMZ qoidalari:
        1. LAN -> WAN: Ruxsat berilgan (Internetga chiqish, NAT/Masquerade).
        2. LAN -> DMZ: Ruxsat berilgan (Serverlarni boshqarish, testlash).
        3. WAN -> DMZ: Faqat belgilangan ochiq portlar (masalan HTTP 80, HTTPS 443) DNAT orqali.
        4. DMZ -> LAN: QAT'IY TAQIQLANGAN (Drop NEW)! DMZ server buzib kirilsa ham LAN xavfsiz qoladi.
        5. DMZ -> WAN: Cheklangan (faqat DNS va zarur tashqi so'rovlar).
        6. Stateful conntrack barcha javob trafigini (ESTABLISHED,RELATED) avtomatik o'tkazadi.
        """
        fw = cls(default_input="drop", default_forward="drop", default_output="accept")

        # 1. NAT (LAN va DMZ uchun Internetga chiqish)
        fw.add_nat(out_if=wan_if, src_net=lan_subnet, comment="LAN Internet NAT")
        fw.add_nat(out_if=wan_if, src_net=dmz_subnet, comment="DMZ Outbound NAT")

        # 2. DMZ qat'iy izolatsiyasi: DMZ dan LAN ga yangi ulanishlar darhol bloklanadi
        fw.add_rule(FirewallRule(
            chain="forward",
            action="drop",
            src=dmz_subnet,
            dst=lan_subnet,
            state="new",
            comment="CRITICAL: DMZ to LAN Isolation (Strict Anti-Pivoting)",
        ))

        # 3. LAN dan DMZ ga ruxsat
        fw.add_rule(FirewallRule(
            chain="forward",
            action="accept",
            src=lan_subnet,
            dst=dmz_subnet,
            state="new",
            comment="LAN to DMZ Management Access",
        ))

        # 4. LAN dan WAN ga ruxsat (Internet browsing)
        fw.add_rule(FirewallRule(
            chain="forward",
            action="accept",
            in_if=lan_if,
            out_if=wan_if,
            src=lan_subnet,
            state="new",
            comment="LAN to Internet Outbound",
        ))

        # 5. DMZ dan WAN ga faqat DNS (53) va NTP (123) ruxsati
        fw.add_rule(FirewallRule(
            chain="forward",
            action="accept",
            in_if=dmz_if,
            out_if=wan_if,
            src=dmz_subnet,
            proto="udp",
            dport=53,
            state="new",
            comment="DMZ DNS Queries",
        ))
        fw.add_rule(FirewallRule(
            chain="forward",
            action="accept",
            in_if=dmz_if,
            out_if=wan_if,
            src=dmz_subnet,
            proto="udp",
            dport=123,
            state="new",
            comment="DMZ NTP Sync",
        ))

        # 6. Ochiq e'lon qilingan DMZ xizmatlari (Port Forwarding / DNAT)
        services = published_services or [
            {"ext_port": 80, "int_ip": "10.0.2.10", "int_port": 80, "proto": "tcp", "comment": "DMZ Web HTTP"},
            {"ext_port": 443, "int_ip": "10.0.2.10", "int_port": 443, "proto": "tcp", "comment": "DMZ Web HTTPS"},
        ]

        for s in services:
            fw.add_port_forward(
                ext_port=s["ext_port"],
                int_ip=s["int_ip"],
                int_port=s["int_port"],
                proto=s.get("proto", "tcp"),
                ext_ip=wan_ip,
                in_if=wan_if,
                comment=s.get("comment", "DMZ Public Service"),
            )

        return fw

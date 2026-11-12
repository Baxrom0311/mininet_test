"""Intrusion Detection System (IDS) dvigateli (Snort / Suricata qoidalar modeli).

Ushbu modul quyidagilarni taqdim etadi:
- Snort va Suricata formatidagi qoidalarni tahlil qilish (parsing)
- Qoidalar sintaksisi: `alert tcp $EXTERNAL_NET any -> $HOME_NET 80 (msg:"..."; content:"..."; sid:...;)`
- Imzo (signature) va pattern matching: content, nocase, offset, depth, pcre, flags, itype
- Ehtimolli xurujlarni (SYN flood, Slowloris, SQLi, Path Traversal, XMAS scan) aniqlash
- Oqimlar va paketlarni tahlil qilish (inspection engine)
- Standart Snort alert_fast formatida ogohlantirish loglarini shakllantirish
- SecurityAlertManager bilan to'g'ridan-to'g'ri integratsiya
"""

from __future__ import annotations

import collections
import ipaddress
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from lab.security.attack_simulator import SecurityAlert, SecurityAlertManager


@dataclass
class IDSRule:
    """Snort / Suricata qoidasi modeli."""
    action: str                        # "alert", "drop", "pass", "reject", "sdrop"
    proto: str                         # "tcp", "udp", "icmp", "ip"
    src_ip: str                        # "$EXTERNAL_NET", "any", "10.0.1.0/24", "!10.0.0.1"
    src_port: str                      # "any", "80", "1024:65535", "!22"
    direction: str                     # "->", "<>"
    dst_ip: str                        # "$HOME_NET", "any", "10.0.2.10"
    dst_port: str                      # "80", "any", "443"
    msg: str                           # Xabar matni
    sid: int                           # Signature ID (masalan 1000001)
    rev: int = 1                       # Versiya
    classtype: str = "bad-unknown"     # Xuruj klassi
    priority: int = 3                  # 1=High, 2=Medium, 3=Low, 4=Info
    content: Optional[str] = None      # Qidiriladigan matn / baytlar
    nocase: bool = False               # Registrga sezgir bo'lmagan qidiruv
    offset: int = 0
    depth: Optional[int] = None
    flags: Optional[str] = None        # TCP flaglari (masalan "S", "SFPU", "0")
    itype: Optional[int] = None        # ICMP turi
    icode: Optional[int] = None        # ICMP kodi
    pcre: Optional[str] = None         # Regex shabloni
    threshold_type: Optional[str] = None    # "limit", "threshold", "both"
    threshold_track: Optional[str] = None   # "by_src", "by_dst"
    threshold_count: int = 0
    threshold_seconds: int = 0
    raw_rule: str = ""
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sid": self.sid,
            "rev": self.rev,
            "action": self.action,
            "proto": self.proto,
            "src": f"{self.src_ip}:{self.src_port}",
            "direction": self.direction,
            "dst": f"{self.dst_ip}:{self.dst_port}",
            "msg": self.msg,
            "classtype": self.classtype,
            "priority": self.priority,
            "content": self.content,
            "nocase": self.nocase,
            "flags": self.flags,
            "raw_rule": self.raw_rule,
            "enabled": self.enabled,
        }


# Standart o'rnatilgan Snort / Suricata qoidalari
DEFAULT_IDS_RULES = """
# NetLab Builtin Snort/Suricata Rule Set
alert tcp $EXTERNAL_NET any -> $HOME_NET 80 (msg:"ET WEB_SERVER Possible SQL Injection Attempt (UNION SELECT)"; content:"UNION SELECT"; nocase; sid:1000001; rev:1; classtype:web-application-attack; priority:1;)
alert tcp $EXTERNAL_NET any -> $HOME_NET 80 (msg:"ET WEB_SERVER Possible SQL Injection Attempt (OR 1=1)"; content:"OR 1=1"; nocase; sid:1000002; rev:1; classtype:web-application-attack; priority:1;)
alert tcp $EXTERNAL_NET any -> $HOME_NET any (msg:"ET WEB_SERVER Directory Traversal Attempt (../..)"; content:"../.."; sid:1000003; rev:1; classtype:web-application-attack; priority:1;)
alert tcp $EXTERNAL_NET any -> $HOME_NET any (msg:"ET WEB_SERVER /etc/passwd Access Attempt"; content:"/etc/passwd"; nocase; sid:1000004; rev:1; classtype:attempted-recon; priority:1;)
alert tcp $EXTERNAL_NET any -> $HOME_NET 80 (msg:"ET DOS Slowloris Partial Header Anomaly"; content:"X-a: b"; nocase; sid:1000005; rev:1; classtype:attempted-dos; priority:2;)
alert tcp any any -> $HOME_NET any (msg:"SCAN Nmap XMAS Tree Scan Detected"; flags:FPU; sid:1000006; rev:1; classtype:attempted-recon; priority:2;)
alert tcp any any -> $HOME_NET any (msg:"SCAN TCP NULL Scan Detected"; flags:0; sid:1000007; rev:1; classtype:attempted-recon; priority:2;)
alert tcp $EXTERNAL_NET any -> $HOME_NET any (msg:"ET DOS Potential TCP SYN Flood"; flags:S; threshold:type both, track by_src, count 20, seconds 2; sid:1000008; rev:1; classtype:attempted-dos; priority:1;)
alert udp any 53 -> $HOME_NET any (msg:"ET DOS DNS Amplification Reflection Response"; threshold:type threshold, track by_dst, count 30, seconds 1; sid:1000009; rev:1; classtype:attempted-dos; priority:1;)
alert icmp $EXTERNAL_NET any -> $HOME_NET any (msg:"ICMP Echo Request Flood"; itype:8; threshold:type both, track by_src, count 25, seconds 1; sid:1000010; rev:1; classtype:attempted-dos; priority:2;)
"""


class IDSEngine:
    """Snort / Suricata qoidalarini bajaruvchi va tahlil qiluvchi dvigatel."""

    def __init__(
        self,
        alert_manager: Optional[SecurityAlertManager] = None,
        home_net: str = "10.0.0.0/8,192.168.0.0/16,172.16.0.0/12",
        external_net: str = "!$HOME_NET",
    ):
        self.alert_manager = alert_manager or SecurityAlertManager()
        self.home_net_raw = home_net
        self.external_net_raw = external_net
        self.rules: Dict[int, IDSRule] = {}
        self._compiled_pcre: Dict[int, re.Pattern] = {}
        # Threshold hisoblagichlari: (sid, track_key) -> deque([timestamps])
        self._threshold_history: Dict[Tuple[int, str], collections.deque] = collections.defaultdict(collections.deque)

        # Standart qoidalarni yuklash
        self.load_rules_from_string(DEFAULT_IDS_RULES)

    def parse_rule(self, rule_str: str) -> Optional[IDSRule]:
        """Bitta Snort / Suricata qatorini tahlil qilib, IDSRule ob'ektini hosil qiladi."""
        clean = rule_str.strip()
        if not clean or clean.startswith("#"):
            return None

        # Format: action proto src_ip src_port dir dst_ip dst_port (options)
        hdr_match = re.match(r"^(\w+)\s+(\w+)\s+(\S+)\s+(\S+)\s+(->|<>)\s+(\S+)\s+(\S+)\s*\((.*)\)\s*$", clean)
        if not hdr_match:
            return None

        action, proto, src_ip, src_port, direction, dst_ip, dst_port, opts_str = hdr_match.groups()

        options: Dict[str, str] = {}
        # Optionlarni ajratish: key:value; yoki oddiy kalit;
        for opt in re.finditer(r'([a-zA-Z0-9_\-]+)(?:\s*:\s*([^;]+))?;', opts_str):
            k = opt.group(1).strip()
            v = opt.group(2).strip() if opt.group(2) else ""
            # Qo'shtirnoqlardan tozalash
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            options[k] = v

        msg = options.get("msg", "Security Event Detected")
        sid = int(options.get("sid", 1000000 + len(self.rules)))
        rev = int(options.get("rev", 1))
        classtype = options.get("classtype", "bad-unknown")
        priority = int(options.get("priority", 3))
        content = options.get("content")
        nocase = "nocase" in options
        offset = int(options.get("offset", 0))
        depth = int(options.get("depth")) if options.get("depth") else None
        flags = options.get("flags")
        itype = int(options.get("itype")) if options.get("itype") else None
        icode = int(options.get("icode")) if options.get("icode") else None
        pcre = options.get("pcre")

        # Threshold
        th_type = None
        th_track = None
        th_count = 0
        th_seconds = 0
        if "threshold" in options:
            th_val = options["threshold"]
            # type both, track by_src, count 20, seconds 2
            t_m = re.search(r"type\s+(\w+)", th_val)
            tr_m = re.search(r"track\s+(\w+)", th_val)
            c_m = re.search(r"count\s+(\d+)", th_val)
            s_m = re.search(r"seconds\s+(\d+)", th_val)
            if t_m: th_type = t_m.group(1)
            if tr_m: th_track = tr_m.group(1)
            if c_m: th_count = int(c_m.group(1))
            if s_m: th_seconds = int(s_m.group(1))

        rule = IDSRule(
            action=action.lower(),
            proto=proto.lower(),
            src_ip=src_ip,
            src_port=src_port,
            direction=direction,
            dst_ip=dst_ip,
            dst_port=dst_port,
            msg=msg,
            sid=sid,
            rev=rev,
            classtype=classtype,
            priority=priority,
            content=content,
            nocase=nocase,
            offset=offset,
            depth=depth,
            flags=flags,
            itype=itype,
            icode=icode,
            pcre=pcre,
            threshold_type=th_type,
            threshold_track=th_track,
            threshold_count=th_count,
            threshold_seconds=th_seconds,
            raw_rule=clean,
        )

        if pcre:
            flags_re = re.IGNORECASE if pcre.endswith("/i") or nocase else 0
            pat = pcre.strip("/")
            if pat.endswith("/i"):
                pat = pat[:-2]
            try:
                self._compiled_pcre[sid] = re.compile(pat, flags_re)
            except Exception:
                pass

        return rule

    def add_rule(self, rule_str: str) -> Optional[IDSRule]:
        rule = self.parse_rule(rule_str)
        if rule:
            self.rules[rule.sid] = rule
        return rule

    def load_rules_from_string(self, content: str) -> int:
        count = 0
        for line in content.splitlines():
            r = self.add_rule(line)
            if r:
                count += 1
        return count

    # ──────────────────────── Moslikni tekshirish (Matching Engine) ────────────────────────
    def match_packet(
        self,
        pkt: Dict[str, Any],
        payload: Optional[Union[bytes, str]] = None,
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Paketni barcha faol qoidalar bilan tekshiradi va hosil bo'lgan ogohlantirishlarni qaytaradi."""
        current_time = now if now is not None else time.time()
        matched_alerts = []

        proto = str(pkt.get("proto", "tcp")).lower()
        src_ip = str(pkt.get("src_ip", pkt.get("src", "0.0.0.0")))
        dst_ip = str(pkt.get("dst_ip", pkt.get("dst", "0.0.0.0")))
        src_port = int(pkt.get("src_port", pkt.get("sport", 0)) or 0)
        dst_port = int(pkt.get("dst_port", pkt.get("dport", 0)) or 0)

        # Flags
        pkt_flags = self._extract_flags(pkt)

        # Payload
        raw_payload_bytes = b""
        if payload is not None:
            raw_payload_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8", errors="ignore")
        elif "payload" in pkt:
            p_val = pkt["payload"]
            raw_payload_bytes = p_val if isinstance(p_val, bytes) else str(p_val).encode("utf-8", errors="ignore")

        for rule in self.rules.values():
            if not rule.enabled:
                continue

            # 1. Protokol tekshiruvi
            if rule.proto != "ip" and rule.proto != proto:
                continue

            # 2. IP va portlar tekshiruvi
            if not self._match_ip(rule.src_ip, src_ip):
                if rule.direction == "<>" and self._match_ip(rule.src_ip, dst_ip):
                    pass
                else:
                    continue

            if not self._match_ip(rule.dst_ip, dst_ip):
                if rule.direction == "<>" and self._match_ip(rule.dst_ip, src_ip):
                    pass
                else:
                    continue

            if not self._match_port(rule.src_port, src_port):
                continue
            if not self._match_port(rule.dst_port, dst_port):
                continue

            # 3. TCP Flaglari
            if rule.flags and proto == "tcp":
                if not self._match_tcp_flags(rule.flags, pkt_flags):
                    continue

            # 4. ICMP turi / kodi
            if proto == "icmp":
                if rule.itype is not None and int(pkt.get("icmp_type", -1)) != rule.itype:
                    continue
                if rule.icode is not None and int(pkt.get("icmp_code", -1)) != rule.icode:
                    continue

            # 5. Payload Content tekshiruvi
            if rule.content:
                if not self._match_content(rule, raw_payload_bytes):
                    continue

            # 6. PCRE Regex tekshiruvi
            if rule.sid in self._compiled_pcre:
                pcre_re = self._compiled_pcre[rule.sid]
                try:
                    text = raw_payload_bytes.decode("utf-8", errors="ignore")
                    if not pcre_re.search(text):
                        continue
                except Exception:
                    continue

            # 7. Threshold / Rate limiting
            if rule.threshold_type and rule.threshold_count > 0 and rule.threshold_seconds > 0:
                track_val = src_ip if rule.threshold_track == "by_src" else dst_ip
                if not self._check_threshold(rule, track_val, current_time):
                    continue

            # Moslik topildi!
            alert_dict = self._trigger_alert(rule, pkt, src_ip, src_port, dst_ip, dst_port, current_time)
            matched_alerts.append(alert_dict)

        return matched_alerts

    def _extract_flags(self, pkt: Dict[str, Any]) -> Set[str]:
        flags_data = pkt.get("flags") or {}
        active = set()
        if isinstance(flags_data, dict):
            for f_name, is_set in flags_data.items():
                if is_set:
                    active.add(f_name.upper())
        elif isinstance(flags_data, (list, set)):
            active = set(f.upper() for f in flags_data)
        elif isinstance(flags_data, str):
            f_str = flags_data.upper()
            if "S" in f_str: active.add("SYN")
            if "A" in f_str: active.add("ACK")
            if "F" in f_str: active.add("FIN")
            if "R" in f_str: active.add("RST")
            if "P" in f_str: active.add("PSH")
            if "U" in f_str: active.add("URG")
        return active

    def _match_tcp_flags(self, rule_flags: str, pkt_flags: Set[str]) -> bool:
        # Masalan: "S" (faqat SYN), "SFPU" (XMAS), "0" (NULL)
        flag_map = {
            "S": "SYN", "A": "ACK", "F": "FIN", "R": "RST", "P": "PSH", "U": "URG"
        }
        if rule_flags == "0":
            return len(pkt_flags) == 0

        required = set(flag_map[c] for c in rule_flags if c in flag_map)
        return required.issubset(pkt_flags)

    def _match_content(self, rule: IDSRule, payload: bytes) -> bool:
        target = rule.content.encode("utf-8", errors="ignore")
        data = payload

        if rule.offset > 0:
            if rule.offset >= len(data):
                return False
            data = data[rule.offset :]

        if rule.depth is not None:
            data = data[: rule.depth]

        if rule.nocase:
            return target.lower() in data.lower()
        return target in data

    def _check_threshold(self, rule: IDSRule, track_key: str, now: float) -> bool:
        key = (rule.sid, track_key)
        history = self._threshold_history[key]
        cutoff = now - rule.threshold_seconds

        while history and history[0] < cutoff:
            history.popleft()

        history.append(now)

        if len(history) >= rule.threshold_count:
            # Agar limit bo'lsa faqat birinchi marta o'tadi
            if rule.threshold_type == "limit" and len(history) > rule.threshold_count:
                return False
            return True
        return False

    def _match_ip(self, rule_ip: str, target_ip_str: str) -> bool:
        if rule_ip in ("any", "*"):
            return True

        is_negated = rule_ip.startswith("!")
        clean_rule = rule_ip[1:] if is_negated else rule_ip

        matched = False
        try:
            target_obj = ipaddress.ip_address(target_ip_str)

            if clean_rule == "$HOME_NET":
                for net_str in self.home_net_raw.split(","):
                    if target_obj in ipaddress.ip_network(net_str.strip(), strict=False):
                        matched = True
                        break
            elif clean_rule == "$EXTERNAL_NET":
                # Home net'da bo'lmaganlar
                in_home = False
                for net_str in self.home_net_raw.split(","):
                    if target_obj in ipaddress.ip_network(net_str.strip(), strict=False):
                        in_home = True
                        break
                matched = not in_home
            elif "/" in clean_rule:
                matched = target_obj in ipaddress.ip_network(clean_rule, strict=False)
            else:
                matched = target_obj == ipaddress.ip_address(clean_rule)
        except Exception:
            matched = False

        return not matched if is_negated else matched

    def _match_port(self, rule_port: str, target_port: int) -> bool:
        if rule_port in ("any", "*", ""):
            return True

        is_negated = rule_port.startswith("!")
        clean_p = rule_port[1:] if is_negated else rule_port

        matched = False
        if ":" in clean_p:
            parts = clean_p.split(":")
            low = int(parts[0]) if parts[0] else 1
            high = int(parts[1]) if parts[1] else 65535
            matched = low <= target_port <= high
        else:
            try:
                matched = int(clean_p) == target_port
            except ValueError:
                matched = False

        return not matched if is_negated else matched

    def _trigger_alert(
        self,
        rule: IDSRule,
        pkt: Dict[str, Any],
        src_ip: str,
        src_port: int,
        dst_ip: str,
        dst_port: int,
        now: float,
    ) -> Dict[str, Any]:
        """Qoida mos kelganda Snort fast alert va SecurityAlertManager ob'ektini yaratadi."""
        sev_map = {1: "critical", 2: "high", 3: "medium", 4: "low"}
        severity = sev_map.get(rule.priority, "medium")

        # Snort alert_fast formati: [**] [GID:SID:REV] MSG [**] [Classification: ...] [Priority: ...] {PROTO} SRC -> DST
        fast_line = (
            f"[**] [1:{rule.sid}:{rule.rev}] {rule.msg} [**] "
            f"[Classification: {rule.classtype}] [Priority: {rule.priority}] "
            f"{{{rule.proto.upper()}}} {src_ip}:{src_port} -> {dst_ip}:{dst_port}"
        )

        alert_obj = self.alert_manager.create_alert(
            severity=severity,
            attack_type="ids_signature",
            source=f"{src_ip}:{src_port}",
            target=f"{dst_ip}:{dst_port}",
            description=f"IDS Alert [SID:{rule.sid}]: {rule.msg}",
            mitigation=f"Snort/Suricata imzo mosligi. Manba IP ({src_ip}) trafigini cheklang yoki perimetr firewall'da bloklang.",
            details={
                "sid": rule.sid,
                "rev": rule.rev,
                "classtype": rule.classtype,
                "priority": rule.priority,
                "snort_fast_log": fast_line,
                "action": rule.action,
            },
            now=now,
        )

        return {
            "sid": rule.sid,
            "msg": rule.msg,
            "action": rule.action,
            "priority": rule.priority,
            "severity": severity,
            "classtype": rule.classtype,
            "fast_log": fast_line,
            "alert_id": alert_obj.id,
        }

    def get_rules(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.rules.values()]

    def clear(self) -> None:
        self.rules.clear()
        self._compiled_pcre.clear()
        self._threshold_history.clear()


# Global yagona instansiya
ids_engine = IDSEngine()

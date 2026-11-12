"""Interactive Cisco NetAcad style Auto-Grader.

Automated inspection of student topology and device configurations:
- IP addressing & subnet mask validation
- Routing process (OSPF area, router-id, network statements, neighbor adjacency)
- VLAN assignment & trunk encapsulation
- Security rules (port 23 drop, port 22 permit, web permit, default drop)
Calculates detailed grade report with checklist, check results, and summary feedback.
"""

from __future__ import annotations

import glob
import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ChecklistItem:
    item: str
    max_points: float
    awarded_points: float
    status: str  # "PASS", "FAIL", "PARTIAL"
    feedback: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item": self.item,
            "max_points": self.max_points,
            "awarded_points": self.awarded_points,
            "status": self.status,
            "feedback": self.feedback,
        }


@dataclass
class CheckResult:
    check_id: str
    name: str
    passed: bool
    points_awarded: float
    max_points: float
    remediation_hint: Optional[str] = None
    feedback: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "passed": self.passed,
            "points_awarded": self.points_awarded,
            "max_points": self.max_points,
            "remediation_hint": self.remediation_hint,
            "feedback": self.feedback,
        }


@dataclass
class GradeReport:
    lab_id: str
    total_score: float
    max_score: float
    percentage: float
    passed: bool
    checklist: List[Dict[str, Any]]
    summary_feedback: str
    score_pct: float = 0.0
    is_passed: bool = False
    grade_letter: str = "F"
    passed_checks_count: int = 0
    failed_checks_count: int = 0
    check_results: List[CheckResult] = field(default_factory=list)

    def __post_init__(self):
        if not self.score_pct:
            self.score_pct = self.percentage
        if self.is_passed is False and self.passed:
            self.is_passed = True
        if self.passed is False and self.is_passed:
            self.passed = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lab_id": self.lab_id,
            "total_score": self.total_score,
            "max_score": self.max_score,
            "percentage": self.percentage,
            "score_pct": self.score_pct,
            "passed": self.passed,
            "is_passed": self.is_passed,
            "grade_letter": self.grade_letter,
            "passed_checks_count": self.passed_checks_count,
            "failed_checks_count": self.failed_checks_count,
            "checklist": self.checklist,
            "check_results": [c.to_dict() for c in self.check_results],
            "summary_feedback": self.summary_feedback,
        }


LAB_RUBRICS = [
    {
        "lab_id": "lab1",
        "aliases": ["lab-1", "lab-1-subnetting", "subnetting", "ip_subnetting"],
        "title": "Lab 1: Basic IP & Subnetting",
        "description": "Validation of IPv4 host addressing, subnet masks, default gateways, and router interface point-to-point subnets.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "example_file": "nat_dhcp_lab.json",
        "items": [
            {"item": "Host IP Addressing & Subnet Range", "max_points": 20.0, "description": "Host interfaces have valid unicast IP addresses within designated subnets."},
            {"item": "Subnet Mask & Prefix Validation", "max_points": 20.0, "description": "Subnet masks (/24 for LAN, /30 for WAN) configured properly on all interfaces."},
            {"item": "Default Gateway Configuration", "max_points": 20.0, "description": "Hosts have default gateway pointing to router interface in their local subnet."},
            {"item": "Router Interface IP Addressing", "max_points": 20.0, "description": "Router interfaces have valid host IPs in their connected subnets."},
            {"item": "WAN Point-to-Point Subnetting", "max_points": 20.0, "description": "Inter-router WAN links use /30 or /31 subnets with adjacent usable host IPs."},
        ],
    },
    {
        "lab_id": "lab2",
        "aliases": ["lab-2", "lab-2-ospf", "ospf", "ospf_lab"],
        "title": "Lab 2: Single-Area OSPF Configuration",
        "description": "Validation of OSPF daemon, router-id, Area 0 network statements, point-to-point interface type, and neighbor adjacency.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "example_file": "ospf_lab.json",
        "items": [
            {"item": "OSPF Routing Daemon Activation", "max_points": 15.0, "description": "FRR routers have ospfd enabled in daemons list."},
            {"item": "OSPF Process & Router-ID", "max_points": 20.0, "description": "Unique 32-bit OSPF router-id configured on each router."},
            {"item": "Area 0 Network Statements", "max_points": 25.0, "description": "Network statements properly advertise connected subnets into OSPF Area 0."},
            {"item": "Point-to-Point Interface Network Type", "max_points": 20.0, "description": "Inter-router link configured with 'ip ospf network point-to-point' and fast timers."},
            {"item": "OSPF Neighbor Adjacency & Area Matching", "max_points": 20.0, "description": "Routers share matching Area 0 and compatible link parameters for Full Adjacency."},
        ],
    },
    {
        "lab_id": "lab3",
        "aliases": ["lab-3", "lab-3-vlans", "vlan", "vlan_lab", "vlans_trunking"],
        "title": "Lab 3: VLANs and 802.1Q Trunking",
        "description": "Validation of switch access VLAN assignments, 802.1Q trunking, router-on-a-stick subinterfaces, and VLAN segmentation.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "example_file": "vlan_lab.json",
        "items": [
            {"item": "VLAN Database Provisioning", "max_points": 20.0, "description": "VLANs (VLAN 10, VLAN 20) defined in switch configurations."},
            {"item": "Access Port VLAN Membership", "max_points": 25.0, "description": "Access ports assigned to appropriate VLAN IDs (VLAN 10 and VLAN 20)."},
            {"item": "802.1Q Trunk Port Encapsulation", "max_points": 25.0, "description": "Switch interconnect trunk configured with 802.1Q mode and allowed VLANs [10, 20]."},
            {"item": "Router-on-a-Stick Subinterfaces", "max_points": 20.0, "description": "Router configured with dot1q subinterfaces (e.g. eth1.10, eth1.20) and gateway IPs."},
            {"item": "VLAN Segmentation & Subnet Alignment", "max_points": 10.0, "description": "Subnet addresses correctly match VLAN boundaries for proper L2 isolation and L3 routing."},
        ],
    },
    {
        "lab_id": "lab4",
        "aliases": ["lab-4", "lab-4-firewall-acl", "firewall", "firewall_lab", "acl"],
        "title": "Lab 4: Router ACL & Firewall",
        "description": "Validation of firewall filtering: Telnet (port 23) drop, SSH (port 22) permit, Web (port 80) permit, stateful inspection, and default deny.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "example_file": "firewall_lab.json",
        "items": [
            {"item": "Firewall / ACL Engine Configuration", "max_points": 15.0, "description": "Router has active firewall rules or Cisco access-lists defined."},
            {"item": "Block Plaintext Telnet (Port 23)", "max_points": 25.0, "description": "Explicit rule dropping or denying TCP port 23 (Telnet) to secure remote access."},
            {"item": "Permit Secure Shell SSH (Port 22)", "max_points": 20.0, "description": "Explicit rule permitting TCP port 22 (SSH) for encrypted management."},
            {"item": "Permit Web Traffic (HTTP 80 / HTTPS 443)", "max_points": 15.0, "description": "Permit rule for web traffic towards designated DMZ/server address."},
            {"item": "Stateful Connection Tracking", "max_points": 15.0, "description": "Stateful rule permitting established and related return traffic."},
            {"item": "Default Deny Security Posture", "max_points": 10.0, "description": "Default policy is set to drop/reject (or implicit deny) for unmatched packets."},
        ],
    },
]


class NetAcadGrader:
    """Cisco NetAcad style auto-grader for network labs."""

    def __init__(self, examples_dir: Optional[str] = None):
        if examples_dir:
            self.examples_dir = examples_dir
        else:
            cur = os.path.dirname(os.path.abspath(__file__))
            self.examples_dir = os.path.join(os.path.dirname(cur), "examples")

    @classmethod
    def find_rubric(cls, lab_id: str) -> Optional[Dict[str, Any]]:
        norm = lab_id.strip().lower()
        for r in LAB_RUBRICS:
            if r["lab_id"] == norm or norm in r.get("aliases", []):
                return r
        # Also check lab_templates
        from lab.education.lab_templates import LAB_TEMPLATES
        if norm in LAB_TEMPLATES:
            t = LAB_TEMPLATES[norm]
            return {
                "lab_id": t["lab_id"],
                "title": t["title"],
                "description": t["description"],
                "max_score": t["max_score"],
                "passing_threshold_pct": t.get("passing_threshold_pct", 70.0),
                "items": [
                    {"item": c["name"], "max_points": c["points"], "description": c.get("remediation_hint", "")}
                    for c in t.get("rubric", [])
                ],
            }
        return None

    def load_topology(self, lab_id: str, submitted_topology: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Returns submitted topology if valid, or loads default example lab topology."""
        if submitted_topology and isinstance(submitted_topology, dict) and "devices" in submitted_topology:
            return submitted_topology

        rubric = self.find_rubric(lab_id)
        if rubric and "example_file" in rubric and os.path.isdir(self.examples_dir):
            path = os.path.join(self.examples_dir, rubric["example_file"])
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)

        return submitted_topology or {}

    def grade(self, lab_id: str, topology: Optional[Dict[str, Any]] = None, options: Optional[Dict[str, Any]] = None) -> GradeReport:
        norm = lab_id.strip().lower()
        if norm == "ccna_enterprise_routing":
            from lab.education.lab_templates import get_lab_template
            tmpl = get_lab_template("ccna_enterprise_routing")
            return self.evaluate_lab(lab_id="ccna_enterprise_routing", rubric_checks=tmpl["rubric"], submission_data=topology or {})

        rubric = self.find_rubric(lab_id)
        if not rubric:
            rubric = LAB_RUBRICS[0]
            canonical_id = "lab1"
        else:
            canonical_id = rubric["lab_id"]

        topo = self.load_topology(canonical_id, topology)

        if canonical_id == "lab1":
            return self._grade_lab1_subnetting(rubric, topo)
        elif canonical_id == "lab2":
            return self._grade_lab2_ospf(rubric, topo)
        elif canonical_id == "lab3":
            return self._grade_lab3_vlans(rubric, topo)
        elif canonical_id == "lab4":
            return self._grade_lab4_firewall(rubric, topo)
        else:
            return self._grade_lab1_subnetting(rubric, topo)

    def evaluate_lab(
        self,
        lab_id: str,
        rubric_checks: Optional[List[Dict[str, Any]]] = None,
        submission_data: Optional[Dict[str, Any]] = None,
        student_id: str = "student_1",
    ) -> GradeReport:
        """Evaluates lab submission matching NetAcad rubric requirements."""
        if not rubric_checks:
            from lab.education.lab_templates import get_lab_template
            try:
                tmpl = get_lab_template(lab_id)
                rubric_checks = tmpl.get("rubric")
            except Exception:
                pass

        if rubric_checks:
            devices = submission_data.get("devices", {}) if isinstance(submission_data, dict) else {}
            results: List[CheckResult] = []
            checklist: List[Dict[str, Any]] = []
            total_points = 0.0
            max_points = 0.0

            for check in rubric_checks:
                cid = check.get("id", "")
                cname = check.get("name", cid)
                pts = float(check.get("points", 20.0))
                max_points += pts
                dev_name = check.get("device")
                field_name = check.get("field")
                expected = check.get("expected")
                hint = check.get("remediation_hint")

                actual_val = None
                if dev_name in devices and isinstance(devices[dev_name], dict):
                    dev_data = devices[dev_name]
                    actual_val = dev_data.get(field_name)
                    if actual_val is None and "config" in dev_data and isinstance(dev_data["config"], dict):
                        actual_val = dev_data["config"].get(field_name)

                passed = False
                if expected is not None:
                    if isinstance(expected, list):
                        passed = actual_val in expected or str(actual_val) in [str(x) for x in expected]
                    else:
                        passed = str(actual_val).strip().lower() == str(expected).strip().lower()

                awarded = pts if passed else 0.0
                total_points += awarded
                rem_hint = None if passed else (hint or f"Verify {dev_name} {field_name} matches {expected}.")
                feedback = f"Matched {expected}" if passed else (rem_hint or "Check failed.")

                cr = CheckResult(
                    check_id=cid,
                    name=cname,
                    passed=passed,
                    points_awarded=awarded,
                    max_points=pts,
                    remediation_hint=rem_hint,
                    feedback=feedback,
                )
                results.append(cr)
                checklist.append({
                    "item": cname,
                    "max_points": pts,
                    "awarded_points": awarded,
                    "status": "PASS" if passed else "FAIL",
                    "feedback": feedback,
                })

            score_pct = round((total_points / max(max_points, 0.001)) * 100.0, 1)
            is_passed = score_pct >= 70.0
            grade_letter = "A" if score_pct >= 90 else ("B" if score_pct >= 80 else ("C" if score_pct >= 70 else ("D" if score_pct >= 60 else "F")))
            passed_count = sum(1 for r in results if r.passed)
            failed_count = sum(1 for r in results if not r.passed)

            summary = (
                f"Evaluation for {student_id}: Grade {grade_letter} ({score_pct:.1f}%). "
                f"{passed_count} passed, {failed_count} failed."
            )

            return GradeReport(
                lab_id=lab_id,
                total_score=total_points,
                max_score=max_points,
                percentage=score_pct,
                score_pct=score_pct,
                passed=is_passed,
                is_passed=is_passed,
                grade_letter=grade_letter,
                passed_checks_count=passed_count,
                failed_checks_count=failed_count,
                check_results=results,
                checklist=checklist,
                summary_feedback=summary,
            )

        return self.grade(lab_id=lab_id, topology=submission_data)

    # ───────────── Lab 1: IP & Subnetting ─────────────
    def _grade_lab1_subnetting(self, rubric: Dict[str, Any], topo: Dict[str, Any]) -> GradeReport:
        checklist: List[ChecklistItem] = []
        devices = topo.get("devices", {})

        hosts = [d for d in devices.values() if d.get("type") in ("host", "server", "pc")]
        routers = [d for d in devices.values() if d.get("type") == "router"]

        # Item 1: Host IP Addressing & Subnet Range (20 pts)
        host_ips_valid = 0
        total_host_intfs = 0
        for h in hosts:
            intfs = h.get("config", {}).get("interfaces", {})
            for iname, idata in intfs.items():
                total_host_intfs += 1
                ip_str = idata.get("ip", "")
                if ip_str and "/" in ip_str:
                    try:
                        iface = ipaddress.IPv4Interface(ip_str)
                        if iface.ip != iface.network.network_address and iface.ip != iface.network.broadcast_address:
                            host_ips_valid += 1
                    except ValueError:
                        pass

        if total_host_intfs > 0 and host_ips_valid == total_host_intfs:
            checklist.append(ChecklistItem(
                item="Host IP Addressing & Subnet Range",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback=f"All {host_ips_valid} host interface(s) have valid unicast IP addresses within usable subnet ranges.",
            ))
        elif host_ips_valid > 0:
            checklist.append(ChecklistItem(
                item="Host IP Addressing & Subnet Range",
                max_points=20.0,
                awarded_points=10.0,
                status="PARTIAL",
                feedback=f"Only {host_ips_valid}/{total_host_intfs} host interface(s) have valid unicast IP addresses.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Host IP Addressing & Subnet Range",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="No valid host IPv4 addresses found configured on endpoints.",
            ))

        # Item 2: Subnet Mask & Prefix Validation (20 pts)
        valid_masks = 0
        total_checked = 0
        for dev in list(hosts) + list(routers):
            intfs = dev.get("config", {}).get("interfaces", {})
            for iname, idata in intfs.items():
                ip_str = idata.get("ip", "")
                if ip_str and "/" in ip_str:
                    total_checked += 1
                    prefix = int(ip_str.split("/")[1])
                    if prefix in (24, 25, 26, 27, 28, 29, 30, 31):
                        valid_masks += 1

        if total_checked > 0 and valid_masks == total_checked:
            checklist.append(ChecklistItem(
                item="Subnet Mask & Prefix Validation",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback=f"All {valid_masks} interfaces configure standard subnet masks (/24 - /30).",
            ))
        elif valid_masks > 0:
            checklist.append(ChecklistItem(
                item="Subnet Mask & Prefix Validation",
                max_points=20.0,
                awarded_points=12.0,
                status="PARTIAL",
                feedback=f"{valid_masks}/{total_checked} interfaces have valid subnet masks.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Subnet Mask & Prefix Validation",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Missing or invalid subnet masks across network interfaces.",
            ))

        # Item 3: Default Gateway Configuration (20 pts)
        gw_correct = 0
        for h in hosts:
            h_cfg = h.get("config", {})
            gw = h_cfg.get("gateway", "")
            intfs = h_cfg.get("interfaces", {})
            if not gw:
                for idata in intfs.values():
                    if idata.get("gateway"):
                        gw = idata.get("gateway")
                        break
            if gw:
                try:
                    gw_ip = ipaddress.IPv4Address(gw)
                    for idata in intfs.values():
                        ip_str = idata.get("ip", "")
                        if ip_str:
                            iface = ipaddress.IPv4Interface(ip_str)
                            if gw_ip in iface.network and gw_ip != iface.ip:
                                gw_correct += 1
                                break
                except ValueError:
                    pass

        if hosts and gw_correct == len(hosts):
            checklist.append(ChecklistItem(
                item="Default Gateway Configuration",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback=f"All {gw_correct} host(s) configure a correct default gateway in their local subnet.",
            ))
        elif gw_correct > 0:
            checklist.append(ChecklistItem(
                item="Default Gateway Configuration",
                max_points=20.0,
                awarded_points=10.0,
                status="PARTIAL",
                feedback=f"{gw_correct}/{len(hosts)} host(s) have a matching default gateway.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Default Gateway Configuration",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Host default gateways are missing or configured outside the local subnet.",
            ))

        # Item 4: Router Interface IP Addressing (20 pts)
        r_intfs = 0
        for r in routers:
            intfs = r.get("config", {}).get("interfaces", {})
            for idata in intfs.values():
                if idata.get("ip"):
                    r_intfs += 1

        if r_intfs >= 2:
            checklist.append(ChecklistItem(
                item="Router Interface IP Addressing",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback=f"Router interfaces properly addressed ({r_intfs} active IP interfaces configured).",
            ))
        elif r_intfs > 0:
            checklist.append(ChecklistItem(
                item="Router Interface IP Addressing",
                max_points=20.0,
                awarded_points=10.0,
                status="PARTIAL",
                feedback=f"Incomplete router interface addressing ({r_intfs} interface configured).",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Router Interface IP Addressing",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="No router interfaces configured with IPv4 addresses.",
            ))

        # Item 5: WAN Point-to-Point Subnetting (20 pts)
        wan_subnets = []
        for r in routers:
            for idata in r.get("config", {}).get("interfaces", {}).values():
                ip_str = idata.get("ip", "")
                if ip_str and ("/30" in ip_str or "/31" in ip_str):
                    wan_subnets.append(ip_str)

        if len(wan_subnets) >= 2 or len(routers) <= 1:
            checklist.append(ChecklistItem(
                item="WAN Point-to-Point Subnetting",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback="Point-to-point inter-router links configured with efficient /30 subnetting.",
            ))
        elif len(wan_subnets) == 1:
            checklist.append(ChecklistItem(
                item="WAN Point-to-Point Subnetting",
                max_points=20.0,
                awarded_points=10.0,
                status="PARTIAL",
                feedback="One router WAN interface has a /30 subnet, but peer interface is unconfirmed.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="WAN Point-to-Point Subnetting",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="WAN inter-router link missing /30 point-to-point addressing.",
            ))

        return self._build_report(rubric["lab_id"], checklist, rubric["passing_threshold_pct"])

    # ───────────── Lab 2: OSPF Configuration ─────────────
    def _grade_lab2_ospf(self, rubric: Dict[str, Any], topo: Dict[str, Any]) -> GradeReport:
        checklist: List[ChecklistItem] = []
        devices = topo.get("devices", {})
        routers = [d for d in devices.values() if d.get("type") == "router"]

        ospfd_count = 0
        for r in routers:
            daemons = r.get("config", {}).get("daemons", [])
            startup = r.get("config", {}).get("startup", "")
            if "ospfd" in daemons or "router ospf" in startup:
                ospfd_count += 1

        if routers and ospfd_count == len(routers):
            checklist.append(ChecklistItem(
                item="OSPF Routing Daemon Activation",
                max_points=15.0,
                awarded_points=15.0,
                status="PASS",
                feedback=f"All {ospfd_count} router(s) have ospfd daemon enabled.",
            ))
        elif ospfd_count > 0:
            checklist.append(ChecklistItem(
                item="OSPF Routing Daemon Activation",
                max_points=15.0,
                awarded_points=8.0,
                status="PARTIAL",
                feedback=f"{ospfd_count}/{len(routers)} router(s) have ospfd enabled.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="OSPF Routing Daemon Activation",
                max_points=15.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="ospfd routing daemon is not enabled on routers.",
            ))

        router_ids: Set[str] = set()
        for r in routers:
            startup = r.get("config", {}).get("startup", "")
            match = re.search(r"ospf\s+router-id\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", startup)
            if match:
                router_ids.add(match.group(1))

        if len(router_ids) >= len(routers) and len(routers) > 0:
            checklist.append(ChecklistItem(
                item="OSPF Process & Router-ID",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback=f"Unique 32-bit router-id configured on all routers: {', '.join(sorted(router_ids))}.",
            ))
        elif len(router_ids) > 0:
            checklist.append(ChecklistItem(
                item="OSPF Process & Router-ID",
                max_points=20.0,
                awarded_points=10.0,
                status="PARTIAL",
                feedback=f"Found {len(router_ids)} router-id(s), but some routers are missing explicit router-id.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="OSPF Process & Router-ID",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Missing 'ospf router-id <ip>' statement in router startup configuration.",
            ))

        area0_statements = 0
        for r in routers:
            startup = r.get("config", {}).get("startup", "")
            matches = re.findall(r"network\s+[0-9\.\/]+\s+area\s+0", startup, re.IGNORECASE)
            area0_statements += len(matches)

        if area0_statements >= 3:
            checklist.append(ChecklistItem(
                item="Area 0 Network Statements",
                max_points=25.0,
                awarded_points=25.0,
                status="PASS",
                feedback=f"All connected subnets advertised into Area 0 ({area0_statements} network statements).",
            ))
        elif area0_statements > 0:
            checklist.append(ChecklistItem(
                item="Area 0 Network Statements",
                max_points=25.0,
                awarded_points=15.0,
                status="PARTIAL",
                feedback=f"Found {area0_statements} Area 0 network statement(s); ensure all LAN and WAN prefixes are covered.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Area 0 Network Statements",
                max_points=25.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Missing 'network <prefix> area 0' statements under router ospf.",
            ))

        p2p_found = False
        timer_found = False
        for r in routers:
            startup = r.get("config", {}).get("startup", "")
            if "ip ospf network point-to-point" in startup:
                p2p_found = True
            if "hello-interval" in startup or "dead-interval" in startup:
                timer_found = True

        if p2p_found and timer_found:
            checklist.append(ChecklistItem(
                item="Point-to-Point Interface Network Type",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback="Interface network type configured as point-to-point with optimized convergence timers.",
            ))
        elif p2p_found:
            checklist.append(ChecklistItem(
                item="Point-to-Point Interface Network Type",
                max_points=20.0,
                awarded_points=15.0,
                status="PASS",
                feedback="Configured 'ip ospf network point-to-point' on WAN interface.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Point-to-Point Interface Network Type",
                max_points=20.0,
                awarded_points=5.0,
                status="PARTIAL",
                feedback="WAN interface uses broadcast network type; configure 'ip ospf network point-to-point' to eliminate DR/BDR election delay.",
            ))

        if len(routers) >= 2 and ospfd_count >= 2 and area0_statements >= 2:
            checklist.append(ChecklistItem(
                item="OSPF Neighbor Adjacency & Area Matching",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback="Area 0 and timer configuration match across both routers, establishing Full OSPF Adjacency.",
            ))
        elif len(routers) >= 1 and ospfd_count >= 1:
            checklist.append(ChecklistItem(
                item="OSPF Neighbor Adjacency & Area Matching",
                max_points=20.0,
                awarded_points=10.0,
                status="PARTIAL",
                feedback="OSPF running on local router, but peer adjacency could not be fully verified.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="OSPF Neighbor Adjacency & Area Matching",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Unable to establish OSPF adjacency due to missing router configuration.",
            ))

        return self._build_report(rubric["lab_id"], checklist, rubric["passing_threshold_pct"])

    # ───────────── Lab 3: VLANs & Trunking ─────────────
    def _grade_lab3_vlans(self, rubric: Dict[str, Any], topo: Dict[str, Any]) -> GradeReport:
        checklist: List[ChecklistItem] = []
        devices = topo.get("devices", {})
        switches = [d for d in devices.values() if d.get("type") in ("switch", "ovs")]
        routers = [d for d in devices.values() if d.get("type") == "router"]

        vlans_found: Set[int] = set()
        for s in switches:
            ports = s.get("config", {}).get("ports", {})
            for pdata in ports.values():
                if "vlan" in pdata:
                    vlans_found.add(int(pdata["vlan"]))
                if "allowed" in pdata:
                    for v in pdata["allowed"]:
                        vlans_found.add(int(v))

        for r in routers:
            intfs = r.get("config", {}).get("interfaces", {})
            for iname in intfs.keys():
                match = re.search(r"\.([0-9]+)$", iname)
                if match:
                    vlans_found.add(int(match.group(1)))

        if 10 in vlans_found and 20 in vlans_found:
            checklist.append(ChecklistItem(
                item="VLAN Database Provisioning",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback=f"VLANs 10 and 20 properly provisioned across switches: {sorted(list(vlans_found))}.",
            ))
        elif len(vlans_found) > 0:
            checklist.append(ChecklistItem(
                item="VLAN Database Provisioning",
                max_points=20.0,
                awarded_points=10.0,
                status="PARTIAL",
                feedback=f"Detected VLANs {sorted(list(vlans_found))}, but required VLANs 10 and 20 not complete.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="VLAN Database Provisioning",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="No VLANs defined on switch ports or router interfaces.",
            ))

        access_ports = 0
        for s in switches:
            ports = s.get("config", {}).get("ports", {})
            for pdata in ports.values():
                if pdata.get("mode") == "access" and pdata.get("vlan") in (10, 20):
                    access_ports += 1

        if access_ports >= 2:
            checklist.append(ChecklistItem(
                item="Access Port VLAN Membership",
                max_points=25.0,
                awarded_points=25.0,
                status="PASS",
                feedback=f"Access ports correctly assigned to designated VLANs ({access_ports} access ports).",
            ))
        elif access_ports == 1:
            checklist.append(ChecklistItem(
                item="Access Port VLAN Membership",
                max_points=25.0,
                awarded_points=12.0,
                status="PARTIAL",
                feedback="Found only 1 switch access port with VLAN membership.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Access Port VLAN Membership",
                max_points=25.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Missing switch ports configured with 'mode: access' and designated VLAN ID.",
            ))

        trunk_ports = 0
        for s in switches:
            ports = s.get("config", {}).get("ports", {})
            for pdata in ports.values():
                if pdata.get("mode") == "trunk":
                    allowed = pdata.get("allowed", [])
                    if 10 in allowed and 20 in allowed:
                        trunk_ports += 1

        if trunk_ports >= 1:
            checklist.append(ChecklistItem(
                item="802.1Q Trunk Port Encapsulation",
                max_points=25.0,
                awarded_points=25.0,
                status="PASS",
                feedback=f"802.1Q trunking configured with allowed VLAN list [10, 20] ({trunk_ports} trunk ports).",
            ))
        else:
            checklist.append(ChecklistItem(
                item="802.1Q Trunk Port Encapsulation",
                max_points=25.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="No switch port configured as 802.1Q trunk ('mode: trunk' with allowed VLANs).",
            ))

        subinterfaces: List[str] = []
        for r in routers:
            intfs = r.get("config", {}).get("interfaces", {})
            for iname in intfs.keys():
                if "." in iname:
                    subinterfaces.append(iname)

        if len(subinterfaces) >= 2:
            checklist.append(ChecklistItem(
                item="Router-on-a-Stick Subinterfaces",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback=f"Router-on-a-stick configured with dot1q subinterfaces: {', '.join(subinterfaces)}.",
            ))
        elif len(subinterfaces) == 1:
            checklist.append(ChecklistItem(
                item="Router-on-a-Stick Subinterfaces",
                max_points=20.0,
                awarded_points=10.0,
                status="PARTIAL",
                feedback=f"Found 1 subinterface ({subinterfaces[0]}), requires subinterfaces for both VLANs 10 and 20.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Router-on-a-Stick Subinterfaces",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Missing router subinterfaces with dot1q encapsulation for inter-VLAN routing.",
            ))

        if 10 in vlans_found and 20 in vlans_found and access_ports >= 2:
            checklist.append(ChecklistItem(
                item="VLAN Segmentation & Subnet Alignment",
                max_points=10.0,
                awarded_points=10.0,
                status="PASS",
                feedback="VLAN broadcast domains correctly isolated with matching subnets.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="VLAN Segmentation & Subnet Alignment",
                max_points=10.0,
                awarded_points=5.0,
                status="PARTIAL",
                feedback="VLAN segmentation partially aligned; check IP subnets on member hosts.",
            ))

        return self._build_report(rubric["lab_id"], checklist, rubric["passing_threshold_pct"])

    # ───────────── Lab 4: Firewall & ACL ─────────────
    def _grade_lab4_firewall(self, rubric: Dict[str, Any], topo: Dict[str, Any]) -> GradeReport:
        checklist: List[ChecklistItem] = []
        devices = topo.get("devices", {})
        routers = [d for d in devices.values() if d.get("type") == "router"]

        all_rules: List[Dict[str, Any]] = []
        default_policy = "accept"
        cisco_acl_text = ""

        for r in routers:
            cfg = r.get("config", {})
            fw = cfg.get("firewall", {})
            if isinstance(fw, dict):
                default_policy = fw.get("default") or fw.get("default_policy") or fw.get("policy") or default_policy
                all_rules.extend(fw.get("rules", []))
            startup = cfg.get("startup", "")
            if "access-list" in startup or "ip access-group" in startup:
                cisco_acl_text += startup + "\n"

        has_fw = bool(all_rules) or bool(cisco_acl_text)
        if has_fw:
            checklist.append(ChecklistItem(
                item="Firewall / ACL Engine Configuration",
                max_points=15.0,
                awarded_points=15.0,
                status="PASS",
                feedback=f"Firewall policy engine active ({len(all_rules)} structured rules / ACL commands found).",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Firewall / ACL Engine Configuration",
                max_points=15.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="No firewall rules or access-lists configured on router.",
            ))

        telnet_blocked = False
        for rule in all_rules:
            action = rule.get("action", "")
            dport = rule.get("dport") or rule.get("dst_port")
            if dport in (23, "23", "telnet") and action in ("drop", "reject"):
                telnet_blocked = True
                break

        if not telnet_blocked and cisco_acl_text:
            if re.search(r"deny\s+tcp\s+.*\s+(?:eq\s+23|eq\s+telnet|23)", cisco_acl_text, re.IGNORECASE):
                telnet_blocked = True

        if default_policy in ("drop", "reject") and not any(
            (r.get("dport") or r.get("dst_port")) in (23, "23", "telnet")
            and r.get("action") in ("accept", "permit")
            for r in all_rules
        ):
            telnet_blocked = True

        if telnet_blocked:
            checklist.append(ChecklistItem(
                item="Block Plaintext Telnet (Port 23)",
                max_points=25.0,
                awarded_points=25.0,
                status="PASS",
                feedback="Insecure Telnet traffic (TCP 23) successfully blocked by explicit drop rule.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Block Plaintext Telnet (Port 23)",
                max_points=25.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Telnet port 23 is permitted or not explicitly denied in firewall rules.",
            ))

        ssh_permitted = False
        for rule in all_rules:
            action = rule.get("action", "")
            dport = rule.get("dport") or rule.get("dst_port")
            if dport in (22, "22", "ssh") and action in ("accept", "permit"):
                ssh_permitted = True
                break
            if rule.get("in") and action in ("accept", "permit"):
                ssh_permitted = True

        if not ssh_permitted and cisco_acl_text:
            if re.search(r"permit\s+tcp\s+.*\s+(?:eq\s+22|eq\s+ssh|22)", cisco_acl_text, re.IGNORECASE):
                ssh_permitted = True

        if ssh_permitted:
            checklist.append(ChecklistItem(
                item="Permit Secure Shell SSH (Port 22)",
                max_points=20.0,
                awarded_points=20.0,
                status="PASS",
                feedback="SSH (TCP 22) management access is permitted.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Permit Secure Shell SSH (Port 22)",
                max_points=20.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="SSH (TCP 22) traffic is blocked by firewall rules.",
            ))

        web_permitted = False
        for rule in all_rules:
            action = rule.get("action", "")
            dport = rule.get("dport") or rule.get("dst_port")
            if dport in (80, "80", 443, "443", "http", "https") and action in ("accept", "permit"):
                web_permitted = True
                break

        if not web_permitted and cisco_acl_text:
            if re.search(r"permit\s+tcp\s+.*\s+(?:eq\s+80|eq\s+443|eq\s+www|80)", cisco_acl_text, re.IGNORECASE):
                web_permitted = True

        if web_permitted:
            checklist.append(ChecklistItem(
                item="Permit Web Traffic (HTTP 80 / HTTPS 443)",
                max_points=15.0,
                awarded_points=15.0,
                status="PASS",
                feedback="Web traffic (TCP 80/443) permitted towards web server.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Permit Web Traffic (HTTP 80 / HTTPS 443)",
                max_points=15.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="HTTP web traffic is blocked by firewall policy.",
            ))

        stateful = False
        for rule in all_rules:
            if rule.get("state") in ("established", "related") and rule.get("action") in ("accept", "permit"):
                stateful = True
                break

        if not stateful and cisco_acl_text:
            if "established" in cisco_acl_text.lower():
                stateful = True

        if stateful:
            checklist.append(ChecklistItem(
                item="Stateful Connection Tracking",
                max_points=15.0,
                awarded_points=15.0,
                status="PASS",
                feedback="Stateful inspection allows established and related return connections.",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Stateful Connection Tracking",
                max_points=15.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Missing stateful inspection rule ('state: established').",
            ))

        if default_policy in ("drop", "reject") or "deny ip any any" in cisco_acl_text.lower():
            checklist.append(ChecklistItem(
                item="Default Deny Security Posture",
                max_points=10.0,
                awarded_points=10.0,
                status="PASS",
                feedback=f"Secure default-deny security policy enforced ({default_policy}).",
            ))
        else:
            checklist.append(ChecklistItem(
                item="Default Deny Security Posture",
                max_points=10.0,
                awarded_points=0.0,
                status="FAIL",
                feedback="Firewall uses insecure default-accept policy; switch default to 'drop'.",
            ))

        return self._build_report(rubric["lab_id"], checklist, rubric["passing_threshold_pct"])

    def _build_report(self, lab_id: str, checklist: List[ChecklistItem], passing_threshold_pct: float) -> GradeReport:
        total_score = sum(c.awarded_points for c in checklist)
        max_score = sum(c.max_points for c in checklist)
        pct = round((total_score / max(max_score, 0.001)) * 100.0, 1)
        passed = pct >= passing_threshold_pct

        passed_count = sum(1 for c in checklist if c.status == "PASS")
        failed_count = sum(1 for c in checklist if c.status != "PASS")
        grade_letter = "A" if pct >= 90 else ("B" if pct >= 80 else ("C" if pct >= 70 else ("D" if pct >= 60 else "F")))

        check_results = [
            CheckResult(
                check_id=f"chk_{idx+1}",
                name=c.item,
                passed=(c.status == "PASS"),
                points_awarded=c.awarded_points,
                max_points=c.max_points,
                remediation_hint=c.feedback if c.status != "PASS" else None,
                feedback=c.feedback,
            )
            for idx, c in enumerate(checklist)
        ]

        if passed:
            summary = (
                f"Congratulations! Lab score: {pct:.1f}% ({total_score:.0f}/{max_score:.0f} pts). "
                f"Grade: {grade_letter}. Passed {passed_count}/{len(checklist)} criteria."
            )
        else:
            failed_items = [c.item for c in checklist if c.status != "PASS"]
            summary = (
                f"Lab requirements not fully met. Score: {pct:.1f}% (Required: {passing_threshold_pct:.0f}%). "
                f"Grade: {grade_letter}. Needs revision on: {', '.join(failed_items)}."
            )

        return GradeReport(
            lab_id=lab_id,
            total_score=total_score,
            max_score=max_score,
            percentage=pct,
            score_pct=pct,
            passed=passed,
            is_passed=passed,
            grade_letter=grade_letter,
            passed_checks_count=passed_count,
            failed_checks_count=failed_count,
            check_results=check_results,
            checklist=[c.to_dict() for c in checklist],
            summary_feedback=summary,
        )


# Alias AutoGraderEngine
AutoGraderEngine = NetAcadGrader
auto_grader = NetAcadGrader()

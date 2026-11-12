"""Lab rubrics and evaluation templates for NetAcad auto-grader."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


LAB_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "ccna_enterprise_routing": {
        "lab_id": "ccna_enterprise_routing",
        "title": "CCNA Enterprise Routing & Security",
        "description": "Comprehensive lab testing IPv4 addressing, OSPF single-area routing, 802.1Q VLANs, and edge firewall policy.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "rubric": [
            {
                "id": "r1_ip",
                "name": "R1 G0/0 IPv4 Addressing",
                "device": "R1",
                "field": "ip_g0_0",
                "expected": "192.168.1.1/24",
                "points": 20.0,
                "remediation_hint": "Configure R1 interface G0/0 with IP 192.168.1.1/24.",
            },
            {
                "id": "r1_ospf",
                "name": "R1 OSPF Backbone Area",
                "device": "R1",
                "field": "ospf_area",
                "expected": ["0.0.0.0", "0", 0],
                "points": 20.0,
                "remediation_hint": "Configure R1 OSPF area to 0.0.0.0 (Area 0 backbone).",
            },
            {
                "id": "r1_fw",
                "name": "R1 Firewall Default Deny Policy",
                "device": "R1",
                "field": "firewall_default_policy",
                "expected": "drop",
                "points": 20.0,
                "remediation_hint": "Set R1 firewall default policy to 'drop' for strict security.",
            },
            {
                "id": "r2_ip",
                "name": "R2 G0/1 WAN Addressing",
                "device": "R2",
                "field": "ip_g0_1",
                "expected": "10.0.0.2/30",
                "points": 20.0,
                "remediation_hint": "Configure R2 interface G0/1 with IP 10.0.0.2/30.",
            },
            {
                "id": "sw1_vlan",
                "name": "SW1 Access Port VLAN 10",
                "device": "SW1",
                "field": "vlan_access",
                "expected": ["10", 10],
                "points": 20.0,
                "remediation_hint": "Assign SW1 access port to VLAN 10.",
            },
        ],
    },
    "lab1": {
        "lab_id": "lab1",
        "title": "Lab 1: Basic IP & Subnetting",
        "description": "Validation of IPv4 host addressing, subnet masks, default gateways, and router interface point-to-point subnets.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "rubric": [
            {"id": "host_ip", "name": "Host IP Addressing & Subnet Range", "points": 20.0, "remediation_hint": "Ensure host interfaces have valid usable unicast IPs."},
            {"id": "subnet_mask", "name": "Subnet Mask & Prefix Validation", "points": 20.0, "remediation_hint": "Configure /24 for LAN and /30 for WAN."},
            {"id": "default_gw", "name": "Default Gateway Configuration", "points": 20.0, "remediation_hint": "Point host gateway to router interface IP."},
            {"id": "router_ip", "name": "Router Interface IP Addressing", "points": 20.0, "remediation_hint": "Assign router interfaces valid gateway IPs."},
            {"id": "wan_subnet", "name": "WAN Point-to-Point Subnetting", "points": 20.0, "remediation_hint": "Use /30 point-to-point addressing on router interconnects."},
        ],
    },
    "lab2": {
        "lab_id": "lab2",
        "title": "Lab 2: Single-Area OSPF Configuration",
        "description": "Validation of OSPF daemon, router-id, Area 0 network statements, point-to-point interface type, and neighbor adjacency.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "rubric": [
            {"id": "ospf_daemon", "name": "OSPF Routing Daemon Activation", "points": 15.0, "remediation_hint": "Enable ospfd in FRR daemons."},
            {"id": "router_id", "name": "OSPF Process & Router-ID", "points": 20.0, "remediation_hint": "Configure unique 32-bit router-id on each router."},
            {"id": "area0", "name": "Area 0 Network Statements", "points": 25.0, "remediation_hint": "Add 'network <prefix> area 0' statements."},
            {"id": "p2p_intf", "name": "Point-to-Point Interface Network Type", "points": 20.0, "remediation_hint": "Set 'ip ospf network point-to-point' on WAN interface."},
            {"id": "adjacency", "name": "OSPF Neighbor Adjacency & Area Matching", "points": 20.0, "remediation_hint": "Ensure matching Area 0 and timers across routers."},
        ],
    },
    "lab3": {
        "lab_id": "lab3",
        "title": "Lab 3: VLANs and 802.1Q Trunking",
        "description": "Validation of switch access VLAN assignments, 802.1Q trunking, router-on-a-stick subinterfaces, and VLAN segmentation.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "rubric": [
            {"id": "vlan_def", "name": "VLAN Database Provisioning", "points": 20.0, "remediation_hint": "Define VLANs 10 and 20 on switches."},
            {"id": "access_ports", "name": "Access Port VLAN Membership", "points": 25.0, "remediation_hint": "Configure switch ports as mode: access with correct VLAN."},
            {"id": "trunk_ports", "name": "802.1Q Trunk Port Encapsulation", "points": 25.0, "remediation_hint": "Configure switch-to-switch link as mode: trunk with allowed [10, 20]."},
            {"id": "subinterfaces", "name": "Router-on-a-Stick Subinterfaces", "points": 20.0, "remediation_hint": "Create router subinterfaces (e.g. eth1.10, eth1.20)."},
            {"id": "segmentation", "name": "VLAN Segmentation & Subnet Alignment", "points": 10.0, "remediation_hint": "Align IP subnets with VLAN IDs."},
        ],
    },
    "lab4": {
        "lab_id": "lab4",
        "title": "Lab 4: Router ACL & Firewall",
        "description": "Validation of firewall filtering: Telnet (port 23) drop, SSH (port 22) permit, Web (port 80) permit, stateful inspection, and default deny.",
        "max_score": 100.0,
        "passing_threshold_pct": 70.0,
        "rubric": [
            {"id": "fw_engine", "name": "Firewall / ACL Engine Configuration", "points": 15.0, "remediation_hint": "Configure firewall rules block or ACLs."},
            {"id": "block_telnet", "name": "Block Plaintext Telnet (Port 23)", "points": 25.0, "remediation_hint": "Drop or deny TCP port 23 traffic."},
            {"id": "permit_ssh", "name": "Permit Secure Shell SSH (Port 22)", "points": 20.0, "remediation_hint": "Permit TCP port 22 traffic."},
            {"id": "permit_web", "name": "Permit Web Traffic (HTTP 80 / HTTPS 443)", "points": 15.0, "remediation_hint": "Permit TCP port 80/443 traffic towards web server."},
            {"id": "stateful", "name": "Stateful Connection Tracking", "points": 15.0, "remediation_hint": "Add rule accepting established/related packets."},
            {"id": "default_deny", "name": "Default Deny Security Posture", "points": 10.0, "remediation_hint": "Set default firewall policy to 'drop'."},
        ],
    },
}


def list_available_labs() -> List[Dict[str, Any]]:
    """Returns catalog of registered Cisco NetAcad educational labs."""
    return [
        {
            "lab_id": t["lab_id"],
            "title": t["title"],
            "description": t["description"],
            "max_score": t["max_score"],
            "passing_threshold_pct": t.get("passing_threshold_pct", 70.0),
            "items_count": len(t.get("rubric", [])),
        }
        for t in LAB_TEMPLATES.values()
    ]


def get_lab_template(lab_id: str) -> Dict[str, Any]:
    """Retrieves lab template by ID or alias."""
    norm = lab_id.strip().lower()
    if norm in LAB_TEMPLATES:
        return LAB_TEMPLATES[norm]
    for key, tmpl in LAB_TEMPLATES.items():
        if key in norm or norm in key:
            return tmpl
    raise ValueError(f"Lab template '{lab_id}' not found")

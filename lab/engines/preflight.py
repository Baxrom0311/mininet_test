"""Read-only simulation diagnostics; no native imports or scheduling."""

import ipaddress
import os

from lab.addressing import interface_address
from lab.model import parse_ifname


def application_issues(scenario):
    """Describe workloads before expansion loses their application types."""
    messages = {
        "http": "HTTP is modeled as scheduled TCP byte transfers; no HTTP requests or TLS handshake are executed. The server initiates the transfer to the client, unlike a normal HTTP download.",
        "ftp": "FTP is modeled as a TCP byte transfer; FTP control and data sessions are not executed.",
        "voip": "VoIP is modeled as two UDP flows with codec-based sizes and rates; RTP headers and a receiver jitter buffer are not implemented. MOS is an estimate.",
        "video": "Video uses a TCP flow; quality, buffer and stalls are estimated after the run. No HLS/DASH segments are requested and ABR does not change transmitted traffic.",
        "http3": "HTTP/3 is modeled as independent UDP flows; QUIC, TLS, multiplexing, recovery and 0-RTT handshakes are not executed.",
    }
    groups = {}
    for index, app in enumerate(scenario.get("apps") or []):
        kind = app.get("type")
        if kind not in messages:
            continue
        item = groups.setdefault(kind, {"code": f"application.{kind}_model", "severity": "warning",
                                        "message": messages[kind], "count": 0, "paths": []})
        item["count"] += 1
        if len(item["paths"]) < 10:
            item["paths"].append(f"scenario.apps.{index}")
    return list(groups.values())


def ns3_options(project, scenario):
    requested = scenario.get("ns3_fast", False)
    forced = os.environ.get("NETLAB_NS3_FAST") == "1"
    wifi = any((link.get("params") or {}).get("medium") == "wifi"
               for link in project["links"].values())
    enabled = (requested or forced) and not wifi
    return {"fast_requested": requested, "fast_environment": forced,
            "fast_enabled": enabled, "fast_disabled_by_wifi": wifi and (requested or forced),
            "mtu": 9000 if enabled else 1500}


def issues(project, scenario, engine):
    """Inspect validated execution inputs; group paths to bound response size."""
    groups = {item["code"]: item for item in application_issues(scenario)}

    def add(code, severity, message, path):
        item = groups.setdefault(code, {"code": code, "severity": severity, "message": message,
                                       "count": 0, "paths": []})
        item["count"] += 1
        if len(item["paths"]) < 10:
            item["paths"].append(path)

    version = scenario.get("ip_version", 4)
    if engine != "ns3":
        if engine == "analytic":
            add("analytic.flow_model", "warning", "Flow approximation; events use the configured sample grid.", "scenario.sample")
        return list(groups.values())
    linked = {}
    for link in project["links"].values():
        for end in (link["a"], link["b"]):
            linked.setdefault(end["dev"], set()).add(end["port"])
    for name, device in project["devices"].items():
        cfg = device.get("config") or {}
        path = f"devices.{name}.config"
        if device["type"] == "switch":
            for port, settings in (cfg.get("ports") or {}).items():
                if settings.get("mode") == "trunk":
                    add("ns3.vlan_trunk", "error", "VLAN trunks are unsupported in ns-3; use analytic or Mininet.", f"{path}.ports.{port}")
        ports = linked.get(name, set())
        for ifn, config in (cfg.get("interfaces") or {}).items():
            _, port, vlan = parse_ifname(ifn)
            if vlan is not None:
                add("ns3.vlan_subinterface", "error", "VLAN subinterfaces are unsupported in ns-3.", f"{path}.interfaces.{ifn}")
            address = interface_address(config, version)
            if version == 6 and port in ports and address and ipaddress.ip_interface(address).ip in ipaddress.ip_network("2001:db8::/32"):
                add("ns3.documentation_prefix", "error", "ns-3 drops forwarded 2001:db8::/32 addresses; use ULA prefixes.", f"{path}.interfaces.{ifn}")
        if version == 4:
            for field, code, message in (
                ("nat", "ns3.nat_ignored", "NAT address translation is not performed."),
                ("dhcp_server", "ns3.dhcp_static", "DHCP leases are resolved as static addresses; DHCP packets are not simulated."),
            ):
                if cfg.get(field):
                    add(code, "warning", message, f"{path}.{field}")
        if cfg.get("firewall"):
            add("ns3.firewall_model", "warning", "Firewall uses initial model flow blocking, not per-packet iptables.", f"{path}.firewall")
        daemons = set(cfg.get("daemons") or [])
        modeled = daemons & ({"ospf6d", "isisd"} if version == 6 else {"ospfd", "isisd", "bgpd", "eigrpd"})
        if modeled:
            add("ns3.routing_model", "warning", "Selected routing protocols use model-derived static routes, not native control-plane packets.", f"{path}.daemons")
        if version == 6 and any(cfg.get(field) for field in ("nat", "bgp", "vrrp", "pim", "dhcp_server")):
            add("ipv6.config_ignored", "warning", "IPv4 NAT/BGP/VRRP/PIM/DHCP settings are omitted by IPv6 family selection.", path)
        if device["type"] in ("p4switch", "sdnrouter") or cfg.get("mode") == "sdn":
            add("ns3.programs_not_executed", "warning", "P4/SDN programs are not executed; devices use model L2/L3 behavior.", path)
    for lid, link in project["links"].items():
        jitter = str((link.get("params") or {}).get("jitter", "0ms"))
        if float(jitter.removesuffix("ms").removesuffix("us").removesuffix("s")) != 0:
            add("ns3.jitter_ignored", "warning", "Configured link jitter is not simulated.", f"links.{lid}.params.jitter")
    options = ns3_options(project, scenario)
    if options["fast_enabled"]:
        add("ns3.fast_approximation", "warning", "Fast mode uses MTU 9000 and changes packet-level delay, loss and queues.", "scenario.ns3_fast")
    elif options["fast_disabled_by_wifi"]:
        add("ns3.fast_disabled_wifi", "warning", "Fast mode is disabled because the project contains Wi-Fi links.", "scenario.ns3_fast")
    return list(groups.values())


def report(manager, project, scenario_name, engine):
    engines = {entry["id"]: entry for entry in manager.engines()}
    selected = engines.get(engine) if isinstance(engine, str) else None
    result = {"engine": engine if isinstance(engine, str) else None, "valid": False,
              "available": bool(selected and selected["available"]), "compatible": False,
              "ready": False, "issues": [], "effective": {}, "counts": {}}
    if selected and not selected["available"]:
        result["unavailable_reason"] = selected["hint"]
    try:
        project, scenario, _ = manager._prepare_inputs(project, scenario_name, engine)
    except ValueError as error:
        result["issues"] = [{"code": "input.invalid", "severity": "error", "message": str(error)}]
        return result
    result["valid"] = True
    result["issues"] = issues(project, scenario, engine)
    result["compatible"] = not any(item["severity"] == "error" for item in result["issues"])
    result["ready"] = result["compatible"] and result["available"]
    result["effective"] = {"ip_version": scenario.get("ip_version", 4), "sample_s": scenario.get("sample", 1)}
    if engine == "ns3":
        result["effective"].update(ns3_options(project, scenario))
    from lab.apps import expand
    from lab.failures import expand as expand_failures
    effective = expand(expand_failures(project, scenario))
    result["counts"] = {"devices": len(project["devices"]), "links": len(project["links"]),
                        "applications": len(scenario.get("apps") or []),
                        "expanded_applications": len(effective.get("apps") or []),
                        "events": len(effective.get("events") or [])}
    return result

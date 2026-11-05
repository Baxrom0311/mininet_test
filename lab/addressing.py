"""Address-family selection shared by the model and simulation engines.

Projects may keep IPv4 ``ip``/``gateway`` and IPv6 ``ip6``/``gateway6`` together.
Each scenario selects one family with ``ip_version`` (4 by default). Selection
returns a copy: running an IPv6 scenario never rewrites the user's IPv4 config.
IPv6 in the legacy ``ip`` field is also accepted for IPv6-only projects.
"""

import copy
import ipaddress


def interface_address(config, version):
    value = config.get("ip6") if version == 6 else None
    value = value or config.get("ip")
    if not value or value == "dhcp":
        return value if version == 4 else None
    return value if ipaddress.ip_interface(value).version == version else None


def project_for_family(project, version):
    """Select a single address family; IPv6 currently models static/SPF routes.

    DHCPv6, NAT66 and BGP IPv6 address families are deliberately unsupported.
    IPv4 DHCP/NAT/BGP settings in a dual-stack project do not apply to IPv6.
    """
    if type(version) is not int or version not in (4, 6):
        raise ValueError("ip_version: 4 | 6")
    out = copy.deepcopy(project)
    for dev in out.get("devices", {}).values():
        cfg = dev.setdefault("config", {})
        for ic in (cfg.get("interfaces") or {}).values():
            ip = interface_address(ic, version)
            ic.pop("ip6", None)
            ic["ip"] = ip or ""
        gw = cfg.get("gateway6") if version == 6 else cfg.get("gateway")
        if version == 6 and not gw and cfg.get("gateway"):
            if ipaddress.ip_address(cfg["gateway"]).version == 6:
                gw = cfg["gateway"]
        cfg["gateway"] = gw or ""
        cfg.pop("gateway6", None)
        cfg["routes"] = [r for r in cfg.get("routes") or []
                         if ipaddress.ip_network(r["prefix"], strict=False).version == version]
        if version == 6:
            for key in ("dhcp_server", "nat", "bgp", "vrrp", "pim"):
                cfg.pop(key, None)
            cfg["daemons"] = [d for d in cfg.get("daemons") or []
                              if d in ("ospf6d", "isisd")]
    return out

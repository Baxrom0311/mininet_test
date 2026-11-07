"""Bounded topology planning without allocating devices, links, or flows."""

import re

from lab.validation_numbers import integer, number

MAX_DEVICES = 3000
MAX_FLOWS = 10000
MAX_TRANSIT_LINKS = 16000
MAX_PORTS = 255
MAX_HOSTS_PER_LAN = 245  # IPv4 LAN addresses are .10 through .254 inclusive.

DEFAULTS = {
    "ring": {"routers": 10, "hosts": 3, "flows": 20, "core_bw": 1000, "lan_bw": 100, "name": "ring"},
    "star": {"branches": 8, "hosts": 3, "flows": 20, "core_bw": 100, "lan_bw": 100, "name": "star"},
    "tree": {"depth": 3, "hosts": 3, "flows": 20, "core_bw": 1000, "lan_bw": 100, "name": "tree"},
    "campus": {"core": 2, "dist": 4, "access": 8, "hosts": 5, "flows": 40,
               "core_bw": 10000, "dist_bw": 1000, "lan_bw": 100, "name": "campus"},
}


def _bandwidth(value, field):
    result = number(value, field, minimum=1e-6, maximum=10000)
    # Keep default/valid numeric spellings stable in generated JSON.
    return value if type(value) in (int, float) else result


def plan(kind, **options):
    """Validate dimensions and return exact counts before any construction work."""
    if not isinstance(kind, str) or kind not in DEFAULTS:
        raise ValueError(f"kind: {' | '.join(DEFAULTS)}")
    unknown = set(options) - DEFAULTS[kind].keys() - {"ipv6"}
    if unknown:
        raise ValueError(f"{kind}: unsupported options: {', '.join(sorted(unknown))}")
    ipv6 = options.get("ipv6", False)
    if type(ipv6) is not bool:
        raise ValueError("ipv6: boolean required")
    values = {**DEFAULTS[kind], **{key: value for key, value in options.items() if key != "ipv6"}}
    name = values["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise ValueError("name: 1..40 characters [A-Za-z0-9_-]")
    for field in values:
        if field.endswith("_bw"):
            values[field] = _bandwidth(values[field], field)
        elif field != "name":
            minimum = 0 if field in ("hosts", "flows") else (3 if field == "routers" else 1)
            maximum = (MAX_HOSTS_PER_LAN if field == "hosts" else MAX_FLOWS if field == "flows"
                       else MAX_DEVICES.bit_length() if field == "depth" else MAX_DEVICES)
            values[field] = integer(values[field], field, minimum=minimum, maximum=maximum)

    hosts = values["hosts"]
    if kind == "ring":
        routers = lans = values["routers"]
        chords = 0 if routers == 3 else (routers // 2 + 2) // 3
        transit = routers + chords
        router_ports = 3 if routers == 3 else 4
    elif kind == "star":
        lans = values["branches"]
        routers, transit = lans + 1, lans
        router_ports = max(lans, 2)
    elif kind == "tree":
        # depth was bounded before exponentiation, even for giant Python ints.
        lans = 1 << (values["depth"] - 1)
        routers = 2 * lans - 1
        transit = routers - 1
        router_ports = min(values["depth"], 3)
    else:
        core, dist, lans = values["core"], values["dist"], values["access"]
        upstream = min(core, 2)
        routers = core + dist
        transit = core * (core - 1) // 2 + dist * upstream
        router_ports = max(core - 1 + dist, upstream + (lans + dist - 1) // dist)

    total_hosts = lans * hosts
    devices = routers + lans + total_hosts
    links = transit + lans * (hosts + 1)
    if devices > MAX_DEVICES:
        raise ValueError(f"topology: {devices} devices exceed limit {MAX_DEVICES}")
    if transit > MAX_TRANSIT_LINKS:
        raise ValueError(f"topology: {transit} transit links exceed limit {MAX_TRANSIT_LINKS}")
    if router_ports > MAX_PORTS or hosts + 1 > MAX_PORTS:
        raise ValueError(f"topology: a device would exceed {MAX_PORTS} ports")
    if values["flows"] and total_hosts < 2:
        raise ValueError("flows: positive traffic requires at least two hosts; use flows=0 for a topology only")
    return {"kind": kind, "ipv6": ipv6, "options": values,
            "counts": {"routers": routers, "switches": lans, "hosts": total_hosts, "devices": devices,
                       "links": links, "transit_links": transit, "flows": values["flows"]},
            "limits": {"devices": MAX_DEVICES, "flows": MAX_FLOWS, "transit_links": MAX_TRANSIT_LINKS,
                       "ports_per_device": MAX_PORTS, "hosts_per_lan": MAX_HOSTS_PER_LAN}}

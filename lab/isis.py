"""Structured IS-IS configuration, adjacency eligibility and weighted SPF model.

The model uses configured NET areas/levels and wide metrics. ns-3 installs its
result as static IPv4/IPv6 routes; only the real FRR driver runs IS-IS packets.
L1/L2 route leaking, DIS election and protocol convergence are not simulated.
"""

import heapq
import ipaddress
import re

LEVELS = {"level-1": {1}, "level-1-2": {1, 2}, "level-2-only": {2}}


def config(dev):
    cfg = dev.get("config") or {}
    if dev.get("type") != "router" or "isisd" not in (cfg.get("daemons") or []):
        return None
    did = dev.get("id")
    sid = f"{did if type(did) is int else 0:012x}"
    default_net = "49.0001." + ".".join(sid[i:i + 4] for i in range(0, 12, 4)) + ".00"
    return {"net": default_net, "tag": "CORE", "level": "level-2-only", "metric": 10,
            "hello_interval": 1, "hello_multiplier": 3, "interfaces": {}, **(cfg.get("isis") or {})}


def validate(project):
    errors, systems = [], {}
    for name, dev in project.get("devices", {}).items():
        cfg = dev.get("config") or {}
        if cfg.get("isis") and not isinstance(cfg["isis"], dict):
            errors.append(f"{name}: IS-IS config obyekt bo'lsin")
            continue
        if cfg.get("isis") and not config(dev):
            errors.append(f"{name}: IS-IS router va isisd daemonini talab qiladi")
            continue
        c = config(dev)
        if not c:
            continue
        raw = str(c["net"]).replace(".", "")
        if not re.fullmatch(r"[0-9a-fA-F]{16,40}", raw) or len(raw) % 2 or not raw.endswith("00"):
            errors.append(f"{name}: IS-IS NET 8..20 oktet, oxirgi NSEL 00 bo'lsin")
        else:
            system = raw[-14:-2].lower()
            if system in systems:
                errors.append(f"{name}: IS-IS system ID {systems[system]} bilan takrorlangan")
            systems[system] = name
        if not isinstance(c["level"], str) or c["level"] not in LEVELS:
            errors.append(f"{name}: IS-IS level {tuple(LEVELS)} dan biri")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,15}", str(c["tag"])):
            errors.append(f"{name}: IS-IS tag noto'g'ri")
        for field, lo, hi in (("metric", 1, 16777215), ("hello_interval", 1, 600), ("hello_multiplier", 2, 100)):
            if type(c[field]) is not int or not lo <= c[field] <= hi:
                errors.append(f"{name}: IS-IS {field} {lo}..{hi}")
        if not isinstance(c["interfaces"], dict):
            errors.append(f"{name}: IS-IS interfaces obyekt bo'lsin")
            continue
        for ifn, values in c["interfaces"].items():
            if not isinstance(values, dict):
                errors.append(f"{name}/{ifn}: IS-IS interfeys sozlamasi obyekt bo'lsin")
                continue
            if ifn not in (cfg.get("interfaces") or {}):
                errors.append(f"{name}: IS-IS interfeys {ifn} mavjud emas")
            metric = values.get("metric", c["metric"])
            if type(metric) is not int or not 1 <= metric <= 16777215:
                errors.append(f"{name}/{ifn}: IS-IS metric 1..16777215")
            if type(values.get("passive", False)) is not bool:
                errors.append(f"{name}/{ifn}: IS-IS passive bool bo'lsin")
    return errors


def peers(a, b, interface_a=None, interface_b=None):
    ca, cb = config(a), config(b)
    if not ca or not cb:
        return False
    if (ca["interfaces"].get(interface_a, {}).get("passive")
            or cb["interfaces"].get(interface_b, {}).get("passive")):
        return False
    levels = LEVELS[ca["level"]] & LEVELS[cb["level"]]
    area_a = ca["net"].replace(".", "")[:-14].lower()
    area_b = cb["net"].replace(".", "")[:-14].lower()
    return 2 in levels or (1 in levels and area_a == area_b)


def routes(net, source, known):
    distance = {source: 0}
    queue = [(0, source, None, None)]
    out = []
    while queue:
        cost, current, first_via, first_if = heapq.heappop(queue)
        if cost != distance[current]:
            continue
        if current != source:
            for port, _ifn, ip in net.ifaces(current):
                link = net.link_on(current, port)
                if ip and ip.network not in known and link and net.link_ok(link[0]):
                    known.add(ip.network)
                    out.append((ip.network, first_via, first_if, f"IS-IS [{cost}]"))
        c = config(net.devs[current])
        for _port, ifn, _ip, neighbor, nip, _path in net.adjacency(current):
            nif = next((f for _p, f, ip in net.ifaces(neighbor) if ip == nip), None)
            if not peers(net.devs[current], net.devs[neighbor], ifn, nif) or net._other_as(current, neighbor):
                continue
            metric = c["interfaces"].get(ifn, {}).get("metric", c["metric"])
            new = cost + metric
            if new < distance.get(neighbor, float("inf")):
                distance[neighbor] = new
                heapq.heappush(queue, (new, neighbor, first_via or nip.ip, first_if or ifn))
    return out


def neighbors(net, name):
    out = []
    for _port, ifn, _ip, nd, nip, _path in net.adjacency(name):
        nif = next((f for _p, f, ip in net.ifaces(nd) if ip == nip), None)
        if peers(net.devs[name], net.devs[nd], ifn, nif):
            out.append({"neighbor": nd, "interface": ifn, "state": "Up", "level": config(net.devs[name])["level"]})
    return out


def frr_config(project, name):
    dev = project["devices"][name]
    c = config(dev)
    if not c:
        return ""
    lines = ["configure terminal", f"router isis {c['tag']}", f" net {c['net']}",
             f" is-type {c['level']}", " metric-style wide", "exit"]
    for ifn, ic in sorted((dev["config"].get("interfaces") or {}).items()):
        if not ic.get("ip"):
            continue
        version = ipaddress.ip_interface(ic["ip"]).version
        opts = c["interfaces"].get(ifn, {})
        lines += [f"interface {ifn}", f" {'ipv6' if version == 6 else 'ip'} router isis {c['tag']}",
                  f" isis metric {opts.get('metric', c['metric'])}",
                  f" isis hello-interval {c['hello_interval']}", f" isis hello-multiplier {c['hello_multiplier']}"]
        if opts.get("passive"):
            lines.append(" isis passive")
        else:
            lines.append(" isis network point-to-point")
        lines.append("exit")
    return "\n".join(lines + ["end", ""])

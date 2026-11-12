"""IPv4 OSPF interface selection and passive-interface policy for the SPF model.

Reads a deliberately small FRR startup subset. This is not an OSPF packet or ABR
implementation. With no explicit OSPF configuration, daemon-only legacy projects
retain their all-interface, area-0 model. OSPFv3 is handled separately by Net.
"""

from collections import deque
from dataclasses import dataclass, field
import ipaddress


class UnsupportedOspf(ValueError):
    """Valid FRR scopes which a real driver may use but this model cannot flatten."""


def _area(value):
    value = str(value)
    if "." in value:
        return int(ipaddress.IPv4Address(value))
    if not value.isdecimal() or not 0 <= int(value) <= 4294967295:
        raise ValueError("area must be 0..4294967295 or dotted IPv4")
    return int(value)


@dataclass
class Policy:
    explicit: bool = False
    networks: dict = field(default_factory=dict)
    interfaces: dict = field(default_factory=dict)
    passive_default: bool = False
    passive: dict = field(default_factory=dict)

    def area(self, ifname, address):
        if address is None or address.version != 4:
            return None
        if ifname in self.interfaces:
            return self.interfaces[ifname]
        matches = [prefix for prefix in self.networks if address.ip in prefix]
        if matches:
            return self.networks[max(matches, key=lambda prefix: prefix.prefixlen)]
        return None if self.explicit else 0

    def is_passive(self, ifname):
        return self.passive.get(ifname, self.passive_default)


def parse(dev):
    """Parse OSPF's default routing process; malformed supported commands fail closed."""
    policy = Policy()
    context, ifname = None, None
    startup = (dev.get("config") or {}).get("startup")
    if startup is None:
        startup = ""
    if not isinstance(startup, str):
        raise ValueError("OSPF startup must be text")
    for number, line in enumerate(startup.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(("!", "#")):
            continue
        words = line.split()
        try:
            if words[:2] == ["router", "ospf"]:
                policy.explicit = True
                if len(words) != 2:
                    raise UnsupportedOspf(f"OSPF startup line {number}: only the default OSPF process is modeled (no process ID/VRF)")
                context, ifname = "ospf", None
                continue
            if words[0] in ("router", "interface", "exit", "end", "exit-vrf"):
                context, ifname = ("interface", words[1]) if words[0] == "interface" and len(words) == 2 else (None, None)
                continue
            negate = words[0] == "no"
            command = words[1:] if negate else words
            if context == "ospf" and command[:1] == ["network"]:
                if len(command) != 4 or command[2] != "area":
                    raise ValueError("expected network IPv4/prefix area AREA")
                prefix, area = ipaddress.IPv4Network(command[1], strict=False), _area(command[3])
                if negate:
                    if policy.networks.get(prefix) == area:
                        policy.networks.pop(prefix)
                else:
                    policy.networks[prefix] = area
            elif context == "ospf" and command[:1] == ["passive-interface"]:
                if len(command) != 2:
                    raise ValueError("expected passive-interface IFNAME|default")
                if command[1] == "default":
                    policy.passive_default = not negate
                else:
                    policy.passive[command[1]] = not negate
            elif context == "interface" and command[:3] == ["ip", "ospf", "area"]:
                policy.explicit = True
                if len(command) != 4:
                    raise ValueError("expected ip ospf area AREA")
                area = _area(command[3])
                if negate:
                    if policy.interfaces.get(ifname) == area:
                        policy.interfaces.pop(ifname)
                else:
                    policy.interfaces[ifname] = area
        except UnsupportedOspf:
            raise
        except ValueError as error:
            raise ValueError(f"OSPF startup line {number}: {error}") from None
    return policy


def validate(project):
    errors = []
    for name, dev in (project.get("devices") or {}).items():
        if dev.get("type") == "router" and "ospfd" in ((dev.get("config") or {}).get("daemons") or []):
            try:
                parse(dev)
            except UnsupportedOspf:
                pass  # Model limitations must not prohibit valid real FRR startup.
            except ValueError as error:
                errors.append(f"{name}: {error}")
    return errors


def _policy(net, name):
    cache = net.__dict__.setdefault("_ospf_policies", {})
    if name not in cache:
        cache[name] = parse(net.devs[name])
    return cache[name]


def routes(net, source, known):
    """Hop SPF per area. Passive prefixes are stubs; no inter-area/ABR propagation."""
    if net.ip_version != 4:
        return []
    policy = _policy(net, source)
    areas = {policy.area(ifname, address) for _port, ifname, address in net.ifaces(source)} - {None}
    queue = deque((source, area, None, None, 0) for area in sorted(areas))
    seen = {(source, area) for area in areas}
    out = []
    while queue:
        current, area, first_via, first_if, hops = queue.popleft()
        policy = _policy(net, current)
        if current != source:
            for port, ifname, address in net.ifaces(current):
                link = net.link_on(current, port)
                if (address and address.network not in known and policy.area(ifname, address) == area
                        and link and net.link_ok(link[0])):
                    known.add(address.network)
                    out.append((address.network, first_via, first_if, f"OSPF [{hops}]"))
        for _port, ifname, address, neighbor, neighbor_address, _path in net.adjacency(current):
            if ((neighbor, area) in seen or "ospfd" not in net.dyn_daemons(neighbor)
                    or policy.area(ifname, address) != area or policy.is_passive(ifname)
                    or net._other_as(current, neighbor)):
                continue
            peer = _policy(net, neighbor)
            peer_if = next((f for _p, f, ip in net.ifaces(neighbor) if ip == neighbor_address), None)
            if peer.area(peer_if, neighbor_address) != area or peer.is_passive(peer_if):
                continue
            seen.add((neighbor, area))
            queue.append((neighbor, area, first_via or neighbor_address.ip, first_if or ifname, hops + 1))
    return out


def frr_config(project_or_dev, name=None):
    """Router konfiguratsiyasidan FRR vtysh sintaksisidagi OSPF konfiguratsiyasi matnini yaratadi."""
    if name is not None and isinstance(project_or_dev, dict) and "devices" in project_or_dev:
        dev = (project_or_dev.get("devices") or {}).get(name)
    else:
        dev = project_or_dev
        name = name or (dev.get("name") if isinstance(dev, dict) else "")
    if not dev:
        return ""
    cfg = dev.get("config") or {}
    ospf_cfg = cfg.get("ospf") or {}
    ifs = cfg.get("interfaces") or {}
    did = dev.get("id", 1)

    rid = ospf_cfg.get("router_id")
    if not rid:
        did_int = int(did) if isinstance(did, (int, str)) and str(did).isdigit() else 1
        rid = f"1.1.{did_int // 256}.{did_int % 256}"

    lines = ["router ospf", f" ospf router-id {rid}"]

    for r in ospf_cfg.get("redistribute") or []:
        lines.append(f" redistribute {r}")

    if ospf_cfg.get("passive_default"):
        lines.append(" passive-interface default")
    for pif in ospf_cfg.get("passive_interfaces") or []:
        lines.append(f" passive-interface {pif}")

    configured_nets = ospf_cfg.get("networks")
    if configured_nets:
        for net_entry in configured_nets:
            if isinstance(net_entry, dict):
                pfx = net_entry.get("network") or net_entry.get("prefix")
                area = net_entry.get("area", 0)
                lines.append(f" network {pfx} area {area}")
            elif isinstance(net_entry, (list, tuple)) and len(net_entry) >= 2:
                lines.append(f" network {net_entry[0]} area {net_entry[1]}")
            elif isinstance(net_entry, str):
                if "area" in net_entry:
                    lines.append(f" network {net_entry}")
                else:
                    lines.append(f" network {net_entry} area 0")
    else:
        policy = parse(dev)
        for ifn, ic in sorted(ifs.items()):
            ip_str = (ic or {}).get("ip")
            if not ip_str or ip_str.lower() == "dhcp":
                continue
            try:
                iface_obj = ipaddress.ip_interface(ip_str)
                if iface_obj.version == 4:
                    area = policy.area(ifn, iface_obj)
                    if area is not None:
                        lines.append(f" network {iface_obj.network} area {area}")
            except ValueError:
                pass

    lines.append("!")

    for ifn, ic in sorted(ifs.items()):
        ip_str = (ic or {}).get("ip")
        if not ip_str or ip_str.lower() == "dhcp":
            continue
        try:
            iface_obj = ipaddress.ip_interface(ip_str)
            if iface_obj.version != 4:
                continue
        except ValueError:
            continue

        if_ospf = (ospf_cfg.get("interfaces") or {}).get(ifn) or {}
        if_lines = [f"interface {ifn}"]

        is_p2p = if_ospf.get("point_to_point") or iface_obj.network.prefixlen in (30, 31)
        if if_ospf.get("network"):
            if_lines.append(f" ip ospf network {if_ospf['network']}")
        elif is_p2p:
            if_lines.append(" ip ospf network point-to-point")

        hello = if_ospf.get("hello_interval") or (1 if is_p2p else None)
        dead = if_ospf.get("dead_interval") or (4 if is_p2p else None)
        if hello:
            if_lines.append(f" ip ospf hello-interval {hello}")
        if dead:
            if_lines.append(f" ip ospf dead-interval {dead}")
        if if_ospf.get("cost"):
            if_lines.append(f" ip ospf cost {if_ospf['cost']}")
        if if_ospf.get("priority") is not None:
            if_lines.append(f" ip ospf priority {if_ospf['priority']}")
        if if_ospf.get("area") is not None:
            if_lines.append(f" ip ospf area {if_ospf['area']}")

        if len(if_lines) > 1:
            if_lines.append("!")
            lines += if_lines

    return "\n".join(lines) + "\n"


"""Scalar boundaries checked before legacy project semantic validators.

Shape validation lives in project_shape. This module intentionally leaves unknown
extension fields alone and never rewrites the caller's project document.
"""

import ipaddress

from lab.validation_numbers import MAX_POSITION, integer, number

MAX_UINT32 = (1 << 32) - 1
# A numeric safety envelope, not a policy on permitted physical radio power.
# dBm -> milliwatts uses 10**(power/10); +/-3000 leaves float headroom.
MAX_TX_POWER_DBM = 3000


def _object(value):
    return value if isinstance(value, dict) else {}


def _objects(value):
    return (item for item in value if isinstance(item, dict)) if isinstance(value, list) else ()


def _check(errors, check, value, name, minimum=None, maximum=None):
    try:
        return check(value, name, minimum=minimum, maximum=maximum)
    except ValueError as exc:
        errors.append(str(exc))
        return None


def _json_integer(errors, value, name, minimum, maximum):
    if type(value) is not int:
        errors.append(f"{name}: JSON integer {minimum}..{maximum}")
        return None
    return _check(errors, integer, value, name, minimum, maximum)


def _configured_integer(errors, cfg, field, name, minimum, maximum, optional=False):
    if field not in cfg or (optional and cfg[field] in (None, "")):
        return None
    return _check(errors, integer, cfg[field], f"{name}.{field}", minimum, maximum)


def _address(errors, value, name, parse):
    try:
        if not isinstance(value, str):
            raise ValueError
        return parse(value)
    except (TypeError, ValueError):
        errors.append(f"{name}: valid address/prefix required")
        return None


def _vlan_stp(errors, cfg, name):
    mode = cfg.get("stp_mode")
    if mode not in (None, "") and (not isinstance(mode, str) or mode.lower() not in ("stp", "rstp")):
        errors.append(f"{name}.stp_mode: stp | rstp")
    priority = _configured_integer(errors, cfg, "stp_priority", name, 0, 61440, optional=True)
    if priority is not None and priority % 4096:
        errors.append(f"{name}.stp_priority: multiple of 4096")
    for key, port in _object(cfg.get("ports")).items():
        where = f"{name}.ports.{key}"
        parsed = _check(errors, integer, key, f"{where} port", 1, 255)
        if parsed is not None and key != str(parsed):
            errors.append(f"{where}: canonical decimal port key required (for example '1')")
        if not isinstance(port, dict):
            continue
        _configured_integer(errors, port, "stp_cost", where, 1, 200000000, optional=True)
        if port.get("mode") == "access":
            _check(errors, integer, port.get("vlan"), f"{where}.vlan (VLAN)", 1, 4094)
        elif port.get("mode") == "trunk":
            allowed = port.get("allowed")
            if isinstance(allowed, list):
                for i, value in enumerate(allowed):
                    _check(errors, integer, value, f"{where}.allowed[{i}] (VLAN)", 1, 4094)
            native = port.get("native")
            if isinstance(native, bool):
                errors.append(f"{where}.native: integer VLAN or 0")
            elif native not in (None, "", 0):
                _check(errors, integer, native, f"{where}.native (VLAN)", 1, 4094)


def _routing(errors, cfg, name):
    timers = _object(cfg.get("rip_timers"))
    for field, minimum, maximum in (("update", 1, 3600), ("timeout", 1, 7200), ("garbage", 1, 7200)):
        _configured_integer(errors, timers, field, f"{name}.rip_timers", minimum, maximum)
    bgp = _object(cfg.get("bgp"))
    if bgp:
        _check(errors, integer, bgp.get("asn"), f"{name}.bgp.asn (BGP)", 1, MAX_UINT32)
        if bgp.get("router_id"):
            _address(errors, bgp["router_id"], f"{name}.bgp.router_id (BGP)", ipaddress.IPv4Address)
        for i, neighbor in enumerate(_objects(bgp.get("neighbors"))):
            where = f"{name}.bgp.neighbors[{i}]"
            _check(errors, integer, neighbor.get("remote_as"), f"{where}.remote_as (BGP)", 1, MAX_UINT32)
            if "ip" in neighbor:
                _address(errors, neighbor["ip"], f"{where}.ip (BGP)", ipaddress.IPv4Address)
        networks = bgp.get("networks")
        if isinstance(networks, list):
            for i, network in enumerate(networks):
                _address(errors, network, f"{name}.bgp.networks[{i}] (BGP)",
                         lambda value: ipaddress.ip_network(value, strict=False))


def _dhcp(errors, cfg, name):
    interfaces = _object(cfg.get("interfaces"))
    server = _object(cfg.get("dhcp_server"))
    for i, pool in enumerate(_objects(server.get("pools"))):
        where = f"{name}.dhcp_server.pools[{i}]"
        _configured_integer(errors, pool, "lease", where, 1, MAX_UINT32)
        interface = _object(interfaces.get(pool.get("iface"))) if isinstance(pool.get("iface"), str) else {}
        for field, value, constructor in (("interface", interface.get("ip"), ipaddress.IPv4Interface),
                                          ("start", pool.get("start"), ipaddress.IPv4Address),
                                          ("end", pool.get("end"), ipaddress.IPv4Address)):
            try:
                if not isinstance(value, str):
                    raise ValueError
                constructor(value)
            except (TypeError, ValueError):
                errors.append(f"{where}.{field}: IPv4 address required")
        gateway = pool.get("gateway")
        if gateway is not None and gateway != "":
            try:
                if not isinstance(gateway, str):
                    raise ValueError
                ipaddress.IPv4Address(gateway)
            except (TypeError, ValueError):
                errors.append(f"{where}.gateway: IPv4 address required")


def _vrrp_bonds_firewall(errors, cfg, name):
    for i, entry in enumerate(_objects(cfg.get("vrrp"))):
        where = f"{name}.vrrp[{i}]"
        _check(errors, integer, entry.get("vrid"), f"{where}.vrid (VRRP)", 1, 255)
        for field, minimum, maximum in (("priority", 1, 254), ("advert_ms", 10, 40950)):
            if field in entry:
                _check(errors, integer, entry[field], f"{where}.{field} (VRRP)", minimum, maximum)
        # Check supplied addressing alongside numeric fields, so independent
        # mistakes remain visible even when unsafe scalar inputs stop later
        # protocol validators. Missing fields retain their semantic checks there.
        if "iface" in entry or "vip" in entry:
            ifname = entry.get("iface")
            interface = _object(_object(cfg.get("interfaces")).get(ifname)) if isinstance(ifname, str) else {}
            ip = _address(errors, interface.get("ip"), f"{where}.iface (VRRP)", ipaddress.ip_interface)
            vip = _address(errors, entry.get("vip"), f"{where}.vip (VRRP)", ipaddress.ip_interface)
            if ip is not None and vip is not None:
                if vip.ip not in ip.network:
                    errors.append(f"{where}.vip (VRRP): address must be in the interface subnet")
                elif vip.ip == ip.ip:
                    errors.append(f"{where}.vip (VRRP): interface owner address is unsupported")
    for bond_name, bond in _object(cfg.get("bonds")).items():
        if not isinstance(bond, dict):
            continue
        ports = bond.get("ports")
        if isinstance(ports, list):
            for i, port in enumerate(ports):
                _check(errors, integer, port, f"{name}.bonds.{bond_name}.ports[{i}]", 1, 255)
    firewall = _object(cfg.get("firewall"))
    for i, rule in enumerate(_objects(firewall.get("rules"))):
        for field in ("dport", "sport"):
            _configured_integer(errors, rule, field, f"{name}.firewall.rules[{i}]", 1, 65535, optional=True)


def _wifi(errors, cfg, name):
    standard = cfg.get("standard")
    if standard not in (None, "", "80211g", "80211n", "80211ac", "80211ax"):
        errors.append(f"{name}.standard: 80211g | 80211n | 80211ac | 80211ax")
    for field in ("band", "tx_power"):
        value = cfg.get(field)
        if value is not None and value != "":
            bounds = (-MAX_TX_POWER_DBM, MAX_TX_POWER_DBM) if field == "tx_power" else (None, None)
            _check(errors, number, value, f"{name}.{field}", *bounds)
    # ap_config deliberately normalizes unsupported widths/channels to defaults.
    # Require exact numeric inputs while preserving that existing fallback.
    for field in ("width", "channel"):
        _configured_integer(errors, cfg, field, name, None, None, optional=True)


def _geometry(errors, project, devices):
    value = project.get("meters_per_px")
    scale = .25 if value is None else _check(errors, number, value, "meters_per_px")
    # The existing radio helper clamps nonpositive finite scales to .001.
    # A scale below 1 cannot reduce the safe canvas-coordinate bound further.
    limit = MAX_POSITION / max(scale if scale is not None else .25, 1)
    for name, device in devices.items():
        if not isinstance(device, dict):
            continue
        for field in ("x", "y"):
            value = device.get(field)
            if value is None:
                continue  # Missing/null positions use the origin after cleanup.
            where = f"devices.{name}.{field}"
            if type(value) not in (int, float):
                errors.append(f"{where}: JSON number")
            else:
                _check(errors, number, value, where, -limit, limit)


def validate_values(project):
    """Return errors for known numeric fields before semantic validators run."""
    if not isinstance(project, dict):
        return ["project: object"]
    errors = []
    devices = _object(project.get("devices"))
    _geometry(errors, project, devices)
    for name, device in devices.items():
        if not isinstance(device, dict):
            continue
        _json_integer(errors, device.get("id"), f"devices.{name}.id", 1, 65000)
        cfg = _object(device.get("config"))
        _vlan_stp(errors, cfg, name)
        _routing(errors, cfg, name)
        _dhcp(errors, cfg, name)
        _vrrp_bonds_firewall(errors, cfg, name)
        if device.get("type") == "wifiap":
            _wifi(errors, cfg, name)
    for name, link in _object(project.get("links")).items():
        if not isinstance(link, dict):
            continue
        for side in ("a", "b"):
            endpoint = _object(link.get(side))
            _json_integer(errors, endpoint.get("port"), f"links.{name}.{side}.port", 1, 255)
    for name, controller in _object(project.get("controllers")).items():
        if isinstance(controller, dict):
            _json_integer(errors, controller.get("port"), f"controllers.{name}.port", 1024, 65535)
    return errors

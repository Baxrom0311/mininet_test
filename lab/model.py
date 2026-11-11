"""NetLab loyiha modeli: qurilmalar, kabellar, P4 dasturlar, SDN kontrollerlar.

Loyiha — oddiy JSON (lab/projects/<nom>.json). Frontend modelning asosiy
manbai; backend esa uni tekshiradi (validate_project) va driverga beradi.

Qurilma turlari (DEVICE_TYPES):
    host       PC / oddiy host           (alohida network namespace)
    server     server host               (alohida network namespace)
    switch     Open vSwitch              (mode: standalone | sdn)
    p4switch   BMv2 simple_switch        (P4 dastur bilan dasturlanadi)
    router     FRRouting router          (zebra/ospfd/bgpd/... namespace ichida)
    sdnrouter  SDN router (OVS + kontroller ilovasi routingni bajaradi)
    wifiap     Wi-Fi kirish nuqtasi (L2 bridge; simsiz kabellar: params.medium == "wifi")

Port raqamlash: interfeys `<qurilma>-eth<N>` har doim OpenFlow/BMv2 port N
ga mos keladi — P4 jadval yozuvlari va SDN ilovalari shu raqamga tayanadi.
"""

import copy
import ipaddress
import re

NAME_RE = re.compile(r"^[a-z][a-z0-9]{0,7}$")

DEVICE_TYPES = {
    "host":      {"label": "PC",          "ns": True,  "l3": True,  "prefix": "pc"},
    "server":    {"label": "Server",      "ns": True,  "l3": True,  "prefix": "srv"},
    "switch":    {"label": "Switch (OVS)", "ns": False, "l3": False, "prefix": "s"},
    "p4switch":  {"label": "P4 Switch",   "ns": False, "l3": False, "prefix": "p"},
    "router":    {"label": "Router (FRR)", "ns": True,  "l3": True,  "prefix": "r"},
    "sdnrouter": {"label": "SDN Router",  "ns": False, "l3": True,  "prefix": "sr"},
    "wifiap":    {"label": "Wi-Fi AP",    "ns": False, "l3": False, "prefix": "ap"},
}

FRR_DAEMONS = ["ospfd", "bgpd", "ripd", "isisd", "ospf6d", "eigrpd", "pimd", "ldpd", "bfdd", "vrrpd"]

DEFAULT_LINK = {"bw": 100, "delay": "1ms", "jitter": "0ms", "loss": 0, "queue": 1000, "medium": "wire",
                "qdisc": "fifo"}
# Navbat intizomi (QoS): fifo — bitta DropTail; prio — qat'iy ustuvorlik (DSCP >= 40: EF/CS5-7
# yuqori sinf); fq_codel — oqimlar bo'yicha adolatli + CoDel AQM (navbat ~5 ms).
QDISCS = ("fifo", "prio", "fq_codel")
HIGH_DSCP = 40


def dscp_class(dscp):
    """0 — yuqori (EF/CS5..CS7), 1 — oddiy."""
    try:
        return 0 if int(dscp or 0) >= HIGH_DSCP else 1
    except (TypeError, ValueError):
        return 1

PROJECT_VERSION = 1


def empty_project(name="untitled"):
    return {
        "version": PROJECT_VERSION,
        "name": name,
        "devices": {},
        "links": {},
        "p4_programs": {},
        "controllers": {},
        "scenarios": {},
        "notes": "",
    }


def ifname(dev, port):
    return f"{dev}-eth{port}"


_IFN_RE = re.compile(r"^(.+)-eth(\d+)(?:\.(\d+))?$")
IFNAMSIZ = 15


def parse_ifname(ifn):
    """'r1-eth2' -> ('r1', 2, None); 'r1-eth2.10' -> ('r1', 2, 10); boshqa -> (None, None, None)."""
    m = _IFN_RE.match(ifn or "")
    if not m:
        return None, None, None
    return m.group(1), int(m.group(2)), (int(m.group(3)) if m.group(3) else None)


# ───── VLAN (802.1Q) ─────
# switch config.ports = {"<port>": {"mode": "access", "vlan": 10}
#                                 | {"mode": "trunk", "allowed": [10, 20], "native": 1}}
# Sozlanmagan port — OVS standarti: barcha VLAN'lar tegli o'tadi, tegsiz kadr = ichki VLAN 0.
# Trunk: allowed bo'sh/yo'q = hammasi; native berilsa tegsiz kadrlar shu VLAN'ga, aks holda tashlanadi.
VLAN_TYPES = ("switch",)


def switch_port_cfg(dev, port):
    if dev.get("type") not in VLAN_TYPES:
        return {"mode": "none"}
    pc = ((dev.get("config") or {}).get("ports") or {}).get(str(port)) or {}
    mode = pc.get("mode") or "none"
    if mode == "access":
        return {"mode": "access", "vlan": int(pc.get("vlan") or 1)}
    if mode == "trunk":
        allowed = pc.get("allowed")
        return {"mode": "trunk", "allowed": {int(v) for v in allowed} if allowed else None,
                "native": int(pc["native"]) if pc.get("native") not in (None, "", 0) else None}
    return {"mode": "none"}


def vlan_ingress(pc, tag):
    """Kirgan kadr (wire tag: None=tegsiz) -> ichki VLAN yoki None (tashlanadi)."""
    m = pc["mode"]
    if m == "access":
        return pc["vlan"] if tag is None else None
    if m == "trunk":
        if tag is None:
            return pc["native"]
        return tag if pc["allowed"] is None or tag in pc["allowed"] or tag == pc["native"] else None
    return 0 if tag is None else tag


def vlan_egress(pc, v):
    """Ichki VLAN v portdan chiqadimi: (ok, wire_tag)."""
    m = pc["mode"]
    if m == "access":
        return (v == pc["vlan"], None)
    if m == "trunk":
        if pc["native"] is not None and v == pc["native"]:
            return True, None
        return (pc["allowed"] is None or v in pc["allowed"]) and v != 0, v
    return True, (None if v == 0 else v)


def device_mac(dev_id, port):
    """Deterministik MAC (namespace'li qurilmalar interfeyslari uchun):
    00:00:00:<id hi>:<id lo>:<port> — P4/SDN jadvallarini qo'lda yozish oson bo'lsin."""
    return "00:00:00:%02x:%02x:%02x" % ((dev_id >> 8) & 0xFF, dev_id & 0xFF, port & 0xFF)


RIP_DEFAULTS = {"update": 30, "timeout": 180, "garbage": 120}
_RIP_TIMERS_RE = re.compile(r"^\s*timers\s+basic\s+(\d+)\s+(\d+)\s+(\d+)", re.M)


def rip_timers(dev):
    """RIP taymerlari (soniya): config.rip_timers > startup'dagi `timers basic U T G` >
    standart (30/180/120, FRR va ns-3 bilan bir xil)."""
    cfg = dev.get("config") or {}
    out = dict(RIP_DEFAULTS)
    m = _RIP_TIMERS_RE.search(cfg.get("startup") or "")
    if m:
        out.update(update=int(m.group(1)), timeout=int(m.group(2)), garbage=int(m.group(3)))
    for k, v in (cfg.get("rip_timers") or {}).items():
        if k in out and v not in (None, ""):
            out[k] = int(v)
    return out


def link_ports(project, dev):
    """Qurilmaning band portlari: {port: (link_id, peer_dev, peer_port)}."""
    out = {}
    for lid, l in project.get("links", {}).items():
        if l["a"]["dev"] == dev:
            out[l["a"]["port"]] = (lid, l["b"]["dev"], l["b"]["port"])
        if l["b"]["dev"] == dev:
            out[l["b"]["port"]] = (lid, l["a"]["dev"], l["a"]["port"])
    return out


def _check_ip(value, where, errors):
    if not value:
        return
    try:
        address = ipaddress.ip_interface(value)
        if "/" not in value:
            errors.append(f"{where}: IP prefiks bilan yozilsin (masalan 10.0.0.1/24)")
        if address.version == 4 and address.network.prefixlen < 31 and address.ip in (address.network.network_address, address.network.broadcast_address):
            errors.append(f"{where}: IPv4 network/broadcast interfeys manzili bo'la olmaydi")
    except ValueError:
        errors.append(f"{where}: noto'g'ri IP '{value}'")


def validate_project(p):
    """Xatolar ro'yxatini qaytaradi (bo'sh — hammasi joyida)."""
    from lab.project_shape import project_for_execution, validate_shape
    from lab.project_values import validate_values
    errors = validate_shape(p)
    if errors:
        return errors
    errors = validate_values(p)
    if errors:
        return errors
    return _validate_project(project_for_execution(p))


def _validate_project(p):
    """Semantic checks run only after container and scalar checks pass."""
    errors = []
    devs = p.get("devices") or {}
    ids = {}
    for name, d in devs.items():
        if not NAME_RE.fullmatch(name):
            errors.append(f"'{name}': nom kichik lotin harf bilan boshlansin, 1-8 belgi [a-z0-9]")
        if d.get("type") not in DEVICE_TYPES:
            errors.append(f"'{name}': noma'lum tur '{d.get('type')}'")
            continue
        did = d.get("id")
        if not isinstance(did, int) or did < 1 or did > 65000:
            errors.append(f"'{name}': id 1..65000 butun son bo'lishi kerak")
        elif did in ids:
            errors.append(f"'{name}': id {did} '{ids[did]}' bilan takrorlangan")
        else:
            ids[did] = name
        cfg = d.get("config") or {}
        for ifn, ic in (cfg.get("interfaces") or {}).items():
            if (ic.get("ip") or "").lower() == "dhcp":
                if d["type"] not in ("host", "server", "router"):
                    errors.append(f"{name}/{ifn}: DHCP mijoz faqat host/server/router'da")
            else:
                _check_ip(ic.get("ip"), f"{name}/{ifn}", errors)
            if ic.get("ip6"):
                _check_ip(ic["ip6"], f"{name}/{ifn} IPv6", errors)
                try:
                    if ipaddress.ip_interface(ic["ip6"]).version != 6:
                        errors.append(f"{name}/{ifn}: ip6 IPv6 bo'lishi kerak")
                except ValueError:
                    pass  # _check_ip already reports malformed addresses
            # Bound interface spellings before int() parses port/VLAN digits.
            if len(ifn) > IFNAMSIZ:
                errors.append(f"{name}/{ifn}: interfeys nomi {IFNAMSIZ} belgidan uzun (Linux cheklovi)")
                continue
            dn, _pt, vl = parse_ifname(ifn)
            if ifn != "lo" and (dn != name or _pt is None or not 1 <= _pt <= 255):
                errors.append(f"{name}/{ifn}: interfeys <qurilma>-eth<port 1..255>[.<vlan>] bo'lsin")
            elif ifn != "lo":
                expected = ifname(name, _pt) + (f".{vl}" if vl is not None else "")
                if ifn != expected:
                    errors.append(f"{name}/{ifn}: interfeys nomi {expected} bo'lsin")
            if vl is not None:
                if dn != name or not 1 <= vl <= 4094:
                    errors.append(f"{name}/{ifn}: subinterfeys nomi <qurilma>-eth<port>.<vlan 1..4094>")
        for pk, pc in (cfg.get("ports") or {}).items():
            if d["type"] not in VLAN_TYPES:
                errors.append(f"{name}: VLAN port sozlamasi faqat switch'da")
                break
            try:
                int(pk)
                if pc.get("mode") == "access":
                    if not 1 <= int(pc.get("vlan")) <= 4094:
                        raise ValueError
                elif pc.get("mode") == "trunk":
                    if any(not 1 <= int(v) <= 4094 for v in pc.get("allowed") or []):
                        raise ValueError
                    if pc.get("native") not in (None, "", 0) and not 1 <= int(pc["native"]) <= 4094:
                        raise ValueError
                elif pc.get("mode") not in (None, "", "none"):
                    raise ValueError
            except (ValueError, TypeError):
                errors.append(f"{name}: port {pk} VLAN sozlamasi noto'g'ri (access vlan / trunk allowed, native 1..4094)")
        gw = cfg.get("gateway")
        if gw:
            try:
                ipaddress.ip_address(gw)
            except ValueError:
                errors.append(f"{name}: noto'g'ri gateway '{gw}'")
        if cfg.get("gateway6"):
            try:
                ipaddress.IPv6Address(cfg["gateway6"])
            except ValueError:
                errors.append(f"{name}: gateway6 IPv6 bo'lishi kerak")
        ctrl = cfg.get("controller")
        if d["type"] == "sdnrouter" or (d["type"] == "switch" and cfg.get("mode") == "sdn"):
            if not ctrl:
                errors.append(f"{name}: SDN rejimi uchun kontroller tanlanmagan")
            elif ctrl not in (p.get("controllers") or {}):
                errors.append(f"{name}: kontroller '{ctrl}' loyihada yo'q")
        if d["type"] == "p4switch":
            prog = cfg.get("program")
            if prog and prog not in (p.get("p4_programs") or {}):
                errors.append(f"{name}: P4 dastur '{prog}' loyihada yo'q")
        rt = cfg.get("rip_timers")
        if rt:
            try:
                u, to, g = (int(rt.get(k, RIP_DEFAULTS[k])) for k in ("update", "timeout", "garbage"))
                if not (1 <= u <= 3600 and u < to <= 7200 and 1 <= g <= 7200):
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                errors.append(f"{name}: rip_timers noto'g'ri (update 1..3600, timeout > update, garbage >= 1)")
        for r in cfg.get("routes") or []:
            try:
                ipaddress.ip_network(r.get("prefix", ""), strict=False)
                ipaddress.ip_address(r.get("via", ""))
                if ipaddress.ip_network(r["prefix"], strict=False).version != ipaddress.ip_address(r["via"]).version:
                    errors.append(f"{name}: prefix va via bir xil IP oilasida bo'lsin")
            except ValueError:
                errors.append(f"{name}: noto'g'ri statik marshrut {r}")

    used = set()
    wifi_sta = {}
    for lid, l in (p.get("links") or {}).items():
        try:
            normalize_link_params(l.get("params"))
        except ValueError as ex:
            errors.append(f"link {lid}: {ex}")
        for side in ("a", "b"):
            e = l.get(side) or {}
            if e.get("dev") not in devs:
                errors.append(f"link {lid}: '{e.get('dev')}' qurilma yo'q")
                continue
            port = e.get("port")
            if not isinstance(port, int) or port < 1 or port > 255:
                errors.append(f"link {lid}: port 1..255 bo'lishi kerak")
                continue
            key = (e["dev"], port)
            if key in used:
                errors.append(f"link {lid}: {e['dev']} port {port} band")
            used.add(key)
        if l.get("a", {}).get("dev") == l.get("b", {}).get("dev"):
            errors.append(f"link {lid}: qurilma o'ziga ulanmaydi")
        if (l.get("params") or {}).get("medium") == "wifi":
            ends = [devs.get((l.get(s_) or {}).get("dev"), {}).get("type") for s_ in ("a", "b")]
            if ends.count("wifiap") != 1 or not any(t in ("host", "server", "router") for t in ends):
                errors.append(f"link {lid}: Wi-Fi kabeli faqat Wi-Fi AP <-> host/server/router orasida")
            else:
                sta = l["a"]["dev"] if ends[1] == "wifiap" else l["b"]["dev"]
                wifi_sta[sta] = wifi_sta.get(sta, 0) + 1
    for sta, n in wifi_sta.items():
        if n > 1:
            errors.append(f"{sta}: bitta qurilma faqat bitta Wi-Fi AP'ga ulanadi")
    ALLOWED_CONTROLLER_TYPES = {"ryu", "os-ken", "pox", "remote", "onos", "odl", "opendaylight", "floodlight"}
    controller_ports = {}
    for cname, c in (p.get("controllers") or {}).items():
        if not NAME_RE.fullmatch(cname):
            errors.append(f"kontroller '{cname}': noto'g'ri nom")
        port = c.get("port")
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            errors.append(f"kontroller '{cname}': port 1024..65535")
        ctype = (c.get("type") or "ryu").lower()
        if ctype not in ALLOWED_CONTROLLER_TYPES:
            errors.append(f"kontroller '{cname}': noma'lum tur '{ctype}' (ryu, pox, remote, onos, odl, floodlight)")
        cip = c.get("ip")
        if cip is not None:
            if not isinstance(cip, str):
                errors.append(f"kontroller '{cname}': IP manzil satr bo'lishi kerak")
            else:
                try:
                    ipaddress.ip_address(cip)
                except ValueError:
                    errors.append(f"kontroller '{cname}': noto'g'ri IP manzil '{cip}'")
        effective_ip = cip or "127.0.0.1"
        addr_key = (effective_ip, port)
        if addr_key in controller_ports:
            errors.append(f"kontroller '{cname}': port {port} '{controller_ports[addr_key]}' bilan takrorlangan")
        else:
            controller_ports[addr_key] = cname
    for pname in (p.get("p4_programs") or {}):
        if not NAME_RE.fullmatch(pname) and not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", pname):
            errors.append(f"P4 dastur '{pname}': noto'g'ri nom")
    if errors:
        return errors
    from lab.services import validate_services   # (aylanma import bo'lmasin)
    errors += validate_services(p)
    from lab.bgp import validate_bgp
    errors += validate_bgp(p)
    from lab.ospf import validate as validate_ospf
    errors += validate_ospf(p)
    from lab.isis import validate as validate_isis
    errors += validate_isis(p)
    from lab.stp import validate as validate_stp
    errors += validate_stp(p)
    from lab.mcast import validate_project as validate_mcast
    errors += validate_mcast(p)
    from lab.vrrp import validate as validate_vrrp
    errors += validate_vrrp(p)
    from lab.lag import validate as validate_lag
    errors += validate_lag(p)
    from lab.survivability import validate_srlg   # loyiha `srlg` (umumiy xavf guruhlari)
    errors += validate_srlg(p)
    return errors


def normalize_link_params(params, *, horizon=3600):
    """Validate link values before applying them; return a fresh defaulted dict."""
    from lab.validation_numbers import MAX_TIMER_S, integer, number
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("link params: object required")
    out = dict(DEFAULT_LINK)
    for k, v in params.items():
        if k in out and v not in (None, ""):
            out[k] = v
    if out["medium"] not in ("wire", "wifi"):
        raise ValueError("medium: wire | wifi")
    if out["qdisc"] not in QDISCS:
        raise ValueError(f"qdisc: {' | '.join(QDISCS)}")
    # ns-3 DataRate is integer bit/s; less than 1 bit/s becomes a zero rate.
    out["bw"] = number(out["bw"], "bw (Mbit/s)", minimum=1e-6, maximum=10000)
    out["loss"] = number(out["loss"], "loss (%)", minimum=0, maximum=100)
    out["queue"] = integer(out["queue"], "queue", minimum=1, maximum=100000)
    for k in ("delay", "jitter"):
        if isinstance(out[k], bool) or not isinstance(out[k], (int, float, str)):
            raise ValueError(f"{k}: duration required (masalan 5ms)")
        try:
            v = str(out[k]).strip()
        except ValueError:
            raise ValueError(f"{k}: finite duration required") from None
        if re.fullmatch(r"\d+(\.\d+)?", v):
            v += "ms"
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(us|ms|s)", v)
        if not match:
            raise ValueError(f"{k}: duration required (masalan 5ms)")
        seconds = number(match[1], k, minimum=0) * {"us": 1e-6, "ms": .001, "s": 1}[match[2]]
        # Leave room for a packet scheduled at the largest scenario horizon.
        number(seconds, k, minimum=0, maximum=MAX_TIMER_S - horizon)
        out[k] = v
    return out


def controller_config(project):
    """SDN kontroller ilovalari o'qiydigan konfiguratsiya (NETLAB_CONFIG):
    har bir SDN qurilma (dpid bo'yicha) — portlari, IP'lari, statik marshrutlari."""
    out = {}
    for cname in (project.get("controllers") or {}):
        devices = {}
        for name, d in project["devices"].items():
            cfg = d.get("config") or {}
            if cfg.get("controller") != cname:
                continue
            if not (d["type"] == "sdnrouter" or (d["type"] == "switch" and cfg.get("mode") == "sdn")):
                continue
            ports = {}
            for port, (_lid, peer, _pp) in sorted(link_ports(project, name).items()):
                ifn = ifname(name, port)
                ports[str(port)] = {
                    "ifname": ifn, "peer": peer,
                    "ip": ((cfg.get("interfaces") or {}).get(ifn) or {}).get("ip", ""),
                }
            devices[str(d["id"])] = {
                "name": name, "type": d["type"], "ports": ports,
                "routes": copy.deepcopy(cfg.get("routes") or []),
            }
        out[cname] = {"controller": cname, "devices": devices}
    return out


APP_TYPES = ("udp", "tcp", "ping")
EVENT_ACTIONS = ("link_down", "link_up", "link_params", "move")


def ospf_convergence_delay(sc):
    """Optional modeled route-install delay; no live OSPF timer is inferred."""
    value = sc.get("ospf_convergence_s", 0)
    if type(value) not in (int, float) or not 0 <= value <= 3600:
        raise ValueError("ospf_convergence_s: number 0..3600 s")
    return float(value)


def validate_scenario(project, sc):
    """Ssenariy xatolari; validation stays independent of engine imports."""
    from lab.scenario_validation import validate_scenario as validate
    return validate(project, sc)


def parse_rate(v):
    """'5Mbps' | '500kbps' | '1.5Gbps' | 5 (Mbit/s) -> finite bit/s.

    Zero is a valid parsed quantity; active applications require a positive rate.
    ns-3 DataRate stores an unsigned 64-bit integer number of bits per second.
    """
    from lab.validation_numbers import MAX_RATE_BPS, number
    message = "tezlik noto'g'ri (masalan 5Mbps; finite 0..uint64 bit/s)"
    try:
        if isinstance(v, bool):
            raise ValueError(message)
        if isinstance(v, (int, float)):
            value = number(v, "rate", minimum=0) * 1e6
        elif isinstance(v, str):
            m = re.fullmatch(r"\s*([\d.]+)\s*([kKmMgG]?)(bps|bit/s|b/s)?\s*", v)
            if not m:
                raise ValueError(message)
            mult = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[m.group(2).lower()]
            if not m.group(2) and not m.group(3):
                mult = 1e6
            value = number(m.group(1), "rate", minimum=0) * mult
        else:
            raise ValueError(message)
        return number(value, "rate", minimum=0, maximum=MAX_RATE_BPS)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(message) from None

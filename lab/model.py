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
}

FRR_DAEMONS = ["ospfd", "bgpd", "ripd", "isisd", "ospf6d", "eigrpd", "pimd", "ldpd", "bfdd"]

DEFAULT_LINK = {"bw": 100, "delay": "1ms", "jitter": "0ms", "loss": 0, "queue": 1000}

PROJECT_VERSION = 1


def empty_project(name="untitled"):
    return {
        "version": PROJECT_VERSION,
        "name": name,
        "devices": {},
        "links": {},
        "p4_programs": {},
        "controllers": {},
        "notes": "",
    }


def ifname(dev, port):
    return f"{dev}-eth{port}"


def device_mac(dev_id, port):
    """Deterministik MAC (namespace'li qurilmalar interfeyslari uchun):
    00:00:00:<id hi>:<id lo>:<port> — P4/SDN jadvallarini qo'lda yozish oson bo'lsin."""
    return "00:00:00:%02x:%02x:%02x" % ((dev_id >> 8) & 0xFF, dev_id & 0xFF, port & 0xFF)


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
        ipaddress.ip_interface(value)
        if "/" not in value:
            errors.append(f"{where}: IP prefiks bilan yozilsin (masalan 10.0.0.1/24)")
    except ValueError:
        errors.append(f"{where}: noto'g'ri IP '{value}'")


def validate_project(p):
    """Xatolar ro'yxatini qaytaradi (bo'sh — hammasi joyida)."""
    errors = []
    devs = p.get("devices") or {}
    ids = {}
    for name, d in devs.items():
        if not NAME_RE.match(name):
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
            _check_ip(ic.get("ip"), f"{name}/{ifn}", errors)
        gw = cfg.get("gateway")
        if gw:
            try:
                ipaddress.ip_address(gw)
            except ValueError:
                errors.append(f"{name}: noto'g'ri gateway '{gw}'")
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
        for r in cfg.get("routes") or []:
            try:
                ipaddress.ip_network(r.get("prefix", ""), strict=False)
                ipaddress.ip_address(r.get("via", ""))
            except ValueError:
                errors.append(f"{name}: noto'g'ri statik marshrut {r}")

    used = set()
    for lid, l in (p.get("links") or {}).items():
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
    for cname, c in (p.get("controllers") or {}).items():
        if not NAME_RE.match(cname):
            errors.append(f"kontroller '{cname}': noto'g'ri nom")
        port = c.get("port")
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            errors.append(f"kontroller '{cname}': port 1024..65535")
    for pname in (p.get("p4_programs") or {}):
        if not NAME_RE.match(pname) and not re.match(r"^[a-z][a-z0-9_]{0,23}$", pname):
            errors.append(f"P4 dastur '{pname}': noto'g'ri nom")
    return errors


def normalize_link_params(params):
    out = dict(DEFAULT_LINK)
    for k, v in (params or {}).items():
        if k in out and v not in (None, ""):
            out[k] = v
    out["bw"] = float(out["bw"])
    out["loss"] = float(out["loss"])
    out["queue"] = int(out["queue"])
    for k in ("delay", "jitter"):
        v = str(out[k]).strip()
        if re.fullmatch(r"\d+(\.\d+)?", v):
            v += "ms"
        if not re.fullmatch(r"\d+(\.\d+)?(us|ms|s)", v):
            raise ValueError(f"{k}: '{v}' noto'g'ri (masalan 5ms)")
        out[k] = v
    if not (0 < out["bw"] <= 10000):
        raise ValueError("bw 0..10000 Mbit/s")
    if not (0 <= out["loss"] <= 100):
        raise ValueError("loss 0..100 %")
    if not (1 <= out["queue"] <= 100000):
        raise ValueError("queue 1..100000 paket")
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

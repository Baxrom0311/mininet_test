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

FRR_DAEMONS = ["ospfd", "bgpd", "ripd", "isisd", "ospf6d", "eigrpd", "pimd", "ldpd", "bfdd"]

DEFAULT_LINK = {"bw": 100, "delay": "1ms", "jitter": "0ms", "loss": 0, "queue": 1000, "medium": "wire"}

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
    wifi_sta = {}
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
    if out["medium"] not in ("wire", "wifi"):
        raise ValueError("medium: wire | wifi")
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


APP_TYPES = ("udp", "tcp", "ping")
EVENT_ACTIONS = ("link_down", "link_up", "link_params")


def validate_scenario(project, sc):
    """Ssenariy (trafik ilovalari + vaqtli hodisalar) xatolari ro'yxati."""
    errors = []
    try:
        dur = float(sc.get("duration", 0))
    except (TypeError, ValueError):
        dur = 0
    if not (1 <= dur <= 3600):
        errors.append("davomiylik 1..3600 s")
    devs = project.get("devices") or {}
    ids = set()
    for i, a in enumerate(sc.get("apps") or []):
        w = f"ilova {a.get('id') or i + 1}"
        if a.get("id") in ids:
            errors.append(f"{w}: id takrorlangan")
        ids.add(a.get("id"))
        if a.get("type") not in APP_TYPES:
            errors.append(f"{w}: tur {APP_TYPES} dan biri")
        for k in ("src", "dst"):
            d = devs.get(a.get(k))
            if not d:
                errors.append(f"{w}: '{a.get(k)}' qurilma yo'q")
            elif not DEVICE_TYPES[d["type"]]["l3"] or d["type"] == "sdnrouter":
                errors.append(f"{w}: {a.get(k)} trafik manbai/qabul qiluvchisi bo'la olmaydi (host/server/router)")
        try:
            st, sp = float(a.get("start", 0)), float(a.get("stop", dur))
            if not (0 <= st < sp):
                errors.append(f"{w}: start < stop bo'lsin")
        except (TypeError, ValueError):
            errors.append(f"{w}: start/stop son bo'lsin")
        if a.get("type") == "udp" and not a.get("rate"):
            errors.append(f"{w}: UDP uchun rate kerak (masalan 5Mbps)")
        if a.get("rate"):
            try:
                parse_rate(a["rate"])
            except ValueError as e:
                errors.append(f"{w}: {e}")
    for i, e in enumerate(sc.get("events") or []):
        w = f"hodisa {i + 1}"
        if e.get("action") not in EVENT_ACTIONS:
            errors.append(f"{w}: amal {EVENT_ACTIONS} dan biri")
        if e.get("link") not in (project.get("links") or {}):
            errors.append(f"{w}: kabel '{e.get('link')}' yo'q")
        try:
            if not (0 <= float(e.get("t", -1)) <= dur):
                errors.append(f"{w}: vaqt 0..davomiylik")
        except (TypeError, ValueError):
            errors.append(f"{w}: vaqt son bo'lsin")
        if e.get("action") == "link_params":
            try:
                normalize_link_params(e.get("params") or {})
            except ValueError as ex:
                errors.append(f"{w}: {ex}")
    return errors


def parse_rate(v):
    """'5Mbps' | '500kbps' | '1.5Gbps' | 5 (Mbit/s) -> bit/s (float)."""
    if isinstance(v, (int, float)):
        return float(v) * 1e6
    m = re.fullmatch(r"\s*([\d.]+)\s*([kKmMgG]?)(bps|bit/s|b/s)?\s*", str(v))
    if not m:
        raise ValueError(f"tezlik '{v}' noto'g'ri (masalan 5Mbps)")
    mult = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[m.group(2).lower()]
    if not m.group(2) and not m.group(3):
        mult = 1e6
    return float(m.group(1)) * mult

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
            if (ic.get("ip") or "").lower() == "dhcp":
                if d["type"] not in ("host", "server", "router"):
                    errors.append(f"{name}/{ifn}: DHCP mijoz faqat host/server/router'da")
            else:
                _check_ip(ic.get("ip"), f"{name}/{ifn}", errors)
            dn, _pt, vl = parse_ifname(ifn)
            if vl is not None:
                if dn != name or not 1 <= vl <= 4094:
                    errors.append(f"{name}/{ifn}: subinterfeys nomi <qurilma>-eth<port>.<vlan 1..4094>")
                elif len(ifn) > IFNAMSIZ:
                    errors.append(f"{name}/{ifn}: interfeys nomi {IFNAMSIZ} belgidan uzun (Linux cheklovi)")
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
    from lab.services import validate_services   # (aylanma import bo'lmasin)
    errors += validate_services(p)
    from lab.bgp import validate_bgp
    errors += validate_bgp(p)
    from lab.stp import validate as validate_stp
    errors += validate_stp(p)
    return errors


def normalize_link_params(params):
    out = dict(DEFAULT_LINK)
    for k, v in (params or {}).items():
        if k in out and v not in (None, ""):
            out[k] = v
    if out["medium"] not in ("wire", "wifi"):
        raise ValueError("medium: wire | wifi")
    if out["qdisc"] not in QDISCS:
        raise ValueError(f"qdisc: {' | '.join(QDISCS)}")
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
EVENT_ACTIONS = ("link_down", "link_up", "link_params", "move")


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
        from lab.apps import MODEL_TYPES, validate_app
        if a.get("type") not in APP_TYPES + MODEL_TYPES:
            errors.append(f"{w}: tur {APP_TYPES + MODEL_TYPES} dan biri")
        elif a.get("type") in MODEL_TYPES:
            errors += [f"{w}: {e}" for e in validate_app(a, dur)]
        if a.get("dscp") not in (None, ""):
            try:
                if not 0 <= int(a["dscp"]) <= 63:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{w}: dscp 0..63 (EF=46)")
        if a.get("dport") not in (None, ""):
            try:
                if not 1 <= int(a["dport"]) <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{w}: dport 1..65535")
        if a.get("bytes") is not None:
            try:
                if int(a["bytes"]) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{w}: bytes musbat butun son")
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
        if e.get("action") == "move":
            if e.get("device") not in devs:
                errors.append(f"{w}: qurilma '{e.get('device')}' yo'q")
            try:
                float(e.get("x")), float(e.get("y"))
                if float(e.get("duration", 0) or 0) < 0:
                    errors.append(f"{w}: davomiylik >= 0")
            except (TypeError, ValueError):
                errors.append(f"{w}: x, y son bo'lsin")
        elif e.get("link") not in (project.get("links") or {}):
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

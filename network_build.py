"""Mininet tarmog'ini qurish, QoS/DiffServ navbatlarini o'rnatish, NAT gateway/monitor,
va real FRRouting (zebra, ospfd, bgpd, isisd) hamda Linux FIB route verifikatsiyasi.
Barcha Mininet importlari funksiya ichida (lazy) — shu tufayli Mininet o'rnatilmagan
muhitda ham modulni import qilish mumkin."""

import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time

from config import CONTROLLER_PORT, DATA_DIR
from netutil import locked_cmd

FRR_BIN = os.environ.get("NETLAB_FRR_BIN", "/usr/lib/frr")


def _get_frr_router_cls():
    """Mininet Host subclass for FRR routing with IP forwarding enabled."""
    try:
        from mininet.node import Host

        class FRRRouter(Host):
            """Linux namespace router with IP forwarding enabled and FRR support."""

            def config(self, **params):
                r = super().config(**params)
                self.cmd("sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1 "
                         "net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.default.rp_filter=0 2>/dev/null")
                return r

        return FRRRouter
    except (ImportError, Exception):
        class FRRRouter:
            """Fallback / standalone FRRRouter placeholder when Mininet is not installed."""

            def __init__(self, name, **kwargs):
                self.name = name
                self.params = kwargs

            def config(self, **params):
                pass

            def cmd(self, *args, **kwargs):
                return ""

        return FRRRouter


FRRRouter = _get_frr_router_cls()


def _parse_ms(val):
    """'3ms' yoki 3 -> 3.0 (float, ms)."""
    if isinstance(val, str):
        return float(val.replace("ms", "").strip() or 0)
    return float(val or 0)


# QoS band -> (htb classid, netem handle, bandwidth ulushi)
QOS_BANDS = [
    (0x10, 0x10, 0.5),   # EF  — real-vaqt (voip/video/gaming)
    (0x20, 0x20, 0.3),   # AF21 — interaktiv (web/https/dns/ssh)
    (0x30, 0x30, 0.2),   # BE/CS1 — fon/bulk trafik
]
QOS_BAND_NAMES = ("EF", "AF21", "BE")  # QOS_BANDS bilan bir tartibda
QOS_TOS_FILTERS = [(0xB8, 0x10), (0x48, 0x20), (0x20, 0x30)]  # (tos, classid)

# Har (ifname, band_nomi) uchun _setup_qos_qdisc o'rnatgan ASL netem argument
# satri. Impairments moduli hodisa tugagach band'ni aynan shu bazaga
# qaytaradi.
_BASE_NETEM = {}


def get_base_netem(ifname, band_name):
    """`_setup_qos_qdisc` saqlagan asl netem argument satrini qaytaradi
    (masalan "delay 5.0ms 1.0ms loss 0.5% limit 100"). Noma'lum bo'lsa None."""
    return _BASE_NETEM.get((ifname, band_name))


def _setup_qos_qdisc(intf, params):
    """TCLink'ning yassi netem qdisc'ini 3-bandli DiffServ navbatiga
    almashtiradi: EF (real-vaqt) / AF21 (interaktiv) / BE (fon), DSCP
    (paket TOS bayti) bo'yicha yo'naltiriladi. Har band ichida asl
    delay/loss/jitter/queue saqlanadi — faqat ustuvorlik farqlanadi."""
    node, ifname = intf.node, intf.name
    bw = max(float(params.get("bw", 10)), 1)
    delay = _parse_ms(params.get("delay", "0ms"))
    jitter = _parse_ms(params.get("jitter", "0ms"))
    loss = params.get("loss", 0)
    queue = params.get("queue", 100)

    cmds = [f"tc qdisc del dev {ifname} root 2>/dev/null",
            f"tc qdisc add dev {ifname} root handle 1: htb default 30"]
    base_netem = f"delay {delay}ms {jitter}ms loss {loss}% limit {queue}"
    for (classid, handle, share), band_name in zip(QOS_BANDS, QOS_BAND_NAMES):
        rate = max(bw * share, 0.1)
        cmds.append(f"tc class add dev {ifname} parent 1: classid 1:{classid:x} "
                    f"htb rate {rate:.2f}mbit ceil {bw:.2f}mbit prio {handle // 0x10 - 1}")
        cmds.append(f"tc qdisc add dev {ifname} parent 1:{classid:x} handle {handle:x}: "
                    f"netem {base_netem}")
        _BASE_NETEM[(ifname, band_name)] = base_netem
    for prio, (tos, classid) in enumerate(QOS_TOS_FILTERS, start=1):
        cmds.append(f"tc filter add dev {ifname} parent 1: protocol ip prio {prio} "
                    f"u32 match ip tos {tos} 0xfc flowid 1:{classid:x}")
    node.cmd(" ; ".join(cmds))


def warn_external_controller_telemetry(controller_type):
    """Tashqi kontroller tanlanganda telemetriya cheklovlari va statistika haqida ogohlantirish beradi."""
    c_type = (controller_type or "").lower()
    if c_type in ("remote", "onos", "odl", "opendaylight", "floodlight"):
        label = c_type.upper() if c_type != "odl" else "OpenDaylight"
        print(f"\n[Telemetry] OGOHLANTIRISH: Tashqi '{label}' kontrolleri tanlangan.")
        print("  - Ichki Ryu transport_monitor agenti ishga tushmaydi.")
        print("  - OpenFlow oqim statistikasi (flow_stats) va transport hodisalari (transport_events)")
        print(f"    ichki loglarga yozilmaydi (ular bevosita {label} kontroller orqali boshqariladi).")
        print("  - Mahalliy telemetriya (tcpdump PCAP, RTT o'lchovlari, NAT tarjimalari, path traces)")
        print("    xatosiz yig'ilishda davom etadi.\n")


def build_topology(topo, controller_type="ryu", controller_ip="127.0.0.1", controller_port=None, of_protocol=None):
    """Mininet topologiya yaratish (switches, hosts, FRR routers, QoS va links)."""
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController
    from mininet.link import TCLink
    from mininet.log import setLogLevel
    setLogLevel("info")

    c_type = (controller_type or "ryu").lower()
    if controller_port is None:
        controller_port = CONTROLLER_PORT if c_type in ("ryu", "os-ken", "pox") else 6653

    if of_protocol is None:
        of_protocol = "OpenFlow10" if c_type == "pox" else "OpenFlow13"

    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True, autoStaticArp=True)
    net.addController("c0", controller=RemoteController,
                      ip=controller_ip, port=controller_port, protocols=of_protocol)

    net.controller_type = c_type
    net.controller_ip = controller_ip
    net.controller_port = controller_port
    net.is_external_controller = c_type in ("remote", "onos", "odl", "opendaylight", "floodlight")

    if net.is_external_controller:
        warn_external_controller_telemetry(c_type)

    switches = {}
    for sw_name in topo.get("switches", {}):
        dpid = f"{int(sw_name[1:]):016x}"
        switches[sw_name] = net.addSwitch(sw_name, dpid=dpid, protocols=of_protocol)

    hosts = {}
    for h_name, h_info in topo.get("hosts", {}).items():
        hosts[h_name] = net.addHost(h_name, ip=h_info["ip"])
        al = topo.get("access_links", {}).get(h_name, {"bw": 10, "delay": "1ms", "loss": 0})
        if h_info.get("switch") in switches:
            net.addLink(hosts[h_name], switches[h_info["switch"]],
                        cls=TCLink, bw=al["bw"], delay=al["delay"], loss=al["loss"])

    net._qos_links = []  # (intf1, intf2, params) — net.start() dan keyin QoS qdisc o'rnatish uchun
    for (s1, s2), params in topo.get("links", {}).items():
        link = net.addLink(switches[s1], switches[s2], cls=TCLink,
                    bw=params["bw"], delay=params["delay"],
                    loss=params["loss"], max_queue_size=params["queue"])
        net._qos_links.append((link.intf1, link.intf2, params))

    # FRR Routers (Faza 5)
    routers = {}
    for r_name, r_info in topo.get("routers", {}).items():
        routers[r_name] = net.addHost(r_name, cls=FRRRouter, ip=r_info.get("ip"))
        sw = r_info.get("switch")
        if sw and sw in switches:
            ral = r_info.get("link", {"bw": 100, "delay": "1ms", "loss": 0})
            link = net.addLink(routers[r_name], switches[sw],
                               cls=TCLink, bw=ral.get("bw", 100), delay=ral.get("delay", "1ms"),
                               loss=ral.get("loss", 0))
            net._qos_links.append((link.intf1, link.intf2, ral))

    for (n1, n2), params in topo.get("router_links", {}).items():
        node1 = net.get(n1)
        node2 = net.get(n2)
        link = net.addLink(node1, node2, cls=TCLink,
                           bw=params.get("bw", 100), delay=params.get("delay", "1ms"),
                           loss=params.get("loss", 0), max_queue_size=params.get("queue", 100))
        net._qos_links.append((link.intf1, link.intf2, params))

    return net


def apply_qos_qdiscs(net):
    """net.start() dan KEYIN chaqirilishi shart — TCLink o'z qdisc'ini
    o'rnatib bo'lgach, uni 3-bandli DiffServ navbatiga almashtiradi."""
    for intf1, intf2, params in getattr(net, "_qos_links", []):
        _setup_qos_qdisc(intf1, params)
        _setup_qos_qdisc(intf2, params)


# ─────────────────────────── FRRouting (Faza 5) ───────────────────────────

def generate_frr_conf(router_name, config=None):
    """Router uchun to'liq, sintaktik to'g'ri frr.conf matnini generatsiya qiladi.

    Qo'llab-quvvatlanadigan bo'limlar:
      - Hostname, logging, ip forwarding
      - Interfeyslar (IP, OSPF p2p/hello/dead/cost, IS-IS tag/metric/p2p)
      - OSPF (router ospf, ospf router-id, networks, redistribute, passive-interfaces)
      - BGP (router bgp, bgp router-id, neighbors, address-family ipv4 unicast)
      - IS-IS (router isis, net, is-type, metric-style wide)
      - Static routes (ip route)
      - Custom startup komandalar
    """
    cfg = config or {}
    lines = [
        f"! FRRouting configuration for {router_name}",
        "frr version 8.4",
        "frr defaults traditional",
        f"hostname {router_name}",
        "log syslog informational",
        "service integrated-vtysh-config",
        "!",
        "ip forwarding",
        "ipv6 forwarding",
        "!",
    ]

    # Interfeyslar
    interfaces = cfg.get("interfaces") or {}
    for ifn, ic in sorted(interfaces.items()):
        if_lines = [f"interface {ifn}"]
        ip_val = (ic or {}).get("ip")
        if ip_val and str(ip_val).lower() != "dhcp":
            if_lines.append(f" ip address {ip_val}")

        # OSPF interfeys parametrlari
        if ic.get("ospf_p2p") or ic.get("ospf_network") == "point-to-point":
            if_lines.append(" ip ospf network point-to-point")
        elif ic.get("ospf_network"):
            if_lines.append(f" ip ospf network {ic['ospf_network']}")
        if ic.get("ospf_hello") or ic.get("ospf_hello_interval"):
            h = ic.get("ospf_hello") or ic.get("ospf_hello_interval")
            if_lines.append(f" ip ospf hello-interval {h}")
        if ic.get("ospf_dead") or ic.get("ospf_dead_interval"):
            d = ic.get("ospf_dead") or ic.get("ospf_dead_interval")
            if_lines.append(f" ip ospf dead-interval {d}")
        if ic.get("ospf_cost"):
            if_lines.append(f" ip ospf cost {ic['ospf_cost']}")
        if ic.get("ospf_priority") is not None:
            if_lines.append(f" ip ospf priority {ic['ospf_priority']}")
        if ic.get("ospf_area") is not None:
            if_lines.append(f" ip ospf area {ic['ospf_area']}")

        # IS-IS interfeys parametrlari
        isis_tag = ic.get("isis_tag") or (cfg.get("isis") or {}).get("tag")
        if isis_tag:
            if_lines.append(f" ip router isis {isis_tag}")
            if ic.get("isis_p2p", True):
                if_lines.append(" isis network point-to-point")
            if ic.get("isis_metric"):
                if_lines.append(f" isis metric {ic['isis_metric']}")
            if ic.get("isis_hello"):
                if_lines.append(f" isis hello-interval {ic['isis_hello']}")
            if ic.get("isis_passive"):
                if_lines.append(" isis passive")

        if len(if_lines) > 1:
            if_lines.append("!")
            lines += if_lines

    # Statik marshrutlar
    for r in cfg.get("routes") or []:
        lines.append(f"ip route {r['prefix']} {r['via']}")
    if cfg.get("routes"):
        lines.append("!")

    # OSPF jarayoni
    ospf_cfg = cfg.get("ospf")
    daemons = cfg.get("daemons") or []
    if ospf_cfg or "ospfd" in daemons:
        o = ospf_cfg or {}
        rid = o.get("router_id") or cfg.get("router_id")
        lines.append("router ospf")
        if rid:
            lines.append(f" ospf router-id {rid}")
        for red in o.get("redistribute") or []:
            lines.append(f" redistribute {red}")
        if o.get("passive_default"):
            lines.append(" passive-interface default")
        for pif in o.get("passive_interfaces") or []:
            lines.append(f" passive-interface {pif}")

        nets = o.get("networks")
        if nets:
            for n in nets:
                if isinstance(n, dict):
                    lines.append(f" network {n.get('network') or n.get('prefix')} area {n.get('area', 0)}")
                elif isinstance(n, (list, tuple)) and len(n) >= 2:
                    lines.append(f" network {n[0]} area {n[1]}")
                elif isinstance(n, str):
                    if "area" in n:
                        lines.append(f" network {n}")
                    else:
                        lines.append(f" network {n} area 0")
        else:
            # Interfeyslardan avtomatik aniqlash
            for ifn, ic in sorted(interfaces.items()):
                ip_val = (ic or {}).get("ip")
                if ip_val and str(ip_val).lower() != "dhcp":
                    try:
                        net_obj = ipaddress.ip_interface(ip_val).network
                        lines.append(f" network {net_obj} area 0")
                    except ValueError:
                        pass
        lines.append("!")

    # BGP jarayoni
    bgp_cfg = cfg.get("bgp")
    if bgp_cfg or "bgpd" in daemons:
        b = bgp_cfg or {}
        asn = b.get("asn", 65000)
        lines.append(f"router bgp {int(asn)}")
        rid = b.get("router_id") or cfg.get("router_id")
        if rid:
            lines.append(f" bgp router-id {rid}")
        lines.append(" no bgp ebgp-requires-policy")
        lines.append(" no bgp default ipv4-unicast")
        for nb in b.get("neighbors") or []:
            nb_ip = nb.get("ip")
            remote_as = nb.get("remote_as") or asn
            lines.append(f" neighbor {nb_ip} remote-as {int(remote_as)}")
            lines.append(f" neighbor {nb_ip} timers connect 5")
        lines.append(" address-family ipv4 unicast")
        for n in b.get("networks") or []:
            lines.append(f"  network {n}")
        for red in b.get("redistribute") or []:
            lines.append(f"  redistribute {red}")
        for nb in b.get("neighbors") or []:
            nb_ip = nb.get("ip")
            lines.append(f"  neighbor {nb_ip} activate")
            if nb.get("next_hop_self"):
                lines.append(f"  neighbor {nb_ip} next-hop-self")
        lines.append(" exit-address-family")
        lines.append("!")

    # IS-IS jarayoni
    isis_cfg = cfg.get("isis")
    if isis_cfg or "isisd" in daemons:
        isc = isis_cfg or {}
        tag = isc.get("tag", "CORE")
        net_addr = isc.get("net", "49.0001.0000.0000.0001.00")
        level = isc.get("level", "level-2-only")
        lines.append(f"router isis {tag}")
        lines.append(f" net {net_addr}")
        lines.append(f" is-type {level}")
        lines.append(f" metric-style {isc.get('metric_style', 'wide')}")
        lines.append(f" lsp-gen-interval {isc.get('lsp_gen_interval', 1)}")
        lines.append(f" spf-interval {isc.get('spf_interval', 1)}")
        lines.append("!")

    # RIP jarayoni
    rip_cfg = cfg.get("rip")
    if (rip_cfg or "ripd" in daemons) and "router rip" not in (cfg.get("startup") or ""):
        from lab.model import rip_timers
        lines.append("router rip")
        lines.append(" version 2")
        lines.append(" no validate-source")
        if cfg.get("rip_timers"):
            tm = rip_timers({"config": cfg})
            lines.append(f" timers basic {tm['update']} {tm['timeout']} {tm['garbage']}")
        nets = (rip_cfg or {}).get("networks")
        if nets:
            for n in nets:
                lines.append(f" network {n}")
        else:
            for ifn, ic in sorted(interfaces.items()):
                ip_val = (ic or {}).get("ip")
                if ip_val and str(ip_val).lower() != "dhcp":
                    try:
                        net_obj = ipaddress.ip_interface(ip_val).network
                        if net_obj.version == 4:
                            lines.append(f" network {net_obj}")
                    except ValueError:
                        pass
        for red in (rip_cfg or {}).get("redistribute") or []:
            lines.append(f" redistribute {red}")
        lines.append("!")

    # Custom startup
    startup = (cfg.get("startup") or "").strip()
    if startup:
        lines.append("! Startup configuration")
        lines.append(startup)
        lines.append("!")

    lines.append("line vty")
    lines.append("!")
    lines.append("end")
    lines.append("")
    return "\n".join(lines)


def start_frr_router(node, config=None, run_dir="/var/run/frr", etc_dir="/etc/frr"):
    """Mininet namespace node ichida FRR demonlarini (zebra, ospfd, bgpd, isisd) ishga tushiradi."""
    cfg = config or {}
    name = getattr(node, "name", str(node))
    r_etc = os.path.join(etc_dir, name)
    r_run = os.path.join(run_dir, name)
    os.makedirs(r_etc, exist_ok=True)
    os.makedirs(r_run, exist_ok=True)

    # 1. Linux IP forwarding yoqish
    node.cmd("sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1 "
             "net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.default.rp_filter=0 2>/dev/null")

    # 2. Daemons ro'yxatini aniqlash
    daemons = ["zebra", "staticd"]
    for dm in cfg.get("daemons") or []:
        if dm in ("ospfd", "bgpd", "isisd", "ripd", "pimd", "bfdd", "vrrpd", "ospf6d") and dm not in daemons:
            daemons.append(dm)
    if cfg.get("ospf") and "ospfd" not in daemons:
        daemons.append("ospfd")
    if cfg.get("bgp") and "bgpd" not in daemons:
        daemons.append("bgpd")
    if cfg.get("isis") and "isisd" not in daemons:
        daemons.append("isisd")

    frr_bin = os.environ.get("NETLAB_FRR_BIN", "/usr/lib/frr")
    if os.path.exists(os.path.join(frr_bin, "mgmtd")):
        daemons.insert(0, "mgmtd")

    # 3. frr.conf va vtysh.conf fayllarini generatsiya qilish
    frr_content = generate_frr_conf(name, {**cfg, "daemons": daemons})
    with open(os.path.join(r_etc, "frr.conf"), "w") as f:
        f.write(frr_content)
    with open(os.path.join(r_etc, "vtysh.conf"), "w") as f:
        f.write("service integrated-vtysh-config\n! NetLab\n")

    # Har bir daemon uchun minimal .conf
    for dm in daemons:
        with open(os.path.join(r_etc, f"{dm}.conf"), "w") as f:
            f.write(f"hostname {name}\n")

    # daemons fayli
    try:
        with open(os.path.join(r_etc, "daemons"), "w") as f:
            for dm_name in ("zebra", "bgpd", "ospfd", "ospf6d", "ripd", "ripngd", "isisd", "pimd", "ldpd", "bfdd", "staticd"):
                f.write(f"{dm_name}={'yes' if dm_name in daemons else 'no'}\n")
    except OSError:
        pass

    # Fayl huquqlari
    try:
        for p in (r_etc, r_run, *[os.path.join(r_etc, f) for f in os.listdir(r_etc)]):
            shutil.chown(p, "frr", "frr")
    except (LookupError, OSError):
        pass

    # 4. Demonlarni ishga tushirish
    started = []
    for dm in daemons:
        exe = os.path.join(frr_bin, dm)
        if not os.path.exists(exe):
            continue
        pid_file = f"{r_run}/{dm}.pid"
        node.cmd(f"{exe} -d -N {name} -F traditional -A 127.0.0.1 -i {pid_file} 2>&1")
        started.append(dm)
        if dm == "zebra":
            # zserv.api kutish
            for _ in range(30):
                if os.path.exists(f"{r_run}/zserv.api"):
                    break
                time.sleep(0.1)

    # 5. Konfiguratsiyani yuklash
    time.sleep(0.2)
    conf_file = os.path.join(r_etc, "frr.conf")
    load_out = node.cmd(f"vtysh -N {name} -f {conf_file} 2>&1")

    return {
        "router": name,
        "daemons": started,
        "etc": r_etc,
        "run": r_run,
        "load_output": load_out,
    }


def stop_frr_router(node_or_name, run_dir="/var/run/frr"):
    """Router'ning ishlayotgan barcha FRR demonlarini to'xtatadi."""
    name = getattr(node_or_name, "name", str(node_or_name))
    r_run = os.path.join(run_dir, name)
    stopped_pids = []
    if os.path.isdir(r_run):
        for f in os.listdir(r_run):
            if f.endswith(".pid"):
                try:
                    with open(os.path.join(r_run, f)) as fh:
                        pid = int(fh.read().strip())
                        os.kill(pid, signal.SIGTERM)
                        stopped_pids.append(pid)
                except (OSError, ValueError):
                    pass
    if hasattr(node_or_name, "cmd"):
        node_or_name.cmd("killall -q zebra ospfd bgpd isisd staticd mgmtd 2>/dev/null")
    return {"router": name, "stopped_pids": stopped_pids}


def setup_frr_routers(net, topo, etc_dir="/etc/frr", run_dir="/var/run/frr"):
    """net.start() dan keyin chaqiriladi — barcha routerlarda FRR demonlarini ishga tushiradi."""
    if not topo_has_frr(topo):
        return {}
    results = {}
    for r_name, r_cfg in topo.get("routers", {}).items():
        node = net.get(r_name)
        res = start_frr_router(node, r_cfg, run_dir=run_dir, etc_dir=etc_dir)
        results[r_name] = res
        print(f"[FRR] {r_name} demonlari ishga tushdi: {', '.join(res.get('daemons', []))}")
    return results


def topo_has_frr(topo):
    """Topologiyada FRR routerlar mavjudligini tekshiradi."""
    return bool(topo.get("routers"))


# ────────────────────── Linux FIB Marshrut Verifikatsiyasi ──────────────────────

def get_linux_fib_routes(node):
    """Linux kernel FIB (Forwarding Information Base) routing jadvalini oladi.

    Qaytaradi:
      list of dict:
        [{"prefix": "10.0.1.0/24", "gateway": "10.0.12.2", "dev": "r1-eth2",
          "proto": "ospf", "metric": 20, "scope": "global"}, ...]
    """
    if not node:
        return []
    # 1. `ip -j route show` (zamonaviy JSON chiqish)
    out = node.cmd("ip -j route show 2>/dev/null")
    if out and out.strip().startswith("["):
        try:
            data = json.loads(out)
            routes = []
            for r in data:
                routes.append({
                    "prefix": r.get("dst"),
                    "gateway": r.get("gateway"),
                    "dev": r.get("dev"),
                    "proto": r.get("protocol") or r.get("proto"),
                    "metric": r.get("metric"),
                    "scope": r.get("scope"),
                    "prefsrc": r.get("prefsrc"),
                    "flags": r.get("flags", []),
                })
            return routes
        except (ValueError, KeyError):
            pass

    # 2. Matnli `ip route show` ni tahlil qilish
    text_out = node.cmd("ip route show 2>/dev/null")
    if text_out:
        parsed = []
        for line in text_out.splitlines():
            line = line.strip()
            if not line:
                continue
            tokens = line.split()
            prefix = tokens[0]
            gw = None
            dev = None
            proto = None
            metric = None
            scope = None
            i = 1
            while i < len(tokens):
                tok = tokens[i]
                if tok == "via" and i + 1 < len(tokens):
                    gw = tokens[i + 1]
                    i += 2
                elif tok == "dev" and i + 1 < len(tokens):
                    dev = tokens[i + 1]
                    i += 2
                elif tok == "proto" and i + 1 < len(tokens):
                    proto = tokens[i + 1]
                    i += 2
                elif tok == "metric" and i + 1 < len(tokens):
                    try:
                        metric = int(tokens[i + 1])
                    except ValueError:
                        metric = tokens[i + 1]
                    i += 2
                elif tok == "scope" and i + 1 < len(tokens):
                    scope = tokens[i + 1]
                    i += 2
                else:
                    i += 1
            parsed.append({
                "prefix": prefix,
                "gateway": gw,
                "dev": dev,
                "proto": proto,
                "metric": metric,
                "scope": scope,
            })
        if parsed:
            return parsed

    # 3. /proc/<pid>/net/route fallback (IPv4 hex format)
    pid = getattr(node, "pid", None)
    if pid:
        try:
            with open(f"/proc/{pid}/net/route") as f:
                lines = f.readlines()[1:]
            routes = []
            for line in lines:
                parts = line.split()
                if len(parts) < 8:
                    continue
                dev, d_hex, g_hex, flags_hex, mask_hex = parts[0], parts[1], parts[2], int(parts[3], 16), parts[7]
                if not (flags_hex & 1):  # RTF_UP
                    continue
                h2ip = lambda h: socket.inet_ntoa(struct.pack("<I", int(h, 16)))
                dst_ip = h2ip(d_hex)
                mask_ip = h2ip(mask_hex)
                gw_ip = h2ip(g_hex) if (flags_hex & 2) else None
                try:
                    net_str = str(ipaddress.ip_network(f"{dst_ip}/{mask_ip}", strict=False))
                except ValueError:
                    net_str = dst_ip
                routes.append({
                    "prefix": "default" if net_str == "0.0.0.0/0" else net_str,
                    "gateway": gw_ip,
                    "dev": dev,
                    "proto": "kernel" if not gw_ip else "dynamic",
                    "metric": int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else None,
                    "scope": None,
                })
            return routes
        except OSError:
            pass

    return []


def verify_fib_route(node, prefix, expected_via=None, expected_dev=None, expected_proto=None):
    """Linux kernel FIB da ko'rsatilgan marshrut mavjudligini tekshiradi."""
    routes = get_linux_fib_routes(node)
    norm_pfx = prefix.strip()
    for r in routes:
        pfx = (r.get("prefix") or "").strip()
        match = (pfx == norm_pfx) or (norm_pfx in ("default", "0.0.0.0/0") and pfx in ("default", "0.0.0.0/0"))
        if match:
            if expected_via is not None and r.get("gateway") != expected_via:
                continue
            if expected_dev is not None and r.get("dev") != expected_dev:
                continue
            if expected_proto is not None and r.get("proto") != expected_proto:
                continue
            return True, r, routes
    return False, None, routes


def verify_all_fib_routes(net, expected_routes_map):
    """Bir nechta routerlar uchun FIB marshrutlarini tekshiradi.

    expected_routes_map:
      {
        "r1": ["10.0.2.0/24", "10.0.3.0/24"],
        "r2": {"10.0.1.0/24": {"via": "10.0.12.1", "proto": "ospf"}}
      }
    """
    details = {}
    all_passed = True
    for node_name, expected in expected_routes_map.items():
        node = net.get(node_name)
        if not node:
            details[node_name] = {"passed": False, "error": "Node topilmadi", "missing": []}
            all_passed = False
            continue
        missing = []
        node_passed = True
        actual_routes = get_linux_fib_routes(node)

        if isinstance(expected, list):
            for pfx in expected:
                ok, _, _ = verify_fib_route(node, pfx)
                if not ok:
                    missing.append(pfx)
                    node_passed = False
        elif isinstance(expected, dict):
            for pfx, crit in expected.items():
                via = crit.get("via") if isinstance(crit, dict) else None
                dev = crit.get("dev") if isinstance(crit, dict) else None
                proto = crit.get("proto") if isinstance(crit, dict) else None
                ok, _, _ = verify_fib_route(node, pfx, expected_via=via, expected_dev=dev, expected_proto=proto)
                if not ok:
                    missing.append({"prefix": pfx, "criteria": crit})
                    node_passed = False

        details[node_name] = {
            "passed": node_passed,
            "missing": missing,
            "routes_count": len(actual_routes),
            "routes": actual_routes
        }
        if not node_passed:
            all_passed = False

    return {"all_passed": all_passed, "details": details}


# ────────────────────── Dinamik Konvergensiya O'lchash ──────────────────────

def get_frr_neighbors(node, proto="ospf"):
    """Router'ning dinamik marshrutlash protokoli (OSPF, BGP, IS-IS) qo'shnilarini vtysh orqali oladi."""
    if not node:
        return []
    p = (proto or "ospf").lower()
    name = getattr(node, "name", str(node))

    if p in ("ospf", "ospfd"):
        out = node.cmd(f"vtysh -N {name} -c 'show ip ospf neighbor json' 2>/dev/null")
        if out and out.strip().startswith("{"):
            try:
                data = json.loads(out)
                neighbors = []
                nb_dict = data.get("neighbors") or {}
                for n_id, entries in nb_dict.items():
                    for entry in entries:
                        state_str = entry.get("converged") or entry.get("state") or ""
                        is_full = "Full" in state_str
                        neighbors.append({
                            "neighbor_id": n_id,
                            "address": entry.get("address"),
                            "interface": entry.get("ifaceName"),
                            "state": "Full" if is_full else state_str,
                            "raw_state": state_str,
                            "priority": entry.get("priority"),
                            "converged": is_full,
                        })
                return neighbors
            except (ValueError, KeyError):
                pass
        text_out = node.cmd(f"vtysh -N {name} -c 'show ip ospf neighbor' 2>/dev/null")
        neighbors = []
        for line in text_out.splitlines():
            line = line.strip()
            m = re.match(r"^(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)", line)
            if m:
                state_str = m.group(3)
                is_full = "Full" in state_str
                if_part = m.group(5).split(":")[0]
                neighbors.append({
                    "neighbor_id": m.group(1),
                    "priority": int(m.group(2)),
                    "state": "Full" if is_full else state_str,
                    "raw_state": state_str,
                    "address": m.group(4),
                    "interface": if_part,
                    "converged": is_full,
                })
        return neighbors

    elif p in ("bgp", "bgpd"):
        out = node.cmd(f"vtysh -N {name} -c 'show bgp summary json' 2>/dev/null")
        if out and out.strip().startswith("{"):
            try:
                data = json.loads(out)
                peers = []
                for p_id, p_info in (data.get("ipv4Unicast", {}).get("peers") or {}).items():
                    state_val = p_info.get("state")
                    pfx_cnt = p_info.get("pfxRcd")
                    is_established = str(state_val).lower() == "established" or pfx_cnt is not None
                    peers.append({
                        "neighbor": p_id,
                        "remote_as": p_info.get("remoteAs"),
                        "state": "Established" if is_established else state_val,
                        "prefixes": pfx_cnt,
                        "converged": is_established,
                    })
                return peers
            except (ValueError, KeyError):
                pass
        text_out = node.cmd(f"vtysh -N {name} -c 'show bgp summary' 2>/dev/null")
        peers = []
        for line in text_out.splitlines():
            line = line.strip()
            tokens = line.split()
            if len(tokens) >= 10 and re.match(r"^\d+\.\d+\.\d+\.\d+$", tokens[0]):
                state_val = tokens[9]
                is_established = state_val.isdigit()
                peers.append({
                    "neighbor": tokens[0],
                    "remote_as": int(tokens[2]) if tokens[2].isdigit() else tokens[2],
                    "state": "Established" if is_established else state_val,
                    "prefixes": int(state_val) if is_established else 0,
                    "converged": is_established,
                })
        return peers

    elif p in ("isis", "isisd"):
        out = node.cmd(f"vtysh -N {name} -c 'show isis neighbor json' 2>/dev/null")
        if out and out.strip().startswith("{"):
            try:
                data = json.loads(out)
                adj = []
                for area in data.get("areas") or []:
                    for circ in area.get("circuits") or []:
                        for adj_entry in circ.get("adjacencies") or []:
                            st = adj_entry.get("state") or ""
                            adj.append({
                                "system_id": adj_entry.get("sysid"),
                                "interface": circ.get("name"),
                                "state": st,
                                "level": adj_entry.get("circuitType"),
                                "converged": st.lower() == "up",
                            })
                return adj
            except (ValueError, KeyError):
                pass
        text_out = node.cmd(f"vtysh -N {name} -c 'show isis neighbor' 2>/dev/null")
        adj = []
        for line in text_out.splitlines():
            line = line.strip()
            tokens = line.split()
            if len(tokens) >= 5 and tokens[3] in ("Up", "Init", "Down"):
                adj.append({
                    "system_id": tokens[0],
                    "interface": tokens[1],
                    "state": tokens[3],
                    "converged": tokens[3] == "Up",
                })
        return adj

    return []


def wait_for_fib_route(node, prefix, expected_via=None, expected_proto=None, timeout=30.0, interval=0.5):
    """FIB da marshrut paydo bo'lguncha kutadi. Muvaffaqiyatli bo'lsa o'tgan vaqt (sekund), aks holda None."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        ok, _, _ = verify_fib_route(node, prefix, expected_via=expected_via, expected_proto=expected_proto)
        if ok:
            return round(time.time() - t0, 3)
        time.sleep(interval)
    return None


def wait_for_frr_convergence(nodes, proto="ospf", timeout=45.0, interval=0.5, min_neighbors=1):
    """Ko'rsatilgan routerlar dinamik qo'shnichiligi o'rnatilguncha kutadi."""
    t0 = time.time()
    last_details = {}
    while time.time() - t0 < timeout:
        all_ready = True
        current_details = {}
        for node in nodes:
            name = getattr(node, "name", str(node))
            nbrs = get_frr_neighbors(node, proto=proto)
            converged_count = sum(1 for n in nbrs if n.get("converged"))
            is_node_ready = converged_count >= min_neighbors
            current_details[name] = {
                "converged": is_node_ready,
                "converged_neighbors": converged_count,
                "total_neighbors": len(nbrs),
                "neighbors": nbrs,
            }
            if not is_node_ready:
                all_ready = False
        last_details = current_details
        if all_ready and len(nodes) > 0:
            return {
                "converged": True,
                "elapsed_s": round(time.time() - t0, 3),
                "details": last_details,
            }
        time.sleep(interval)
    return {
        "converged": False,
        "elapsed_s": round(time.time() - t0, 3),
        "details": last_details,
    }


def measure_routing_convergence(trigger_fn, verify_fn, timeout=30.0, interval=0.05):
    """Topologiya hodisasidan keyin yangi yo'lga o'tish (failover/convergence) vaqtini millisoniyalarda o'lchaydi."""
    t0 = time.time()
    if trigger_fn:
        trigger_fn()
    while time.time() - t0 < timeout:
        if verify_fn():
            return round(time.time() - t0, 4)
        time.sleep(interval)
    return None


# ─────────────────────────── NAT Gateway & Monitor ───────────────────────────

def _nat_gateway_name(topo):
    for name, info in topo.get("hosts", {}).items():
        if info.get("role") == "gateway":
            return name
    return None


def _nat_private_hosts(topo, subnet_prefix):
    return [n for n, info in topo.get("hosts", {}).items()
            if info.get("ip", "").split("/")[0].startswith(subnet_prefix)]


def setup_nat_gateway(net, topo):
    """net.start() dan KEYIN chaqiriladi. nat_gw — bitta interfeysli
    "one-armed" NAT router: xususiy va ommaviy (10.x.x.x) manzil bir xil
    interfeysda birga yashaydi, SNAT orqali tarjima qilinadi."""
    subnet = topo.get("nat_private_subnet")
    gw_name = _nat_gateway_name(topo)
    if not subnet or not gw_name:
        return
    net_addr = subnet.split("/")[0]
    prefix = net_addr.rsplit(".", 1)[0] + "."
    gw_private_ip = f"{prefix}1/24"

    gw = net.get(gw_name)
    ifname = gw.defaultIntf().name
    gw.cmd(f"ip addr add {gw_private_ip} dev {ifname}")
    gw.cmd("sysctl -w net.ipv4.ip_forward=1 2>/dev/null")
    gw.cmd(f"iptables -t nat -A POSTROUTING -s {subnet} -j SNAT --to-source {gw.IP()}")
    for hname in _nat_private_hosts(topo, prefix):
        net.get(hname).cmd(f"ip route add default via {prefix}1")
    print(f"[NAT] {gw_name} tayyor: xususiy {subnet} -> ommaviy {gw.IP()}")


class NATMonitor:
    """nat_gw'dagi conntrack jadvalini davriy o'qib, xususiy<->ommaviy
    IP/port tarjimalarini stats/nat_translations.jsonl'ga yozadi."""

    def __init__(self, net, topo, interval=5):
        self.net = net
        self.topo = topo
        self.interval = interval
        self._running = False
        self._thread = None
        self._log = os.path.join(DATA_DIR, "stats/nat_translations.jsonl")

    def start(self):
        if not topo_has_nat(self.topo):
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        gw_name = _nat_gateway_name(self.topo)
        subnet_prefix = self.topo["nat_private_subnet"].split("/")[0].rsplit(".", 1)[0] + "."
        gw = self.net.get(gw_name)
        while self._running:
            time.sleep(self.interval)
            if not self._running:
                break
            ts = time.time()
            out = locked_cmd(gw, "cat /proc/net/nf_conntrack 2>/dev/null")
            for line in out.splitlines():
                if subnet_prefix not in line:
                    continue
                fields = line.split()
                proto = fields[2] if len(fields) > 2 else ""
                src = dst = sport = dport = None
                for tok in fields:
                    if tok.startswith("src=") and src is None:
                        src = tok.split("=", 1)[1]
                    elif tok.startswith("dst=") and dst is None:
                        dst = tok.split("=", 1)[1]
                    elif tok.startswith("sport=") and sport is None:
                        sport = tok.split("=", 1)[1]
                    elif tok.startswith("dport=") and dport is None:
                        dport = tok.split("=", 1)[1]
                if not src or not src.startswith(subnet_prefix):
                    continue
                entry = {
                    "ts": ts, "proto": proto,
                    "private_ip": src, "private_port": sport,
                    "public_ip": gw.IP(),
                    "dst_ip": dst, "dst_port": dport,
                }
                try:
                    with open(self._log, "a") as f:
                        f.write(json.dumps(entry) + "\n")
                except OSError:
                    pass


def topo_has_nat(topo):
    return bool(topo.get("nat_private_subnet") and _nat_gateway_name(topo))

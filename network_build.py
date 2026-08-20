"""Mininet tarmog'ini qurish, QoS/DiffServ navbatlarini o'rnatish, va NAT
gateway/monitor. Barcha Mininet importlari funksiya ichida (lazy) — shu
tufayli Mininet o'rnatilmagan muhitda ham modulni import qilish mumkin."""

import json
import os
import threading
import time

from config import CONTROLLER_PORT, DATA_DIR
from netutil import locked_cmd


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
# qaytaradi (aks holda "delay 0ms" bilan restore link'ning bazaviy
# delay/jitter/loss/limit qiymatlarini butun run davomida o'chirib yuboradi).
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

    # Barcha tc buyruqlari BITTA .cmd() chaqiruviga birlashtiriladi — Mininet
    # Node.cmd() ketma-ket ko'p marta chaqirilganda (100+ marta, startup
    # paytida) ichki sendCmd/waitOutput mexanizmi qotib qolishi mumkin edi.
    cmds = [f"tc qdisc del dev {ifname} root 2>/dev/null",
            f"tc qdisc add dev {ifname} root handle 1: htb default 30"]
    # Har band uchun bazaviy netem argumentlari — barcha band bir xil
    # delay/jitter/loss/limit oladi (faqat HTB ustuvorligi farqlanadi).
    # Impairments moduli restore paytida shu satrni aynan qayta ishlatadi.
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


def build_topology(topo):
    """Mininet topologiya yaratish."""
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController
    from mininet.link import TCLink
    from mininet.log import setLogLevel
    setLogLevel("info")

    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True, autoStaticArp=True)
    net.addController("c0", controller=RemoteController,
                      ip="127.0.0.1", port=CONTROLLER_PORT, protocols="OpenFlow13")

    switches = {}
    for sw_name in topo["switches"]:
        dpid = f"{int(sw_name[1:]):016x}"
        switches[sw_name] = net.addSwitch(sw_name, dpid=dpid, protocols="OpenFlow13")

    hosts = {}
    for h_name, h_info in topo["hosts"].items():
        hosts[h_name] = net.addHost(h_name, ip=h_info["ip"])
        al = topo["access_links"].get(h_name, {"bw": 10, "delay": "1ms", "loss": 0})
        net.addLink(hosts[h_name], switches[h_info["switch"]],
                    cls=TCLink, bw=al["bw"], delay=al["delay"], loss=al["loss"])

    net._qos_links = []  # (intf1, intf2, params) — net.start() dan keyin QoS qdisc o'rnatish uchun
    for (s1, s2), params in topo["links"].items():
        link = net.addLink(switches[s1], switches[s2], cls=TCLink,
                    bw=params["bw"], delay=params["delay"],
                    loss=params["loss"], max_queue_size=params["queue"])
        net._qos_links.append((link.intf1, link.intf2, params))

    return net


def apply_qos_qdiscs(net):
    """net.start() dan KEYIN chaqirilishi shart — TCLink o'z qdisc'ini
    o'rnatib bo'lgach, uni 3-bandli DiffServ navbatiga almashtiradi."""
    for intf1, intf2, params in getattr(net, "_qos_links", []):
        _setup_qos_qdisc(intf1, params)
        _setup_qos_qdisc(intf2, params)


# NAT — topologiyaga bog'liq emas (generic). Bir topologiya NAT'ga ega
# bo'lishi uchun ikki narsa kerak: (1) topo["nat_private_subnet"] — masalan
# "192.168.50.0/24", (2) topo["hosts"] ichida role="gateway" bo'lgan bitta
# host (uning "ip" maydoni — ommaviy tomon manzili). Shu ikkisi bo'lmasa,
# setup_nat_gateway()/NATMonitor avtomatik hech narsa qilmaydi.

def _nat_gateway_name(topo):
    for name, info in topo["hosts"].items():
        if info.get("role") == "gateway":
            return name
    return None


def _nat_private_hosts(topo, subnet_prefix):
    return [n for n, info in topo["hosts"].items()
            if info.get("ip", "").split("/")[0].startswith(subnet_prefix)]


def setup_nat_gateway(net, topo):
    """net.start() dan KEYIN chaqiriladi. nat_gw — bitta interfeysli
    "one-armed" NAT router: xususiy va ommaviy (10.x.x.x) manzil bir xil
    interfeysda birga yashaydi, SNAT orqali tarjima qilinadi. compute_paths()/
    switch grafigi butunlay o'zgarishsiz qoladi — nat_gw oddiy host sifatida
    mavjud switch fabrikiga ulanadi, yangi switch-link qo'shilmaydi."""
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

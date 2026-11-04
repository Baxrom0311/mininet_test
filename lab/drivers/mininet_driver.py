"""Haqiqiy driver: Mininet + Open vSwitch + FRRouting + BMv2 (P4) + os-ken.

Ishga tushirish root talab qiladi (sudo). Barcha Mininet importlari lazy —
modulni Mininet o'rnatilmagan muhitda ham import qilish mumkin.

Asosiy g'oyalar:
  * Tarmoq DOIMIY ushlab turiladi — qurilma/kabel qo'shish-o'chirish, kabel
    parametrlari, IP sozlamalari tarmoqni qayta ishga tushirmasdan qo'llanadi.
  * Har bir switch o'z kontrolleriga ulanadi (net.start() hamma switchni
    hamma kontrollerga ulaydi — shuning uchun switchlar qo'lda start qilinadi).
  * Paket ushlash: har bir namespace uchun bitta `tcpdump -i any` (+ root ns
    uchun bitta). "Out" yo'nalishdagi paket aniq bitta kabelga tegishli —
    shu orqali UI'da kabel bo'ylab animatsiya qilinadi.
  * Statistika: /proc/<pid>/net/dev — pid'ning namespace'idagi interfeys
    hisoblagichlari (node.cmd chaqirmasdan, arzon).
"""

import fcntl
import ipaddress
import json
import os
import pty
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time

from lab.wifi import link_estimate as wifi_link_estimate
from lab.model import (DEVICE_TYPES, FRR_DAEMONS, VLAN_TYPES, controller_config, device_mac,
                       parse_ifname, rip_timers, switch_port_cfg,
                       ifname, link_ports, normalize_link_params, validate_project)
from .base import (BaseDriver, DriverError, Terminal, p4_compile_standalone,
                   p4_pipeline_from_json)

RUN_DIR = "/tmp/netlab"
FRR_BIN = os.environ.get("NETLAB_FRR_BIN", "/usr/lib/frr")
THRIFT_BASE = 9090
PKT_RATE_LIMIT = 15        # har bir kabel uchun UI'ga sekundiga ko'pi bilan shuncha paket hodisasi


def _which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _run(cmd, input_text=None, timeout=15):
    """Root namespace'da buyruq (OVS/BMv2 CLI) — matn qaytaradi."""
    try:
        r = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + (r.stderr if r.returncode else "")).strip()
    except FileNotFoundError:
        return f"{cmd[0]}: topilmadi"
    except subprocess.TimeoutExpired:
        return f"{cmd[0]}: vaqt tugadi"


def _wait_port(port, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


_KCAPS = None


def kernel_caps():
    """Kernel imkoniyatlari (bir marta aniqlanadi):
      ovs_datapath — "kernel" (openvswitch moduli bor) yoki "user" (userspace/netdev datapath).
          WSL2 va ko'p konteyner kernellarida openvswitch moduli yo'q — shunda OVS faqat
          userspace datapath bilan ishlaydi (sekinroq, lekin to'liq funksional).
          NETLAB_OVS_DATAPATH=kernel|user bilan majburlash mumkin.
      netem — sch_netem bormi (kechikish/jitter/loss/navbat emulyatsiyasi uchun). Yo'q bo'lsa
          kabellar faqat tezlik (HTB) bilan cheklanadi.
      vlan — 8021q (Linux VLAN subinterfeyslari, router-on-a-stick). Switch access/trunk
          portlari OVS'da bo'lgani uchun ularga bu shart emas."""
    global _KCAPS
    if _KCAPS is not None:
        return _KCAPS
    forced = os.environ.get("NETLAB_OVS_DATAPATH", "").strip().lower()
    if forced in ("kernel", "user"):
        dp = forced
    else:
        dp = "kernel" if os.path.isdir("/sys/module/openvswitch") else "user"
    netem = vlan = fq = False
    a, b = "nlprobe0", "nlprobe1"
    try:
        if subprocess.run(["ip", "link", "add", a, "type", "veth", "peer", "name", b], capture_output=True).returncode == 0:
            netem = subprocess.run(["tc", "qdisc", "add", "dev", a, "root", "netem", "delay", "1ms"],
                                   capture_output=True).returncode == 0
            vlan = subprocess.run(["ip", "link", "add", "link", a, "name", a + ".9", "type", "vlan", "id", "9"],
                                  capture_output=True).returncode == 0
            fq = subprocess.run(["tc", "qdisc", "replace", "dev", a, "root", "fq_codel"],
                                capture_output=True).returncode == 0
            subprocess.run(["ip", "link", "del", a], capture_output=True)
    except FileNotFoundError:
        pass
    _KCAPS = {"ovs_datapath": dp, "netem": netem, "vlan": vlan, "fq_codel": fq}
    return _KCAPS


WMEM_USERSPACE = 16 * 1024 * 1024


def _sysctl(key, value=None):
    path = "/proc/sys/" + key.replace(".", "/")
    try:
        if value is None:
            with open(path) as f:
                return f.read().strip()
        with open(path, "w") as f:
            f.write(str(value))
        return str(value)
    except OSError:
        return None


def _ovs_ok():
    try:
        return subprocess.run(["ovs-vsctl", "--timeout=3", "show"], capture_output=True,
                              timeout=6).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_ovs():
    """ovsdb-server/ovs-vswitchd ishlamayotgan bo'lsa ishga tushiradi
    (WSL2/konteynerda systemd yo'q — servis o'zi turmaydi)."""
    if _ovs_ok():
        return True
    for cmd in (["/usr/share/openvswitch/scripts/ovs-ctl", "start", "--system-id=random"],
                ["service", "openvswitch-switch", "start"]):
        if os.path.exists(cmd[0]) or _which(cmd[0]):
            _run(cmd, timeout=60)
            if _ovs_ok():
                return True
    return False


def restart_ovs():
    ctl = "/usr/share/openvswitch/scripts/ovs-ctl"
    if os.path.exists(ctl):
        _run([ctl, "restart", "--system-id=random"], timeout=60)
    else:
        _run(["service", "openvswitch-switch", "restart"], timeout=60)
    return ensure_ovs()


_CTRL_CMD = None


def controller_cmd():
    """os-ken/ryu manager buyrug'i. Skript shebang'idagi python paketlarni
    ko'tara olmasligi mumkin (masalan, /usr/bin/python3 -> 3.13, Debian
    paketlari 3.12 uchun) — shuning uchun import ishlaydigan interpreter tanlanadi."""
    global _CTRL_CMD
    if _CTRL_CMD is not None:
        return _CTRL_CMD
    cands = [("os_ken.cmd.manager", "osken-manager"), ("ryu.cmd.manager", "ryu-manager")]
    pys = [sys.executable, "python3", "python3.12", "python3.11", "python3.10"]
    for mod, script in cands:
        for py in pys:
            exe = shutil.which(py) if not os.path.isabs(py) else py
            if not exe:
                continue
            r = subprocess.run([exe, "-c", f"import {mod}"], capture_output=True, timeout=30)
            if r.returncode == 0:
                _CTRL_CMD = [exe, "-m", mod]
                return _CTRL_CMD
        if _which(script):
            _CTRL_CMD = [_which(script)]
            return _CTRL_CMD
    _CTRL_CMD = []
    return _CTRL_CMD


def _read_net_dev(pid):
    """/proc/<pid>/net/dev -> {ifname: (rx_bytes, rx_pkts, rx_drop, tx_bytes, tx_pkts, tx_drop)}"""
    out = {}
    try:
        with open(f"/proc/{pid}/net/dev") as f:
            lines = f.readlines()[2:]
    except OSError:
        return out
    for line in lines:
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        v = data.split()
        if len(v) >= 12:
            out[name.strip()] = (int(v[0]), int(v[1]), int(v[3]), int(v[8]), int(v[9]), int(v[11]))
    return out


def _netns_id(pid):
    try:
        return os.readlink(f"/proc/{pid}/ns/net")
    except OSError:
        return None


def _read_qdisc(pid):
    """Namespace'dagi barcha interfeyslar navbati (tc -s -j qdisc):
    {ifname: (backlog_pkts, backlog_bytes, drops_total, overlimits)}.
    root qdisc qlen/drops (HTB bolalarini ham qamraydi)."""
    out = {}
    argv = ["tc", "-s", "-j", "qdisc", "show"]
    if _netns_id(pid) != _netns_id(os.getpid()):
        argv = ["mnexec", "-a", str(pid)] + argv
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        data = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return out
    for q in data:
        dev = q.get("dev")
        if not dev:
            continue
        if q.get("root"):   # root (htb) statistikasi bolalar (pfifo/netem) drops'ini ham o'z ichiga oladi
            out[dev] = (int(q.get("qlen", 0)), int(q.get("backlog", 0)), int(q.get("drops", 0)),
                        int(q.get("overlimits", 0)))
    return out


class QdiscSampler:
    """Har netns uchun bitta tc chaqiruvi; read(pid) — kesh (shu sikl ichida)."""

    def __init__(self):
        self._by_ns = {}

    def reset(self):
        self._by_ns = {}

    def read(self, pid):
        ns = _netns_id(pid)
        if ns not in self._by_ns:
            self._by_ns[ns] = _read_qdisc(pid)
        return self._by_ns[ns]


_PROTO_RULES = [
    ("ARP", re.compile(r"^ARP")),
    ("STP", re.compile(r"STP|802\.1d", re.I)),
    ("LLDP", re.compile(r"LLDP")),
    ("OSPF", re.compile(r"OSPF")),
    ("BGP", re.compile(r"\.179[:\s>]|BGP")),
    ("RIP", re.compile(r"RIP")),
    ("IS-IS", re.compile(r"IS-IS|isis", re.I)),
    ("ICMP", re.compile(r"ICMP")),
    ("DNS", re.compile(r"\.53[:\s]")),
    ("TCP", re.compile(r"Flags \[")),
    ("UDP", re.compile(r"UDP")),
    ("IPv6", re.compile(r"^IP6")),
]


def classify(summary):
    for name, rx in _PROTO_RULES:
        if rx.search(summary):
            return name
    return "other"


_TCPDUMP_LINE = re.compile(r"^(\d+\.\d+)\s+(\S+)\s+(In|Out|B|M|P)\s+(.*)$")


_NLINTF = None


def _nl_intf():
    """TCIntf + netem'siz navbat cheklovi: HTB leaf (5:1) ostiga `pfifo limit N`.
    Mininet max_queue_size'ni faqat netem orqali qo'yadi — netem yo'q yadrolarda
    (WSL2) navbat uzunligi aks holda cheksiz (txqueuelen) bo'lib qoladi."""
    global _NLINTF
    if _NLINTF is None:
        from mininet.link import TCIntf

        class NLIntf(TCIntf):
            def config(self, pfifo_limit=None, qdisc="fifo", **params):
                r = super().config(**params)
                bw = params.get("bw")
                if bw is None:
                    return r
                limit = int(pfifo_limit or params.get("max_queue_size") or 1000)
                if qdisc == "prio":
                    self._qos_prio(float(bw), limit, params)
                elif qdisc == "fq_codel":
                    self.tc("%s qdisc replace dev %s parent 5:1 handle 10: fq_codel limit " + str(limit))
                elif pfifo_limit:
                    self.tc("%s qdisc replace dev %s parent 5:1 handle 10: pfifo limit " + str(int(pfifo_limit)))
                return r

            def _qos_prio(self, bw, limit, params):
                """Qat'iy ustuvorlik HTB bilan (sch_prio shart emas): 5:1 (bw) ostida
                5:10 — yuqori sinf (prio 0, kafolatli rate=bw), 5:20 — oddiy (prio 1, faqat qolgani);
                u32 filtr DSCP >= 40 (EF/CS5..CS7) -> 5:10. Barglar — netem (bo'lsa) yoki pfifo."""
                self.tc("%s qdisc del dev %s parent 5:1 handle 10:")
                for cid, prio, rate in (("5:10", 0, bw), ("5:20", 1, 0.008)):
                    self.tc("%s class replace dev %s parent 5:1 classid " +
                            f"{cid} htb rate {rate:.3f}Mbit ceil {bw:.3f}Mbit prio {prio} burst 15k")
                    leaf = cid.split(":")[1]
                    if params.get("delay") or params.get("loss"):
                        net = "netem" + (f" delay {params['delay']}" if params.get("delay") else "") + \
                              (f" {params['jitter']}" if params.get("delay") and params.get("jitter") else "") + \
                              (f" loss {params['loss']}%" if params.get("loss") else "") + f" limit {limit}"
                        self.tc(f"%s qdisc replace dev %s parent {cid} handle {leaf}: {net}")
                    else:
                        self.tc(f"%s qdisc replace dev %s parent {cid} handle {leaf}: pfifo limit {limit}")
                self.tc("%s filter del dev %s parent 5: >/dev/null 2>&1")
                for tos in (0xa0, 0xb8, 0xc0, 0xe0):      # CS5, EF, CS6, CS7 (DSCP >= 40)
                    self.tc("%s filter add dev %s parent 5: protocol ip prio 1 u32 match ip dsfield " +
                            f"{tos:#x} 0xfc flowid 5:10")
                self.tc("%s filter add dev %s parent 5: protocol ip prio 9 u32 match u32 0 0 flowid 5:20")
        _NLINTF = NLIntf
    return _NLINTF


def _mn_classes():
    """Mininet'ga bog'liq klasslar (lazy)."""
    from mininet.node import Host, Switch

    class LabHost(Host):
        """PC/server — oddiy namespace host."""

    class FRRRouter(Host):
        """FRR router: namespace + IP forwarding. Daemonlar driver tomonidan ishga tushiriladi."""

        def config(self, **params):
            r = super().config(**params)
            self.cmd("sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1 "
                     "net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.default.rp_filter=0")
            return r

    class P4Switch(Switch):
        """BMv2 simple_switch. Konfiguratsiyasiz (--no-p4) ishga tushadi, dastur
        keyin thrift CLI orqali yuklanadi (load_new_config_file + swap_configs) —
        shu tufayli dasturni tarmoqni to'xtatmasdan almashtirish mumkin."""

        def __init__(self, name, thrift_port=THRIFT_BASE, device_id=1, **kw):
            Switch.__init__(self, name, **kw)
            self.thrift_port = thrift_port
            self.device_id = device_id
            self.bmv2_pid = None
            self.log_path = os.path.join(RUN_DIR, "p4", name, "bmv2.log")

        def start(self, controllers):
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            exe = _which("simple_switch")
            if not exe:
                raise DriverError("simple_switch (BMv2) topilmadi")
            args = [exe, "--device-id", str(self.device_id), "--thrift-port", str(self.thrift_port),
                    "--log-file", self.log_path[:-4], "--log-flush", "--no-p4"]
            for port, intf in sorted(self.intfs.items()):
                if intf.name != "lo":
                    args += ["-i", f"{port}@{intf.name}"]
            out = self.cmd(" ".join(args) + f" > {self.log_path}.out 2>&1 & echo $!")
            try:
                self.bmv2_pid = int(out.strip().split()[-1])
            except (ValueError, IndexError):
                raise DriverError(f"{self.name}: simple_switch ishga tushmadi: {out[-200:]}")
            if not _wait_port(self.thrift_port, 15):
                raise DriverError(f"{self.name}: BMv2 thrift porti {self.thrift_port} ochilmadi")

        def stop(self, deleteIntfs=True):
            if self.bmv2_pid:
                try:
                    os.kill(self.bmv2_pid, signal.SIGTERM)
                except OSError:
                    pass
                self.bmv2_pid = None
            super().stop(deleteIntfs)

    return LabHost, FRRRouter, P4Switch


class PtyTerminal(Terminal):
    def __init__(self, argv, env=None):
        super().__init__()
        self._master, slave = pty.openpty()
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                     env=env, start_new_session=True, close_fds=True)
        os.close(slave)
        self._closed = False

    def start(self):
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while True:
            try:
                data = os.read(self._master, 4096)
            except OSError:
                break
            if not data:
                break
            self.on_output(data)
        self.on_exit()

    def write(self, data):
        if not self._closed:
            try:
                os.write(self._master, data)
            except OSError:
                pass

    def resize(self, cols, rows):
        try:
            fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            os.close(self._master)
        except OSError:
            pass


class MininetDriver(BaseDriver):
    name = "real"

    def __init__(self, bus):
        super().__init__(bus)
        self.net = None
        self._links = {}        # link_id -> mininet Link
        self._ctrl_procs = {}   # controller -> Popen
        self._ctrl_objs = {}    # controller -> mininet RemoteController
        self._p4_json = {}      # p4switch -> compiled json path
        self._threads_stop = threading.Event()
        self._cap_procs = {}    # owner key ('root' | node name) -> Popen
        self._intf_map = {}     # ifname -> (link_id, 'ab'|'ba')
        self._pkt_budget = {}   # link_id -> (second, count)
        self._netem_warned = False
        self._datapath = "kernel"

    # ─────────────────────────── imkoniyatlar ───────────────────────────
    def capabilities(self):
        try:
            import mininet  # noqa: F401
            has_mn = True
        except ImportError:
            has_mn = False
        return {
            "root": os.geteuid() == 0,
            "mininet": has_mn,
            "ovs": bool(_which("ovs-vsctl")),
            "frr": os.path.exists(os.path.join(FRR_BIN, "zebra")),
            "p4": bool(_which("simple_switch")),
            "sdn": bool(controller_cmd()),
            "p4c": bool(_which("p4c-bm2-ss", "p4c")),
            "tcpdump": bool(_which("tcpdump")),
            "ethtool": bool(_which("ethtool")),
            "iperf3": bool(_which("iperf3")),
            **(kernel_caps() if os.geteuid() == 0 else {}),
        }

    def _preflight(self, project):
        caps = self.capabilities()
        if not caps["root"]:
            raise DriverError("Real driver root talab qiladi: sudo python3 -m lab.netlab")
        if not caps["mininet"] or not caps["ovs"]:
            raise DriverError("Mininet/Open vSwitch o'rnatilmagan (sudo bash lab/install_lab.sh)")
        subifs = [f"{n}/{k}" for n, d in project["devices"].items()
                  for k in ((d.get("config") or {}).get("interfaces") or {}) if parse_ifname(k)[2]]
        if subifs and not kernel_caps()["vlan"]:
            raise DriverError("Kernelda 802.1Q (8021q) yo'q — VLAN subinterfeyslar ishlamaydi: "
                              f"{', '.join(subifs[:4])}. `modprobe 8021q` yoki subinterfeys o'rniga "
                              "router'ni har VLAN uchun alohida access portga ulang")
        if not ensure_ovs():
            raise DriverError("Open vSwitch ishga tushmadi (ovsdb-server/ovs-vswitchd): "
                              "sudo /usr/share/openvswitch/scripts/ovs-ctl start")
        types = {d["type"] for d in project["devices"].values()}
        if "router" in types and not caps["frr"]:
            raise DriverError("FRR router bor, lekin FRRouting o'rnatilmagan (install_lab.sh --frr)")
        if "p4switch" in types and not caps["p4"]:
            raise DriverError("P4 switch bor, lekin BMv2 o'rnatilmagan (install_lab.sh --p4)")
        if project.get("controllers") and not caps["sdn"]:
            needs = [n for n, d in project["devices"].items()
                     if d["type"] == "sdnrouter" or (d.get("config") or {}).get("mode") == "sdn"]
            if needs:
                raise DriverError("SDN qurilmalar bor, lekin os-ken/ryu o'rnatilmagan")

    # ─────────────────────────── start / stop ───────────────────────────
    def start(self, project):
        with self.lock:
            if self.running:
                raise DriverError("Tarmoq allaqachon ishlayapti")
            errors = validate_project(project)
            if errors:
                raise DriverError("; ".join(errors[:5]))
            self._preflight(project)
            from mininet.clean import cleanup
            from mininet.link import TCLink
            from mininet.log import setLogLevel
            from mininet.net import Mininet
            setLogLevel("warning")
            kc = kernel_caps()
            self._datapath = kc["ovs_datapath"]
            self._netem_warned = False
            if self._datapath == "user":
                self.bus.log("info", "OVS kernel moduli yo'q — userspace datapath (netdev) ishlatiladi")
                # netdev OVS port socket'i (AF_PACKET) qdisc'dagi paketlar uchun ham sndbuf
                # sarflaydi: standart 208 KB ~94 paketdan keyin paketlar qdisc'ga yetmay
                # hisobsiz tashlanadi -> kabel `queue` parametri ishlamaydi. Vaqtincha oshiramiz.
                # Socket ovs-vswitchd ishga tushganda bir marta ochiladi — shuning uchun
                # sysctl'dan keyin daemon bir marta qayta ishga tushiriladi (qiymat qoldiriladi).
                cur = _sysctl("net.core.wmem_default")
                if cur and int(cur) < WMEM_USERSPACE:
                    _sysctl("net.core.wmem_default", WMEM_USERSPACE)
                    self.bus.log("info", f"net.core.wmem_default {cur} -> {WMEM_USERSPACE} "
                                         "(port navbati uchun), Open vSwitch qayta ishga tushirilmoqda")
                    restart_ovs()
                if not _which("ethtool"):
                    self.bus.log("warn", "ethtool yo'q — userspace OVS'da TCP/UDP ishlamasligi mumkin "
                                         "(apt install ethtool)")
            self.bus.log("info", "Oldingi sessiya tozalanmoqda (mn -c)...")
            cleanup()
            shutil.rmtree(RUN_DIR, ignore_errors=True)
            os.makedirs(RUN_DIR, exist_ok=True)
            self._set_project(project)
            try:
                self.net = Mininet(build=False, controller=None, link=TCLink,
                                   autoSetMacs=False, autoStaticArp=False, waitConnected=False)
                for cname in self.project.get("controllers", {}):
                    self._ctrl_start(cname)
                for name, d in sorted(self.project["devices"].items(), key=lambda kv: kv[1]["id"]):
                    self._add_node(name, d)
                for lid, l in self.project["links"].items():
                    self._add_link_obj(lid, l)
                self.net.build()
                for name, d in self.project["devices"].items():
                    self._start_device(name, d)
                self._dhcp_sync()
                self.started_at = time.time()
                self._threads_stop.clear()
                self._rebuild_intf_map()
                threading.Thread(target=self._stats_loop, daemon=True).start()
                self._capture_sync()
            except Exception:
                self._teardown()
                raise
            self.bus.log("ok", f"Tarmoq ishga tushdi: {len(self.project['devices'])} qurilma, "
                               f"{len(self.project['links'])} kabel")
            self._publish_state()
            return self.status()

    def stop(self):
        with self.lock:
            if not self.running:
                return self.status()
            self._teardown()
            self.bus.log("info", "Tarmoq to'xtatildi")
            self._publish_state()
            return self.status()

    def _teardown(self):
        self._threads_stop.set()
        self._dhcp_kill()
        for p in list(self._cap_procs.values()):
            self._kill(p)
        self._cap_procs.clear()
        if self.project:
            for name, d in self.project["devices"].items():
                if d["type"] == "router":
                    self._frr_stop(name)
        if self.net:
            try:
                self.net.stop()
            except Exception as e:  # noqa: BLE001
                self.bus.log("warn", f"net.stop: {e}")
        for p in list(self._ctrl_procs.values()):
            self._kill(p)
        self._ctrl_procs.clear()
        self._ctrl_objs.clear()
        self._links.clear()
        self._p4_json.clear()
        self.net = None
        self.project = None
        self.started_at = None

    @staticmethod
    def _kill(p):
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    # ─────────────────────────── qurilmalar ───────────────────────────
    def _add_node(self, name, d):
        LabHost, FRRRouter, P4Switch = _mn_classes()
        from mininet.node import OVSSwitch
        t, cfg, did = d["type"], d.get("config") or {}, d["id"]
        if t in ("host", "server"):
            self.net.addHost(name, cls=LabHost, ip=None)
        elif t == "router":
            self.net.addHost(name, cls=FRRRouter, ip=None)
        elif t == "switch":
            sdn = cfg.get("mode") == "sdn"
            self.net.addSwitch(name, cls=OVSSwitch, dpid=f"{did:016x}", protocols="OpenFlow13", datapath=self._datapath,
                               failMode="secure" if sdn else "standalone",
                               stp=bool(cfg.get("stp")) and not sdn)
        elif t == "sdnrouter":
            self.net.addSwitch(name, cls=OVSSwitch, dpid=f"{did:016x}", protocols="OpenFlow13", datapath=self._datapath,
                               failMode="secure")
        elif t == "wifiap":
            # Mininet'da radio yo'q: AP = OVS L2 switch, Wi-Fi kabellari radio modelidan
            # hisoblangan tezlikdagi TCLink (umumiy efir emulyatsiya qilinmaydi).
            self.net.addSwitch(name, cls=OVSSwitch, dpid=f"{did:016x}", protocols="OpenFlow13", datapath=self._datapath,
                               failMode="standalone")
        elif t == "p4switch":
            self.net.addSwitch(name, cls=P4Switch, dpid=f"{did:016x}",
                               thrift_port=THRIFT_BASE + did, device_id=did)
        else:
            raise DriverError(f"{name}: noma'lum tur {t}")

    def _ctrls_for(self, d):
        cfg = d.get("config") or {}
        if d["type"] == "sdnrouter" or (d["type"] == "switch" and cfg.get("mode") == "sdn"):
            c = self._ctrl_objs.get(cfg.get("controller"))
            return [c] if c else []
        return []

    def _start_device(self, name, d):
        node = self.net.get(name)
        t = d["type"]
        if t in ("switch", "sdnrouter", "wifiap"):
            node.start(self._ctrls_for(d))
            self._apply_vlans(name)
        elif t == "p4switch":
            node.start([])
            if (d.get("config") or {}).get("program"):
                res = self.p4_load(self.project, name, _locked=True)
                if not res["ok"]:
                    self.bus.log("error", f"P4 kompilyatsiya xatosi:\n{res['log'][-800:]}", name)
            else:
                self._p4_mcast(name)
        else:
            self._config_ns_node(name)
            if t == "router":
                self._frr_start(name, d)

    def _config_ns_node(self, name):
        """Namespace qurilmasi: interfeys MAC/IP, gateway."""
        d = self.project["devices"][name]
        node = self.net.get(name)
        cfg = d.get("config") or {}
        cmds = []
        if d["type"] in ("host", "server"):
            cmds.append("sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1")
        ics = cfg.get("interfaces") or {}
        want_sub = set()
        for port in sorted(link_ports(self.project, name)):
            base = ifname(name, port)
            if base not in node.nameToIntf:
                continue
            subs = sorted(k for k in ics if k.startswith(base + ".") and parse_ifname(k)[2])
            want_sub.update(subs)
            for ifn in [base] + subs:
                vl = parse_ifname(ifn)[2]
                if vl:      # 802.1Q subinterfeys (router-on-a-stick)
                    cmds.append(f"ip link show {ifn} >/dev/null 2>&1 || "
                                f"ip link add link {base} name {ifn} type vlan id {vl}")
                ip = (ics.get(ifn) or {}).get("ip", "")
                if ip.lower() != "dhcp":        # DHCP manzili dhclient'niki — tegmaymiz
                    cmds.append(f"ip addr flush dev {ifn}")
                    if ip:
                        cmds.append(f"ip addr add {ip} dev {ifn}")
                cmds.append(f"ip link set {ifn} up")
                if self._datapath == "user":
                    # userspace (netdev) OVS veth'dagi "partial" checksum'ni to'ldirmaydi —
                    # TX offload o'chirilmasa TCP/UDP paketlar tashlanadi (ICMP o'tadi)
                    cmds.append(f"ethtool -K {ifn} tx off >/dev/null")
        # konfiguratsiyadan olib tashlangan subinterfeyslar
        for line in node.cmd("ip -o link show type vlan").splitlines():
            m = re.match(r"\d+:\s+([^@:]+)", line)
            if m and m.group(1) not in want_sub:
                cmds.append(f"ip link del {m.group(1)}")
        if d["type"] == "router":
            cmds += self._nat_cmds(cfg)
        gw = cfg.get("gateway")
        if gw and d["type"] in ("host", "server"):
            cmds.append(f"ip route replace default via {gw}")
        for r in cfg.get("routes") or []:
            # router'da ham kernel marshruti (zebra uni "K" sifatida ko'radi)
            cmds.append(f"ip route replace {r['prefix']} via {r['via']}")
        if cmds:
            node.cmd(" ; ".join(c + " 2>&1" for c in cmds))

    # ─────────────────────────── DHCP ───────────────────────────
    _DHCLIENT_SCRIPT = """#!/bin/sh
# NetLab: minimal dhclient-script — faqat namespace ichida IP/marshrut (resolv.conf'ga tegmaydi)
case "$reason" in
  PREINIT) ip link set "$interface" up ;;
  BOUND|RENEW|REBIND|REBOOT)
    [ -n "$old_ip_address" ] && [ "$old_ip_address" != "$new_ip_address" ] && ip addr flush dev "$interface"
    ip addr replace "$new_ip_address/$new_subnet_mask" dev "$interface"
    [ -n "$new_routers" ] && ip route replace default via "${new_routers%% *}" dev "$interface" ;;
  EXPIRE|FAIL|RELEASE|STOP) ip addr flush dev "$interface" ;;
esac
exit 0
"""

    def _dhcp_kill(self):
        d = os.path.join(RUN_DIR, "dhcp")
        if not os.path.isdir(d):
            return
        for f in os.listdir(d):
            if f.endswith(".pid"):
                try:
                    with open(os.path.join(d, f)) as fh:
                        os.kill(int(fh.read().strip()), signal.SIGTERM)
                except (OSError, ValueError):
                    pass
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass

    def _dhcp_sync(self):
        """DHCP serverlar (dnsmasq) va mijozlar (dhclient). Manzillar lab.services.resolve_dhcp
        bilan bir xil bo'lishi uchun dnsmasq'ga MAC bo'yicha rezervatsiya (dhcp-host) beriladi.
        dnsmasq/dhclient bo'lmasa — o'sha manzillar statik o'rnatiladi (ogohlantirish bilan)."""
        from lab.services import dhcp_pools, resolve_dhcp
        _rp, leases = resolve_dhcp(self.project)
        pools = dhcp_pools(self.project)
        self._dhcp_kill()
        if not pools and not leases:
            return
        d = os.path.join(RUN_DIR, "dhcp")
        os.makedirs(d, exist_ok=True)
        real = bool(_which("dnsmasq")) and bool(_which("dhclient"))
        if not real:
            self.bus.log("warn", "dnsmasq/dhclient topilmadi — DHCP manzillari statik o'rnatiladi "
                                 "(apt install dnsmasq-base isc-dhcp-client)")
        by_srv = {}
        for i, (srv, ifn, ipi, pool) in enumerate(pools):
            by_srv.setdefault(srv, []).append((i, ifn, ipi, pool))
        if real:
            for srv, items in by_srv.items():
                if srv not in self.net:
                    continue
                args = ["dnsmasq", "--conf-file=/dev/null", "--port=0", "--bind-interfaces", "--dhcp-authoritative",
                        "--user=root", "--log-dhcp", f"--log-facility={d}/{srv}.log",
                        f"--pid-file={d}/dnsmasq-{srv}.pid", f"--dhcp-leasefile={d}/{srv}.leases"]
                for i, ifn, ipi, pool in items:
                    args += [f"--interface={ifn}",
                             f"--dhcp-range=set:p{i},{pool['start']},{pool['end']},{ipi.netmask},{int(pool.get('lease', 3600))}",
                             f"--dhcp-option=tag:p{i},option:router,{pool.get('gateway') or ipi.ip}"]
                for ls in leases:
                    if ls["server"] == srv:
                        args.append(f"--dhcp-host={ls['mac']},{ls['ip']}")
                out = self.net.get(srv).cmd(" ".join(args) + " 2>&1")
                if out.strip():
                    self.bus.log("warn", f"dnsmasq: {out.strip()[-300:]}", srv)
            script = os.path.join(d, "dhclient-netlab.sh")
            with open(script, "w") as f:
                f.write(self._DHCLIENT_SCRIPT)
            os.chmod(script, 0o755)
            conf = os.path.join(d, "dhclient.conf")
            with open(conf, "w") as f:
                f.write("timeout 15;\nretry 5;\nrequest subnet-mask, routers;\n")
        for ls in leases:
            node = self.net.get(ls["client"]) if ls["client"] in self.net else None
            if not node:
                continue
            ifn = ls["iface"]
            if real:
                node.cmd(f"dhclient -4 -1 -cf {conf} -sf {script} -pf {d}/dhc-{ifn}.pid "
                         f"-lf {d}/dhc-{ifn}.leases {ifn} >/dev/null 2>&1 &")
            else:
                node.cmd(f"ip addr replace {ls['ip']}/{ls['prefix']} dev {ifn}; "
                         f"ip route replace default via {ls['gateway']} dev {ifn}")
        if real and leases:
            pending = {(ls["client"], ls["iface"]): ls["ip"] for ls in leases if ls["client"] in self.net}
            t0 = time.time()
            while pending and time.time() - t0 < 20:
                for (c, ifn), ip in list(pending.items()):
                    if f"inet {ip}/" in self.net.get(c).cmd(f"ip -4 -o addr show dev {ifn}"):
                        pending.pop((c, ifn))
                if pending:
                    time.sleep(0.3)
            got = len(leases) - len(pending)
            self.bus.log("ok" if not pending else "warn",
                         f"DHCP: {got}/{len(leases)} mijoz manzil oldi ({time.time() - t0:.1f} s)"
                         + (f"; olmadi: {', '.join(f'{c}/{i}' for c, i in pending)}" if pending else ""))

    def dhcp_leases(self, name):
        """Server lizinglari (dnsmasq lease fayli): [[MAC, IP, mijoz, muddat]]."""
        path = os.path.join(RUN_DIR, "dhcp", f"{name}.leases")
        rows = []
        try:
            with open(path) as f:
                for line in f:
                    v = line.split()
                    if len(v) >= 4:
                        rows.append([v[1], v[2], v[3], time.strftime("%H:%M:%S", time.localtime(int(v[0])))])
        except OSError:
            pass
        return rows

    @staticmethod
    def _nat_cmds(cfg):
        """NAT (masquerade): config.nat = {"outside": "r1-eth2"} — ichki tarmoqlar shu
        interfeys IP'si ostida chiqadi (iptables, router namespace'ida)."""
        cmds = ["iptables -t nat -F POSTROUTING >/dev/null 2>&1 || true"]
        out = (cfg.get("nat") or {}).get("outside")
        if out:
            cmds.append(f"iptables -t nat -A POSTROUTING -o {out} -j MASQUERADE")
        return cmds

    def _apply_vlans(self, name):
        """Switch portlari VLAN rejimi -> OVS (tag / trunks / vlan_mode)."""
        d = self.project["devices"][name]
        if d["type"] not in VLAN_TYPES:
            return
        node = self.net.get(name)
        cmds = []
        for port in sorted(link_ports(self.project, name)):
            ifn = ifname(name, port)
            pc = switch_port_cfg(d, port)
            cmds.append(f"ovs-vsctl clear port {ifn} tag trunks vlan_mode")
            if pc["mode"] == "access":
                cmds.append(f"ovs-vsctl set port {ifn} vlan_mode=access tag={pc['vlan']}")
            elif pc["mode"] == "trunk":
                allowed = sorted(pc["allowed"] or [])
                if pc["native"]:
                    tr = sorted(set(allowed) | {pc["native"]}) if allowed else []
                    cmds.append(f"ovs-vsctl set port {ifn} vlan_mode=native-untagged tag={pc['native']}"
                                + (f" trunks={','.join(map(str, tr))}" if tr else ""))
                else:
                    cmds.append(f"ovs-vsctl set port {ifn} vlan_mode=trunk"
                                + (f" trunks={','.join(map(str, allowed))}" if allowed else ""))
        out = node.cmd(" ; ".join(c + " 2>&1" for c in cmds))
        if out.strip():
            self.bus.log("warn", f"VLAN: {out.strip()[-300:]}", name)

    def add_device(self, project, name):
        with self.lock:
            self._require_running()
            self._set_project(project)
            d = self.project["devices"].get(name)
            if not d:
                raise DriverError(f"{name} loyihada yo'q")
            c = (d.get("config") or {}).get("controller")
            if c and c not in self._ctrl_procs:
                self._ctrl_start(c)
            self._add_node(name, d)
            if DEVICE_TYPES[d["type"]]["ns"]:
                # net.build() o'tib bo'lgan — configHosts() bu node uchun chaqirilmaydi
                # (FRRRouter.config() — ip_forward ham shu yerda yoqiladi)
                self.net.get(name).configDefault()
            self._start_device(name, d)
            self._capture_sync()
            self.bus.log("ok", f"{name} qo'shildi", name)
            return {"ok": True}

    def remove_device(self, name):
        with self.lock:
            self._require_running()
            if name not in self.project["devices"]:
                raise DriverError(f"{name} yo'q")
            for port, (lid, _p, _pp) in list(link_ports(self.project, name).items()):
                self._remove_link_locked(lid)
            d = self.project["devices"][name]
            if d["type"] == "router":
                self._frr_stop(name)
            node = self.net.get(name)
            self.net.delNode(node)
            del self.project["devices"][name]
            self._capture_sync()
            self._write_ctrl_configs()
            self.bus.log("info", f"{name} o'chirildi", name)
            return {"ok": True}

    def apply_device_config(self, project, name):
        """IP/gateway/marshrut yoki kontroller sozlamasini jonli qo'llaydi."""
        with self.lock:
            self._require_running()
            old = self.project["devices"].get(name)
            self._set_project(project)
            d = self.project["devices"][name]
            t = d["type"]
            if t in ("host", "server", "router"):
                self._config_ns_node(name)
            elif t == "wifiap":
                for _p, (lid_, _peer, _pp) in link_ports(self.project, name).items():
                    l_ = self.project["links"][lid_]
                    if (l_.get("params") or {}).get("medium") == "wifi" and lid_ in self._links:
                        opts, ok = self._tc_opts(lid_, normalize_link_params(l_.get("params")))
                        for intf in (self._links[lid_].intf1, self._links[lid_].intf2):
                            intf.params.update(opts)
                            intf.config(**opts)
                            intf.ifconfig("up" if ok and l_.get("up") is not False else "down")
            elif t in ("switch", "sdnrouter"):
                node = self.net.get(name)
                self._apply_vlans(name)
                oc = (old or {}).get("config") or {}
                nc = d.get("config") or {}
                if (oc.get("mode"), oc.get("controller"), oc.get("stp")) != (nc.get("mode"), nc.get("controller"), nc.get("stp")):
                    c = nc.get("controller")
                    if c and c not in self._ctrl_procs:
                        self._ctrl_start(c)
                    sdn = t == "sdnrouter" or nc.get("mode") == "sdn"
                    node.cmd(f"ovs-vsctl set-fail-mode {name} {'secure' if sdn else 'standalone'}")
                    node.cmd(f"ovs-vsctl set bridge {name} stp_enable={'true' if (nc.get('stp') and not sdn) else 'false'}")
                    ctrls = self._ctrls_for(d)
                    if ctrls:
                        node.cmd(f"ovs-vsctl set-controller {name} tcp:127.0.0.1:{ctrls[0].port}")
                    else:
                        node.cmd(f"ovs-vsctl del-controller {name}")
                        node.cmd(f"ovs-ofctl -O OpenFlow13 del-flows {name}")
            from lab.services import has_dhcp_clients
            oc_, nc_ = (old or {}).get("config") or {}, d.get("config") or {}
            if oc_.get("dhcp_server") != nc_.get("dhcp_server") or (oc_.get("interfaces") != nc_.get("interfaces")
                                                                    and has_dhcp_clients(self.project)):
                self._dhcp_sync()
            self._write_ctrl_configs()
            self.bus.log("ok", f"{name}: sozlama qo'llandi", name)
            return {"ok": True}

    # ─────────────────────────── kabellar ───────────────────────────
    def _tc_opts(self, lid, p):
        """Kabel parametrlari -> TCLink opsiyalari. Wi-Fi kabeli: tezlik radio modelidan
        (masofa, AP sozlamasi), MAC kechikishi ~1ms + jitter. Qaytaradi: (opts, radio_ok)."""
        opts = {"bw": p["bw"], "delay": p["delay"], "loss": p["loss"] or None, "max_queue_size": p["queue"]}
        if p["jitter"] not in ("0ms", "0us", "0s", "0"):
            opts["jitter"] = p["jitter"]
        ok = True
        if p.get("medium") == "wifi":
            est = wifi_link_estimate(self.project, lid)
            opts.update(bw=max(est["thr_mbps"], 0.1), delay="1ms", jitter="0.5ms", loss=None)
            ok = est["ok"]
        if self._datapath == "user":
            opts["txo"] = False      # TCIntf.config har safar `ethtool -K tx on` qiladi — userspace OVS'da TCP buziladi
        qd = p.get("qdisc") or "fifo"
        if qd == "fq_codel" and not kernel_caps().get("fq_codel"):
            self.bus.log("warn", f"{lid}: kernelda sch_fq_codel yo'q — fifo ishlatiladi")
            qd = "fifo"
        opts["qdisc"] = qd           # har doim (fifo ham) — eski qiymat intf.params'da qolib ketmasin
        if not kernel_caps()["netem"]:
            # sch_netem yo'q: Mininet delay/jitter/loss VA navbat (limit) uchun netem ishlatadi.
            # Navbat — HTB leaf ostida pfifo bilan (NLIntf), qolganlari e'tiborsiz.
            opts["pfifo_limit"] = opts.pop("max_queue_size", None)
            for k in ("delay", "jitter", "loss"):
                opts.pop(k, None)
            if not self._netem_warned:
                self._netem_warned = True
                self.bus.log("warn", "Kernelda netem yo'q: kabellarda tezlik (HTB) va navbat (pfifo) emulyatsiya "
                                     "qilinadi — kechikish, jitter va loss e'tiborsiz qoldiriladi")
        return opts, ok

    def _add_link_obj(self, lid, l):
        from mininet.link import TCLink
        a, b = l["a"], l["b"]
        da, db = self.project["devices"][a["dev"]], self.project["devices"][b["dev"]]
        p = normalize_link_params(l.get("params"))
        opts, radio_ok = self._tc_opts(lid, p)
        if not radio_ok:
            self.bus.log("warn", f"Wi-Fi {lid}: signal yetmaydi — kabel uzilgan holatda", a["dev"])
        kw = dict(port1=a["port"], port2=b["port"],
                  intfName1=ifname(a["dev"], a["port"]), intfName2=ifname(b["dev"], b["port"]))
        if DEVICE_TYPES[da["type"]]["ns"]:
            kw["addr1"] = device_mac(da["id"], a["port"])
        if DEVICE_TYPES[db["type"]]["ns"]:
            kw["addr2"] = device_mac(db["id"], b["port"])
        link = self.net.addLink(self.net.get(a["dev"]), self.net.get(b["dev"]), cls=TCLink,
                                cls1=_nl_intf(), cls2=_nl_intf(), **kw, **opts)
        self._links[lid] = link
        if l.get("up") is False or not radio_ok:
            link.intf1.ifconfig("down")
            link.intf2.ifconfig("down")
        return link

    def add_link(self, project, link_id):
        with self.lock:
            self._require_running()
            self._set_project(project)
            l = self.project["links"].get(link_id)
            if not l:
                raise DriverError(f"kabel {link_id} loyihada yo'q")
            link = self._add_link_obj(link_id, l)
            for side, intf in (("a", link.intf1), ("b", link.intf2)):
                dev = l[side]["dev"]
                self._attach_port(dev, l[side]["port"], intf)
            self._rebuild_intf_map()
            self._write_ctrl_configs()
            self.bus.log("ok", f"Kabel {l['a']['dev']}:{l['a']['port']} <-> {l['b']['dev']}:{l['b']['port']}")
            return {"ok": True}

    def _attach_port(self, dev, port, intf):
        d = self.project["devices"][dev]
        node = self.net.get(dev)
        t = d["type"]
        if t in ("switch", "sdnrouter", "wifiap"):
            node.cmd(f"ovs-vsctl add-port {dev} {intf.name} -- set Interface {intf.name} ofport_request={port}")
            node.cmd(f"ip link set {intf.name} up")
            node.TCReapply(intf)   # OVS port qo'shganda tc qdisc'ni tozalashi mumkin
            self._apply_vlans(dev)
        elif t == "p4switch":
            node.cmd(f"ip link set {intf.name} up")
            self._p4_cli(dev, f"port_add {intf.name} {port}")
            self._p4_mcast(dev)
        else:
            self._config_ns_node(dev)

    def remove_link(self, link_id):
        with self.lock:
            self._require_running()
            self._remove_link_locked(link_id)
            self._rebuild_intf_map()
            self._write_ctrl_configs()
            return {"ok": True}

    def _remove_link_locked(self, link_id):
        l = self.project["links"].get(link_id)
        link = self._links.pop(link_id, None)
        if l and link:
            for side, intf in (("a", link.intf1), ("b", link.intf2)):
                dev = l[side]["dev"]
                t = self.project["devices"][dev]["type"]
                if t in ("switch", "sdnrouter", "wifiap"):
                    _run(["ovs-vsctl", "--if-exists", "del-port", dev, intf.name])
                elif t == "p4switch":
                    self._p4_cli(dev, f"port_remove {l[side]['port']}")
            self.net.delLink(link)
        self.project["links"].pop(link_id, None)
        if l:
            for side in ("a", "b"):
                if self.project["devices"].get(l[side]["dev"], {}).get("type") == "p4switch":
                    self._p4_mcast(l[side]["dev"])

    def update_link(self, link_id, params=None, up=None):
        with self.lock:
            self._require_running()
            link = self._links.get(link_id)
            l = self.project["links"].get(link_id)
            if not link or not l:
                raise DriverError(f"kabel {link_id} topilmadi")
            if params is not None:
                p = normalize_link_params(params)
                opts, _ok = self._tc_opts(link_id, p)
                for intf in (link.intf1, link.intf2):
                    intf.params.update(opts)      # keyingi TCReapply ham yangi qiymatlarni olsin
                    intf.config(**opts)
                l["params"] = p
            if up is not None:
                state = "up" if up else "down"
                link.intf1.ifconfig(state)
                link.intf2.ifconfig(state)
                l["up"] = bool(up)
            self.bus.publish({"type": "link", "link": link_id, "up": l.get("up", True),
                              "params": l.get("params")})
            return {"ok": True, "link": l}

    def wifi_refresh(self, positions):
        """Harakat: qurilma pozitsiyalarini yangilab, ularning Wi-Fi kabellari TCLink
        tezligini radio modelidan qayta o'rnatadi (qamrovdan chiqsa — link down)."""
        with self.lock:
            self._require_running()
            touched = set()
            for dev, (x, y) in positions.items():
                d = self.project["devices"].get(dev)
                if d and (d.get("x"), d.get("y")) != (x, y):
                    d["x"], d["y"] = x, y
                    touched.add(dev)
            for lid, l in self.project["links"].items():
                if (l.get("params") or {}).get("medium") != "wifi" or lid not in self._links:
                    continue
                if l["a"]["dev"] not in touched and l["b"]["dev"] not in touched:
                    continue
                opts, ok = self._tc_opts(lid, normalize_link_params(l.get("params")))
                state = "up" if ok and l.get("up") is not False else "down"
                for intf in (self._links[lid].intf1, self._links[lid].intf2):
                    intf.params.update(opts)
                    intf.config(**opts)
                    intf.ifconfig(state)
        if touched:
            self.bus.publish({"type": "positions", "devices": {n: list(positions[n]) for n in touched}})

    # ─────────────────────────── FRR ───────────────────────────
    def _frr_dirs(self, name):
        return f"/etc/frr/{name}", f"/var/run/frr/{name}"

    def _frr_start(self, name, d):
        cfg = d.get("config") or {}
        node = self.net.get(name)
        etc, run = self._frr_dirs(name)
        os.makedirs(etc, exist_ok=True)
        os.makedirs(run, exist_ok=True)
        daemons = ["zebra", "staticd"] + [x for x in cfg.get("daemons", []) if x in FRR_DAEMONS]
        for dm in daemons:
            with open(os.path.join(etc, f"{dm}.conf"), "w") as f:
                f.write(f"hostname {name}\n")
        with open(os.path.join(etc, "vtysh.conf"), "w") as f:
            f.write("! NetLab\n")
        try:
            for p in (etc, run, *[os.path.join(etc, f) for f in os.listdir(etc)]):
                shutil.chown(p, "frr", "frr")
        except (LookupError, OSError):
            pass
        for dm in daemons:
            exe = os.path.join(FRR_BIN, dm)
            if not os.path.exists(exe):
                self.bus.log("warn", f"{dm} topilmadi ({exe})", name)
                continue
            out = node.cmd(f"{exe} -d -N {name} -F traditional -A 127.0.0.1 -i {run}/{dm}.pid 2>&1")
            if out.strip():
                self.bus.log("warn", f"{dm}: {out.strip()[-300:]}", name)
            if dm == "zebra":
                for _ in range(30):
                    if os.path.exists(f"{run}/zserv.api"):
                        break
                    time.sleep(0.1)
        startup = (cfg.get("startup") or "").strip()
        if startup:
            path = os.path.join(etc, "startup.conf")
            with open(path, "w") as f:
                f.write(startup + "\n")
            time.sleep(0.5)
            out = node.cmd(f"vtysh -N {name} -f {path} 2>&1")
            if "error" in out.lower() or "unknown command" in out.lower():
                self.bus.log("warn", f"startup-config: {out.strip()[-500:]}", name)
        if "ripd" in daemons and cfg.get("rip_timers"):
            tm = rip_timers(d)
            time.sleep(0.3)
            out = node.cmd(f"vtysh -N {name} -c 'conf t' -c 'router rip' "
                           f"-c 'timers basic {tm['update']} {tm['timeout']} {tm['garbage']}' 2>&1")
            if out.strip():
                self.bus.log("warn", f"rip timers: {out.strip()[-300:]}", name)
        if "bgpd" in daemons and cfg.get("bgp"):
            from lab.bgp import frr_config
            path = os.path.join(etc, "bgp-netlab.conf")
            with open(path, "w") as f:
                f.write(frr_config(cfg["bgp"]))
            time.sleep(0.3)
            out = node.cmd(f"vtysh -N {name} -f {path} 2>&1")
            if "error" in out.lower() or "unknown command" in out.lower():
                self.bus.log("warn", f"bgp config: {out.strip()[-400:]}", name)
        self.bus.log("ok", f"FRR ishga tushdi: {', '.join(daemons)}", name)

    def _frr_stop(self, name):
        _etc, run = self._frr_dirs(name)
        if not os.path.isdir(run):
            return
        for f in os.listdir(run):
            if f.endswith(".pid"):
                try:
                    with open(os.path.join(run, f)) as fh:
                        os.kill(int(fh.read().strip()), signal.SIGTERM)
                except (OSError, ValueError):
                    pass

    def frr_running_config(self, name):
        with self.lock:
            self._require_running()
            d = self.project["devices"].get(name)
            if not d or d["type"] != "router":
                raise DriverError(f"{name} FRR router emas")
            out = self.net.get(name).cmd(f"vtysh -N {name} -c 'show running-config' 2>&1")
            lines = out.splitlines()
            # vtysh sarlavhasini olib tashlash ("Building configuration..." va h.k.)
            while lines and (not lines[0].strip() or lines[0].startswith(("Building", "Current configuration"))):
                lines.pop(0)
            return "\n".join(lines).strip()

    # ─────────────────────────── SDN kontrollerlar ───────────────────────────
    def _write_ctrl_configs(self):
        if not self.project:
            return
        for cname, conf in controller_config(self.project).items():
            d = os.path.join(RUN_DIR, "sdn", cname)
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, "config.json.tmp")
            with open(tmp, "w") as f:
                json.dump(conf, f, indent=2)
            os.replace(tmp, os.path.join(d, "config.json"))

    def _ctrl_start(self, cname):
        from mininet.node import RemoteController
        c = self.project["controllers"][cname]
        d = os.path.join(RUN_DIR, "sdn", cname)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "app.py"), "w") as f:
            f.write(c.get("code", ""))
        self._write_ctrl_configs()
        mgr = controller_cmd()
        if not mgr:
            raise DriverError("osken-manager/ryu-manager topilmadi")
        logf = open(os.path.join(d, "controller.log"), "ab")
        env = dict(os.environ, NETLAB_CONFIG=os.path.join(d, "config.json"))
        p = subprocess.Popen([*mgr, "--ofp-listen-host", "127.0.0.1",
                              "--ofp-tcp-listen-port", str(c["port"]), os.path.join(d, "app.py")],
                             stdout=logf, stderr=subprocess.STDOUT, env=env, cwd=d,
                             start_new_session=True)
        logf.close()
        self._ctrl_procs[cname] = p
        if not _wait_port(c["port"], 15):
            self.bus.log("error", f"Kontroller '{cname}' {c['port']}-portda ochilmadi:\n"
                                  f"{self.controller_log(cname)[-600:]}")
        if cname not in self._ctrl_objs and self.net is not None:
            # net.addController — net.stop() uni ham to'xtatadi. Switchlar
            # unga net.start() orqali emas, _start_device'da tanlab ulanadi.
            self._ctrl_objs[cname] = self.net.addController(
                f"c_{cname}", controller=RemoteController, ip="127.0.0.1", port=c["port"])
        self.bus.log("ok", f"Kontroller '{cname}' :{c['port']} ishga tushdi")

    def controller_restart(self, project, cname):
        with self.lock:
            self._require_running()
            self._set_project(project)
            if cname not in self.project.get("controllers", {}):
                raise DriverError(f"kontroller {cname} yo'q")
            p = self._ctrl_procs.pop(cname, None)
            if p:
                self._kill(p)
            self._ctrl_start(cname)
            return {"ok": True, "log": self.controller_log(cname)[-2000:]}

    def controller_log(self, cname):
        path = os.path.join(RUN_DIR, "sdn", cname, "controller.log")
        try:
            with open(path, errors="replace") as f:
                return f.read()[-20000:]
        except OSError:
            return ""

    # ─────────────────────────── P4 ───────────────────────────
    def _p4_cli(self, name, commands):
        d = self.project["devices"][name]
        return _run(["simple_switch_CLI", "--thrift-port", str(THRIFT_BASE + d["id"])],
                    input_text=commands.strip() + "\n", timeout=30)

    def _p4_mcast(self, name):
        """1-multicast guruh = switchning barcha portlari (broadcast/flood uchun)."""
        ports = sorted(link_ports(self.project, name))
        out = self._p4_cli(name, "mc_mgrp_destroy 1\nmc_mgrp_create 1\n"
                                 f"mc_node_create 0 {' '.join(map(str, ports))}")
        m = re.findall(r"handle (\d+)", out)
        if m:
            self._p4_cli(name, f"mc_node_associate 1 {m[-1]}")

    def p4_load(self, project, name, _locked=False):
        def work():
            self._require_running()
            if not _locked:
                self._set_project(project)
            d = self.project["devices"].get(name)
            if not d or d["type"] != "p4switch":
                raise DriverError(f"{name} P4 switch emas")
            cfg = d.get("config") or {}
            prog = cfg.get("program")
            src = (self.project.get("p4_programs") or {}).get(prog)
            if not src:
                raise DriverError(f"{name}: P4 dastur tanlanmagan")
            res = p4_compile_standalone(src, os.path.join(RUN_DIR, "p4", name))
            if not res["ok"]:
                return res
            out = self._p4_cli(name, f"load_new_config_file {res['json_path']}\nswap_configs")
            self._p4_json[name] = res["json_path"]
            self._p4_mcast(name)
            rt = (cfg.get("runtime") or "").strip()
            rt_out = self._p4_cli(name, rt) if rt else ""
            res["log"] = (res["log"] + "\n\n[load]\n" + out + ("\n\n[runtime]\n" + rt_out if rt_out else "")).strip()
            self.bus.log("ok", f"P4 dastur '{prog}' yuklandi", name)
            return res
        if _locked:
            return work()
        with self.lock:
            return work()

    def p4_runtime(self, name, commands):
        with self.lock:
            self._require_running()
            d = self.project["devices"].get(name)
            if not d or d["type"] != "p4switch":
                raise DriverError(f"{name} P4 switch emas")
            return {"ok": True, "output": self._p4_cli(name, commands)}

    # ─────────────────────────── inspect ───────────────────────────
    @staticmethod
    def _table(title, columns, rows, hint=None):
        return {"title": title, "kind": "table", "columns": columns, "rows": rows, "hint": hint}

    @staticmethod
    def _text(title, text):
        return {"title": title, "kind": "text", "text": text or "(bo'sh)"}

    def _ns_json(self, node, cmd):
        out = node.cmd(cmd + " 2>/dev/null")
        try:
            return json.loads(out)
        except ValueError:
            return []

    def inspect(self, name):
        with self.lock:
            self._require_running()
            d = self.project["devices"].get(name)
            if not d:
                raise DriverError(f"{name} yo'q")
            node = self.net.get(name)
            t = d["type"]
            secs = []
            counters = _read_net_dev(node.pid)
            if t in ("host", "server", "router"):
                rows = []
                for i in self._ns_json(node, "ip -j addr"):
                    if i.get("ifname") == "lo":
                        continue
                    c = counters.get(i.get("ifname"), (0,) * 6)
                    ips = ", ".join(f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", [])
                                    if a.get("family") == "inet")
                    rows.append([i.get("ifname"), i.get("operstate"), i.get("address"), ips or "—",
                                 i.get("mtu"), c[1], c[4], c[2] + c[5]])
                secs.append(self._table("Interfeyslar", ["Interfeys", "Holat", "MAC", "IPv4", "MTU",
                                                         "RX pkt", "TX pkt", "Drop"], rows))
                rows = [[r.get("dst"), r.get("gateway", "—"), r.get("dev"), r.get("protocol", ""),
                         r.get("metric", "")] for r in self._ns_json(node, "ip -j route")]
                secs.append(self._table("Routing jadvali (kernel FIB)", ["Manzil", "Gateway", "Interfeys",
                                                                         "Protokol", "Metric"], rows))
                rows = [[n.get("dst"), n.get("lladdr", "—"), n.get("dev"), ",".join(n.get("state", []))]
                        for n in self._ns_json(node, "ip -j neigh")]
                secs.append(self._table("ARP jadvali", ["IP", "MAC", "Interfeys", "Holat"], rows))
                cfg_ = d.get("config") or {}
                if cfg_.get("dhcp_server"):
                    secs.append(self._table("DHCP lizinglari (dnsmasq)", ["MAC", "IP", "Mijoz", "Tugaydi"],
                                            self.dhcp_leases(name)))
                if cfg_.get("nat"):
                    secs.append(self._text("NAT (iptables -t nat)", node.cmd("iptables -t nat -L POSTROUTING -n -v 2>&1")))
                    secs.append(self._text("NAT ulanishlari (conntrack)",
                                           node.cmd("cat /proc/net/nf_conntrack 2>/dev/null | head -30 || "
                                                    "conntrack -L 2>&1 | head -30")))
                if t == "router":
                    secs.append(self._text("FRR: show ip route", node.cmd(f"vtysh -N {name} -c 'show ip route' 2>&1")))
                    daemons = (d.get("config") or {}).get("daemons", [])
                    extra = {"ospfd": "show ip ospf neighbor", "bgpd": "show bgp summary",
                             "ripd": "show ip rip", "isisd": "show isis neighbor",
                             "ospf6d": "show ipv6 ospf6 neighbor", "eigrpd": "show ip eigrp neighbors"}
                    for dm in daemons:
                        if dm in extra:
                            secs.append(self._text(f"FRR: {extra[dm]}",
                                                   node.cmd(f"vtysh -N {name} -c '{extra[dm]}' 2>&1")))
                    if "bgpd" in daemons:
                        secs.append(self._text("FRR: show ip bgp", node.cmd(f"vtysh -N {name} -c 'show ip bgp' 2>&1")))
                qd = []
                for port in sorted(link_ports(self.project, name)):
                    qd.append(_run(["mnexec", "-a", str(node.pid), "tc", "-s", "qdisc", "show",
                                    "dev", ifname(name, port)]))
                secs.append(self._text("Hardware: interfeys navbatlari (tc qdisc)", "\n\n".join(qd)))
            elif t in ("switch", "sdnrouter", "wifiap"):
                rows = []
                for port, (lid, peer, pport) in sorted(link_ports(self.project, name).items()):
                    ifn = ifname(name, port)
                    c = counters.get(ifn, (0,) * 6)
                    rows.append([port, ifn, f"{peer}:{pport}", c[1], c[4], c[0], c[3], c[2] + c[5]])
                secs.append(self._table("Portlar", ["Port", "Interfeys", "Ulangan", "RX pkt", "TX pkt",
                                                     "RX bayt", "TX bayt", "Drop"], rows))
                if t == "wifiap":
                    est_rows = []
                    for p_, (lid_, peer_, _pp) in sorted(link_ports(self.project, name).items()):
                        e_ = wifi_link_estimate(self.project, lid_)
                        if e_:
                            est_rows.append([lid_, peer_, e_["distance_m"], e_["rssi_dbm"], e_["snr_db"],
                                             e_["mcs"] if e_["ok"] else "—", e_["thr_mbps"]])
                    secs.append(self._table("Radio (model): stansiyalar", ["Kabel", "Stansiya", "Masofa m", "RSSI dBm",
                                                                           "SNR dB", "MCS", "Taxminiy Mbit/s"], est_rows,
                                            hint="Mininet'da radio yo'q — TCLink tezligi shu modeldan"))
                if t in ("switch", "wifiap") and (d.get("config") or {}).get("mode") != "sdn":
                    fdb = _run(["ovs-appctl", "fdb/show", name]).splitlines()
                    rows = [l.split() for l in fdb[1:] if l.strip()]
                    secs.append(self._table("MAC jadvali (FDB)", ["Port", "VLAN", "MAC", "Yosh (s)"], rows))
                    if (d.get("config") or {}).get("stp"):
                        secs.append(self._text("STP", _run(["ovs-appctl", "stp/show", name])))
                secs.append(self._text("OpenFlow jadvallari (dump-flows)",
                                       _run(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", name])))
                secs.append(self._text("OpenFlow portlari (hardware)",
                                       _run(["ovs-ofctl", "-O", "OpenFlow13", "dump-ports-desc", name])))
                qd = [_run(["tc", "-s", "qdisc", "show", "dev", ifname(name, p)])
                      for p in sorted(link_ports(self.project, name))]
                secs.append(self._text("Hardware: port navbatlari (tc qdisc)", "\n\n".join(qd)))
                c = (d.get("config") or {}).get("controller")
                if c:
                    secs.append(self._text(f"Kontroller '{c}' logi (oxiri)", self.controller_log(c)[-4000:]))
            elif t == "p4switch":
                jp = self._p4_json.get(name)
                if jp and os.path.exists(jp):
                    pipe = p4_pipeline_from_json(jp)
                    secs.append({"title": "P4 pipeline (hardware)", "kind": "pipeline", "data": pipe})
                    for pl in pipe["pipelines"]:
                        for tb in pl["tables"]:
                            secs.append(self._text(f"Jadval: {tb['name']}",
                                                   self._p4_cli(name, f"table_dump {tb['name']}")))
                else:
                    secs.append(self._text("P4 pipeline", "Dastur yuklanmagan"))
                rows = []
                for port, (lid, peer, pport) in sorted(link_ports(self.project, name).items()):
                    c = counters.get(ifname(name, port), (0,) * 6)
                    rows.append([port, ifname(name, port), f"{peer}:{pport}", c[1], c[4], c[2] + c[5]])
                secs.append(self._table("Portlar", ["Port", "Interfeys", "Ulangan", "RX pkt", "TX pkt", "Drop"], rows))
                for cand in (os.path.join(RUN_DIR, "p4", name, "bmv2.txt"),):
                    if os.path.exists(cand):
                        with open(cand, errors="replace") as f:
                            secs.append(self._text("BMv2 pipeline trace (oxirgi qatorlar)",
                                                   "".join(f.readlines()[-120:])))
                        break
            return {"device": name, "type": t, "sections": secs}

    # ─────────────────────────── terminal ───────────────────────────
    def open_terminal(self, name, shell="default"):
        with self.lock:
            self._require_running()
            d = self.project["devices"].get(name)
            if not d:
                raise DriverError(f"{name} yo'q")
            node = self.net.get(name)
            pid = str(node.pid)
            t = d["type"]
        env = dict(os.environ, TERM="xterm-256color", NETLAB_DEVICE=name)
        if t == "router" and shell in ("default", "vtysh"):
            argv = ["mnexec", "-a", pid, "vtysh", "-N", name]
        elif t == "p4switch" and shell in ("default", "cli"):
            argv = ["simple_switch_CLI", "--thrift-port", str(THRIFT_BASE + d["id"])]
        else:
            rc = self._write_rc(name, t, d)
            argv = ["mnexec", "-a", pid, "bash", "--rcfile", rc, "-i"]
        term = PtyTerminal(argv, env)
        return term

    def _write_rc(self, name, t, d):
        os.makedirs(os.path.join(RUN_DIR, "rc"), exist_ok=True)
        path = os.path.join(RUN_DIR, "rc", f"{name}.bashrc")
        lines = [f"PS1='\\[\\e[1;36m\\]{name}\\[\\e[0m\\]:\\w# '",
                 "alias ll='ls -la'"]
        if t in ("switch", "sdnrouter", "wifiap"):
            lines += [f"alias flows='ovs-ofctl -O OpenFlow13 dump-flows {name}'",
                      f"alias ports='ovs-ofctl -O OpenFlow13 dump-ports {name}'",
                      f"alias fdb='ovs-appctl fdb/show {name}'",
                      f"alias stp='ovs-appctl stp/show {name}'",
                      f"echo 'NetLab {name} (OVS). Buyruqlar: flows, ports, fdb, stp, ovs-ofctl ... {name}'"]
        elif t == "p4switch":
            lines += [f"alias p4cli='simple_switch_CLI --thrift-port {THRIFT_BASE + d['id']}'",
                      f"echo 'NetLab {name} (BMv2). p4cli — runtime CLI'"]
        else:
            lines += [f"echo 'NetLab {name}. ip a, ip r, ping, traceroute, tcpdump, iperf3 ...'"]
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    # ─────────────────────────── capture / stats ───────────────────────────
    def _rebuild_intf_map(self):
        m = {}
        for lid, l in self.project["links"].items():
            m[ifname(l["a"]["dev"], l["a"]["port"])] = (lid, "ab")
            m[ifname(l["b"]["dev"], l["b"]["port"])] = (lid, "ba")
        self._intf_map = m

    def _capture_sync(self):
        """Har namespace qurilma uchun + root ns uchun bitta tcpdump."""
        self._rebuild_intf_map()
        if not _which("tcpdump"):
            return
        want = {"root"} | {n for n, d in self.project["devices"].items() if DEVICE_TYPES[d["type"]]["ns"]}
        for key in list(self._cap_procs):
            if key not in want:
                self._kill(self._cap_procs.pop(key))
        for key in want:
            if key in self._cap_procs and self._cap_procs[key].poll() is None:
                continue
            base = ["tcpdump", "-l", "-n", "-tt", "-i", "any", "-s", "160", "not host 127.0.0.1"]
            if key == "root":
                argv = base
            else:
                argv = ["mnexec", "-a", str(self.net.get(key).pid)] + base
            try:
                p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
            except OSError:
                continue
            self._cap_procs[key] = p
            threading.Thread(target=self._cap_reader, args=(p,), daemon=True).start()

    def _cap_reader(self, proc):
        for line in proc.stdout:
            if self._threads_stop.is_set():
                break
            if not self.capture_on:
                continue
            m = _TCPDUMP_LINE.match(line.strip())
            if not m or m.group(3) != "Out":
                continue
            hit = self._intf_map.get(m.group(2))
            if not hit:
                continue
            lid, direction = hit
            sec = int(time.time())
            s, c = self._pkt_budget.get(lid, (sec, 0))
            if s != sec:
                s, c = sec, 0
            if c >= PKT_RATE_LIMIT:
                self._pkt_budget[lid] = (s, c + 1)
                continue
            self._pkt_budget[lid] = (s, c + 1)
            summary = m.group(4)
            self.bus.publish({"type": "packet", "link": lid, "dir": direction,
                              "proto": classify(summary), "summary": summary[:220],
                              "intf": m.group(2), "ts": float(m.group(1))})

    def _stats_loop(self):
        prev = {}
        while not self._threads_stop.wait(1.0):
            with self.lock:
                if not self.project or not self.net:
                    continue
                items = []
                pids = {}
                for lid, l in self.project["links"].items():
                    try:
                        pa = self.net.get(l["a"]["dev"]).pid
                        pb = self.net.get(l["b"]["dev"]).pid
                    except KeyError:
                        continue
                    items.append((lid, l, pa, pb))
                    pids[pa] = pids[pb] = None
            now = time.time()
            for pid in pids:
                pids[pid] = _read_net_dev(pid)
            qs = QdiscSampler()
            out = {}
            for lid, l, pa, pb in items:
                ia = pids[pa].get(ifname(l["a"]["dev"], l["a"]["port"]))
                ib = pids[pb].get(ifname(l["b"]["dev"], l["b"]["port"]))
                if not ia or not ib:
                    continue
                cur = (now, ia[3], ib[3], ia[4], ib[4], ia[5] + ib[5])
                p = prev.get(lid)
                prev[lid] = cur
                if not p:
                    continue
                dt = max(cur[0] - p[0], 0.001)
                ab = max(cur[1] - p[1], 0) * 8 / dt
                ba = max(cur[2] - p[2], 0) * 8 / dt
                bw = float((l.get("params") or {}).get("bw", 100)) * 1e6
                out[lid] = {"ab_bps": round(ab), "ba_bps": round(ba),
                            "ab_pps": round(max(cur[3] - p[3], 0) / dt, 1),
                            "ba_pps": round(max(cur[4] - p[4], 0) / dt, 1),
                            "util": round(min(max(ab, ba) / bw, 1.0), 4),
                            "drops": cur[5]}
                qa = qs.read(pa).get(ifname(l["a"]["dev"], l["a"]["port"]), (0, 0, 0, 0))
                qb = qs.read(pb).get(ifname(l["b"]["dev"], l["b"]["port"]), (0, 0, 0, 0))
                out[lid].update(queue_ab=qa[0], queue_ba=qb[0], qdrops=qa[2] + qb[2])
            if out:
                self.bus.publish({"type": "stats", "links": out})

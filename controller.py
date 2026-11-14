"""SDN kontroller: os-ken/Ryu ilovasi (CONTROLLER_APP) alohida subprocess
sifatida ishga tushiriladi va boshqariladi."""

import collections
import os
import subprocess
import sys
import threading
import time

from config import CONTROLLER_PORT, DATA_DIR

# CONTROLLER_APP uchta bo'lakka bo'lingan, chunki matn subprocess sifatida
# ishga tushiriladigan alohida Python fayliga yoziladi:
#   HEADER — importlar (raw, o'zgarmas)
#   PATHS  — DATA_DIR'ga bog'liq log yo'llari (config.DATA_DIR interpolatsiya
#            qilinadi — shu bo'lak {}'siz, shuning uchun .format() xavfsiz)
#   BODY   — TransportMonitor klassi (raw; ko'p joyda dict literal {} bor,
#            shuning uchun butun faylni bitta f-string qilish xato qilish
#            xavfini oshiradi — shu sabab faqat kerakli qismini formatlaymiz)
_CONTROLLER_APP_HEADER = r'''
"""Transport Monitor - L2 learning + stats collection."""
try:
    from os_ken.base import app_manager
    from os_ken.controller import ofp_event
    from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from os_ken.ofproto import ofproto_v1_3
    from os_ken.lib.packet import packet, ethernet, arp, ipv4, tcp, udp, icmp
    from os_ken.lib import hub
    BaseApp = getattr(app_manager, "OSKenApp", None) or getattr(app_manager, "RyuApp")
except ImportError:
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from ryu.ofproto import ofproto_v1_3
    from ryu.lib.packet import packet, ethernet, arp, ipv4, tcp, udp, icmp
    from ryu.lib import hub
    BaseApp = app_manager.RyuApp

import json, time, os
'''

_CONTROLLER_APP_PATHS = '''
EVENTS_LOG = "{data_dir}/stats/transport_events.jsonl"
FLOW_STATS_LOG = "{data_dir}/stats/flow_stats.jsonl"
PORT_STATS_LOG = "{data_dir}/stats/port_stats.jsonl"
'''

_CONTROLLER_APP_BODY = r'''
class TransportMonitor(BaseApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self._datapaths = {}
        for p in [EVENTS_LOG, FLOW_STATS_LOG, PORT_STATS_LOG]:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.exists(p):
                os.remove(p)
        self.monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        while True:
            for dpid, dp in list(self._datapaths.items()):
                try:
                    ofp = dp.ofproto; p = dp.ofproto_parser
                    dp.send_msg(p.OFPFlowStatsRequest(dp))
                    dp.send_msg(p.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))
                except Exception:
                    pass
            hub.sleep(5)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath; ofp = dp.ofproto; p = dp.ofproto_parser
        self._datapaths[dp.id] = dp
        match = p.OFPMatch()
        actions = [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=0, match=match, instructions=inst))
        self.logger.info("Switch %s connected", dp.id)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg; dp = msg.datapath; ofp = dp.ofproto; p = dp.ofproto_parser
        in_port = msg.match['in_port']; dpid = dp.id
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if not eth:
            return
        dst = eth.dst; src = eth.src
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Log
        event = {"ts": time.time(), "dpid": dpid, "in_port": in_port, "eth_src": src, "eth_dst": dst}
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt:
            event.update({"ip_src": ip_pkt.src, "ip_dst": ip_pkt.dst, "ip_proto": ip_pkt.proto,
                          "ip_ttl": ip_pkt.ttl, "ip_len": ip_pkt.total_length})
            tcp_pkt = pkt.get_protocol(tcp.tcp)
            if tcp_pkt:
                event.update({"tcp_src": tcp_pkt.src_port, "tcp_dst": tcp_pkt.dst_port,
                              "tcp_flags": tcp_pkt.bits, "tcp_seq": tcp_pkt.seq,
                              "tcp_ack": tcp_pkt.ack, "tcp_win": tcp_pkt.window_size})
            udp_pkt = pkt.get_protocol(udp.udp)
            if udp_pkt:
                event.update({"udp_src": udp_pkt.src_port, "udp_dst": udp_pkt.dst_port,
                              "udp_len": udp_pkt.total_length})
            icmp_pkt = pkt.get_protocol(icmp.icmp)
            if icmp_pkt:
                event.update({"icmp_type": icmp_pkt.type, "icmp_code": icmp_pkt.code})
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            event.update({"arp_op": arp_pkt.opcode, "arp_src_ip": arp_pkt.src_ip, "arp_dst_ip": arp_pkt.dst_ip})
        try:
            with open(EVENTS_LOG, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            self.logger.warning("EVENTS_LOG write failed: %s", e)

        out_port = self.mac_to_port[dpid].get(dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out_port)]
        if out_port != ofp.OFPP_FLOOD and ip_pkt:
            match = p.OFPMatch(eth_type=0x0800, ipv4_src=ip_pkt.src, ipv4_dst=ip_pkt.dst)
            inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            dp.send_msg(p.OFPFlowMod(datapath=dp, priority=10, match=match,
                                      instructions=inst, idle_timeout=30, hard_timeout=120,
                                      buffer_id=msg.buffer_id if msg.buffer_id != ofp.OFP_NO_BUFFER else ofp.OFP_NO_BUFFER))
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                    in_port=in_port, actions=actions, data=data))

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply(self, ev):
        ts = time.time()
        for stat in ev.msg.body:
            try:
                with open(FLOW_STATS_LOG, "a") as f:
                    f.write(json.dumps({"ts": ts, "dpid": ev.msg.datapath.id, "priority": stat.priority,
                            "packets": stat.packet_count, "bytes": stat.byte_count,
                            "duration_sec": stat.duration_sec, "idle_timeout": stat.idle_timeout,
                            "hard_timeout": stat.hard_timeout, "match": str(stat.match)}) + "\n")
            except Exception as e:
                self.logger.warning("FLOW_STATS_LOG write failed: %s", e)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        ts = time.time()
        for stat in ev.msg.body:
            try:
                with open(PORT_STATS_LOG, "a") as f:
                    f.write(json.dumps({"ts": ts, "dpid": ev.msg.datapath.id, "port": stat.port_no,
                            "rx_packets": stat.rx_packets, "tx_packets": stat.tx_packets,
                            "rx_bytes": stat.rx_bytes, "tx_bytes": stat.tx_bytes,
                            "rx_dropped": stat.rx_dropped, "tx_dropped": stat.tx_dropped,
                            "rx_errors": stat.rx_errors, "tx_errors": stat.tx_errors}) + "\n")
            except Exception as e:
                self.logger.warning("PORT_STATS_LOG write failed: %s", e)
'''


def _render_controller_app():
    """CONTROLLER_APP shablonini config.DATA_DIR bilan to'ldirib, yagona
    Python manba matnini qaytaradi (subprocess shu matnni ishga tushiradi)."""
    return (_CONTROLLER_APP_HEADER
            + _CONTROLLER_APP_PATHS.format(data_dir=DATA_DIR)
            + _CONTROLLER_APP_BODY)


def find_pox():
    """POX skripti (pox.py) yo'lini aniqlaydi:
    POX_DIR / POX_PATH muhit o'zgaruvchilari, ~/pox, /home/user/pox, /opt/pox, PATH yoki joriy katalog."""
    import shutil
    env_dir = os.environ.get("POX_DIR") or os.environ.get("POX_PATH")
    if env_dir:
        cand = os.path.join(env_dir, "pox.py") if not env_dir.endswith("pox.py") else env_dir
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    cands = [
        os.path.expanduser("~/pox/pox.py"),
        "/home/user/pox/pox.py",
        "/opt/pox/pox.py",
        "/usr/local/pox/pox.py",
        os.path.abspath("pox/pox.py"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return os.path.abspath(c)
    which_pox = shutil.which("pox.py") or shutil.which("pox")
    if which_pox:
        return os.path.abspath(which_pox)
    return None


def find_ryu():
    """Ryu yoki os-ken mavjudligini aniqlaydi (buyruq yoki python moduli)."""
    import shutil
    cands = [("os_ken.cmd.manager", "osken-manager"), ("ryu.cmd.manager", "ryu-manager")]
    pys = [sys.executable, "python3", "python3.12", "python3.11", "python3.10"]
    for mod, script in cands:
        for py in pys:
            exe = shutil.which(py) if not os.path.isabs(py) else py
            if not exe or not os.path.exists(exe):
                continue
            try:
                r = subprocess.run([exe, "-c", f"import {mod}"], capture_output=True, timeout=5)
                if r.returncode == 0:
                    return [exe, "-m", mod]
            except Exception:
                pass
        which_script = shutil.which(script)
        if which_script:
            return [which_script]
    return None


def check_controller_health(ip="127.0.0.1", port=6653, timeout=2.0):
    """Soket orqali kontrollerning ochiqligini (health) tekshiradi.
    Graceful socket timeouts va resurslarni to'liq tozalashni ta'minlaydi."""
    import socket
    if not ip or not port:
        return False
    try:
        with socket.create_connection((str(ip).strip(), int(port)), timeout=float(timeout)):
            return True
    except (socket.timeout, TimeoutError, ConnectionRefusedError, OSError, ValueError):
        return False


def get_controller_guidance(controller_type="remote"):
    """Berilgan SDN kontroller turi bo'yicha o'rnatish va ishga tushirish qo'llanmasini qaytaradi.
    ONOS, OpenDaylight, Floodlight, POX, Ryu uchun to'liq tavsiyalar."""
    c_type = (controller_type or "remote").lower()
    if c_type == "onos":
        return (
            "    ONOS SDN Controller Yo'riqnomasi:\n"
            "      1. Docker orqali ishga tushirish:\n"
            "         docker run -d --name onos -p 6653:6653 -p 8181:8181 onosproject/onos:latest\n"
            "      2. Web GUI: http://<ip>:8181/onos/ui (login: karaf, parol: karaf)\n"
            "      3. OpenFlow porti: 6653, REST API porti: 8181\n"
            "      4. Standart forwarding ilovasini faollashtirish:\n"
            "         docker exec -it onos /root/onos/bin/onos-app localhost activate org.onosproject.openflow org.onosproject.fwd"
        )
    elif c_type in ("odl", "opendaylight"):
        return (
            "    OpenDaylight (ODL) SDN Controller Yo'riqnomasi:\n"
            "      1. Docker orqali ishga tushirish:\n"
            "         docker run -d --name odl -p 6653:6653 -p 8181:8181 opendaylight/odl:latest\n"
            "      2. RESTCONF: http://<ip>:8181/restconf/ (login: admin, parol: admin)\n"
            "      3. OpenFlow porti: 6653, Karaf boshqaruv porti: 8101"
        )
    elif c_type == "floodlight":
        return (
            "    Floodlight SDN Controller Yo'riqnomasi:\n"
            "      1. Docker orqali ishga tushirish:\n"
            "         docker run -d --name floodlight -p 6653:6653 -p 8080:8080 floodlight/floodlight\n"
            "      2. Web UI / REST: http://<ip>:8080/ui/index.html\n"
            "      3. OpenFlow porti: 6653, REST API porti: 8080"
        )
    elif c_type == "pox":
        return (
            "    POX SDN Controller Yo'riqnomasi:\n"
            "      1. POX omborini klonlash:\n"
            "         git clone -b gar-experimental https://github.com/noxrepo/pox.git ~/pox\n"
            "      2. Muhit o'zgaruvchisi (ixtiyoriy):\n"
            "         export POX_DIR=~/pox\n"
            "      3. Mustaqil sinab ko'rish:\n"
            "         python3 ~/pox/pox.py openflow.of_01 --port=6633 forwarding.l2_learning"
        )
    elif c_type in ("ryu", "os-ken"):
        return (
            "    Ryu / os-ken SDN Controller Yo'riqnomasi:\n"
            "      1. Pip orqali o'rnatish:\n"
            "         pip install os-ken   # yoki: pip install ryu\n"
            "      2. Ishga tushirish:\n"
            "         osken-manager --ofp-tcp-listen-port 6633 ryu.app.simple_switch_13\n"
            "         # yoki: ryu-manager --ofp-tcp-listen-port 6633 ryu.app.simple_switch_13"
        )
    else:
        return (
            "    Tashqi (Remote) SDN Controller Yo'riqnomasi:\n"
            "      1. Masofaviy kontroller (ONOS, ODL, Floodlight, POX, Ryu) ishlayotganini tekshiring.\n"
            "      2. IP manzil va OpenFlow porti (odatda 6653 yoki 6633) to'g'riligiga ishonch hosil qiling.\n"
            "      3. Firewall yoki xavfsizlik guruhi OpenFlow portini bloklamayotganini tekshiring."
        )


def auto_detect_controllers(host="127.0.0.1", timeout=0.8):
    """Tizimda o'rnatilgan va tarmoqda/localhostda faol ishlayotgan SDN kontrollerlarni aniqlaydi.

    Qaytuvchi lug'at:
      - 'installed': tizimda o'rnatilgan kontrollerlar ({'ryu': bool, 'pox': bool, ...})
      - 'listening_ports': faol tekshirilgan standart portlar ({6653: bool, 6633: bool, ...})
      - 'detected': aniqlangan faol kontroller turlari ro'yxati
    """
    results = {
        "installed": {
            "ryu": bool(find_ryu()),
            "pox": bool(find_pox()),
        },
        "listening_ports": {},
        "detected": [],
    }

    # Standart portlarni tekshirish
    check_ports = {
        6653: "OpenFlow standard (ONOS/ODL/Floodlight/Remote)",
        6633: "OpenFlow legacy (POX/Ryu)",
        8181: "REST/Web (ONOS/OpenDaylight)",
        8080: "REST/Web (Floodlight/Ryu)",
    }
    for port, label in check_ports.items():
        is_up = check_controller_health(host, port, timeout=timeout)
        results["listening_ports"][port] = {"open": is_up, "description": label}

    # Standart portlar ochiqligiga qarab taxmin qilish
    if results["listening_ports"].get(6653, {}).get("open"):
        if results["listening_ports"].get(8181, {}).get("open"):
            results["detected"].append("onos/odl")
        elif results["listening_ports"].get(8080, {}).get("open"):
            results["detected"].append("floodlight")
        else:
            results["detected"].append("remote/openflow_6653")

    if results["listening_ports"].get(6633, {}).get("open"):
        results["detected"].append("ryu/pox")

    # Docker tekshiruvi (agar mavjud bo'lsa)
    import shutil
    if shutil.which("docker"):
        try:
            p = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                               capture_output=True, text=True, timeout=2)
            if p.returncode == 0:
                names = p.stdout.lower()
                for cname in ["onos", "odl", "opendaylight", "floodlight"]:
                    if cname in names and cname not in results["detected"]:
                        results["detected"].append(cname)
        except Exception:
            pass

    return results


def _drain_proc(proc, port, name="Controller", ip="127.0.0.1"):
    log_path = os.path.join(DATA_DIR, "stats", "controller.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    tail = collections.deque(maxlen=200)

    def _drain_stdout():
        try:
            with open(log_path, "wb") as logf:
                for line in iter(proc.stdout.readline, b""):
                    tail.append(line)
                    logf.write(line)
                    logf.flush()
        except Exception:
            pass

    drain_thread = threading.Thread(target=_drain_stdout, daemon=True)
    drain_thread.start()

    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                drain_thread.join(timeout=1)
                out = b"".join(tail).decode(errors="replace")
                raise RuntimeError(f"{name} o'chdi (exit={proc.returncode})!\n{out[-500:]}")
            if check_controller_health(ip, port, timeout=1.0) and proc.poll() is None:
                print(f"[{name}] Tayyor ({ip}:{port})!")
                return proc
            time.sleep(1)
        raise RuntimeError(f"{name} 30s ichida tayyor bo'lmadi")
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        drain_thread.join(timeout=1)
        if not drain_thread.is_alive():
            proc.stdout.close()
        raise


def start_controller(controller_type="ryu", ip="127.0.0.1", port=None):
    """Tanlangan SDN kontrollerni ishga tushiradi yoki masofaviy kontroller holatini tekshiradi.

    Qo'llab-quvvatlanadigan controller_type turlari:
      - 'ryu' / 'os-ken': Ryu yoki os-ken OpenFlow 1.3 ilovasi (default port: config.CONTROLLER_PORT yoki 6633)
      - 'pox': POX OpenFlow 1.0 kontrolleri (default port: 6633)
      - 'remote' / 'onos' / 'odl' / 'floodlight': Tashqi kontroller (default port: 6653)
    """
    c_type = (controller_type or "ryu").lower()
    if port is None:
        if c_type in ("ryu", "os-ken", "pox"):
            port = CONTROLLER_PORT
        else:
            port = 6653

    # Masofaviy / tashqi kontrollerlar (ONOS, OpenDaylight, Floodlight, Remote)
    if c_type in ("remote", "onos", "odl", "opendaylight", "floodlight"):
        label = c_type.upper() if c_type != "odl" else "OpenDaylight"
        print(f"[Controller] Masofaviy {label} kontroller ({ip}:{port}) ga ulanish tekshirilmoqda...")
        is_healthy = check_controller_health(ip, port, timeout=2.5)
        if is_healthy:
            print(f"[Controller] Masofaviy {label} kontroller ({ip}:{port}) tayyor va faol!")
        else:
            print(f"[!] OGOHLANTIRISH: Masofaviy {label} kontroller ({ip}:{port}) ga ulanib bo'lmadi.")
            print(get_controller_guidance(c_type))
        return None

    # POX kontroller
    if c_type == "pox":
        pox_path = find_pox()
        if not pox_path:
            raise RuntimeError(
                f"POX kontrolleri topilmadi!\n{get_controller_guidance('pox')}"
            )
        print(f"[Controller] POX ishga tushirilmoqda ({pox_path}, port={port})...")
        cmd = [sys.executable, pox_path, "openflow.of_01", f"--port={port}", "forwarding.l2_learning"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        _drain_proc(proc, port, "POX", ip=ip)
        return proc

    # Ryu / os-ken kontroller (default)
    ryu_cmd = find_ryu()
    if not ryu_cmd:
        raise RuntimeError(
            f"Ryu/os-ken topilmadi!\n{get_controller_guidance('ryu')}"
        )
    with open("/tmp/transport_monitor.py", "w") as f:
        f.write(_render_controller_app())
    print(f"[Controller] Ryu/os-ken ishga tushirilmoqda (port={port})...")
    # Use the manager/interpreter that discovery actually verified. The manager
    # also performs the framework's normal hub initialization.
    proc = subprocess.Popen([*ryu_cmd, "--ofp-listen-host", ip,
                             "--ofp-tcp-listen-port", str(port), "/tmp/transport_monitor.py"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    _drain_proc(proc, port, "Ryu/os-ken", ip=ip)
    return proc

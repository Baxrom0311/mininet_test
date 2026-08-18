"""SDN kontroller: os-ken/Ryu ilovasi (CONTROLLER_APP) alohida subprocess
sifatida ishga tushiriladi va boshqariladi."""

import subprocess
import sys
import time

from config import CONTROLLER_PORT

CONTROLLER_APP = r'''
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

EVENTS_LOG = "/data/stats/transport_events.jsonl"
FLOW_STATS_LOG = "/data/stats/flow_stats.jsonl"
PORT_STATS_LOG = "/data/stats/port_stats.jsonl"

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
        except Exception:
            pass

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
            except Exception:
                pass

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
            except Exception:
                pass
'''


def start_controller():
    import socket
    with open("/tmp/transport_monitor.py", "w") as f:
        f.write(CONTROLLER_APP)
    with open("/tmp/run_controller.py", "w") as f:
        f.write(f'''#!/usr/bin/env python3
import sys
sys.path.insert(0, "/tmp")
from os_ken import cfg
from os_ken.base.app_manager import AppManager
from os_ken.lib import hub
cfg.CONF(["--ofp-listen-host", "127.0.0.1", "--ofp-tcp-listen-port", "{CONTROLLER_PORT}"])
app_mgr = AppManager.get_instance()
app_mgr.load_apps(["transport_monitor"])
contexts = app_mgr.create_contexts()
services = app_mgr.instantiate_apps(**contexts)
hub.joinall(services)
''')
    print("[Controller] os-ken ishga tushirilmoqda...")
    proc = subprocess.Popen(["python3", "/tmp/run_controller.py"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            s = socket.socket(); s.settimeout(1)
            s.connect(("127.0.0.1", CONTROLLER_PORT)); s.close()
            print("[Controller] Tayyor!")
            return proc
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(1)
            if proc.poll() is not None:
                out = proc.stdout.read().decode() if proc.stdout else ""
                print(f"XATO: Controller o'chdi!\n{out[-500:]}")
                sys.exit(1)
    print("XATO: Controller 30s ichida tayyor bo'lmadi")
    proc.terminate(); sys.exit(1)

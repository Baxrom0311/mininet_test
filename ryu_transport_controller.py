#!/usr/bin/env python3
"""Ryu/os-ken controller that learns L2 paths and logs transport metadata."""

from __future__ import annotations

import json
import time
from pathlib import Path

try:
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from ryu.lib import hub
    from ryu.lib.packet import arp, ethernet, ether_types, icmp, ipv4, packet, tcp, udp
    from ryu.ofproto import ofproto_v1_3
except ImportError:
    from os_ken.base import app_manager
    from os_ken.controller import ofp_event
    from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from os_ken.lib import hub
    from os_ken.lib.packet import arp, ethernet, ether_types, icmp, ipv4, packet, tcp, udp
    from os_ken.ofproto import ofproto_v1_3


EVENT_LOG = Path("/tmp/ryu_transport_events.jsonl")
FLOW_LOG = Path("/tmp/ryu_flow_stats.jsonl")


class TransportMonitor(app_manager.RyuApp):
    """OpenFlow 1.3 learning switch with lightweight L4 observability."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.datapaths[datapath.id] = datapath

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, priority=0, match=match, actions=actions)

    def add_flow(self, datapath, priority, match, actions, idle_timeout=30, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=instructions,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
        )
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER])
    def state_change_handler(self, ev):
        self.datapaths[ev.datapath.id] = ev.datapath

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port
        out_port = self.mac_to_port[dpid].get(eth.dst, ofproto.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        tcp_pkt = pkt.get_protocol(tcp.tcp)
        udp_pkt = pkt.get_protocol(udp.udp)
        icmp_pkt = pkt.get_protocol(icmp.icmp)
        arp_pkt = pkt.get_protocol(arp.arp)

        if ip_pkt and out_port != ofproto.OFPP_FLOOD:
            match = self._build_ip_match(parser, in_port, eth, ip_pkt, tcp_pkt, udp_pkt, icmp_pkt)
            self.add_flow(datapath, priority=10, match=match, actions=actions, idle_timeout=60)
        elif out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst, eth_src=eth.src)
            self.add_flow(datapath, priority=1, match=match, actions=actions, idle_timeout=30)

        if ip_pkt:
            self._log_transport_event(dpid, in_port, out_port, eth, ip_pkt, tcp_pkt, udp_pkt, icmp_pkt)
        elif arp_pkt:
            self._write_json(
                EVENT_LOG,
                {
                    "ts": time.time(),
                    "dpid": dpid,
                    "in_port": in_port,
                    "out_port": out_port,
                    "protocol": "ARP",
                    "src_mac": eth.src,
                    "dst_mac": eth.dst,
                    "src_ip": arp_pkt.src_ip,
                    "dst_ip": arp_pkt.dst_ip,
                },
            )

        data = None if msg.buffer_id != ofproto.OFP_NO_BUFFER else msg.data
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)

    def _build_ip_match(self, parser, in_port, eth, ip_pkt, tcp_pkt, udp_pkt, icmp_pkt):
        base = {
            "in_port": in_port,
            "eth_type": ether_types.ETH_TYPE_IP,
            "eth_src": eth.src,
            "eth_dst": eth.dst,
            "ipv4_src": ip_pkt.src,
            "ipv4_dst": ip_pkt.dst,
            "ip_proto": ip_pkt.proto,
        }
        if tcp_pkt:
            base["tcp_src"] = tcp_pkt.src_port
            base["tcp_dst"] = tcp_pkt.dst_port
        elif udp_pkt:
            base["udp_src"] = udp_pkt.src_port
            base["udp_dst"] = udp_pkt.dst_port
        elif icmp_pkt:
            base["icmpv4_type"] = icmp_pkt.type
            base["icmpv4_code"] = icmp_pkt.code
        return parser.OFPMatch(**base)

    def _log_transport_event(self, dpid, in_port, out_port, eth, ip_pkt, tcp_pkt, udp_pkt, icmp_pkt):
        event = {
            "ts": time.time(),
            "dpid": dpid,
            "in_port": in_port,
            "out_port": out_port,
            "src_mac": eth.src,
            "dst_mac": eth.dst,
            "src_ip": ip_pkt.src,
            "dst_ip": ip_pkt.dst,
            "ip_proto": ip_pkt.proto,
        }

        if tcp_pkt:
            event.update(
                {
                    "protocol": "TCP",
                    "src_port": tcp_pkt.src_port,
                    "dst_port": tcp_pkt.dst_port,
                    "tcp_bits": tcp_pkt.bits,
                    "seq": tcp_pkt.seq,
                    "ack": tcp_pkt.ack,
                }
            )
        elif udp_pkt:
            event.update(
                {
                    "protocol": "UDP",
                    "src_port": udp_pkt.src_port,
                    "dst_port": udp_pkt.dst_port,
                    "udp_total_length": udp_pkt.total_length,
                }
            )
        elif icmp_pkt:
            event.update({"protocol": "ICMP", "icmp_type": icmp_pkt.type, "icmp_code": icmp_pkt.code})
        else:
            event["protocol"] = f"IP-{ip_pkt.proto}"

        self._write_json(EVENT_LOG, event)

    def _monitor(self):
        while True:
            for datapath in list(self.datapaths.values()):
                parser = datapath.ofproto_parser
                request = parser.OFPFlowStatsRequest(datapath)
                datapath.send_msg(request)
            hub.sleep(5)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        datapath = ev.msg.datapath
        for stat in ev.msg.body:
            if stat.priority == 0:
                continue
            self._write_json(
                FLOW_LOG,
                {
                    "ts": time.time(),
                    "dpid": datapath.id,
                    "priority": stat.priority,
                    "packet_count": stat.packet_count,
                    "byte_count": stat.byte_count,
                    "duration_sec": stat.duration_sec,
                    "match": str(stat.match),
                },
            )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

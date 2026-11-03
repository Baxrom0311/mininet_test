"""NetLab SDN ilova: SDN router + L2 switch (OpenFlow 1.3, os-ken/Ryu).

NetLab har bir kontrollerga NETLAB_CONFIG (JSON) beradi:
    {"devices": {"<dpid>": {"name", "type": "sdnrouter"|"switch",
                            "ports": {"<port>": {"ifname", "ip": "10.0.1.1/24", "peer"}},
                            "routes": [{"prefix": "10.9.0.0/16", "via": "10.0.2.2"}]}}}
UI'da IP yoki marshrut o'zgarsa, fayl yangilanadi — ilova uni avtomatik qayta o'qiydi.

"sdnrouter" qurilmalar uchun bu ilova routerning butun "miyasi":
  * o'z interfeys IP'lariga ARP javob beradi, o'z IP'siga ping'ga javob beradi
  * LPM (eng uzun prefiks) bo'yicha ulangan subnet / statik marshrut tanlaydi
  * keyingi hop MAC'ini ARP orqali aniqlaydi (javob kelguncha paket navbatda turadi)
  * flow o'rnatadi: eth_src/eth_dst almashtirish + TTL--, chiqish porti
  * TTL tugasa ICMP Time Exceeded qaytaradi (traceroute ishlaydi)
"switch" (SDN rejimidagi OVS) qurilmalar uchun oddiy L2 learning.
"""
import ipaddress
import json
import os

try:
    from os_ken.base import app_manager
    from os_ken.controller import ofp_event
    from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from os_ken.lib.packet import arp, ethernet, icmp, ipv4, packet
    from os_ken.ofproto import ofproto_v1_3
    BaseApp = getattr(app_manager, "OSKenApp", None) or app_manager.RyuApp
except ImportError:  # Ryu
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from ryu.lib.packet import arp, ethernet, icmp, ipv4, packet
    from ryu.ofproto import ofproto_v1_3
    BaseApp = app_manager.RyuApp

ETH_ARP, ETH_IP = 0x0806, 0x0800


class SDNRouter(BaseApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cfg_path = os.environ.get("NETLAB_CONFIG", "config.json")
        self.cfg_mtime = 0
        self.devices = {}
        self.port_mac = {}      # dpid -> {port: mac}
        self.arp_table = {}     # dpid -> {ip: (mac, port)}
        self.pending = {}       # dpid -> {next_hop_ip: [(msg_data, out_port)]}
        self.mac_to_port = {}   # L2 switchlar uchun
        self.reload()

    # ---------- konfiguratsiya ----------
    def reload(self):
        try:
            m = os.path.getmtime(self.cfg_path)
        except OSError:
            return
        if m == self.cfg_mtime:
            return
        with open(self.cfg_path) as f:
            self.devices = json.load(f).get("devices", {})
        self.cfg_mtime = m
        self.logger.info("config yuklandi: %s qurilma", len(self.devices))

    def dev(self, dpid):
        self.reload()
        return self.devices.get(str(dpid))

    def ifaces(self, dpid):
        """[(port, IPv4Interface)]"""
        d = self.dev(dpid) or {}
        out = []
        for port, info in d.get("ports", {}).items():
            if info.get("ip"):
                try:
                    out.append((int(port), ipaddress.ip_interface(info["ip"])))
                except ValueError:
                    pass
        return out

    def lookup(self, dpid, dst):
        """LPM: (out_port, next_hop_ip) yoki None."""
        best = None
        for port, ifc in self.ifaces(dpid):
            if dst in ifc.network and (best is None or ifc.network.prefixlen > best[0]):
                best = (ifc.network.prefixlen, port, dst)
        for r in (self.dev(dpid) or {}).get("routes", []):
            try:
                net = ipaddress.ip_network(r["prefix"], strict=False)
                via = ipaddress.ip_address(r["via"])
            except (KeyError, ValueError):
                continue
            if dst in net and (best is None or net.prefixlen > best[0]):
                for port, ifc in self.ifaces(dpid):
                    if via in ifc.network:
                        best = (net.prefixlen, port, via)
        return (best[1], best[2]) if best else None

    # ---------- OpenFlow yordamchilari ----------
    def add_flow(self, dp, priority, match, actions, idle=0):
        p = dp.ofproto_parser
        inst = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=priority, match=match,
                                 instructions=inst, idle_timeout=idle))

    def send(self, dp, port, data, in_port=None):
        p, ofp = dp.ofproto_parser, dp.ofproto
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
                                   in_port=in_port if in_port is not None else ofp.OFPP_CONTROLLER,
                                   actions=[p.OFPActionOutput(port)], data=data))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_features(self, ev):
        dp = ev.msg.datapath
        p, ofp = dp.ofproto_parser, dp.ofproto
        self.add_flow(dp, 0, p.OFPMatch(), [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])
        dp.send_msg(p.OFPPortDescStatsRequest(dp, 0))
        d = self.dev(dp.id)
        self.logger.info("ulandi dpid=%s (%s)", dp.id, d["name"] if d else "noma'lum")

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def on_port_desc(self, ev):
        dp = ev.msg.datapath
        self.port_mac[dp.id] = {pd.port_no: pd.hw_addr for pd in ev.msg.body}

    # ---------- paket ishlov berish ----------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def on_packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == 0x88CC:
            return
        d = self.dev(dp.id)
        if d and d.get("type") == "sdnrouter":
            if eth.ethertype == ETH_ARP:
                self.handle_arp(dp, in_port, pkt.get_protocol(arp.arp))
            elif eth.ethertype == ETH_IP:
                self.handle_ip(dp, in_port, msg.data, pkt)
        else:
            self.l2(dp, in_port, msg, eth)

    def l2(self, dp, in_port, msg, eth):
        p, ofp = dp.ofproto_parser, dp.ofproto
        table = self.mac_to_port.setdefault(dp.id, {})
        table[eth.src] = in_port
        out = table.get(eth.dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out)]
        if out != ofp.OFPP_FLOOD:
            self.add_flow(dp, 10, p.OFPMatch(in_port=in_port, eth_dst=eth.dst), actions, idle=60)
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id, in_port=in_port,
                                   actions=actions, data=data))

    def my_ip_on(self, dpid, port):
        for pt, ifc in self.ifaces(dpid):
            if pt == port:
                return ifc
        return None

    def handle_arp(self, dp, in_port, a):
        if a is None:
            return
        src_ip = ipaddress.ip_address(a.src_ip)
        self.arp_table.setdefault(dp.id, {})[src_ip] = (a.src_mac, in_port)
        self.flush_pending(dp, src_ip)
        ifc = self.my_ip_on(dp.id, in_port)
        if a.opcode == arp.ARP_REQUEST and ifc and str(ifc.ip) == a.dst_ip:
            mac = self.port_mac.get(dp.id, {}).get(in_port)
            if not mac:
                return
            r = packet.Packet()
            r.add_protocol(ethernet.ethernet(dst=a.src_mac, src=mac, ethertype=ETH_ARP))
            r.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=mac, src_ip=a.dst_ip,
                                   dst_mac=a.src_mac, dst_ip=a.src_ip))
            r.serialize()
            self.send(dp, in_port, r.data)

    def handle_ip(self, dp, in_port, data, pkt):
        ip = pkt.get_protocol(ipv4.ipv4)
        dst = ipaddress.ip_address(ip.dst)
        # 1) routerning o'ziga
        for port, ifc in self.ifaces(dp.id):
            if ifc.ip == dst:
                ic = pkt.get_protocol(icmp.icmp)
                if ic and ic.type == icmp.ICMP_ECHO_REQUEST:
                    self.icmp_reply(dp, in_port, pkt, ip, icmp.ICMP_ECHO_REPLY, 0, ic.data, src_ip=ip.dst)
                return
        # 2) TTL
        if ip.ttl <= 1:
            ifc = self.my_ip_on(dp.id, in_port)
            if ifc:
                raw = data[14:14 + ip.header_length * 4 + 8]
                self.icmp_reply(dp, in_port, pkt, ip, icmp.ICMP_TIME_EXCEEDED, 0,
                                icmp.TimeExceeded(data_len=len(raw), data=raw), src_ip=str(ifc.ip))
            return
        # 3) marshrut
        r = self.lookup(dp.id, dst)
        if not r:
            self.logger.info("%s: %s uchun marshrut yo'q", dp.id, dst)
            return
        out_port, nh = r
        known = self.arp_table.get(dp.id, {}).get(nh)
        if known:
            self.forward(dp, data, dst, out_port, known[0])
        else:
            self.pending.setdefault(dp.id, {}).setdefault(nh, []).append((data, out_port, dst))
            self.arp_request(dp, out_port, nh)

    def forward(self, dp, data, dst, out_port, nh_mac):
        p = dp.ofproto_parser
        src_mac = self.port_mac.get(dp.id, {}).get(out_port)
        if not src_mac:
            return
        actions = [p.OFPActionSetField(eth_src=src_mac), p.OFPActionSetField(eth_dst=nh_mac),
                   p.OFPActionDecNwTtl(), p.OFPActionOutput(out_port)]
        self.add_flow(dp, 100, p.OFPMatch(eth_type=ETH_IP, ipv4_dst=str(dst)), actions, idle=120)
        ofp = dp.ofproto
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
                                   in_port=ofp.OFPP_CONTROLLER, actions=actions, data=data))

    def flush_pending(self, dp, ip_addr):
        q = self.pending.get(dp.id, {}).pop(ip_addr, [])
        mac = self.arp_table[dp.id][ip_addr][0]
        for data, out_port, dst in q:
            self.forward(dp, data, dst, out_port, mac)

    def arp_request(self, dp, port, target):
        ifc = self.my_ip_on(dp.id, port)
        mac = self.port_mac.get(dp.id, {}).get(port)
        if not ifc or not mac:
            return
        r = packet.Packet()
        r.add_protocol(ethernet.ethernet(dst="ff:ff:ff:ff:ff:ff", src=mac, ethertype=ETH_ARP))
        r.add_protocol(arp.arp(opcode=arp.ARP_REQUEST, src_mac=mac, src_ip=str(ifc.ip),
                               dst_mac="00:00:00:00:00:00", dst_ip=str(target)))
        r.serialize()
        self.send(dp, port, r.data)

    def icmp_reply(self, dp, port, pkt, ip, typ, code, body, src_ip):
        eth = pkt.get_protocol(ethernet.ethernet)
        mac = self.port_mac.get(dp.id, {}).get(port)
        if not mac:
            return
        r = packet.Packet()
        r.add_protocol(ethernet.ethernet(dst=eth.src, src=mac, ethertype=ETH_IP))
        r.add_protocol(ipv4.ipv4(dst=ip.src, src=src_ip, proto=ip.proto if typ == icmp.ICMP_ECHO_REPLY else 1, ttl=64))
        r.add_protocol(icmp.icmp(type_=typ, code=code, csum=0, data=body))
        r.serialize()
        self.send(dp, port, r.data)

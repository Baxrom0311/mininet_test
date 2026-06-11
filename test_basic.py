#!/usr/bin/env python3
"""Minimal test: os-ken controller + 2 switch + 2 host."""

import os, sys, time, subprocess, socket

if os.geteuid() != 0:
    print("sudo python3 test_basic.py")
    sys.exit(1)

os.makedirs("/data/stats", exist_ok=True)

# ── 1. Controller code ──
CTRL = '''
try:
    from os_ken.base import app_manager
    from os_ken.controller import ofp_event
    from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from os_ken.ofproto import ofproto_v1_3
    from os_ken.lib.packet import packet, ethernet
    BaseApp = getattr(app_manager, "OSKenApp", None) or getattr(app_manager, "RyuApp")
except ImportError:
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from ryu.ofproto import ofproto_v1_3
    from ryu.lib.packet import packet, ethernet
    BaseApp = app_manager.RyuApp

class SimpleSwitch(BaseApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.mac_to_port = {}
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def sf(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        p = dp.ofproto_parser
        match = p.OFPMatch()
        actions = [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=0, match=match, instructions=inst))
        self.logger.info("Switch %s connected", dp.id)
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def pi(self, ev):
        msg = ev.msg; dp = msg.datapath; ofp = dp.ofproto; p = dp.ofproto_parser
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if not eth: return
        self.mac_to_port.setdefault(dp.id, {})
        self.mac_to_port[dp.id][eth.src] = in_port
        out_port = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out_port)]
        if out_port != ofp.OFPP_FLOOD:
            match = p.OFPMatch(in_port=in_port, eth_dst=eth.dst, eth_src=eth.src)
            inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            dp.send_msg(p.OFPFlowMod(datapath=dp, priority=1, match=match,
                                      instructions=inst, idle_timeout=60))
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                    in_port=in_port, actions=actions, data=data))
'''

# ── 2. Launcher code ──
LAUNCHER = '''
import sys
sys.path.insert(0, "/tmp")
from os_ken import cfg
from os_ken.base.app_manager import AppManager
from os_ken.lib import hub
cfg.CONF(["--ofp-listen-host", "127.0.0.1", "--ofp-tcp-listen-port", "6633"])
app_mgr = AppManager.get_instance()
app_mgr.load_app("simple_switch")
contexts = app_mgr.create_contexts()
services = list(app_mgr.instantiate_apps(**contexts))
hub.joinall(services)
'''

# ── Write files ──
with open("/tmp/simple_switch.py", "w") as f:
    f.write(CTRL)
with open("/tmp/launch_ctrl.py", "w") as f:
    f.write(LAUNCHER)

# ── Cleanup ──
from mininet.clean import cleanup
cleanup()

# ── Start controller ──
print("[1] Controller ishga tushirilmoqda...")
ctrl = subprocess.Popen(
    ["python3", "/tmp/launch_ctrl.py"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)

for i in range(20):
    try:
        s = socket.socket(); s.settimeout(1)
        s.connect(("127.0.0.1", 6633)); s.close()
        print("[1] Controller tayyor!")
        break
    except (ConnectionRefusedError, socket.timeout, OSError):
        time.sleep(1)
        if ctrl.poll() is not None:
            out = ctrl.stdout.read().decode()
            print(f"Controller XATO:\n{out[-1000:]}")
            sys.exit(1)
else:
    print("Controller 20s da tayyor bo'lmadi")
    ctrl.terminate()
    sys.exit(1)

# ── Mininet ──
print("[2] Mininet yaratilmoqda...")
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.link import TCLink

net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
              autoSetMacs=True, autoStaticArp=True)
net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6633)

s1 = net.addSwitch("s1", protocols="OpenFlow13")
s2 = net.addSwitch("s2", protocols="OpenFlow13")
h1 = net.addHost("h1", ip="10.0.1.1/24")
h2 = net.addHost("h2", ip="10.0.2.1/24")
net.addLink(h1, s1, cls=TCLink, bw=10, delay="5ms")
net.addLink(h2, s2, cls=TCLink, bw=10, delay="5ms")
net.addLink(s1, s2, cls=TCLink, bw=100, delay="2ms")

net.start()
print("[2] Tarmoq ishga tushdi. Kutilmoqda...")
time.sleep(8)

# ── Ping test ──
print("\n[3] Ping test:")
for i in range(3):
    result = h1.cmd("ping -c 1 -W 3 10.0.2.1")
    if "bytes from" in result:
        # Extract RTT
        for line in result.split("\n"):
            if "bytes from" in line:
                print(f"  Urinish {i+1}: OK - {line.strip()}")
                break
        break
    else:
        print(f"  Urinish {i+1}: FAIL")
        time.sleep(2)

# iperf test
print("\n[4] iperf3 test:")
h2.cmd("iperf3 -s -p 5201 -D")
time.sleep(1)
r = h1.cmd("iperf3 -c 10.0.2.1 -p 5201 -t 3 -J")
import json
try:
    j = json.loads(r)
    bps = j["end"]["sum_sent"]["bits_per_second"]
    print(f"  Throughput: {bps/1e6:.1f} Mbps")
except Exception:
    print(f"  iperf result: {r[:200]}")

# OVS flows
print("\n[5] OVS flow'lar:")
print(subprocess.run(["ovs-ofctl", "dump-flows", "s1", "-O", "OpenFlow13"],
      capture_output=True, text=True).stdout)

# Cleanup
net.stop()
ctrl.terminate()
cleanup()
print("\n[DONE]")

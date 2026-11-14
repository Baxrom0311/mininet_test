"""Exercise real os-ken packet/OF1.3 serializers without Mininet or root."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

try:
    from os_ken.lib.packet import ethernet, ipv4, packet
    from os_ken.ofproto import ofproto_v1_3 as ofp
    from os_ken.ofproto import ofproto_v1_3_parser as parser
except ImportError:
    ofp = None


@unittest.skipIf(ofp is None, "os-ken is required for OpenFlow serializer tests")
class ForwardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import controller
        scope = {'__name__': 'transport_monitor_test'}
        exec(compile(controller._render_controller_app(), '<transport-monitor>', 'exec'), scope)
        cls.apps = [('monitor', scope['TransportMonitor'], 'packet_in_handler')]
        for filename, name in [('l2_learning', 'L2Learning'), ('l3_router', 'SDNRouter')]:
            path = Path(__file__).resolve().parents[1] / 'templates' / 'sdn' / f'{filename}.py'
            spec = importlib.util.spec_from_file_location(filename, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.apps.append((filename, getattr(module, name), 'on_packet_in'))

    def app(self, cls):
        app = cls.__new__(cls)
        app.mac_to_port = {}
        app.logger = Mock()
        app.dev = lambda dpid: None  # L2 branch of the router template
        return app

    def datapath(self, dpid=1):
        dp = SimpleNamespace(id=dpid, ofproto=ofp, ofproto_parser=parser, sent=[])
        def send(message):
            message.serialize()  # Exercise OF wire formatting as well as fields.
            dp.sent.append(message)
        dp.send_msg = send
        return dp

    def event(self, dp, src='00:00:00:00:00:01', dst='00:00:00:00:00:02',
              port=1, buffer_id=None, ethertype=0x0800):
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(src=src, dst=dst, ethertype=ethertype))
        if ethertype == 0x0800:
            pkt.add_protocol(ipv4.ipv4(src='192.0.2.1', dst='192.0.2.2', proto=253))
        pkt.add_protocol(b'payload')
        pkt.serialize()
        return SimpleNamespace(msg=SimpleNamespace(datapath=dp, match={'in_port': port},
            buffer_id=ofp.OFP_NO_BUFFER if buffer_id is None else buffer_id, data=bytes(pkt.data)))

    def dispatch(self, app, method, event):
        with patch('builtins.open', mock_open()):
            getattr(app, method)(event)

    def test_buffered_unicast_has_exactly_one_buffer_consumer(self):
        for name, cls, method in self.apps:
            for buffer_id in (0, 17):
                with self.subTest(app=name, buffer_id=buffer_id):
                    app, dp = self.app(cls), self.datapath()
                    app.mac_to_port[dp.id] = {'00:00:00:00:00:02': 2}
                    self.dispatch(app, method, self.event(dp, buffer_id=buffer_id))
                    consumers = [m for m in dp.sent if getattr(m, 'buffer_id', None) == buffer_id]
                    self.assertEqual(len(consumers), 1)
                    self.assertEqual(consumers[0].actions[0].port if isinstance(consumers[0], parser.OFPPacketOut)
                                     else consumers[0].instructions[0].actions[0].port, 2)

    def test_unbuffered_unicast_sends_packet_data_once(self):
        for name, cls, method in self.apps:
            with self.subTest(app=name):
                app, dp = self.app(cls), self.datapath()
                app.mac_to_port[1] = {'00:00:00:00:00:02': 2}
                event = self.event(dp)
                self.dispatch(app, method, event)
                outputs = [m for m in dp.sent if isinstance(m, parser.OFPPacketOut)]
                self.assertEqual(len(outputs), 1)
                self.assertEqual(outputs[0].data, event.msg.data)
                self.assertEqual(outputs[0].actions[0].port, 2)

    def test_learned_flow_is_scoped_to_ingress_and_ethernet_endpoints(self):
        for name, cls, method in self.apps:
            with self.subTest(app=name):
                app, dp = self.app(cls), self.datapath()
                app.mac_to_port[1] = {'00:00:00:00:00:02': 2}
                self.dispatch(app, method, self.event(dp))
                flows = [m for m in dp.sent if isinstance(m, parser.OFPFlowMod)]
                self.assertEqual(len(flows), 1)
                self.assertEqual(flows[0].match['in_port'], 1)
                self.assertEqual(flows[0].match['eth_src'], '00:00:00:00:00:01')
                self.assertEqual(flows[0].match['eth_dst'], '00:00:00:00:00:02')

    def test_unknown_destination_floods_without_installing_flow(self):
        for name, cls, method in self.apps:
            for buffer_id in (None, 0):
                with self.subTest(app=name, buffer_id=buffer_id):
                    app, dp = self.app(cls), self.datapath()
                    self.dispatch(app, method, self.event(dp, buffer_id=buffer_id))
                    self.assertEqual(len(dp.sent), 1)
                    self.assertIsInstance(dp.sent[0], parser.OFPPacketOut)
                    self.assertEqual(dp.sent[0].actions[0].port, ofp.OFPP_FLOOD)
                    self.assertEqual(app.mac_to_port[1]['00:00:00:00:00:01'], 1)

    def test_lldp_is_not_learned_or_forwarded(self):
        for name, cls, method in self.apps:
            with self.subTest(app=name):
                app, dp = self.app(cls), self.datapath()
                self.dispatch(app, method, self.event(dp, ethertype=0x88cc))
                self.assertEqual(dp.sent, [])
                self.assertEqual(app.mac_to_port, {})

    def test_destination_on_ingress_is_filtered(self):
        for name, cls, method in self.apps:
            with self.subTest(app=name):
                app, dp = self.app(cls), self.datapath()
                app.mac_to_port[1] = {'00:00:00:00:00:02': 1}
                self.dispatch(app, method, self.event(dp))
                self.assertEqual(dp.sent, [])

    def test_learning_is_isolated_between_switches(self):
        for name, cls, method in self.apps:
            with self.subTest(app=name):
                app = self.app(cls)
                self.dispatch(app, method, self.event(self.datapath(1), port=3))
                dp = self.datapath(2)
                self.dispatch(app, method, self.event(dp, src='00:00:00:00:00:02', dst='00:00:00:00:00:01'))
                self.assertEqual(dp.sent[-1].actions[0].port, ofp.OFPP_FLOOD)


if __name__ == '__main__':
    unittest.main()

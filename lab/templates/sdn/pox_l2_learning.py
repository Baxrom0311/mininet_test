"""NetLab SDN ilova: POX L2 learning switch (OpenFlow 1.0).

POX kontrolleri uchun L2 learning switch ilovasi.
POX ishga tushganda 'launch()' funksiyasi chaqiriladi va
OpenFlow ConnectionUp hamda PacketIn hodisalarini tinglaydi.
"""
from pox.core import core
import pox.openflow.libopenflow_01 as of

log = core.getLogger()


class LearningSwitch:
    def __init__(self, connection):
        self.connection = connection
        self.mac_to_port = {}
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed:
            log.warning("Chala paket e'tiborsiz qoldirildi")
            return

        in_port = event.port
        src_mac = packet.src
        dst_mac = packet.dst

        # MAC manzilni portga bog'lash (learning)
        self.mac_to_port[src_mac] = in_port

        if dst_mac in self.mac_to_port:
            out_port = self.mac_to_port[dst_mac]
            # Flow o'rnatish (flow mod)
            msg = of.ofp_flow_mod()
            msg.match = of.ofp_match.from_prn(packet)
            msg.idle_timeout = 30
            msg.hard_timeout = 60
            msg.actions.append(of.ofp_action_output(port=out_port))
            msg.data = event.ofp
            self.connection.send(msg)
            log.debug("Flow o'rnatildi: %s -> %s (port %s)", src_mac, dst_mac, out_port)
        else:
            # Agar maqsad MAC noma'lum bo'lsa, flood qilish
            msg = of.ofp_packet_out()
            msg.data = event.ofp
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
            msg.in_port = in_port
            self.connection.send(msg)


def launch():
    """POX modulini ishga tushirish kirish nuqtasi."""
    def start_switch(event):
        log.info("Switch ulandi: dpid=%s", event.dpid)
        LearningSwitch(event.connection)

    core.openflow.addListenerByName("ConnectionUp", start_switch)
    log.info("POX L2 Learning switch moduli faollashtirildi")

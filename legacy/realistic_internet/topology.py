"""
Multi-AS real internet topologiyasi.
5 ta AS: Tier-1, Regional ISP, CDN, Enterprise, Residential ISP.
24 ta OVS switch, ~32 host, har xil link parametrlari.
"""

import json
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI

from realistic_internet.config import (
    AS_DEFINITIONS, INTER_AS_LINKS, LINK_PROFILES, ONOS_OF_PORT,
)


class RealisticInternetTopo(Topo):
    """
    5 AS'li real internet topologiyasi.
    Har xil link turlari: fiber backbone, DSL, LTE, cable, peering.
    """

    def build(self):
        info("*** Topologiya qurilmoqda: 5 AS, 24 switch, ~32 host\n")

        self._switch_to_as = {}
        self._host_to_as = {}
        self._link_info = []

        # ── Har bir AS uchun switchlar va hostlar ──────────
        for as_num, as_def in AS_DEFINITIONS.items():
            as_name = as_def["name"]
            info(f"    AS {as_num} ({as_name}): ")

            # Switchlar
            for sw_name in as_def["switches"]:
                dpid = self._make_dpid(sw_name)
                self.addSwitch(
                    sw_name,
                    dpid=dpid,
                    protocols="OpenFlow13",
                    cls=OVSSwitch,
                )
                self._switch_to_as[sw_name] = as_num

            # Hostlar
            host_count = 0
            for sw_name, host_list in as_def.get("hosts", {}).items():
                for host_name, host_ip in host_list:
                    self.addHost(host_name, ip=host_ip)
                    self._host_to_as[host_name] = as_num

                    # Host -> switch link (lokal, past delay)
                    self.addLink(
                        host_name, sw_name,
                        cls=TCLink,
                        bw=100,
                        delay="0.5ms",
                        loss=0,
                    )
                    host_count += 1

            # AS ichki linklari
            for sw_a, sw_b, profile_name in as_def.get("internal_links", []):
                profile = LINK_PROFILES[profile_name]
                self.addLink(
                    sw_a, sw_b,
                    cls=TCLink,
                    bw=profile["bw"],
                    delay=profile["delay"],
                    loss=profile["loss"],
                    max_queue_size=profile.get("max_queue_size", 200),
                )
                self._link_info.append({
                    "src": sw_a, "dst": sw_b,
                    "profile": profile_name, "type": "intra-AS",
                    "as": as_num,
                })

            info(f"{len(as_def['switches'])} switch, {host_count} host\n")

        # ── AS'lar arasi linklar (peering) ─────────────────
        info("    Inter-AS peering linklari:\n")
        for sw_a, sw_b, profile_name in INTER_AS_LINKS:
            profile = LINK_PROFILES[profile_name]
            self.addLink(
                sw_a, sw_b,
                cls=TCLink,
                bw=profile["bw"],
                delay=profile["delay"],
                loss=profile["loss"],
                max_queue_size=profile.get("max_queue_size", 200),
            )
            as_a = self._switch_to_as.get(sw_a, "?")
            as_b = self._switch_to_as.get(sw_b, "?")
            info(f"      {sw_a}(AS{as_a}) <-> {sw_b}(AS{as_b}): {profile_name}\n")
            self._link_info.append({
                "src": sw_a, "dst": sw_b,
                "profile": profile_name, "type": "inter-AS",
                "as_src": as_a, "as_dst": as_b,
            })

    @staticmethod
    def _make_dpid(sw_name: str) -> str:
        """s1 -> '0000000000000001', s24 -> '0000000000000024'"""
        num = int(sw_name.replace("s", ""))
        return f"{num:016x}"

    def get_topology_metadata(self) -> dict:
        """Topologiya haqida metadata (dataset uchun)."""
        return {
            "total_switches": len(self.switches()),
            "total_hosts": len(self.hosts()),
            "total_links": len(self.links()),
            "as_count": len(AS_DEFINITIONS),
            "switch_to_as": dict(self._switch_to_as),
            "host_to_as": dict(self._host_to_as),
            "link_info": list(self._link_info),
            "as_definitions": {
                k: {"name": v["name"], "type": v["type"]}
                for k, v in AS_DEFINITIONS.items()
            },
        }


def build_network(controller_ip="127.0.0.1", controller_port=ONOS_OF_PORT):
    """Mininet tarmoqni yaratib qaytaradi."""
    setLogLevel("info")

    topo = RealisticInternetTopo()

    net = Mininet(
        topo=topo,
        controller=None,     # tashqi controller (ONOS)
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True,
    )

    # ONOS controllerni qo'shish
    net.addController(
        "onos",
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port,
        protocols="OpenFlow13",
    )

    # Metadata saqlash
    metadata = topo.get_topology_metadata()
    metadata_path = f"/data/stats/topology_metadata.json"
    try:
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
        info(f"*** Topologiya metadata saqlandi: {metadata_path}\n")
    except OSError:
        pass

    return net


def print_topology_summary(net):
    """Topologiya xulosa chiqarish."""
    print("\n" + "=" * 60)
    print("TARMOQ TOPOLOGIYASI XULOSA")
    print("=" * 60)

    for as_num, as_def in AS_DEFINITIONS.items():
        print(f"\n  AS {as_num} - {as_def['name']} ({as_def['description']})")
        print(f"    Switchlar: {', '.join(as_def['switches'])}")
        host_count = sum(len(v) for v in as_def.get("hosts", {}).values())
        print(f"    Hostlar:   {host_count} ta")

    print(f"\n  Inter-AS linklar: {len(INTER_AS_LINKS)} ta")
    for sw_a, sw_b, profile in INTER_AS_LINKS:
        print(f"    {sw_a} <-> {sw_b} ({profile})")

    print(f"\n  Jami: {len(net.switches)} switch, {len(net.hosts)} host")
    print("=" * 60 + "\n")

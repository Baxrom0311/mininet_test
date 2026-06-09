#!/usr/bin/env python3
"""
Small Mininet Python API lab.

Run examples:
  sudo python3 main.py --mode demo
  sudo python3 main.py --mode test
  sudo python3 main.py --mode cli
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.topo import Topo


@dataclass(frozen=True)
class LinkConfig:
    """Bandwidth and delay settings for learning traffic behavior."""

    bandwidth_mbps: int = 10
    delay: str = "5ms"


class LearningTopo(Topo):
    r"""Two-switch, four-host topology.

        h1 ---\
               s1 ===== s2
        h2 ---/          \--- h3
                         \--- h4
    """

    def build(self, link_config: LinkConfig = LinkConfig()) -> None:
        s1 = self.addSwitch("s1")
        s2 = self.addSwitch("s2")

        hosts = {
            "h1": self.addHost("h1", ip="10.0.0.1/24"),
            "h2": self.addHost("h2", ip="10.0.0.2/24"),
            "h3": self.addHost("h3", ip="10.0.0.3/24"),
            "h4": self.addHost("h4", ip="10.0.0.4/24"),
        }

        for host_name in ("h1", "h2"):
            self.addLink(
                hosts[host_name],
                s1,
                bw=link_config.bandwidth_mbps,
                delay=link_config.delay,
            )

        for host_name in ("h3", "h4"):
            self.addLink(
                hosts[host_name],
                s2,
                bw=link_config.bandwidth_mbps,
                delay=link_config.delay,
            )

        self.addLink(
            s1,
            s2,
            bw=link_config.bandwidth_mbps,
            delay=link_config.delay,
        )


def require_root() -> None:
    if os.geteuid() != 0:
        print("Mininet root huquqi bilan ishlaydi: sudo python3 main.py", file=sys.stderr)
        raise SystemExit(1)


def create_network(args: argparse.Namespace) -> Mininet:
    topo = LearningTopo(
        link_config=LinkConfig(
            bandwidth_mbps=args.bandwidth,
            delay=args.delay,
        )
    )
    return Mininet(
        topo=topo,
        link=TCLink,
        switch=OVSSwitch,
        autoSetMacs=True,
        autoStaticArp=True,
    )


def show_network_info(net: Mininet) -> None:
    info("\n*** Host IP manzillari\n")
    for host in net.hosts:
        info(f"{host.name}: {host.IP()}\n")

    info("\n*** Switchlar\n")
    for switch in net.switches:
        info(f"{switch.name}: {', '.join(switch.intfNames())}\n")

    info("\n*** Linklar\n")
    for link in net.links:
        info(f"{link}\n")

    info("\n*** h1 interfeys va route ma'lumotlari\n")
    h1 = net.get("h1")
    info(h1.cmd("ip -brief addr"))
    info(h1.cmd("ip route"))


def run_demo(net: Mininet) -> int:
    show_network_info(net)

    info("\n*** h1 -> h4 ping testi\n")
    h1, h4 = net.get("h1", "h4")
    ping_output = h1.cmd("ping -c 3 10.0.0.4")
    info(ping_output)

    info("\n*** h1 <-> h4 iperf testi\n")
    try:
        result = net.iperf((h1, h4), l4Type="TCP")
        info(f"iperf result: {result}\n")
    except Exception as exc:
        info(f"iperf ishlamadi: {exc}\n")

    return 0 if " 0% packet loss" in ping_output else 1


def run_tests(net: Mininet) -> int:
    info("\n*** pingAll testi\n")
    packet_loss = net.pingAll()
    if packet_loss != 0:
        info(f"\nXato: packet loss {packet_loss}%\n")
        return 1

    info("\n*** h2 -> h3 aniq ping testi\n")
    h2 = net.get("h2")
    output = h2.cmd("ping -c 2 10.0.0.3")
    info(output)
    if " 0% packet loss" not in output:
        return 1

    info("\nBarcha testlar muvaffaqiyatli o'tdi.\n")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mininet Python API uchun oddiy o'quv laboratoriya."
    )
    parser.add_argument(
        "--mode",
        choices=("demo", "test", "cli"),
        default="demo",
        help="demo: avtomatik ko'rsatadi, test: tekshiradi, cli: Mininet CLI ochadi",
    )
    parser.add_argument(
        "--bandwidth",
        type=int,
        default=10,
        help="Har bir link bandwidth qiymati, Mbps",
    )
    parser.add_argument(
        "--delay",
        default="5ms",
        help="Har bir link delay qiymati, masalan: 5ms, 20ms",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_root()
    setLogLevel("info")

    net = create_network(args)
    try:
        info("*** Network ishga tushmoqda\n")
        net.start()

        if args.mode == "cli":
            show_network_info(net)
            info("\n*** CLI buyruqlari: nodes, net, h1 ping h4, iperf h1 h4, exit\n")
            CLI(net)
            return 0

        if args.mode == "test":
            return run_tests(net)

        return run_demo(net)
    finally:
        info("\n*** Network to'xtatilmoqda\n")
        net.stop()


if __name__ == "__main__":
    raise SystemExit(main())

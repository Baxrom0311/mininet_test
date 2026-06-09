#!/usr/bin/env python3
"""Mininet launcher for a 9-switch SDN lab controlled by Ryu/os-ken."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo


ROOT = Path(__file__).resolve().parent
EVENT_LOG = Path("/tmp/ryu_transport_events.jsonl")
FLOW_LOG = Path("/tmp/ryu_flow_stats.jsonl")


class NineSwitchLanTopo(Topo):
    """Nine OpenFlow switches with a small LAN hanging from every switch."""

    def build(self, hosts_per_switch: int = 2, bandwidth: int = 20, delay: str = "2ms") -> None:
        switches = {
            index: self.addSwitch(f"s{index}", protocols="OpenFlow13")
            for index in range(1, 10)
        }

        # Core/aggregation/access shape:
        # s1 is core, s2-s4 are aggregation, s5-s9 are access.
        backbone_links = [
            (1, 2),
            (1, 3),
            (1, 4),
            (2, 5),
            (2, 6),
            (3, 7),
            (3, 8),
            (4, 9),
        ]
        for left, right in backbone_links:
            self.addLink(
                switches[left],
                switches[right],
                cls=TCLink,
                bw=bandwidth,
                delay=delay,
            )

        for switch_index, switch in switches.items():
            for host_index in range(1, hosts_per_switch + 1):
                host_number = ((switch_index - 1) * hosts_per_switch) + host_index
                host = self.addHost(
                    f"h{host_number}",
                    ip=f"10.0.{switch_index}.{host_index}/16",
                    defaultRoute=None,
                )
                self.addLink(host, switch, cls=TCLink, bw=bandwidth, delay=delay)


def require_root() -> None:
    if os.geteuid() != 0:
        print("Mininet root huquqi bilan ishlaydi: sudo python3 main.py", file=sys.stderr)
        raise SystemExit(1)


def find_controller_manager() -> str:
    for command in ("ryu-manager", "osken-manager"):
        path = shutil.which(command)
        if path:
            return path
    print(
        "Ryu/os-ken manager topilmadi. Ubuntu'da o'rnatish: "
        "sudo apt update && sudo apt install -y python3-os-ken",
        file=sys.stderr,
    )
    raise SystemExit(1)


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Controller {host}:{port} vaqtida ishga tushmadi")


def start_controller(args: argparse.Namespace) -> subprocess.Popen[str]:
    manager = find_controller_manager()
    for path in (EVENT_LOG, FLOW_LOG):
        path.unlink(missing_ok=True)

    command = [
        manager,
        "--ofp-tcp-listen-port",
        str(args.controller_port),
        str(ROOT / "ryu_transport_controller.py"),
    ]
    info(f"*** Controller ishga tushmoqda: {' '.join(command)}\n")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_port(args.controller_ip, args.controller_port)
    except RuntimeError:
        output = ""
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
        else:
            process.terminate()
            output, _ = process.communicate(timeout=5)
        if output:
            info("\n*** Controller start xatosi\n")
            info(output[-4000:])
        raise
    return process


def stop_controller(process: subprocess.Popen[str] | None) -> None:
    if not process:
        return
    process.terminate()
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=5)
    if output:
        info("\n*** Controller log\n")
        info(output[-4000:])


def create_network(args: argparse.Namespace) -> Mininet:
    topo = NineSwitchLanTopo(
        hosts_per_switch=args.hosts_per_switch,
        bandwidth=args.bandwidth,
        delay=args.delay,
    )
    return Mininet(
        topo=topo,
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True,
    )


def add_remote_controller(net: Mininet, args: argparse.Namespace) -> None:
    net.addController(
        "c0",
        controller=RemoteController,
        ip=args.controller_ip,
        port=args.controller_port,
        protocols="OpenFlow13",
    )


def show_lab_summary(net: Mininet) -> None:
    info("\n*** Switchlar\n")
    info(" ".join(switch.name for switch in net.switches) + "\n")

    info("\n*** LAN hostlar\n")
    for host in net.hosts:
        info(f"{host.name:<4} {host.IP():<12} {host.defaultIntf()}\n")

    info("\n*** Backbone linklar\n")
    for link in net.links:
        left = link.intf1.node.name
        right = link.intf2.node.name
        if left.startswith("s") and right.startswith("s"):
            info(f"{link}\n")


def start_tcp_sample(net: Mininet) -> None:
    h1 = net.get("h1")
    h18 = net.get("h18")
    h1.cmd("pkill -f 'http.server 8000' || true")
    h1.cmd("python3 -m http.server 8000 >/tmp/h1-http.log 2>&1 &")
    time.sleep(1)
    output = h18.cmd(
        "timeout 6 python3 -c \"import urllib.request; "
        "print(urllib.request.urlopen('http://10.0.1.1:8000', timeout=5).read(80).decode('utf-8', 'ignore'))\""
    )
    info(f"\n*** TCP sample h18 -> h1: {output.strip()}\n")


def start_udp_sample(net: Mininet) -> None:
    h1 = net.get("h1")
    h18 = net.get("h18")
    command = (
        "python3 -c \"import socket; "
        "s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
        "s.sendto(b'mininet-udp-sample', ('10.0.9.2', 5001))\""
    )
    h1.cmd(command)
    # The first UDP packet is enough for controller packet-in metadata.
    h18.cmd("true")
    info("\n*** UDP sample h1 -> h18 yuborildi\n")


def read_json_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def print_transport_events(limit: int = 20) -> None:
    events = read_json_lines(EVENT_LOG)
    info(f"\n*** Transport eventlar: {len(events)} ta\n")
    for event in events[-limit:]:
        info(json.dumps(event, ensure_ascii=False) + "\n")


def run_test(net: Mininet) -> int:
    info("\n*** pingAll testi\n")
    loss = net.pingAll()
    if loss != 0:
        info(f"\nXato: pingAll packet loss {loss}%\n")
        return 1

    start_tcp_sample(net)
    start_udp_sample(net)
    time.sleep(5)
    print_transport_events()

    events = read_json_lines(EVENT_LOG)
    protocols = {event.get("protocol") for event in events}
    if "TCP" not in protocols or "UDP" not in protocols:
        info("\nXato: TCP va UDP transport eventlari to'liq yozilmadi\n")
        return 1

    info("\nBarcha SDN/Ryu testlar muvaffaqiyatli o'tdi.\n")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="9 switchli Ryu/os-ken Mininet lab.")
    parser.add_argument("--mode", choices=("test", "cli"), default="test")
    parser.add_argument("--hosts-per-switch", type=int, default=2)
    parser.add_argument("--bandwidth", type=int, default=20)
    parser.add_argument("--delay", default="2ms")
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6633)
    parser.add_argument(
        "--external-controller",
        action="store_true",
        help="Controller'ni alohida ishga tushirgan bo'lsangiz shu flagni bering.",
    )
    args = parser.parse_args()
    if args.hosts_per_switch < 2:
        parser.error("--hosts-per-switch kamida 2 bo'lishi kerak, test h18 hostidan foydalanadi")
    return args


def main() -> int:
    args = parse_args()
    require_root()
    setLogLevel("info")

    controller_process = None
    net = None
    try:
        if not args.external_controller:
            controller_process = start_controller(args)

        net = create_network(args)
        add_remote_controller(net, args)
        info("*** Mininet ishga tushmoqda\n")
        net.start()
        show_lab_summary(net)

        if args.mode == "cli":
            info("\n*** CLI: nodes, net, h1 ping h18, xterm h1, exit\n")
            info(f"*** Transport log: {EVENT_LOG}\n")
            CLI(net)
            print_transport_events()
            return 0

        return run_test(net)
    finally:
        if net is not None:
            info("\n*** Mininet to'xtatilmoqda\n")
            net.stop()
        stop_controller(controller_process)


if __name__ == "__main__":
    raise SystemExit(main())

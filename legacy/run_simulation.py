#!/usr/bin/env python3
"""
Real Internet Simulyatsiyasi - Asosiy Boshqaruv Skripti
========================================================
ONOS SDN controller + Mininet 5-AS topologiya + real trafik + data collection.

Ishlatish:
    sudo python3 run_simulation.py                    # 5 daqiqa test
    sudo python3 run_simulation.py --duration 1800    # 30 daqiqa
    sudo python3 run_simulation.py --cli              # interaktiv CLI
    sudo python3 run_simulation.py --dataset-only     # faqat dataset yaratish
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time


def check_root():
    if os.geteuid() != 0:
        print("XATO: root huquqi kerak. Ishlatish: sudo python3 run_simulation.py")
        sys.exit(1)


def check_dependencies():
    """Zarur dasturlar o'rnatilganini tekshirish."""
    missing = []
    for cmd in ["docker", "ovs-vsctl", "iperf3", "tcpdump"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            missing.append(cmd)

    if missing:
        print(f"XATO: Topilmagan dasturlar: {', '.join(missing)}")
        print("Avval o'rnating: sudo bash install.sh")
        sys.exit(1)

    # Docker ishlayaptimi?
    r = subprocess.run(["docker", "info"], capture_output=True)
    if r.returncode != 0:
        print("XATO: Docker ishlamayapti. Ishga tushiring: sudo systemctl start docker")
        sys.exit(1)

    # ONOS image bormi?
    r = subprocess.run(
        ["docker", "images", "-q", "onosproject/onos:2.7.0"],
        capture_output=True, text=True,
    )
    if not r.stdout.strip():
        print("XATO: ONOS Docker image topilmadi.")
        print("Yuklab oling: docker pull onosproject/onos:2.7.0")
        sys.exit(1)


def cleanup_previous():
    """Oldingi Mininet va OVS sesiyalarni tozalash."""
    print("[Cleanup] Oldingi sesiyalar tozalanmoqda...")
    subprocess.run(["mn", "-c"], capture_output=True)
    # Eskirgan OVS bridge'larni o'chirish
    r = subprocess.run(["ovs-vsctl", "list-br"], capture_output=True, text=True)
    for br in r.stdout.strip().split("\n"):
        if br:
            subprocess.run(["ovs-vsctl", "--if-exists", "del-br", br], capture_output=True)


def main():
    parser = argparse.ArgumentParser(
        description="Real Internet Simulyatsiyasi (ONOS + Mininet)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--duration", type=int, default=300,
        help="Simulyatsiya davomiyligi sekundlarda (default: 300 = 5 min)",
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="Topologiya yaratilgandan keyin Mininet CLI ochish",
    )
    parser.add_argument(
        "--dataset-only", action="store_true",
        help="Faqat mavjud datalardan dataset yaratish (tarmoq ishga tushirilmaydi)",
    )
    parser.add_argument(
        "--no-traffic", action="store_true",
        help="Trafik generatsiyasiz (faqat topologiya + ONOS)",
    )
    parser.add_argument(
        "--no-impairments", action="store_true",
        help="Dinamik buzilishlarsiz",
    )
    parser.add_argument(
        "--controller-ip", default="127.0.0.1",
        help="ONOS controller IP (default: 127.0.0.1)",
    )
    args = parser.parse_args()

    # ── Faqat dataset yaratish ────────────────────────────
    if args.dataset_only:
        from realistic_internet.dataset_builder import DatasetBuilder
        builder = DatasetBuilder()
        builder.build_all()
        return

    # ── Tekshiruvlar ──────────────────────────────────────
    check_root()
    check_dependencies()

    # ── Import ────────────────────────────────────────────
    from mininet.cli import CLI
    from mininet.log import setLogLevel

    from realistic_internet.onos_manager import ONOSManager
    from realistic_internet.topology import build_network, print_topology_summary
    from realistic_internet.traffic_generator import TrafficOrchestrator
    from realistic_internet.data_collector import DataCollector
    from realistic_internet.impairments import ImpairmentManager
    from realistic_internet.dataset_builder import DatasetBuilder

    setLogLevel("info")

    # ── Data kataloglarni tozalash ────────────────────────
    for d in ["/data/pcap", "/data/stats", "/data/flows", "/data/onos_logs", "/data/sflow_data"]:
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if os.path.isfile(fp):
                os.remove(fp)

    # ── ONOS ──────────────────────────────────────────────
    onos = ONOSManager()
    net = None

    # Signal handler (Ctrl+C uchun)
    def signal_handler(sig, frame):
        print("\n\n[!] To'xtatilmoqda (Ctrl+C)...")
        if net:
            try:
                net.stop()
            except Exception:
                pass
        onos.stop()
        cleanup_previous()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # 1. Eski sesiyalarni tozalash
        cleanup_previous()

        # 2. ONOS ishga tushirish
        print("\n" + "=" * 60)
        print("  REAL INTERNET SIMULYATSIYASI")
        print("  ONOS + Mininet + 5 AS + Real Trafik")
        print("=" * 60 + "\n")

        onos.start()
        if not onos.wait_ready(timeout=120):
            print("XATO: ONOS ishga tushmadi!")
            onos.stop()
            sys.exit(1)

        onos.activate_apps()

        # 3. Tarmoq topologiyasi
        print("\n[Network] Topologiya yaratilmoqda...")
        net = build_network(
            controller_ip=args.controller_ip,
            controller_port=6653,
        )
        net.start()

        # 4. ONOS topologiyani aniqlashini kutish
        if not onos.wait_topology(expected_devices=24, expected_links=30, timeout=90):
            print("OGOHLANTIRISH: ONOS to'liq topologiyani aniqlamadi")
            print("Davom etilmoqda...")

        print_topology_summary(net)

        # 5. Impairments
        impairments = ImpairmentManager(net)
        impairments.apply_static()

        # ── CLI rejimi ────────────────────────────────────
        if args.cli:
            print("\n[CLI] Mininet CLI ochilmoqda...")
            print("  Foydali buyruqlar:")
            print("    pingall          - barcha hostlar ping")
            print("    h_res_fib1 ping h_cdn_web1  - ikki host orasida ping")
            print("    net              - tarmoq tuzilmasi")
            print("    links            - barcha linklar")
            print("    exit             - chiqish")
            print()
            CLI(net)

        # ── Avtomatik rejim ───────────────────────────────
        else:
            # 6. Data collection
            collector = DataCollector(onos, net)
            collector.start()

            # 7. Trafik
            traffic = None
            if not args.no_traffic:
                traffic = TrafficOrchestrator(net)
                traffic.start_all()
                time.sleep(2)
                traffic.generate_dns_traffic(duration_sec=args.duration)

            # 8. Dinamik impairments
            if not args.no_impairments:
                impairments.start_dynamic()

            # 9. Simulyatsiya davomiyligi
            minutes = args.duration / 60
            print(f"\n{'=' * 60}")
            print(f"  Simulyatsiya ishlayapti: {minutes:.1f} daqiqa")
            print(f"  Ma'lumot yig'ilmoqda: /data/")
            print(f"  ONOS Web UI: http://<server-ip>:8181/onos/ui")
            print(f"    Login: karaf / karaf")
            print(f"{'=' * 60}\n")

            start_time = time.time()
            try:
                while time.time() - start_time < args.duration:
                    elapsed = time.time() - start_time
                    remaining = args.duration - elapsed
                    print(
                        f"\r  [{elapsed:.0f}s / {args.duration}s] "
                        f"Qoldi: {remaining:.0f}s  ",
                        end="", flush=True,
                    )
                    time.sleep(5)
            except KeyboardInterrupt:
                print("\n  To'xtatilmoqda...")

            # 10. To'xtatish
            print("\n\n[Shutdown] Yig'ishtirish...")
            if traffic:
                traffic.stop_all()
            if not args.no_impairments:
                impairments.stop_dynamic()
            collector.stop()

            # 11. Dataset yaratish
            print("\n[Dataset] Ma'lumotlar qayta ishlanmoqda...")
            builder = DatasetBuilder()
            builder.build_all()

    except Exception as e:
        print(f"\nXATO: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Cleanup
        if net:
            print("\n[Cleanup] Tarmoq to'xtatilmoqda...")
            net.stop()
        cleanup_previous()
        print("[Done] Simulyatsiya tugadi.\n")


if __name__ == "__main__":
    main()

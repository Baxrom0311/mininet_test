#!/usr/bin/env python3
"""
Real Internet Simulation v2
============================
Turli topologiya + routing algoritm + realistik trafik.

Bu fayl faqat orkestrator (CLI + main()) — haqiqiy mantiq alohida
modullarga bo'lingan: topologies.py, routing.py, network_build.py,
traffic_gen.py, impairments.py, collector.py, path_tracer.py,
dataset_builder.py, controller.py, visualize.py, config.py.

Topologiyalar:
    three_as   - 3 AS, 6 switch (kichik, tez)
    five_as    - 5 AS, 9 switch (o'rtacha, realistik)
    datacenter - Fat-tree DC, 6 switch
    campus     - Kampus tarmog'i, 7 switch

Routing (11 ta protokol):
    l2_learn   - L2 MAC learning (reactive flooding)
    rip        - RIP v2 (hop count, max 15 hop, split horizon)
    ospf       - OSPF (link-state, Dijkstra, area-based cost)
    isis       - IS-IS (link-state, wide metric, L1/L2 hierarchy)
    eigrp      - EIGRP (composite metric: BW + delay, DUAL)
    bgp        - BGP (AS-PATH, local-pref, MED, route dampening)
    ecmp       - Equal-Cost Multi-Path (hash-based load balancing)
    spf        - Generic Shortest Path First (Dijkstra)
    policy     - Policy-based AS preference routing
    static     - Static routes (admin-defined)
    hybrid     - Real internet: AS ichida OSPF (IGP) + AS orasida BGP (EGP) + ECMP

Ishlatish:
    sudo python3 light_simulation.py
    sudo python3 light_simulation.py --topology five_as --routing spf --duration 600
    sudo python3 light_simulation.py --topology datacenter --routing ecmp
    sudo python3 light_simulation.py --cli
"""

import argparse
import os
import random
import signal
import sys
import time

from config import DATA_DIR
from collector import Collector
from controller import start_controller
from dataset_builder import build_dataset
from impairments import Impairments
from netutil import graph_diameter
from network_build import NATMonitor, apply_qos_qdiscs, build_topology, setup_nat_gateway
from path_tracer import PathTracer
from topologies import TOPOLOGIES
from traffic_gen import TrafficGen
from visualize import visualize_topology


def main():
    parser = argparse.ArgumentParser(
        description="Real Internet Simulation v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Topologiyalar:
  three_as    3 AS, 6 switch, 11 host  (tez, kichik)
  five_as     5 AS, 9 switch, 11 host  (realistik internet)
  datacenter  Fat-tree DC, 6 switch     (datacenter)
  campus      Kampus, 7 switch          (enterprise)

Routing (11 ta):
  l2_learn    L2 MAC learning (default)
  rip         RIP v2 (hop count, max 15, split horizon)
  ospf        OSPF (link-state, Dijkstra, cost=ref_bw/bw, areas)
  isis        IS-IS (link-state, wide metric, L1/L2 hierarchy)
  eigrp       EIGRP (composite: BW+delay, unequal-cost LB)
  bgp         BGP (AS-PATH, local-pref, MED, communities)
  ecmp        Equal-Cost Multi-Path
  spf         Shortest Path First (Dijkstra)
  policy      Policy-based routing (AS preference)
  static      Static routes (admin-defined)
  hybrid      Real internet: AS ichida OSPF (IGP) + AS orasida BGP (EGP) + ECMP

Misollar:
  sudo python3 light_simulation.py --topology five_as --routing ospf --duration 300
  sudo python3 light_simulation.py --topology three_as --routing bgp
  sudo python3 light_simulation.py --topology datacenter --routing eigrp
  sudo python3 light_simulation.py --topology campus --routing isis
""")
    parser.add_argument("--topology", choices=list(TOPOLOGIES.keys()), default="three_as")
    parser.add_argument("--routing", choices=[
        "l2_learn", "rip", "ospf", "isis", "eigrp", "bgp",
        "ecmp", "spf", "policy", "static", "hybrid"
    ], default="l2_learn")
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG urug'i (reproducibility). Barcha modul global "
                             "`random` modulidan foydalanadi, shuning uchun bitta "
                             "random.seed() barchasini qamrab oladi. Default: 42.")
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--dataset-only", action="store_true")
    parser.add_argument("--no-traffic", action="store_true")
    parser.add_argument("--no-impairments", action="store_true")
    parser.add_argument("--visualize", action="store_true",
                        help="Topologiya PNG rasmini generatsiya qiladi (sudo kerak emas)")
    args = parser.parse_args()

    # RNG urug'ini ENG BOSHIDA o'rnatamiz — tarmoq qurish, thread'lar,
    # PathTracer va boshqa RNG'ga bog'liq tanlovlardan (traffic mix,
    # impairment tanlash, host-juftlik namunasi) OLDIN. Barcha modullar
    # global `random` modulidan foydalanadi, shuning uchun bitta chaqiruv
    # kifoya. Ko'p-thread'li trafik vaqtlanishi baribir to'liq deterministik
    # bo'lmaydi (OS scheduler), lekin RNG-tanlovlar reproducible bo'ladi.
    random.seed(args.seed)

    topo = TOPOLOGIES[args.topology]

    if args.visualize:
        visualize_topology(topo, args.topology, args.routing)
        return

    if args.dataset_only:
        build_dataset(topo, args.routing, seed=args.seed)
        return

    if os.geteuid() != 0:
        print("XATO: sudo python3 light_simulation.py")
        sys.exit(1)

    from mininet.cli import CLI
    from mininet.clean import cleanup

    for d in ["pcap", "stats", "flows", "datasets"]:
        p = f"{DATA_DIR}/{d}"
        os.makedirs(p, exist_ok=True)
        for f in os.listdir(p):
            fp = os.path.join(p, f)
            if os.path.isfile(fp):
                os.remove(fp)

    print("[Cleanup] Oldingi sesiyalar...")
    cleanup()

    ctrl_proc = None
    net = None

    def signal_handler(sig, frame):
        print("\n[!] Ctrl+C")
        if net:
            try: net.stop()
            except Exception: pass
        if ctrl_proc:
            ctrl_proc.terminate()
        cleanup(); sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        n_sw = len(topo["switches"])
        n_h = len(topo["hosts"])
        print(f"\n{'='*55}")
        print(f"  REAL INTERNET SIMULATION v2")
        print(f"  Topology: {args.topology} ({n_sw} sw, {n_h} hosts)")
        print(f"  Routing:  {args.routing}")
        print(f"  Duration: {args.duration}s ({args.duration/60:.1f} min)")
        print(f"{'='*55}\n")

        ctrl_proc = start_controller()

        print(f"\n[Network] {args.topology} topologiya yaratilmoqda...")
        net = build_topology(topo)
        net.start()

        print("[Network] Switch'lar ulanmoqda...")
        time.sleep(3)
        net.waitConnected(timeout=30)

        # STP (loop bo'lsa) — ikkita mustaqil omil convergence vaqtiga
        # ta'sir qiladi, va ikkalasi ham kerak:
        #  1) excess_links — necha marta ortiqcha link bo'lsa, shuncha
        #     mustaqil halqa bor deb hisoblanadi (masalan five_as'da 2 ta
        #     kesishgan halqa bor: s1-s2-s3-s8 va s1-s4-s6-s3 — yadro
        #     switchlarni bo'lishadi). Har qo'shimcha halqa root bridge
        #     tomonidan qayta hisoblanishi/bloklanishi kerak bo'lgan
        #     qo'shimcha portlar demakdir.
        #  2) graf diametri (eng uzun eng-qisqa-yo'l, hop sonida) — BPDU
        #     xabarlari topologiya bo'ylab bitta-bitta hop orqali
        #     tarqaladi, shuning uchun "core" switchdan uzoqdagi
        #     switch/host STP holatini diametr oshgani sayin kechroq
        #     bilib oladi. Faqat halqa soniga qarab hisoblash bunga
        #     e'tibor bermaydi — shu sabab core'ga ulangan host ba'zan
        #     STP "tugagach" ham uzoq hostlarni pinglay olmasligi mumkin.
        excess_links = len(topo["links"]) - (len(topo["switches"]) - 1)
        has_loop = excess_links > 0
        if has_loop:
            print("[Network] STP yoqilmoqda...")
            for sw in net.switches:
                sw.cmd(f"ovs-vsctl set bridge {sw.name} stp_enable=true")
            diameter = graph_diameter(topo["switches"], topo["links"])
            # 30s baza (IEEE 802.1D listening+learning, ~2 x forward_delay)
            # + 15s har qo'shimcha mustaqil halqa uchun (qayta hisoblash)
            # + 4s har diametr hop uchun (BPDU'ning core'dan chekka
            #   switchgacha yetib borish + qayta yo'nalish vaqti).
            stp_wait = 30 + 15 * (excess_links - 1) + 4 * diameter
            print(f"[Network] STP convergence ({stp_wait}s, {excess_links} ta halqa, "
                  f"diametr={diameter} hop)...")
            time.sleep(stp_wait)
        else:
            time.sleep(3)

        # Switchlar to'liq barqarorlashgach QoS/NAT sozlanadi — bu ko'plab
        # ketma-ket .cmd() chaqiruvi, tayyor bo'lmagan tarmoqda ishonchsiz.
        print("[Network] QoS navbatlari o'rnatilmoqda...")
        apply_qos_qdiscs(net)
        setup_nat_gateway(net, topo)

        print("\n[Test] pingAll (1)...")
        net.pingAll(timeout="5")
        time.sleep(2)
        print("[Test] pingAll (2)...")
        loss = net.pingAll(timeout="5")
        print(f"  Loss: {loss}%")

        # Topologiya xulosa
        print(f"\n  Switches: {len(net.switches)}, Hosts: {len(net.hosts)}, Links: {len(net.links)}")
        for (s1, s2), p in topo["links"].items():
            print(f"    {s1}<->{s2}: {p['bw']}Mbps {p['delay']} loss={p['loss']}%")

        if args.cli:
            CLI(net)
        else:
            path_tracer = PathTracer(net, topo, args.routing)
            path_tracer.start()
            collector = Collector(net)
            collector.start()
            traffic = None
            if not args.no_traffic:
                traffic = TrafficGen(net, topo)
                traffic.start()
            impairments = None
            if not args.no_impairments:
                impairments = Impairments(net)
                impairments.start()
            nat_monitor = NATMonitor(net, topo)
            nat_monitor.start()

            print(f"\n{'='*55}")
            print(f"  Simulyatsiya: {args.duration/60:.1f} min | {args.topology} | {args.routing}")
            print(f"{'='*55}\n")

            start_time = time.time()
            while time.time() - start_time < args.duration:
                elapsed = time.time() - start_time
                ev = sum(1 for _ in open(f"{DATA_DIR}/stats/transport_events.jsonl")) if os.path.exists(f"{DATA_DIR}/stats/transport_events.jsonl") else 0
                fs = sum(1 for _ in open(f"{DATA_DIR}/stats/flow_stats.jsonl")) if os.path.exists(f"{DATA_DIR}/stats/flow_stats.jsonl") else 0
                pt = sum(1 for _ in open(f"{DATA_DIR}/stats/path_traces.jsonl")) if os.path.exists(f"{DATA_DIR}/stats/path_traces.jsonl") else 0
                print(f"\r  [{elapsed:.0f}s/{args.duration}s] events:{ev} flows:{fs} paths:{pt}   ",
                      end="", flush=True)
                time.sleep(5)

            print("\n\n[Stop]...")
            if traffic: traffic.stop()
            if impairments: impairments.stop()
            nat_monitor.stop()
            path_tracer.stop()
            collector.stop()

    except Exception as e:
        print(f"\nXATO: {e}")
        import traceback; traceback.print_exc()
    finally:
        # Avval barcha background jarayonlarni o'chirish
        if net:
            for h in net.hosts:
                try:
                    h.cmd("killall -9 iperf3 wget curl hping3 2>/dev/null")
                    h.waitOutput(verbose=False)
                except Exception:
                    pass
            try:
                net.stop()
            except Exception:
                pass
        if ctrl_proc:
            ctrl_proc.terminate()
            try: ctrl_proc.wait(timeout=5)
            except Exception: pass
        from mininet.clean import cleanup
        cleanup()

    # Dataset builder — Mininet to'xtagandan keyin (xotira bo'shaydi)
    import gc; gc.collect()
    try:
        build_dataset(topo, args.routing, seed=args.seed)
    except Exception as e:
        print(f"Dataset XATO: {e}")
    print("\n[Done]\n")


if __name__ == "__main__":
    main()

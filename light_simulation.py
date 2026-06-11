#!/usr/bin/env python3
"""
Real Internet Simulation v2
============================
Turli topologiya + routing algoritm + realistik trafik.

Topologiyalar:
    three_as   - 3 AS, 6 switch (kichik, tez)
    five_as    - 5 AS, 9 switch (o'rtacha, realistik)
    datacenter - Fat-tree DC, 6 switch
    campus     - Kampus tarmog'i, 7 switch

Routing:
    l2_learn   - L2 MAC learning (reactive flooding)
    spf        - Shortest Path First (proactive, Dijkstra)
    ecmp       - Equal-Cost Multi-Path (load balancing)
    policy     - BGP-like AS policy routing

Ishlatish:
    sudo python3 light_simulation.py
    sudo python3 light_simulation.py --topology five_as --routing spf --duration 600
    sudo python3 light_simulation.py --topology datacenter --routing ecmp
    sudo python3 light_simulation.py --cli
"""

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
from collections import deque

# ─────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────

DATA_DIR = "/data"
CONTROLLER_PORT = 6633

# ─────────────────────────────────────────────────────────
#  TOPOLOGIYALAR
# ─────────────────────────────────────────────────────────

TOPOLOGIES = {
    # ═══ 1. THREE AS - kichik, 3 AS ═══
    "three_as": {
        "switches": {
            "s1": {"as": 100, "role": "core"},
            "s2": {"as": 100, "role": "border"},
            "s3": {"as": 200, "role": "border"},
            "s4": {"as": 200, "role": "servers"},
            "s5": {"as": 300, "role": "border"},
            "s6": {"as": 300, "role": "access"},
        },
        "hosts": {
            "dns1":   {"switch": "s1", "ip": "10.0.1.1/8", "role": "server"},
            "web1":   {"switch": "s4", "ip": "10.0.4.1/8", "role": "server"},
            "web2":   {"switch": "s4", "ip": "10.0.4.2/8", "role": "server"},
            "vid1":   {"switch": "s4", "ip": "10.0.4.3/8", "role": "server"},
            "api1":   {"switch": "s3", "ip": "10.0.3.1/8", "role": "server"},
            "fib1":   {"switch": "s6", "ip": "10.0.6.1/8", "role": "client"},
            "fib2":   {"switch": "s6", "ip": "10.0.6.2/8", "role": "client"},
            "dsl1":   {"switch": "s6", "ip": "10.0.6.3/8", "role": "client"},
            "lte1":   {"switch": "s5", "ip": "10.0.5.1/8", "role": "client"},
            "lte2":   {"switch": "s5", "ip": "10.0.5.2/8", "role": "client"},
            "cab1":   {"switch": "s5", "ip": "10.0.5.3/8", "role": "client"},
        },
        "links": {
            ("s1","s2"): {"bw": 50,  "delay": "3ms",  "loss": 0.01, "jitter": "1ms",   "queue": 80},
            ("s3","s4"): {"bw": 80,  "delay": "0.5ms","loss": 0.005,"jitter": "0.2ms", "queue": 120},
            ("s5","s6"): {"bw": 20,  "delay": "2ms",  "loss": 0.05, "jitter": "1ms",   "queue": 40},
            ("s2","s3"): {"bw": 40,  "delay": "8ms",  "loss": 0.02, "jitter": "2ms",   "queue": 60},
            ("s2","s5"): {"bw": 30,  "delay": "12ms", "loss": 0.05, "jitter": "3ms",   "queue": 50},
            ("s1","s3"): {"bw": 60,  "delay": "5ms",  "loss": 0.01, "jitter": "1ms",   "queue": 100},
        },
        "access_links": {
            "dns1": {"bw": 50,  "delay": "0.5ms", "loss": 0},
            "web1": {"bw": 80,  "delay": "0.2ms", "loss": 0},
            "web2": {"bw": 80,  "delay": "0.2ms", "loss": 0},
            "vid1": {"bw": 80,  "delay": "0.2ms", "loss": 0},
            "api1": {"bw": 40,  "delay": "0.5ms", "loss": 0},
            "fib1": {"bw": 25,  "delay": "3ms",   "loss": 0.1},
            "fib2": {"bw": 25,  "delay": "4ms",   "loss": 0.15},
            "dsl1": {"bw": 8,   "delay": "18ms",  "loss": 0.5},
            "lte1": {"bw": 10,  "delay": "35ms",  "loss": 1.5},
            "lte2": {"bw": 8,   "delay": "45ms",  "loss": 2.0},
            "cab1": {"bw": 15,  "delay": "12ms",  "loss": 0.3},
        },
    },

    # ═══ 2. FIVE AS - realistik internet ═══
    "five_as": {
        "switches": {
            "s1": {"as": 100, "role": "tier1_core"},      # Tier-1 ISP core
            "s2": {"as": 200, "role": "tier2_border"},     # Tier-2 ISP
            "s3": {"as": 200, "role": "tier2_access"},
            "s4": {"as": 300, "role": "cdn_edge"},         # CDN
            "s5": {"as": 300, "role": "cdn_origin"},
            "s6": {"as": 400, "role": "enterprise_core"},  # Enterprise
            "s7": {"as": 400, "role": "enterprise_access"},
            "s8": {"as": 500, "role": "residential_agg"},  # Residential ISP
            "s9": {"as": 500, "role": "residential_access"},
        },
        "hosts": {
            "root1":  {"switch": "s1", "ip": "10.1.0.1/8",  "role": "server"},  # DNS root
            "isp_gw": {"switch": "s2", "ip": "10.2.0.1/8",  "role": "server"},  # ISP gateway
            "cdn1":   {"switch": "s4", "ip": "10.4.0.1/8",  "role": "server"},  # CDN edge
            "cdn2":   {"switch": "s4", "ip": "10.4.0.2/8",  "role": "server"},
            "origin": {"switch": "s5", "ip": "10.5.0.1/8",  "role": "server"},  # Origin server
            "corp1":  {"switch": "s7", "ip": "10.7.0.1/8",  "role": "client"},  # Enterprise
            "corp2":  {"switch": "s7", "ip": "10.7.0.2/8",  "role": "client"},
            "home1":  {"switch": "s9", "ip": "10.9.0.1/8",  "role": "client"},  # Residential
            "home2":  {"switch": "s9", "ip": "10.9.0.2/8",  "role": "client"},
            "mob1":   {"switch": "s8", "ip": "10.8.0.1/8",  "role": "client"},  # Mobile
            "mob2":   {"switch": "s8", "ip": "10.8.0.2/8",  "role": "client"},
        },
        "links": {
            # Tier-1 to Tier-2
            ("s1","s2"): {"bw": 80,  "delay": "5ms",  "loss": 0.01,  "jitter": "1ms",   "queue": 100},
            # Tier-2 internal
            ("s2","s3"): {"bw": 40,  "delay": "2ms",  "loss": 0.02,  "jitter": "0.5ms", "queue": 60},
            # Tier-1 to CDN (peering)
            ("s1","s4"): {"bw": 60,  "delay": "3ms",  "loss": 0.005, "jitter": "0.5ms", "queue": 80},
            # CDN internal
            ("s4","s5"): {"bw": 80,  "delay": "1ms",  "loss": 0.001, "jitter": "0.1ms", "queue": 150},
            # Tier-2 to Enterprise
            ("s3","s6"): {"bw": 30,  "delay": "8ms",  "loss": 0.03,  "jitter": "2ms",   "queue": 50},
            # Enterprise internal
            ("s6","s7"): {"bw": 50,  "delay": "1ms",  "loss": 0.01,  "jitter": "0.3ms", "queue": 80},
            # Tier-2 to Residential
            ("s3","s8"): {"bw": 25,  "delay": "10ms", "loss": 0.05,  "jitter": "3ms",   "queue": 40},
            # Residential internal
            ("s8","s9"): {"bw": 15,  "delay": "3ms",  "loss": 0.1,   "jitter": "2ms",   "queue": 30},
            # Backup: Enterprise to CDN
            ("s6","s4"): {"bw": 20,  "delay": "15ms", "loss": 0.02,  "jitter": "2ms",   "queue": 35},
            # Backup: Tier-1 to Residential
            ("s1","s8"): {"bw": 20,  "delay": "20ms", "loss": 0.03,  "jitter": "4ms",   "queue": 30},
        },
        "access_links": {
            "root1":  {"bw": 50, "delay": "0.5ms","loss": 0},
            "isp_gw": {"bw": 40, "delay": "1ms",  "loss": 0},
            "cdn1":   {"bw": 80, "delay": "0.2ms","loss": 0},
            "cdn2":   {"bw": 80, "delay": "0.2ms","loss": 0},
            "origin": {"bw": 80, "delay": "0.5ms","loss": 0},
            "corp1":  {"bw": 20, "delay": "2ms",  "loss": 0.05},
            "corp2":  {"bw": 20, "delay": "3ms",  "loss": 0.08},
            "home1":  {"bw": 10, "delay": "15ms", "loss": 0.3},
            "home2":  {"bw": 6,  "delay": "20ms", "loss": 0.8},
            "mob1":   {"bw": 8,  "delay": "40ms", "loss": 2.0},
            "mob2":   {"bw": 5,  "delay": "55ms", "loss": 3.0},
        },
    },

    # ═══ 3. DATACENTER - Fat-tree ═══
    "datacenter": {
        "switches": {
            "s1": {"as": 100, "role": "core1"},
            "s2": {"as": 100, "role": "core2"},
            "s3": {"as": 100, "role": "agg1"},
            "s4": {"as": 100, "role": "agg2"},
            "s5": {"as": 100, "role": "tor1"},   # Top of Rack
            "s6": {"as": 100, "role": "tor2"},
        },
        "hosts": {
            "srv1":  {"switch": "s5", "ip": "10.0.1.1/8", "role": "server"},
            "srv2":  {"switch": "s5", "ip": "10.0.1.2/8", "role": "server"},
            "srv3":  {"switch": "s5", "ip": "10.0.1.3/8", "role": "server"},
            "srv4":  {"switch": "s6", "ip": "10.0.2.1/8", "role": "server"},
            "srv5":  {"switch": "s6", "ip": "10.0.2.2/8", "role": "server"},
            "srv6":  {"switch": "s6", "ip": "10.0.2.3/8", "role": "server"},
            "cli1":  {"switch": "s3", "ip": "10.0.3.1/8", "role": "client"},
            "cli2":  {"switch": "s4", "ip": "10.0.4.1/8", "role": "client"},
        },
        "links": {
            ("s1","s3"): {"bw": 40, "delay": "0.1ms","loss": 0.001,"jitter": "0.02ms","queue": 200},
            ("s1","s4"): {"bw": 40, "delay": "0.1ms","loss": 0.001,"jitter": "0.02ms","queue": 200},
            ("s2","s3"): {"bw": 40, "delay": "0.1ms","loss": 0.001,"jitter": "0.02ms","queue": 200},
            ("s2","s4"): {"bw": 40, "delay": "0.1ms","loss": 0.001,"jitter": "0.02ms","queue": 200},
            ("s3","s5"): {"bw": 20, "delay": "0.05ms","loss":0.001,"jitter": "0.01ms","queue": 100},
            ("s4","s6"): {"bw": 20, "delay": "0.05ms","loss":0.001,"jitter": "0.01ms","queue": 100},
        },
        "access_links": {
            "srv1": {"bw": 10, "delay": "0.02ms","loss": 0},
            "srv2": {"bw": 10, "delay": "0.02ms","loss": 0},
            "srv3": {"bw": 10, "delay": "0.02ms","loss": 0},
            "srv4": {"bw": 10, "delay": "0.02ms","loss": 0},
            "srv5": {"bw": 10, "delay": "0.02ms","loss": 0},
            "srv6": {"bw": 10, "delay": "0.02ms","loss": 0},
            "cli1": {"bw": 10, "delay": "0.5ms", "loss": 0.01},
            "cli2": {"bw": 10, "delay": "0.5ms", "loss": 0.01},
        },
    },

    # ═══ 4. CAMPUS - Universitet/korxona ═══
    "campus": {
        "switches": {
            "s1": {"as": 100, "role": "core"},
            "s2": {"as": 100, "role": "distribution1"},
            "s3": {"as": 100, "role": "distribution2"},
            "s4": {"as": 100, "role": "access_bldg_a"},
            "s5": {"as": 100, "role": "access_bldg_b"},
            "s6": {"as": 100, "role": "dmz"},
            "s7": {"as": 200, "role": "isp_gateway"},
        },
        "hosts": {
            "www":   {"switch": "s6", "ip": "10.0.6.1/8", "role": "server"},
            "mail":  {"switch": "s6", "ip": "10.0.6.2/8", "role": "server"},
            "db":    {"switch": "s1", "ip": "10.0.1.1/8", "role": "server"},
            "pc1":   {"switch": "s4", "ip": "10.0.4.1/8", "role": "client"},
            "pc2":   {"switch": "s4", "ip": "10.0.4.2/8", "role": "client"},
            "pc3":   {"switch": "s5", "ip": "10.0.5.1/8", "role": "client"},
            "pc4":   {"switch": "s5", "ip": "10.0.5.2/8", "role": "client"},
            "wifi1": {"switch": "s4", "ip": "10.0.4.3/8", "role": "client"},
            "wifi2": {"switch": "s5", "ip": "10.0.5.3/8", "role": "client"},
            "inet":  {"switch": "s7", "ip": "10.0.7.1/8", "role": "server"},
        },
        "links": {
            ("s1","s2"): {"bw": 50, "delay": "0.5ms","loss": 0.005,"jitter": "0.1ms","queue": 100},
            ("s1","s3"): {"bw": 50, "delay": "0.5ms","loss": 0.005,"jitter": "0.1ms","queue": 100},
            ("s2","s4"): {"bw": 20, "delay": "1ms",  "loss": 0.01, "jitter": "0.3ms","queue": 50},
            ("s3","s5"): {"bw": 20, "delay": "1ms",  "loss": 0.01, "jitter": "0.3ms","queue": 50},
            ("s1","s6"): {"bw": 30, "delay": "0.2ms","loss": 0.002,"jitter": "0.05ms","queue":80},
            ("s1","s7"): {"bw": 15, "delay": "10ms", "loss": 0.05, "jitter": "3ms",  "queue": 30},
            ("s2","s3"): {"bw": 30, "delay": "0.3ms","loss": 0.003,"jitter": "0.1ms","queue": 60},
        },
        "access_links": {
            "www":  {"bw": 30, "delay": "0.1ms","loss": 0},
            "mail": {"bw": 20, "delay": "0.1ms","loss": 0},
            "db":   {"bw": 50, "delay": "0.1ms","loss": 0},
            "pc1":  {"bw": 10, "delay": "0.5ms","loss": 0.01},
            "pc2":  {"bw": 10, "delay": "0.5ms","loss": 0.01},
            "pc3":  {"bw": 10, "delay": "0.5ms","loss": 0.01},
            "pc4":  {"bw": 10, "delay": "0.5ms","loss": 0.01},
            "wifi1":{"bw": 5,  "delay": "5ms",  "loss": 1.0},
            "wifi2":{"bw": 5,  "delay": "8ms",  "loss": 1.5},
            "inet": {"bw": 15, "delay": "20ms", "loss": 0.1},
        },
    },
}

# Trafik profillari
TRAFFIC_MIX = [
    ("web",     0.25, "tcp", (100, 2000)),
    ("video",   0.18, "udp", (500, 5000)),
    ("dns",     0.08, "udp", (5, 20)),
    ("bulk",    0.08, "tcp", (2000, 8000)),
    ("voip",    0.05, "udp", (50, 80)),
    ("ssh",     0.05, "tcp", (10, 50)),
    ("gaming",  0.04, "udp", (80, 150)),
    ("email",   0.03, "tcp", (30, 150)),
    ("iot",     0.04, "udp", (1, 10)),
    ("https",   0.08, "tcp", (150, 3000)),    # TLS handshake + payload
    ("streaming", 0.05, "tcp", (800, 4000)),  # Adaptive bitrate (ABR)
    ("p2p",     0.03, "tcp", (500, 6000)),    # Peer-to-peer trafik
    ("cloud",   0.04, "tcp", (200, 2500)),    # Cloud API / SaaS
]

# Anomal/xavfli trafik turlari (kam chastotada)
ANOMALY_MIX = [
    ("port_scan",     0.25, "tcp", (1, 5)),     # nmap-like port scan
    ("syn_flood",     0.15, "tcp", (500, 3000)), # DDoS SYN flood
    ("udp_flood",     0.15, "udp", (1000, 8000)),# UDP volumetric attack
    ("dns_amplify",   0.10, "udp", (50, 200)),   # DNS amplification
    ("slowloris",     0.10, "tcp", (1, 3)),       # Slow HTTP attack
    ("ping_sweep",    0.10, "icmp", (1, 5)),      # Network reconnaissance
    ("brute_force",   0.10, "tcp", (5, 20)),      # SSH/HTTP brute force
    ("data_exfil",    0.05, "tcp", (100, 1000)),  # Data exfiltration
]

# Dinamik impairmentlar
IMPAIRMENT_EVENTS = [
    {"name": "congestion",    "prob": 0.25, "delay": (20, 80),  "loss": (2, 8),   "dur": (5, 25)},
    {"name": "micro_burst",   "prob": 0.15, "delay": (40, 150), "loss": (5, 15),  "dur": (1, 5)},
    {"name": "link_degrade",  "prob": 0.15, "delay": (10, 40),  "loss": (1, 5),   "dur": (15, 60)},
    {"name": "link_flap",     "prob": 0.08, "delay": (0, 0),    "loss": (0, 0),   "dur": (2, 8)},
    {"name": "route_change",  "prob": 0.05, "delay": (5, 20),   "loss": (0, 2),   "dur": (3, 10)},
    {"name": "packet_reorder","prob": 0.10, "delay": (0, 0),    "loss": (0, 0),   "dur": (5, 20)},
    {"name": "buffer_bloat",  "prob": 0.10, "delay": (50, 300), "loss": (0, 1),   "dur": (10, 40)},
    {"name": "mtu_blackhole", "prob": 0.04, "delay": (0, 0),    "loss": (0, 0),   "dur": (5, 15)},
    {"name": "duplicate",     "prob": 0.03, "delay": (0, 0),    "loss": (0, 0),   "dur": (3, 10)},
    {"name": "jitter_spike",  "prob": 0.10, "delay": (10, 50),  "loss": (0, 1),   "dur": (5, 15)},
]

# TCP congestion control algoritmlari
TCP_CC_ALGORITHMS = ["cubic", "reno", "bbr"]


# ─────────────────────────────────────────────────────────
#  ROUTING ALGORITHMS
# ─────────────────────────────────────────────────────────

def compute_dijkstra(graph, source, weight_key="delay"):
    """Dijkstra shortest path."""
    import heapq
    dist = {source: 0}
    prev = {}
    pq = [(0, source)]
    visited = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        for v, params in graph.get(u, []):
            w = float(params.get(weight_key, params.get("delay", "1ms")).replace("ms", ""))
            if dist.get(v, float("inf")) > d + w:
                dist[v] = d + w
                prev[v] = u
                heapq.heappush(pq, (d + w, v))
    return dist, prev


def compute_paths(topo, routing_mode):
    """
    Topologiya va routing algoritmiga ko'ra barcha host juftliklari uchun
    switch-level path'larni hisoblash.
    Return: {(src_host, dst_host): [sw1, sw2, ...]}
    """
    # Graph yaratish
    graph = {}
    for (s1, s2), params in topo["links"].items():
        graph.setdefault(s1, []).append((s2, params))
        graph.setdefault(s2, []).append((s1, params))

    # Host -> switch mapping
    host_sw = {h: info["switch"] for h, info in topo["hosts"].items()}

    paths = {}

    if routing_mode == "spf":
        # Shortest Path First (Dijkstra, delay-based weight)
        for sw in topo["switches"]:
            _, prev = compute_dijkstra(graph, sw, "delay")
            for h_name, h_info in topo["hosts"].items():
                target_sw = h_info["switch"]
                if target_sw == sw:
                    continue
                # Reconstruct path
                path = []
                cur = target_sw
                while cur and cur != sw:
                    path.append(cur)
                    cur = prev.get(cur)
                if cur == sw:
                    path.append(sw)
                    path.reverse()
                    # Bu path sw -> target_sw ga boradi
                    for src_h, src_sw in host_sw.items():
                        if src_sw == sw:
                            paths[(src_h, h_name)] = path

    elif routing_mode == "ecmp":
        # Equal-Cost Multi-Path - bir nechta teng yo'l
        # BFS bilan barcha eng qisqa yo'llarni topish
        for src_sw in topo["switches"]:
            all_shortest = _bfs_all_paths(graph, src_sw)
            for src_h, s_sw in host_sw.items():
                if s_sw != src_sw:
                    continue
                for dst_h, d_sw in host_sw.items():
                    if s_sw == d_sw:
                        paths[(src_h, dst_h)] = [src_sw]
                        continue
                    candidates = all_shortest.get(d_sw, [])
                    if candidates:
                        # Tasodifiy tanlash (har safar boshqa yo'l)
                        paths[(src_h, dst_h)] = candidates

    elif routing_mode == "policy":
        # BGP-like: AS local preference asosida
        # Prefer: same AS > customer > peer > transit
        for src_h, src_sw in host_sw.items():
            src_as = topo["switches"][src_sw]["as"]
            for dst_h, dst_sw in host_sw.items():
                if src_sw == dst_sw:
                    paths[(src_h, dst_h)] = [src_sw]
                    continue
                # Barcha yo'llarni topish
                all_p = _bfs_all_paths(graph, src_sw).get(dst_sw, [])
                if not all_p:
                    continue
                # AS preference bo'yicha tartiblash
                scored = []
                for p in all_p:
                    as_set = set(topo["switches"].get(s, {}).get("as", 0) for s in p)
                    cross_as = len(as_set)
                    # Kam AS o'tish - yaxshi
                    # Lekin ba'zan uzoqroq yo'l tanlash (policy)
                    score = cross_as * 10 + len(p)
                    scored.append((score, p))
                scored.sort()
                # 70% eng yaxshi, 30% ikkinchi
                if len(scored) > 1 and random.random() < 0.3:
                    paths[(src_h, dst_h)] = scored[1][1]
                else:
                    paths[(src_h, dst_h)] = scored[0][1]

    else:  # l2_learn - BFS shortest path (default)
        for src_sw in topo["switches"]:
            for dst_sw in topo["switches"]:
                if src_sw == dst_sw:
                    continue
                path = _bfs_shortest(graph, src_sw, dst_sw)
                if path:
                    for src_h, s_sw in host_sw.items():
                        if s_sw == src_sw:
                            for dst_h, d_sw in host_sw.items():
                                if d_sw == dst_sw:
                                    paths[(src_h, dst_h)] = path
        # Same switch
        for src_h, src_sw in host_sw.items():
            for dst_h, dst_sw in host_sw.items():
                if src_h != dst_h and src_sw == dst_sw:
                    paths[(src_h, dst_h)] = [src_sw]

    return paths


def _bfs_shortest(graph, src, dst):
    """BFS shortest path."""
    visited = {src}
    queue = deque([(src, [src])])
    while queue:
        node, path = queue.popleft()
        for neighbor, _ in graph.get(node, []):
            if neighbor == dst:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None


def _bfs_all_paths(graph, src):
    """BFS: har bir destination uchun barcha eng qisqa yo'llar."""
    dist = {src: 0}
    all_paths = {src: [[src]]}
    queue = deque([src])
    while queue:
        node = queue.popleft()
        for neighbor, _ in graph.get(node, []):
            if neighbor not in dist:
                dist[neighbor] = dist[node] + 1
                all_paths[neighbor] = [p + [neighbor] for p in all_paths[node]]
                queue.append(neighbor)
            elif dist[neighbor] == dist[node] + 1:
                all_paths[neighbor].extend(p + [neighbor] for p in all_paths[node])
    return all_paths


# ─────────────────────────────────────────────────────────
#  CONTROLLER (os-ken)
# ─────────────────────────────────────────────────────────

CONTROLLER_APP = r'''
"""Transport Monitor - L2 learning + stats collection."""
try:
    from os_ken.base import app_manager
    from os_ken.controller import ofp_event
    from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from os_ken.ofproto import ofproto_v1_3
    from os_ken.lib.packet import packet, ethernet, arp, ipv4, tcp, udp, icmp
    from os_ken.lib import hub
    BaseApp = getattr(app_manager, "OSKenApp", None) or getattr(app_manager, "RyuApp")
except ImportError:
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from ryu.ofproto import ofproto_v1_3
    from ryu.lib.packet import packet, ethernet, arp, ipv4, tcp, udp, icmp
    from ryu.lib import hub
    BaseApp = app_manager.RyuApp

import json, time, os

EVENTS_LOG = "/data/stats/transport_events.jsonl"
FLOW_STATS_LOG = "/data/stats/flow_stats.jsonl"
PORT_STATS_LOG = "/data/stats/port_stats.jsonl"

class TransportMonitor(BaseApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self._datapaths = {}
        for p in [EVENTS_LOG, FLOW_STATS_LOG, PORT_STATS_LOG]:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.exists(p):
                os.remove(p)
        self.monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        while True:
            for dpid, dp in list(self._datapaths.items()):
                try:
                    ofp = dp.ofproto; p = dp.ofproto_parser
                    dp.send_msg(p.OFPFlowStatsRequest(dp))
                    dp.send_msg(p.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))
                except Exception:
                    pass
            hub.sleep(5)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath; ofp = dp.ofproto; p = dp.ofproto_parser
        self._datapaths[dp.id] = dp
        match = p.OFPMatch()
        actions = [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=0, match=match, instructions=inst))
        self.logger.info("Switch %s connected", dp.id)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg; dp = msg.datapath; ofp = dp.ofproto; p = dp.ofproto_parser
        in_port = msg.match['in_port']; dpid = dp.id
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if not eth:
            return
        dst = eth.dst; src = eth.src
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Log
        event = {"ts": time.time(), "dpid": dpid, "in_port": in_port, "eth_src": src, "eth_dst": dst}
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt:
            event.update({"ip_src": ip_pkt.src, "ip_dst": ip_pkt.dst, "ip_proto": ip_pkt.proto,
                          "ip_ttl": ip_pkt.ttl, "ip_len": ip_pkt.total_length})
            tcp_pkt = pkt.get_protocol(tcp.tcp)
            if tcp_pkt:
                event.update({"tcp_src": tcp_pkt.src_port, "tcp_dst": tcp_pkt.dst_port,
                              "tcp_flags": tcp_pkt.bits, "tcp_seq": tcp_pkt.seq,
                              "tcp_ack": tcp_pkt.ack, "tcp_win": tcp_pkt.window_size})
            udp_pkt = pkt.get_protocol(udp.udp)
            if udp_pkt:
                event.update({"udp_src": udp_pkt.src_port, "udp_dst": udp_pkt.dst_port,
                              "udp_len": udp_pkt.total_length})
            icmp_pkt = pkt.get_protocol(icmp.icmp)
            if icmp_pkt:
                event.update({"icmp_type": icmp_pkt.type, "icmp_code": icmp_pkt.code})
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            event.update({"arp_op": arp_pkt.opcode, "arp_src_ip": arp_pkt.src_ip, "arp_dst_ip": arp_pkt.dst_ip})
        try:
            with open(EVENTS_LOG, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass

        out_port = self.mac_to_port[dpid].get(dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out_port)]
        if out_port != ofp.OFPP_FLOOD and ip_pkt:
            match = p.OFPMatch(eth_type=0x0800, ipv4_src=ip_pkt.src, ipv4_dst=ip_pkt.dst)
            inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            dp.send_msg(p.OFPFlowMod(datapath=dp, priority=10, match=match,
                                      instructions=inst, idle_timeout=30, hard_timeout=120,
                                      buffer_id=msg.buffer_id if msg.buffer_id != ofp.OFP_NO_BUFFER else ofp.OFP_NO_BUFFER))
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                    in_port=in_port, actions=actions, data=data))

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply(self, ev):
        ts = time.time()
        for stat in ev.msg.body:
            try:
                with open(FLOW_STATS_LOG, "a") as f:
                    f.write(json.dumps({"ts": ts, "dpid": ev.msg.datapath.id, "priority": stat.priority,
                            "packets": stat.packet_count, "bytes": stat.byte_count,
                            "duration_sec": stat.duration_sec, "idle_timeout": stat.idle_timeout,
                            "hard_timeout": stat.hard_timeout, "match": str(stat.match)}) + "\n")
            except Exception:
                pass

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        ts = time.time()
        for stat in ev.msg.body:
            try:
                with open(PORT_STATS_LOG, "a") as f:
                    f.write(json.dumps({"ts": ts, "dpid": ev.msg.datapath.id, "port": stat.port_no,
                            "rx_packets": stat.rx_packets, "tx_packets": stat.tx_packets,
                            "rx_bytes": stat.rx_bytes, "tx_bytes": stat.tx_bytes,
                            "rx_dropped": stat.rx_dropped, "tx_dropped": stat.tx_dropped,
                            "rx_errors": stat.rx_errors, "tx_errors": stat.tx_errors}) + "\n")
            except Exception:
                pass
'''


# ─────────────────────────────────────────────────────────
#  TOPOLOGY BUILDER
# ─────────────────────────────────────────────────────────

def build_topology(topo):
    """Mininet topologiya yaratish."""
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController
    from mininet.link import TCLink
    from mininet.log import setLogLevel
    setLogLevel("info")

    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True, autoStaticArp=True)
    net.addController("c0", controller=RemoteController,
                      ip="127.0.0.1", port=CONTROLLER_PORT, protocols="OpenFlow13")

    switches = {}
    for sw_name in topo["switches"]:
        dpid = f"{int(sw_name[1:]):016x}"
        switches[sw_name] = net.addSwitch(sw_name, dpid=dpid, protocols="OpenFlow13")

    hosts = {}
    for h_name, h_info in topo["hosts"].items():
        hosts[h_name] = net.addHost(h_name, ip=h_info["ip"])
        al = topo["access_links"].get(h_name, {"bw": 10, "delay": "1ms", "loss": 0})
        net.addLink(hosts[h_name], switches[h_info["switch"]],
                    cls=TCLink, bw=al["bw"], delay=al["delay"], loss=al["loss"])

    for (s1, s2), params in topo["links"].items():
        net.addLink(switches[s1], switches[s2], cls=TCLink,
                    bw=params["bw"], delay=params["delay"],
                    loss=params["loss"], max_queue_size=params["queue"])
    return net


# ─────────────────────────────────────────────────────────
#  TRAFFIC GENERATOR (diurnal patterns)
# ─────────────────────────────────────────────────────────

class TrafficGen:
    def __init__(self, net, topo):
        self.net = net
        self.topo = topo
        self._running = False
        self._threads = []
        self._log = os.path.join(DATA_DIR, "stats/traffic_log.jsonl")
        self._conn_log = os.path.join(DATA_DIR, "stats/connection_states.jsonl")
        self._dns_log = os.path.join(DATA_DIR, "stats/dns_queries.jsonl")
        self._anomaly_log = os.path.join(DATA_DIR, "stats/anomaly_events.jsonl")
        self._http_log = os.path.join(DATA_DIR, "stats/http_transactions.jsonl")
        self.servers = {n: net.get(n) for n, h in topo["hosts"].items() if h["role"] == "server"}
        self.clients = {n: net.get(n) for n, h in topo["hosts"].items() if h["role"] == "client"}
        self._sim_start = time.time()
        self._time_scale = 3600 / 60
        self._active_connections = deque(maxlen=500)
        self._dns_cache = {}  # DNS cache simulyatsiyasi
        self._conn_counter = 0

    def _safe_cmd(self, host, cmd_str):
        try:
            return host.cmd(cmd_str)
        except Exception:
            return ""

    def _get_load_factor(self):
        """Vaqtga bog'liq yuklanish koeffitsienti (diurnal pattern)."""
        elapsed = time.time() - self._sim_start
        sim_hour = (elapsed / 60) % 24
        import math
        base = 0.3
        morning_peak = 0.4 * max(0, math.exp(-0.5 * (sim_hour - 12) ** 2 / 4))
        evening_peak = 0.5 * max(0, math.exp(-0.5 * (sim_hour - 20) ** 2 / 3))
        night_dip = -0.2 * max(0, math.exp(-0.5 * (sim_hour - 4) ** 2 / 4))
        noise = random.uniform(-0.05, 0.05)
        return max(0.1, min(1.0, base + morning_peak + evening_peak + night_dip + noise))

    def start(self):
        self._running = True
        srv_list = list(self.servers.values())

        # ── 1. Real HTTP serverlar ──
        for name, host in self.servers.items():
            self._safe_cmd(host, "iperf3 -s -p 5201 -D 2>/dev/null")
            self._safe_cmd(host, "iperf3 -s -p 5202 -D 2>/dev/null")
            # Real HTTP server (Python) — yengil fayllar
            self._safe_cmd(host, "mkdir -p /tmp/www")
            self._safe_cmd(host, "dd if=/dev/urandom of=/tmp/www/small.bin bs=1K count=5 2>/dev/null")
            self._safe_cmd(host, "dd if=/dev/urandom of=/tmp/www/medium.bin bs=1K count=50 2>/dev/null")
            self._safe_cmd(host, "dd if=/dev/urandom of=/tmp/www/large.bin bs=1K count=200 2>/dev/null")
            self._safe_cmd(host, "echo '<html><body>Server " + name + "</body></html>' > /tmp/www/index.html")
            self._safe_cmd(host, "cd /tmp/www && python3 -m http.server 80 &")

        # ── 2. TCP CC algoritmlarini aralash qo'yish ──
        for name, client in self.clients.items():
            cc = random.choice(TCP_CC_ALGORITHMS)
            self._safe_cmd(client, f"sysctl -w net.ipv4.tcp_congestion_control={cc} 2>/dev/null")
            self._log_event("tcp_cc_set", name, "", "config", cc)

        # ── 3. Background traffic ──
        for name, client in self.clients.items():
            srv = random.choice(srv_list)
            bw = random.choice(["30K", "50K", "100K"])
            self._safe_cmd(client, f"iperf3 -c {srv.IP()} -p 5201 -t 86400 -b {bw} --logfile /dev/null &")
            self._log_event("background", name, srv.name, "tcp", bw)

        # ── 4. Barcha loop'lar ──
        for target in [self._app_loop, self._burst_loop, self._congestion_loop,
                       self._http_loop, self._dns_loop, self._anomaly_loop,
                       self._connection_state_loop, self._adaptive_bitrate_loop]:
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        print(f"[Traffic] {len(self.clients)} client -> {len(self.servers)} server (diurnal+http+dns+anomaly)")

    def stop(self):
        self._running = False
        for h in list(self.servers.values()) + list(self.clients.values()):
            self._safe_cmd(h, "killall -9 iperf3 2>/dev/null")
        for t in self._threads:
            t.join(timeout=5)

    def _app_loop(self):
        srv_list = list(self.servers.values())
        cli_list = list(self.clients.values())
        while self._running:
            load = self._get_load_factor()
            r = random.random()
            cumul = 0
            chosen = TRAFFIC_MIX[0]
            for mix in TRAFFIC_MIX:
                cumul += mix[1]
                if r <= cumul:
                    chosen = mix
                    break
            name, _, proto, rate_range = chosen
            # Rate scales with load factor
            base_rate = random.randint(rate_range[0], rate_range[1])
            rate = int(base_rate * load)
            duration = random.randint(2, 20)
            client = random.choice(cli_list)
            server = random.choice(srv_list)
            port = 5201 if proto == "tcp" else 5202
            flag = "" if proto == "tcp" else "-u "
            self._safe_cmd(client, f"iperf3 -c {server.IP()} -p {port} {flag}-t {duration} -b {rate}K --logfile /dev/null &")
            self._log_event(name, client.name, server.name, proto, f"{rate}K", load_factor=round(load, 2))
            time.sleep(random.uniform(0.3, 2.0))

    def _burst_loop(self):
        while self._running:
            time.sleep(random.randint(15, 50))
            if not self._running:
                break
            load = self._get_load_factor()
            if random.random() > load:
                continue  # Kechasi burst kam
            srv = random.choice(list(self.servers.values()))
            clients = random.sample(list(self.clients.values()), min(3, len(self.clients)))
            for c in clients:
                rate = random.randint(2000, 8000)
                self._safe_cmd(c, f"iperf3 -c {srv.IP()} -p 5201 -t 3 -b {rate}K --logfile /dev/null &")
            self._log_event("burst", ",".join(c.name for c in clients), srv.name, "tcp", "high",
                           load_factor=round(load, 2))

    def _congestion_loop(self):
        """Maxsus congestion - bottleneck linkni to'ldirish."""
        while self._running:
            time.sleep(random.randint(20, 60))
            if not self._running:
                break
            load = self._get_load_factor()
            if load < 0.5:
                continue
            # Eng kichik bandwidth linkni topish va uni to'ldirish
            min_bw_link = min(self.topo["links"].items(), key=lambda x: x[1]["bw"])
            bw = min_bw_link[1]["bw"]
            # Bottleneck'ni to'ldiradigan trafik
            srv = random.choice(list(self.servers.values()))
            for c in list(self.clients.values()):
                rate = int(bw * 1000 * 0.3)  # Link kapasitetining 30%
                self._safe_cmd(c, f"iperf3 -c {srv.IP()} -p 5201 -t 5 -b {rate}K --logfile /dev/null &")
            self._log_event("congestion_gen", "all_clients", srv.name, "tcp",
                           f"{bw}Mbps_link", load_factor=round(load, 2))

    # ── Real HTTP trafik (wget/curl) ──
    def _http_loop(self):
        """Real HTTP GET/POST so'rovlari — turli URL, turli o'lcham."""
        srv_list = list(self.servers.values())
        cli_list = list(self.clients.values())
        files = ["index.html", "small.bin", "medium.bin", "large.bin"]
        methods = ["GET"] * 8 + ["POST"] * 2  # 80% GET, 20% POST
        status_codes = [200] * 85 + [301] * 3 + [304] * 4 + [404] * 5 + [500] * 2 + [503] * 1
        while self._running:
            load = self._get_load_factor()
            client = random.choice(cli_list)
            server = random.choice(srv_list)
            method = random.choice(methods)
            target = random.choice(files)
            url = f"http://{server.IP()}/{target}"
            start_t = time.time()
            if method == "GET":
                result = self._safe_cmd(client,
                    f"wget -q -O /dev/null --timeout=5 {url} 2>&1; echo $?")
            else:
                payload_size = random.randint(100, 5000)
                result = self._safe_cmd(client,
                    f"curl -s -o /dev/null -w '%{{http_code}} %{{time_total}} %{{size_download}} %{{speed_download}}' "
                    f"-X POST -d '@/dev/urandom' --max-time 5 {url} 2>/dev/null || echo 'fail'")
            elapsed_ms = (time.time() - start_t) * 1000
            # Connection state tracking
            self._conn_counter += 1
            conn_id = self._conn_counter
            status = random.choice(status_codes)
            entry = {
                "ts": time.time(), "conn_id": conn_id,
                "client": client.name, "client_ip": client.IP(),
                "server": server.name, "server_ip": server.IP(),
                "method": method, "url": f"/{target}", "port": 80,
                "status_code": status, "response_time_ms": round(elapsed_ms, 2),
                "bytes_transferred": {"index.html": 100, "small.bin": 5120,
                                       "medium.bin": 51200, "large.bin": 204800}.get(target, 0),
                "keep_alive": random.random() > 0.3,
                "user_agent": random.choice(["Mozilla/5.0", "Chrome/125", "curl/8.0", "python-requests/2.31"]),
                "tls": target != "index.html" and random.random() > 0.4,
            }
            try:
                with open(self._http_log, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
            self._log_event("http", client.name, server.name, "tcp", f"{method}:{target}",
                           status=status, response_ms=round(elapsed_ms, 2))
            time.sleep(random.uniform(0.3, 1.5))

    # ── DNS Resolution zanjiri ──
    def _dns_loop(self):
        """DNS so'rovlarni simulyatsiya — caching, TTL, recursive lookup."""
        cli_list = list(self.clients.values())
        dns_servers = [h for n, h in self.servers.items() if "dns" in n.lower()]
        if not dns_servers:
            dns_servers = list(self.servers.values())[:1]
        domains = [
            ("example.com", "A", 300), ("cdn.example.com", "CNAME", 60),
            ("api.service.io", "A", 120), ("mail.example.com", "MX", 3600),
            ("video.stream.tv", "A", 30), ("login.secure.bank", "A", 180),
            ("iot-hub.device.net", "A", 600), ("game.server.gg", "A", 15),
            ("news.portal.org", "A", 90), ("search.engine.com", "A", 45),
            ("social.media.app", "A", 60), ("cloud.storage.io", "AAAA", 120),
            ("malware.bad.evil", "A", 0),  # Suspicious domain
            ("c2.hidden.onion", "A", 0),   # C2 callback attempt
        ]
        while self._running:
            client = random.choice(cli_list)
            domain, qtype, ttl = random.choice(domains)
            dns_srv = random.choice(dns_servers)
            cache_key = f"{client.name}:{domain}"
            # DNS cache check
            now = time.time()
            cached = self._dns_cache.get(cache_key)
            cache_hit = cached and (now - cached["ts"]) < cached["ttl"]
            if cache_hit:
                response_time = random.uniform(0.1, 0.5)  # Cache hit = tez
                source = "cache"
            else:
                # Real ping to DNS server to measure latency
                start_t = time.time()
                self._safe_cmd(client, f"ping -c 1 -W 1 {dns_srv.IP()} > /dev/null 2>&1")
                response_time = (time.time() - start_t) * 1000
                # Recursive lookup adds more latency
                if random.random() > 0.7:
                    response_time *= random.uniform(1.5, 3.0)  # Recursive = sekin
                    source = "recursive"
                else:
                    source = "authoritative"
                self._dns_cache[cache_key] = {"ts": now, "ttl": ttl}

            # DNS response codes
            rcode = "NOERROR"
            if "malware" in domain or "hidden" in domain:
                rcode = random.choice(["NXDOMAIN", "NOERROR", "SERVFAIL"])
            elif random.random() < 0.02:
                rcode = random.choice(["NXDOMAIN", "SERVFAIL", "REFUSED"])

            entry = {
                "ts": now, "client": client.name, "client_ip": client.IP(),
                "dns_server": dns_srv.name, "dns_server_ip": dns_srv.IP(),
                "domain": domain, "query_type": qtype, "ttl": ttl,
                "response_time_ms": round(response_time, 2),
                "cache_hit": cache_hit, "source": source,
                "rcode": rcode, "is_suspicious": "malware" in domain or "hidden" in domain,
            }
            try:
                with open(self._dns_log, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
            time.sleep(random.uniform(0.5, 4.0))

    # ── Anomal trafik (scan, DDoS, exfil) ──
    def _anomaly_loop(self):
        """Xavfli/anomal trafik — IDS/IPS dataset uchun."""
        cli_list = list(self.clients.values())
        srv_list = list(self.servers.values())
        all_hosts = list(self.net.hosts)
        while self._running:
            time.sleep(random.randint(5, 20))
            if not self._running:
                break
            load = self._get_load_factor()
            # Anomallar tunda ko'p (attackerlar tunda faol)
            anomaly_prob = 0.6 if load < 0.3 else 0.35
            if random.random() > anomaly_prob:
                continue

            r = random.random()
            cumul = 0
            chosen = ANOMALY_MIX[0]
            for mix in ANOMALY_MIX:
                cumul += mix[1]
                if r <= cumul:
                    chosen = mix
                    break

            a_name, _, proto, rate_range = chosen
            attacker = random.choice(cli_list)
            target = random.choice(srv_list)
            duration = random.randint(2, 15)

            if a_name == "port_scan":
                start_port = random.randint(1, 1000)
                count = random.randint(5, 20)
                self._safe_cmd(attacker,
                    f"hping3 -S -p ++{start_port} -c {count} "
                    f"{target.IP()} 2>/dev/null &")
                details = f"ports={start_port}-{start_port+count}"
            elif a_name == "syn_flood":
                count = random.randint(50, 200)
                port = random.choice([80, 443, 22, 8080])
                self._safe_cmd(attacker,
                    f"hping3 -S -p {port} -i u10000 "
                    f"-c {count} {target.IP()} 2>/dev/null &")
                details = f"port={port} count={count}"
            elif a_name == "udp_flood":
                count = random.randint(50, 200)
                port = random.choice([53, 123, 161, 1900])
                self._safe_cmd(attacker,
                    f"hping3 --udp -p {port} -i u10000 -c {count} {target.IP()} 2>/dev/null &")
                details = f"port={port} count={count}"
            elif a_name == "dns_amplify":
                self._safe_cmd(attacker,
                    f"hping3 --udp -p 53 -d 40 -c 50 {target.IP()} 2>/dev/null &")
                details = "dns_amplification"
            elif a_name == "ping_sweep":
                subnet = target.IP().rsplit(".", 1)[0]
                self._safe_cmd(attacker, f"ping -c 1 -W 1 {subnet}.1 > /dev/null 2>&1 &")
                self._safe_cmd(attacker, f"ping -c 1 -W 1 {subnet}.2 > /dev/null 2>&1 &")
                self._safe_cmd(attacker, f"ping -c 1 -W 1 {subnet}.254 > /dev/null 2>&1 &")
                details = f"subnet={subnet}.0/24"
            elif a_name == "brute_force":
                for _ in range(random.randint(2, 5)):
                    self._safe_cmd(attacker,
                        f"curl -s -o /dev/null --max-time 2 "
                        f"http://{target.IP()}/login?user=admin&pass={random.randint(1000,9999)} 2>/dev/null &")
                details = "http_brute_force"
            elif a_name == "data_exfil":
                rate = random.randint(rate_range[0], rate_range[1])
                self._safe_cmd(attacker,
                    f"iperf3 -c {target.IP()} -p 5201 -t {min(duration,5)} -b {rate}K --logfile /dev/null &")
                details = f"exfil_rate={rate}K"
            else:
                # slowloris
                for _ in range(random.randint(2, 5)):
                    self._safe_cmd(attacker,
                        f"curl -s -o /dev/null --max-time {min(duration,5)} "
                        f"http://{target.IP()}/large.bin 2>/dev/null &")
                details = "slowloris_connections"

            entry = {
                "ts": time.time(), "type": a_name, "attacker": attacker.name,
                "attacker_ip": attacker.IP(), "target": target.name,
                "target_ip": target.IP(), "proto": proto,
                "duration_sec": duration, "details": details,
                "severity": {"port_scan": "medium", "syn_flood": "critical",
                             "udp_flood": "critical", "dns_amplify": "high",
                             "slowloris": "high", "ping_sweep": "low",
                             "brute_force": "high", "data_exfil": "critical"}.get(a_name, "medium"),
                "is_anomaly": True,
                "sim_hour": round((time.time() - self._sim_start) / 60 % 24, 1),
            }
            try:
                with open(self._anomaly_log, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
            self._log_event(f"anomaly:{a_name}", attacker.name, target.name, proto,
                           details, severity=entry["severity"])

    # ── TCP Connection state tracking ──
    def _connection_state_loop(self):
        """TCP connection holatlari — SYN, ESTABLISHED, FIN_WAIT, TIME_WAIT."""
        while self._running:
            time.sleep(8)
            if not self._running:
                break
            for host in random.sample(list(self.net.hosts), min(4, len(self.net.hosts))):
                try:
                    # ss (socket statistics) orqali connection holatlarini o'qish
                    result = self._safe_cmd(host, "ss -tan state all 2>/dev/null | tail -30")
                    if not result:
                        continue
                    states = {"LISTEN": 0, "SYN-SENT": 0, "SYN-RECV": 0,
                              "ESTAB": 0, "FIN-WAIT-1": 0, "FIN-WAIT-2": 0,
                              "CLOSE-WAIT": 0, "CLOSING": 0, "LAST-ACK": 0,
                              "TIME-WAIT": 0, "CLOSED": 0}
                    for line in result.split("\n"):
                        for state in states:
                            if state in line:
                                states[state] += 1
                    entry = {
                        "ts": time.time(), "host": host.name, "host_ip": host.IP(),
                        **states,
                        "total_connections": sum(states.values()),
                        "active_connections": states["ESTAB"] + states["SYN-SENT"] + states["SYN-RECV"],
                    }
                    try:
                        with open(self._conn_log, "a") as f:
                            f.write(json.dumps(entry) + "\n")
                    except OSError:
                        pass
                except Exception:
                    pass

    # ── Adaptive Bitrate Streaming (ABR) ──
    def _adaptive_bitrate_loop(self):
        """Video streaming — bitrate dinamik o'zgaradi tarmoq holatiga qarab."""
        cli_list = list(self.clients.values())
        srv_list = list(self.servers.values())
        quality_levels = [
            ("240p", 300), ("360p", 700), ("480p", 1500),
            ("720p", 3000), ("1080p", 6000), ("4K", 15000),
        ]
        while self._running:
            time.sleep(random.randint(5, 15))
            if not self._running:
                break
            load = self._get_load_factor()
            if random.random() > load * 0.6:
                continue
            client = random.choice(cli_list)
            server = random.choice(srv_list)
            # Avval kichik segment (bitrate probe)
            current_quality = 2  # 480p dan boshlash
            segments = random.randint(3, 8)
            for seg in range(segments):
                if not self._running:
                    break
                quality_name, bitrate = quality_levels[current_quality]
                duration = random.uniform(2, 4)  # segment davomiyligi
                rate = int(bitrate * random.uniform(0.8, 1.2))
                start_t = time.time()
                self._safe_cmd(client,
                    f"iperf3 -c {server.IP()} -p 5201 -t {int(duration)} -b {rate}K --logfile /dev/null")
                real_time = time.time() - start_t
                # Buffer ratio — agar segment tez yuklansa quality oshadi
                buffer_ratio = duration / max(real_time, 0.1)
                if buffer_ratio > 1.5 and current_quality < len(quality_levels) - 1:
                    current_quality += 1  # Quality up
                elif buffer_ratio < 0.8 and current_quality > 0:
                    current_quality -= 1  # Quality down (buffering)
                rebuffer = real_time > duration * 1.2
                entry = {
                    "ts": time.time(), "client": client.name, "server": server.name,
                    "segment": seg, "quality": quality_name, "bitrate_kbps": rate,
                    "segment_duration_sec": round(duration, 2),
                    "download_time_sec": round(real_time, 2),
                    "buffer_ratio": round(buffer_ratio, 2),
                    "rebuffer_event": rebuffer,
                    "quality_change": seg > 0,
                }
                self._log_event("abr_stream", client.name, server.name, "tcp",
                               f"{quality_name}:{rate}K", rebuffer=rebuffer,
                               buffer_ratio=round(buffer_ratio, 2))

    def _log_event(self, traffic_type, client, server, proto, rate, **extra):
        entry = {"ts": time.time(), "type": traffic_type, "client": client,
                 "server": server, "proto": proto, "rate": rate, **extra}
        try:
            with open(self._log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass


# ─────────────────────────────────────────────────────────
#  IMPAIRMENTS
# ─────────────────────────────────────────────────────────

class Impairments:
    def __init__(self, net):
        self.net = net
        self._running = False
        self._thread = None
        self._log = os.path.join(DATA_DIR, "stats/impairment_log.jsonl")

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self):
        sw_links = [l for l in self.net.links
                     if l.intf1.node.name.startswith("s") and l.intf2.node.name.startswith("s")]
        while self._running:
            time.sleep(random.randint(8, 30))
            if not self._running:
                break
            link = random.choice(sw_links)
            intf = link.intf1
            node = intf.node
            src, dst = link.intf1.node.name, link.intf2.node.name
            event = random.choice(IMPAIRMENT_EVENTS)
            if random.random() > event["prob"]:
                continue
            dur = random.randint(event["dur"][0], event["dur"][1])
            ename = event["name"]

            if ename == "link_flap":
                self._log_event("link_flap_start", src, dst, dur)
                try:
                    link.intf1.node.cmd(f"ip link set {link.intf1.name} down")
                    link.intf2.node.cmd(f"ip link set {link.intf2.name} down")
                except Exception:
                    continue
                def restore(l=link, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try:
                        l.intf1.node.cmd(f"ip link set {l.intf1.name} up")
                        l.intf2.node.cmd(f"ip link set {l.intf2.name} up")
                    except Exception:
                        pass
                    self._log_event("link_flap_end", s, ds, 0)
                threading.Thread(target=restore, daemon=True).start()

            elif ename == "packet_reorder":
                # Real paket tartibsizligi — tc netem reorder
                reorder_pct = random.randint(5, 25)
                gap = random.randint(3, 10)
                self._log_event("packet_reorder", src, dst, dur,
                               reorder_pct=reorder_pct, gap=gap)
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} root netem "
                             f"delay 10ms reorder {reorder_pct}% gap {gap}")
                except Exception:
                    continue
                def restore_reorder(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} root netem delay 0ms")
                    except Exception: pass
                    self._log_event("packet_reorder_end", s, ds, 0)
                threading.Thread(target=restore_reorder, daemon=True).start()

            elif ename == "buffer_bloat":
                # Buffer bloat — katta queue delay, past loss
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                self._log_event("buffer_bloat", src, dst, dur, delay_add=delay_add)
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} root netem "
                             f"delay {delay_add}ms {delay_add//4}ms distribution normal")
                except Exception:
                    continue
                def restore_bloat(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} root netem delay 0ms")
                    except Exception: pass
                    self._log_event("buffer_bloat_end", s, ds, 0)
                threading.Thread(target=restore_bloat, daemon=True).start()

            elif ename == "mtu_blackhole":
                # MTU blackhole — katta paketlar yo'qoladi
                self._log_event("mtu_blackhole", src, dst, dur, mtu=576)
                try:
                    node.cmd(f"ip link set {intf.name} mtu 576")
                except Exception:
                    continue
                def restore_mtu(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"ip link set {i} mtu 1500")
                    except Exception: pass
                    self._log_event("mtu_blackhole_end", s, ds, 0)
                threading.Thread(target=restore_mtu, daemon=True).start()

            elif ename == "duplicate":
                # Paket duplikatsiyasi
                dup_pct = random.randint(1, 10)
                self._log_event("duplicate", src, dst, dur, duplicate_pct=dup_pct)
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} root netem "
                             f"duplicate {dup_pct}%")
                except Exception:
                    continue
                def restore_dup(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} root netem delay 0ms")
                    except Exception: pass
                    self._log_event("duplicate_end", s, ds, 0)
                threading.Thread(target=restore_dup, daemon=True).start()

            elif ename == "jitter_spike":
                # Kuchli jitter — VoIP/gaming uchun yomon
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                jitter = delay_add * random.uniform(0.5, 1.5)
                corr = random.randint(20, 50)
                self._log_event("jitter_spike", src, dst, dur,
                               delay_add=delay_add, jitter=round(jitter, 1), correlation=corr)
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} root netem "
                             f"delay {delay_add}ms {int(jitter)}ms {corr}%")
                except Exception:
                    continue
                def restore_jitter(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} root netem delay 0ms")
                    except Exception: pass
                    self._log_event("jitter_spike_end", s, ds, 0)
                threading.Thread(target=restore_jitter, daemon=True).start()

            else:
                # congestion, micro_burst, link_degrade, route_change
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                loss_add = random.uniform(event["loss"][0], event["loss"][1])
                self._log_event(ename, src, dst, dur, delay_add=delay_add, loss_add=round(loss_add, 2))
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} root netem "
                             f"delay {delay_add}ms {delay_add//3}ms 25% loss {loss_add}% 25%")
                except Exception:
                    continue
                def restore_netem(n=node, i=intf.name, d=dur, s=src, ds=dst, en=ename):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} root netem delay 0ms loss 0%")
                    except Exception: pass
                    self._log_event(f"{en}_end", s, ds, 0)
                threading.Thread(target=restore_netem, daemon=True).start()

    def _log_event(self, event_type, src, dst, dur, **extra):
        entry = {"ts": time.time(), "event": event_type, "link": f"{src}-{dst}", "duration": dur, **extra}
        try:
            with open(self._log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass


# ─────────────────────────────────────────────────────────
#  DATA COLLECTOR
# ─────────────────────────────────────────────────────────

class Collector:
    def __init__(self, net):
        self.net = net
        self._running = False
        self._procs = {}
        self._threads = []

    def start(self):
        self._running = True
        for sw in self.net.switches:
            for intf in sw.intfList():
                if intf.name == "lo" or not intf.name.startswith("s"):
                    continue
                try:
                    proc = subprocess.Popen(
                        ["tcpdump", "-i", intf.name, "-w", f"{DATA_DIR}/pcap/{intf.name}.pcap",
                         "-s", "96", "-c", "500000", "-q"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._procs[intf.name] = proc
                except Exception:
                    pass
        t = threading.Thread(target=self._rtt_loop, daemon=True)
        t.start()
        self._threads.append(t)
        print(f"[Collector] tcpdump: {len(self._procs)} intf, RTT: ON")

    def stop(self):
        self._running = False
        for proc in self._procs.values():
            try:
                proc.terminate(); proc.wait(timeout=5)
            except Exception:
                try: proc.kill()
                except Exception: pass
        for t in self._threads:
            t.join(timeout=5)

    def _rtt_loop(self):
        hosts = self.net.hosts
        path = f"{DATA_DIR}/stats/rtt_measurements.jsonl"
        while self._running:
            for _ in range(3):
                if len(hosts) < 2:
                    break
                h1, h2 = random.sample(hosts, 2)
                try:
                    result = h1.cmd(f"ping -c 3 -W 2 {h2.IP()}")
                except Exception:
                    result = ""
                if result:
                    rtt = self._parse_ping(result)
                    if rtt:
                        try:
                            with open(path, "a") as f:
                                f.write(json.dumps({"ts": time.time(), "src": h1.name, "dst": h2.name,
                                                     "src_ip": h1.IP(), "dst_ip": h2.IP(), **rtt}) + "\n")
                        except OSError:
                            pass
            time.sleep(10)

    @staticmethod
    def _parse_ping(output):
        result = {}
        for line in output.split("\n"):
            if "packet loss" in line:
                for part in line.split(","):
                    if "packet loss" in part:
                        try: result["loss_pct"] = float(part.strip().split("%")[0])
                        except ValueError: pass
            if "min/avg/max" in line:
                try:
                    vals = line.split("=")[1].strip().split("/")
                    result["rtt_min"] = float(vals[0])
                    result["rtt_avg"] = float(vals[1])
                    result["rtt_max"] = float(vals[2])
                    if len(vals) > 3:
                        result["rtt_mdev"] = float(vals[3].split()[0])
                except (ValueError, IndexError):
                    pass
        return result


# ─────────────────────────────────────────────────────────
#  PATH TRACER
# ─────────────────────────────────────────────────────────

class PathTracer:
    """Graph-based path tracing + real ping RTT."""
    def __init__(self, net, topo, routing_mode):
        self.net = net
        self.topo = topo
        self.routing_mode = routing_mode
        self._running = False
        self._thread = None
        self._log = os.path.join(DATA_DIR, "stats/path_traces.jsonl")
        self._paths = compute_paths(topo, routing_mode)
        self._host_info = {}
        for h, info in topo["hosts"].items():
            sw = info["switch"]
            al = topo["access_links"].get(h, {})
            self._host_info[h] = {
                "switch": sw, "ip": info["ip"].split("/")[0], "role": info["role"],
                "as": topo["switches"].get(sw, {}).get("as", 0),
                "access_bw": al.get("bw", 10), "access_delay": al.get("delay", "1ms"),
                "access_loss": al.get("loss", 0),
            }

    def start(self):
        self._running = True
        self._trace_all_pairs()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self):
        hosts = self.net.hosts
        while self._running:
            time.sleep(15)
            if not self._running:
                break
            for _ in range(4):
                if len(hosts) < 2:
                    break
                h1, h2 = random.sample(hosts, 2)
                self._trace_and_log(h1, h2)

    def _trace_all_pairs(self):
        hosts = self.net.hosts
        count = 0
        for i, h1 in enumerate(hosts):
            for h2 in hosts[i+1:]:
                self._trace_and_log(h1, h2)
                self._trace_and_log(h2, h1)
                count += 2
        print(f"[PathTracer] {count} paths ({self.routing_mode})")

    def _trace_and_log(self, src_host, dst_host):
        trace = self._build_trace(src_host, dst_host)
        if trace:
            try:
                with open(self._log, "a") as f:
                    f.write(json.dumps(trace) + "\n")
            except OSError:
                pass

    def _get_link_params(self, sw1, sw2):
        for (s1, s2), p in self.topo["links"].items():
            if (sw1 in (s1, s2)) and (sw2 in (s1, s2)):
                return p
        return None

    def _build_trace(self, src_host, dst_host):
        src = self._host_info.get(src_host.name)
        dst = self._host_info.get(dst_host.name)
        if not src or not dst:
            return None

        path_key = (src_host.name, dst_host.name)
        sw_path = self._paths.get(path_key)
        if sw_path is None:
            return None
        # ECMP: list of paths -> random choice
        if isinstance(sw_path, list) and sw_path and isinstance(sw_path[0], list):
            sw_path = random.choice(sw_path)

        hops = []
        # Src access
        hops.append({"hop": 0, "from": src_host.name, "to": sw_path[0], "type": "access",
                      "bw_mbps": src["access_bw"],
                      "delay_ms": float(src["access_delay"].replace("ms", "")),
                      "loss_pct": src["access_loss"],
                      "as": self.topo["switches"].get(sw_path[0], {}).get("as", 0),
                      "role": self.topo["switches"].get(sw_path[0], {}).get("role", "")})
        # Switch-switch
        for i in range(len(sw_path) - 1):
            params = self._get_link_params(sw_path[i], sw_path[i+1])
            if params:
                link_type = "backbone" if params["bw"] >= 40 else "peering" if params["bw"] >= 20 else "access_agg"
                hops.append({"hop": i+1, "from": sw_path[i], "to": sw_path[i+1], "type": link_type,
                              "bw_mbps": params["bw"],
                              "delay_ms": float(params["delay"].replace("ms", "")),
                              "loss_pct": params["loss"],
                              "jitter_ms": float(params.get("jitter", "0ms").replace("ms", "")),
                              "queue_size": params.get("queue", 0),
                              "as": self.topo["switches"].get(sw_path[i+1], {}).get("as", 0),
                              "role": self.topo["switches"].get(sw_path[i+1], {}).get("role", "")})
        # Dst access
        hops.append({"hop": len(hops), "from": sw_path[-1], "to": dst_host.name, "type": "access",
                      "bw_mbps": dst["access_bw"],
                      "delay_ms": float(dst["access_delay"].replace("ms", "")),
                      "loss_pct": dst["access_loss"],
                      "as": self.topo["switches"].get(sw_path[-1], {}).get("as", 0),
                      "role": self.topo["switches"].get(sw_path[-1], {}).get("role", "")})

        # Real ping
        rtt = {}
        try:
            result = src_host.cmd(f"ping -c 3 -W 2 {dst_host.IP()}")
            rtt = Collector._parse_ping(result)
        except Exception:
            pass

        theory_delay = sum(h.get("delay_ms", 0) for h in hops)
        theory_loss = sum(h.get("loss_pct", 0) for h in hops)
        bottleneck = min(h.get("bw_mbps", 9999) for h in hops)
        as_path = []
        for h in hops:
            a = h.get("as", 0)
            if not as_path or as_path[-1] != a:
                as_path.append(a)

        return {
            "ts": time.time(), "src": src_host.name, "dst": dst_host.name,
            "src_ip": src["ip"], "dst_ip": dst["ip"],
            "src_as": src["as"], "dst_as": dst["as"],
            "routing": self.routing_mode,
            "path_switches": sw_path, "as_path": as_path,
            "num_switch_hops": len(sw_path), "num_total_hops": len(hops),
            "theoretical_delay_ms": round(theory_delay, 2),
            "theoretical_rtt_ms": round(theory_delay * 2, 2),
            "theoretical_loss_pct": round(theory_loss, 4),
            "bottleneck_bw_mbps": bottleneck,
            "real_rtt_ms": rtt.get("rtt_avg"), "real_rtt_min": rtt.get("rtt_min"),
            "real_rtt_max": rtt.get("rtt_max"), "real_loss_pct": rtt.get("loss_pct"),
            "hops": hops,
        }


# ─────────────────────────────────────────────────────────
#  DATASET BUILDER
# ─────────────────────────────────────────────────────────

def build_dataset(topo, routing_mode):
    import pandas as pd
    print("\n[Dataset] Ma'lumotlar qayta ishlanmoqda...")
    os.makedirs(f"{DATA_DIR}/datasets", exist_ok=True)

    datasets = {}
    for name, filename in [
        ("transport_events", "stats/transport_events.jsonl"),
        ("flow_stats", "stats/flow_stats.jsonl"),
        ("port_stats", "stats/port_stats.jsonl"),
        ("rtt", "stats/rtt_measurements.jsonl"),
        ("traffic_log", "stats/traffic_log.jsonl"),
        ("impairments", "stats/impairment_log.jsonl"),
        ("path_traces", "stats/path_traces.jsonl"),
        ("dns_queries", "stats/dns_queries.jsonl"),
        ("http_transactions", "stats/http_transactions.jsonl"),
        ("anomaly_events", "stats/anomaly_events.jsonl"),
        ("connection_states", "stats/connection_states.jsonl"),
    ]:
        path = f"{DATA_DIR}/{filename}"
        if not os.path.exists(path):
            continue
        rows = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try: rows.append(json.loads(line))
                    except json.JSONDecodeError: pass
        if rows:
            datasets[name] = pd.DataFrame(rows)

    # Path traces -> hop details
    if "path_traces" in datasets:
        pt = datasets["path_traces"]
        for col in ["path_switches", "as_path", "hops"]:
            if col in pt.columns:
                pt[col] = pt[col].apply(lambda x: json.dumps(x) if isinstance(x, list) else x)
        datasets["path_traces"] = pt

        hop_rows = []
        path_file = f"{DATA_DIR}/stats/path_traces.jsonl"
        if os.path.exists(path_file):
            with open(path_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try: rec = json.loads(line)
                    except json.JSONDecodeError: continue
                    for hop in rec.get("hops", []):
                        hop_rows.append({
                            "ts": rec["ts"], "src": rec["src"], "dst": rec["dst"],
                            "routing": rec.get("routing", ""),
                            "num_switch_hops": rec.get("num_switch_hops", 0),
                            "theoretical_rtt_ms": rec.get("theoretical_rtt_ms", 0),
                            "bottleneck_bw_mbps": rec.get("bottleneck_bw_mbps", 0),
                            "real_rtt_ms": rec.get("real_rtt_ms"),
                            "real_loss_pct": rec.get("real_loss_pct"),
                            "hop_num": hop.get("hop", 0),
                            "hop_from": hop.get("from", ""), "hop_to": hop.get("to", ""),
                            "hop_type": hop.get("type", ""),
                            "hop_as": hop.get("as", 0), "hop_role": hop.get("role", ""),
                            "hop_bw_mbps": hop.get("bw_mbps", 0),
                            "hop_delay_ms": hop.get("delay_ms", 0),
                            "hop_loss_pct": hop.get("loss_pct", 0),
                            "hop_jitter_ms": hop.get("jitter_ms", 0),
                            "hop_queue_size": hop.get("queue_size", 0),
                        })
            if hop_rows:
                datasets["hop_details"] = pd.DataFrame(hop_rows)

    # Port stats enrichment
    if "port_stats" in datasets:
        df = datasets["port_stats"]
        if "rx_bytes" in df.columns:
            df["total_bytes"] = df["rx_bytes"] + df["tx_bytes"]
            df["total_packets"] = df["rx_packets"] + df["tx_packets"]
            df["total_dropped"] = df["rx_dropped"] + df["tx_dropped"]
            df = df.sort_values(["dpid", "port", "ts"])
            ts_diff = df.groupby(["dpid", "port"])["ts"].diff().clip(lower=0.1)
            df["bytes_per_sec"] = df.groupby(["dpid", "port"])["total_bytes"].diff() / ts_diff
            df["packets_per_sec"] = df.groupby(["dpid", "port"])["total_packets"].diff() / ts_diff
            datasets["port_stats"] = df

    # Save
    for name, df in datasets.items():
        df.to_csv(f"{DATA_DIR}/datasets/{name}.csv", index=False)
        try: df.to_parquet(f"{DATA_DIR}/datasets/{name}.parquet", index=False)
        except Exception: pass

    # Metadata
    topo_json = {k: v for k, v in topo.items() if k != "links"}
    topo_json["links"] = {f"{k[0]}-{k[1]}": v for k, v in topo["links"].items()}
    with open(f"{DATA_DIR}/datasets/metadata.json", "w") as f:
        json.dump({"topology": topo_json, "routing": routing_mode,
                    "traffic_mix": [{"name": m[0], "weight": m[1], "proto": m[2]} for m in TRAFFIC_MIX],
                    "impairments": IMPAIRMENT_EVENTS}, f, indent=2, default=str)

    # Summary
    print(f"\n{'='*55}\n  DATASET XULOSA\n{'='*55}")
    total = 0
    for name, df in datasets.items():
        print(f"  {name:25s} {len(df):>8,} rows x {len(df.columns):>2} cols")
        total += len(df)
    print(f"  {'─'*45}\n  {'JAMI':25s} {total:>8,} rows")
    total_size = sum(os.path.getsize(f"{DATA_DIR}/datasets/{f}") for f in os.listdir(f"{DATA_DIR}/datasets"))
    print(f"  Hajm: {total_size/1024:.1f} KB\n{'='*55}")
    return datasets


# ─────────────────────────────────────────────────────────
#  CONTROLLER MANAGER
# ─────────────────────────────────────────────────────────

def start_controller():
    import socket
    with open("/tmp/transport_monitor.py", "w") as f:
        f.write(CONTROLLER_APP)
    with open("/tmp/run_controller.py", "w") as f:
        f.write(f'''#!/usr/bin/env python3
import sys
sys.path.insert(0, "/tmp")
from os_ken import cfg
from os_ken.base.app_manager import AppManager
from os_ken.lib import hub
cfg.CONF(["--ofp-listen-host", "127.0.0.1", "--ofp-tcp-listen-port", "{CONTROLLER_PORT}"])
app_mgr = AppManager.get_instance()
app_mgr.load_apps(["transport_monitor"])
contexts = app_mgr.create_contexts()
services = app_mgr.instantiate_apps(**contexts)
hub.joinall(services)
''')
    print("[Controller] os-ken ishga tushirilmoqda...")
    proc = subprocess.Popen(["python3", "/tmp/run_controller.py"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            s = socket.socket(); s.settimeout(1)
            s.connect(("127.0.0.1", CONTROLLER_PORT)); s.close()
            print("[Controller] Tayyor!")
            return proc
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(1)
            if proc.poll() is not None:
                out = proc.stdout.read().decode() if proc.stdout else ""
                print(f"XATO: Controller o'chdi!\n{out[-500:]}")
                sys.exit(1)
    print("XATO: Controller 30s ichida tayyor bo'lmadi")
    proc.terminate(); sys.exit(1)


# ─────────────────────────────────────────────────────────
#  TOPOLOGY VISUALIZATION
# ─────────────────────────────────────────────────────────

def visualize_topology(topo, topo_name, routing):
    """Topologiyani PNG rasm sifatida generatsiya qiladi."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import networkx as nx
    except ImportError:
        print("Kerakli kutubxonalar yo'q. O'rnating:")
        print("  pip install matplotlib networkx")
        sys.exit(1)

    G = nx.Graph()

    # AS ranglar
    as_numbers = sorted(set(s["as"] for s in topo["switches"].values()))
    color_palette = ["#4A90D9", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6",
                     "#1ABC9C", "#E67E22", "#3498DB"]
    as_colors = {asn: color_palette[i % len(color_palette)] for i, asn in enumerate(as_numbers)}

    # Switch nodelar
    for sw, info in topo["switches"].items():
        G.add_node(sw, node_type="switch", as_num=info["as"], role=info["role"])

    # Host nodelar
    for h, info in topo["hosts"].items():
        G.add_node(h, node_type="host", role=info["role"], ip=info["ip"])

    # Switch-switch linklar
    for (a, b), params in topo["links"].items():
        G.add_edge(a, b, bw=params["bw"], delay=params["delay"],
                   loss=params.get("loss", 0), link_type="backbone")

    # Host-switch linklar
    for h, info in topo["hosts"].items():
        sw = info["switch"]
        al = topo["access_links"].get(h, {})
        G.add_edge(h, sw, bw=al.get("bw", 10), delay=al.get("delay", "1ms"),
                   link_type="access")

    # Layout
    fig, ax = plt.subplots(1, 1, figsize=(16, 12))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    # Kamoshima layout
    pos = nx.kamada_kawai_layout(G, scale=2.5)

    # AS bo'yicha guruhlab, switchlarni grouplab chizish
    switch_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "switch"]
    server_nodes = [n for n, d in G.nodes(data=True) if d.get("role") == "server"]
    client_nodes = [n for n, d in G.nodes(data=True) if d.get("role") == "client"]

    # Backbone linklar (qalin)
    backbone_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("link_type") == "backbone"]
    access_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("link_type") == "access"]

    # Linklar bw bo'yicha qalinligi
    backbone_widths = []
    for u, v in backbone_edges:
        bw = G[u][v].get("bw", 10)
        backbone_widths.append(max(1.5, bw / 15))

    nx.draw_networkx_edges(G, pos, edgelist=backbone_edges, width=backbone_widths,
                           edge_color="#FFD700", alpha=0.7, style="solid", ax=ax)
    nx.draw_networkx_edges(G, pos, edgelist=access_edges, width=0.8,
                           edge_color="#666666", alpha=0.5, style="dashed", ax=ax)

    # Switch nodelar - AS ranglar
    sw_colors = [as_colors[G.nodes[n]["as_num"]] for n in switch_nodes]
    nx.draw_networkx_nodes(G, pos, nodelist=switch_nodes, node_size=900,
                           node_color=sw_colors, node_shape="s",
                           edgecolors="white", linewidths=2, ax=ax)

    # Server nodelar
    nx.draw_networkx_nodes(G, pos, nodelist=server_nodes, node_size=500,
                           node_color="#00E676", node_shape="^",
                           edgecolors="white", linewidths=1.5, ax=ax)

    # Client nodelar
    nx.draw_networkx_nodes(G, pos, nodelist=client_nodes, node_size=400,
                           node_color="#FF5252", node_shape="o",
                           edgecolors="white", linewidths=1.5, ax=ax)

    # Labellar
    nx.draw_networkx_labels(G, pos, font_size=7, font_color="white",
                            font_weight="bold", ax=ax)

    # Link labellari (bw + delay)
    edge_labels = {}
    for u, v, d in G.edges(data=True):
        if d.get("link_type") == "backbone":
            edge_labels[(u, v)] = f"{d['bw']}Mb\n{d['delay']}"
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels,
                                 font_size=5, font_color="#AAAAAA",
                                 bbox=dict(boxstyle="round,pad=0.1",
                                           facecolor="#1a1a2e", edgecolor="none"),
                                 ax=ax)

    # Legend
    legend_items = []
    for asn in as_numbers:
        legend_items.append(mpatches.Patch(color=as_colors[asn], label=f"AS {asn}"))
    legend_items.append(plt.Line2D([0], [0], marker="^", color="w", markerfacecolor="#00E676",
                                   markersize=10, linestyle="None", label="Server"))
    legend_items.append(plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#FF5252",
                                   markersize=10, linestyle="None", label="Client"))
    legend_items.append(plt.Line2D([0], [0], color="#FFD700", linewidth=2, label="Backbone link"))
    legend_items.append(plt.Line2D([0], [0], color="#666666", linewidth=1,
                                   linestyle="--", label="Access link"))
    ax.legend(handles=legend_items, loc="upper left", fontsize=8,
              facecolor="#2a2a4e", edgecolor="#444", labelcolor="white")

    # Title
    sw_count = len(topo["switches"])
    host_count = len(topo["hosts"])
    link_count = len(topo["links"])
    ax.set_title(f"Topology: {topo_name.upper()}  |  Routing: {routing.upper()}\n"
                 f"{sw_count} switches, {host_count} hosts, {link_count} backbone links, "
                 f"{len(as_numbers)} AS",
                 fontsize=14, fontweight="bold", color="white", pad=20)

    ax.axis("off")
    plt.tight_layout()

    out_path = f"{topo_name}_topology.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()
    print(f"Topologiya rasmi saqlandi: {out_path}")
    print(f"  Switchlar: {sw_count}")
    print(f"  Hostlar:   {host_count}")
    print(f"  Linklar:   {link_count}")
    print(f"  AS lar:    {len(as_numbers)}")

    # Text version ham chiqaramiz
    print(f"\n{'='*60}")
    print(f"  {topo_name.upper()} TOPOLOGY MAP")
    print(f"{'='*60}")
    for asn in as_numbers:
        print(f"\n  AS {asn}:")
        for sw, info in topo["switches"].items():
            if info["as"] == asn:
                hosts_on_sw = [h for h, hi in topo["hosts"].items() if hi["switch"] == sw]
                print(f"    [{sw}] ({info['role']})")
                for h in hosts_on_sw:
                    hi = topo["hosts"][h]
                    print(f"       └── {h} ({hi['ip']}) [{hi['role']}]")
    print(f"\n  Backbone linklar:")
    for (a, b), params in topo["links"].items():
        print(f"    {a} ←──({params['bw']}Mb, {params['delay']})──→ {b}")
    print(f"{'='*60}")


# ─────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────

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

Routing:
  l2_learn    L2 MAC learning (default)
  spf         Shortest Path First (Dijkstra)
  ecmp        Equal-Cost Multi-Path
  policy      BGP-like AS policy

Misollar:
  sudo python3 light_simulation.py --topology five_as --routing spf --duration 300
  sudo python3 light_simulation.py --topology datacenter --routing ecmp
""")
    parser.add_argument("--topology", choices=list(TOPOLOGIES.keys()), default="three_as")
    parser.add_argument("--routing", choices=["l2_learn", "spf", "ecmp", "policy"], default="l2_learn")
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--dataset-only", action="store_true")
    parser.add_argument("--no-traffic", action="store_true")
    parser.add_argument("--no-impairments", action="store_true")
    parser.add_argument("--visualize", action="store_true",
                        help="Topologiya PNG rasmini generatsiya qiladi (sudo kerak emas)")
    args = parser.parse_args()

    topo = TOPOLOGIES[args.topology]

    if args.visualize:
        visualize_topology(topo, args.topology, args.routing)
        return

    if args.dataset_only:
        build_dataset(topo, args.routing)
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

        # STP (loop bo'lsa)
        has_loop = len(topo["links"]) > len(topo["switches"]) - 1
        if has_loop:
            print("[Network] STP yoqilmoqda...")
            for sw in net.switches:
                sw.cmd(f"ovs-vsctl set bridge {sw.name} stp_enable=true")
            print("[Network] STP convergence (30s)...")
            time.sleep(30)
        else:
            time.sleep(3)

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
        build_dataset(topo, args.routing)
    except Exception as e:
        print(f"Dataset XATO: {e}")
    print("\n[Done]\n")


if __name__ == "__main__":
    main()

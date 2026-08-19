"""Routing algoritmlari: 11 xil rejim uchun switch-level yo'l hisoblash.

Routing (11 ta):
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
"""

import random
from collections import deque

# OSPF area va cost konfiguratsiyasi
OSPF_CONFIG = {
    "reference_bandwidth": 100,  # Mbps — cost = ref_bw / interface_bw
    "areas": {
        0: {"type": "backbone"},         # Area 0 — backbone (core switchlar)
        1: {"type": "normal"},           # Area 1 — normal
        2: {"type": "stub"},             # Area 2 — stub (default route only)
        3: {"type": "nssa"},             # Area 3 — not-so-stubby
    },
    "hello_interval": 10,
    "dead_interval": 40,
    "spf_delay": 5,
}

# IS-IS metric va level konfiguratsiyasi
ISIS_CONFIG = {
    "metric_style": "wide",  # wide (24-bit) yoki narrow (6-bit)
    "levels": {
        "L1": {"max_metric": 63 if False else 16777215},   # narrow: 63, wide: 16M
        "L2": {"max_metric": 63 if False else 16777215},
    },
    "overload_bit": False,
    "lsp_lifetime": 1200,
}

# RIP konfiguratsiyasi
RIP_CONFIG = {
    "version": 2,
    "max_hop_count": 15,            # 16 = unreachable
    "update_interval": 30,          # sekundda
    "timeout": 180,
    "garbage_collection": 120,
    "split_horizon": True,
    "poison_reverse": True,
    "triggered_updates": True,
}

# EIGRP konfiguratsiyasi
EIGRP_CONFIG = {
    "as_number": 100,
    "k_values": {
        "k1": 1,    # Bandwidth
        "k2": 0,    # Load
        "k3": 1,    # Delay
        "k4": 0,    # Reliability
        "k5": 0,    # MTU
    },
    "variance": 2,          # Unequal-cost load balancing multiplier
    "max_hop_count": 100,
    "hello_interval": 5,
    "hold_time": 15,
}

# BGP konfiguratsiyasi
BGP_CONFIG = {
    "local_pref_default": 100,
    "med_default": 0,
    "keepalive": 60,
    "hold_time": 180,
    "communities": {
        "no_export": "65535:65281",
        "no_advertise": "65535:65282",
        "blackhole": "65535:666",
    },
    "path_selection": [
        "highest_local_pref",
        "shortest_as_path",
        "lowest_origin",
        "lowest_med",
        "ebgp_over_ibgp",
        "lowest_router_id",
    ],
    "route_dampening": {
        "enabled": True,
        "half_life": 15,        # min
        "suppress_limit": 2000,
        "reuse_limit": 750,
        "max_suppress_time": 60,
    },
}


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


def _compute_ospf_cost(params):
    """OSPF cost = reference_bandwidth / interface_bandwidth."""
    bw = params.get("bw", 10)
    ref_bw = OSPF_CONFIG["reference_bandwidth"]
    return max(1, int(ref_bw / bw))


def _compute_isis_metric(params):
    """IS-IS wide metric: BW va delay asosida."""
    bw = params.get("bw", 10)
    delay = float(str(params.get("delay", "1ms")).replace("ms", ""))
    # Wide metric: katta BW = kichik metric
    bw_metric = max(1, int(1000 / bw))
    delay_metric = max(1, int(delay))
    return bw_metric + delay_metric


def _compute_eigrp_metric(params):
    """EIGRP composite metric: (K1*BW + K3*Delay) * 256."""
    bw = params.get("bw", 10) * 1000  # Kbps ga aylantirish
    delay = float(str(params.get("delay", "1ms")).replace("ms", "")) * 10  # tens of microseconds
    k = EIGRP_CONFIG["k_values"]
    # Classic formula: metric = [K1*BW + (K2*BW)/(256-load) + K3*Delay] * 256
    bw_component = k["k1"] * (10_000_000 / bw) if bw > 0 else 10_000_000
    delay_component = k["k3"] * delay
    return int((bw_component + delay_component) * 256)


def _compute_rip_metric(path):
    """RIP metric = hop count."""
    return len(path) - 1


def _compute_static_weight(params):
    """Static-route weight: pure inverse-bandwidth, delay ignored.

    Models an admin who hard-codes routes once to favor the fattest pipe
    ("bandwidth wins, latency doesn't matter") -- deliberately different
    dimension from l2_learn's hop-count BFS, so the two modes genuinely
    diverge on any topology with heterogeneous link bandwidth.
    """
    bw = params.get("bw", 10)
    return 1.0 / max(bw, 0.001)


def _ospf_staleness_prob():
    """OSPF timer-driven stale-route probability.

    Larger Hello/dead/SPF-delay windows mean SPF reconvergence lags a real
    topology change for longer, so a small share of computed paths keep an
    already-superseded (stale) extra hop -- same idea as the RIP poison-
    reverse perturbation below, but keyed off OSPF's own configured timers
    instead of a bare constant. Bounded to 5% to stay a subtle bias.
    """
    cfg = OSPF_CONFIG
    raw = (cfg["hello_interval"] + cfg["spf_delay"]) / cfg["dead_interval"] * 0.05
    return min(0.05, raw)


def _isis_staleness_prob():
    """IS-IS lsp_lifetime-driven stale-route probability.

    A longer LSP lifetime means a stale LSP lingers in the link-state
    database longer before refresh/flush, so occasionally a path is
    computed from slightly outdated link-state info. Bounded to 5%.
    """
    lifetime = ISIS_CONFIG["lsp_lifetime"]
    return min(0.05, lifetime / 1200 * 0.02)


# Timer-derived, deterministic — computed once from the config dicts above.
_OSPF_STALE_PROB = _ospf_staleness_prob()
_ISIS_STALE_PROB = _isis_staleness_prob()

# Static routes are admin-defined once and never recomputed per packet; cache
# the per-source Dijkstra result per topology so repeated compute_paths()
# calls on the same topology don't redo the O(V log V) work every time.
_static_prev_cache = {}


def _topology_signature(topo):
    """Stable hashable signature of a topology's switch/link graph.

    Used as a cache key for static routes -- safer than id(topo), which can
    alias a freed-then-reallocated dict at the same address.
    """
    switches = tuple(sorted(topo["switches"].keys()))
    links = tuple(sorted(
        (s1, s2, params.get("bw", 10)) for (s1, s2), params in topo["links"].items()
    ))
    return (switches, links)


def _dijkstra_weighted(graph, source, weight_fn):
    """Umumiy Dijkstra — ixtiyoriy weight funksiyasi bilan."""
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
            w = weight_fn(params)
            if dist.get(v, float("inf")) > d + w:
                dist[v] = d + w
                prev[v] = u
                heapq.heappush(pq, (d + w, v))
    return dist, prev


def _reconstruct_path(prev, source, target):
    """prev dict dan yo'lni qayta qurish."""
    path = []
    cur = target
    while cur and cur != source:
        path.append(cur)
        cur = prev.get(cur)
    if cur == source:
        path.append(source)
        path.reverse()
        return path
    return None


def _assign_ospf_areas(topo):
    """Switchlarni OSPF arealarga tayinlash.

    Topology author bergan aniq "area" kalitiga ustunlik beriladi; u yo'q
    bo'lsa (eski shakldagi topo dict) role substring evristikasiga qaytadi,
    shuning uchun bu funksiya hech qachon eski topologiyalarda crash bo'lmaydi.
    """
    areas = {}
    for sw, info in topo["switches"].items():
        if "area" in info:
            areas[sw] = info["area"]
            continue
        role = info.get("role", "")
        if "core" in role or "tier1" in role:
            areas[sw] = 0   # Backbone
        elif "border" in role or "distribution" in role or "agg" in role:
            areas[sw] = 1   # Normal
        elif "access" in role or "tor" in role:
            areas[sw] = 2   # Stub
        else:
            areas[sw] = 3   # NSSA
    return areas


def _assign_isis_levels(topo):
    """Switchlarga IS-IS level tayinlash.

    Xuddi _assign_ospf_areas kabi — aniq "isis_level" kalitiga ustunlik,
    bo'lmasa role substring evristikasiga fallback.
    """
    levels = {}
    for sw, info in topo["switches"].items():
        if "isis_level" in info:
            levels[sw] = info["isis_level"]
            continue
        role = info.get("role", "")
        if "core" in role or "tier1" in role:
            levels[sw] = "L2"     # Inter-area
        elif "border" in role or "distribution" in role:
            levels[sw] = "L1L2"   # Redistribution point
        else:
            levels[sw] = "L1"     # Intra-area
    return levels


def _bgp_path_score(topo, path):
    """BGP best path selection: local_pref > AS-PATH len > MED."""
    as_list = [topo["switches"].get(s, {}).get("as", 0) for s in path]
    # AS-PATH (unique AS count)
    as_path = []
    prev_as = None
    for a in as_list:
        if a != prev_as:
            as_path.append(a)
            prev_as = a
    as_path_len = len(as_path)

    # Local preference (same AS = higher pref)
    src_as = as_list[0] if as_list else 0
    dst_as = as_list[-1] if as_list else 0
    if src_as == dst_as:
        local_pref = 200   # iBGP — yuqori preference
    else:
        local_pref = BGP_CONFIG["local_pref_default"]

    # MED (delay-based)
    med = len(path) * 10

    # Community-based adjustment
    if as_path_len == 1:
        local_pref += 50    # Directly connected AS

    # BGP selection: highest local_pref → shortest AS-PATH → lowest MED
    # Score: pastroq = yaxshiroq
    score = (-local_pref * 10000) + (as_path_len * 1000) + med
    return score, as_path


def _bgp_route_dampening(path_changes, half_life=15):
    """BGP route dampening: tez-tez o'zgaradigan yo'llarni suppress qilish."""
    # Penalty formula: penalty += 1000 per flap, decays by half_life
    penalty = path_changes * 1000
    suppress = penalty > BGP_CONFIG["route_dampening"]["suppress_limit"]
    return suppress, penalty


def compute_paths(topo, routing_mode):
    """
    Topologiya va routing algoritmiga ko'ra barcha host juftliklari uchun
    switch-level path'larni hisoblash.

    Routing protokollar:
      l2_learn - L2 MAC learning (BFS)
      rip      - RIP v2 (hop count, max 15, split horizon)
      ospf     - OSPF (link-state, Dijkstra, cost=ref_bw/bw, areas)
      isis     - IS-IS (link-state, wide metric, L1/L2 levels)
      eigrp    - EIGRP (composite metric: BW + delay)
      bgp      - BGP (AS-PATH, local-pref, MED, communities)
      ecmp     - ECMP (equal-cost multi-path)
      spf      - Generic SPF (Dijkstra delay-weighted)
      policy   - Policy routing (AS preference)
      static   - Static routes (manually defined shortest)
      hybrid   - Real internet: AS ichida OSPF (IGP), AS orasida BGP (EGP), ECMP

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

    # ══════════════════════════════════════════════
    #  RIP v2 — Hop count metric, max 15 hop
    # ══════════════════════════════════════════════
    if routing_mode == "rip":
        max_hops = RIP_CONFIG["max_hop_count"]
        split_horizon = RIP_CONFIG["split_horizon"]

        # RIP distance-vector: har bir switch o'z routing table'ini build qiladi
        # BFS bilan hop count hisoblash
        for src_sw in topo["switches"]:
            # BFS — hop count
            hop_dist = {src_sw: 0}
            prev = {src_sw: None}
            queue = deque([src_sw])
            while queue:
                node = queue.popleft()
                if hop_dist[node] >= max_hops:
                    continue  # RIP: 16 = unreachable
                for neighbor, params in graph.get(node, []):
                    # Split horizon: don't advertise route back
                    if split_horizon and prev.get(node) == neighbor:
                        continue
                    new_dist = hop_dist[node] + 1
                    if neighbor not in hop_dist or new_dist < hop_dist[neighbor]:
                        hop_dist[neighbor] = new_dist
                        prev[neighbor] = node
                        queue.append(neighbor)

            # Path'larni reconstruct qilish
            for src_h, s_sw in host_sw.items():
                if s_sw != src_sw:
                    continue
                for dst_h, d_sw in host_sw.items():
                    if s_sw == d_sw:
                        paths[(src_h, dst_h)] = [src_sw]
                        continue
                    if d_sw in prev and hop_dist.get(d_sw, 999) <= max_hops:
                        path = _reconstruct_path(prev, src_sw, d_sw)
                        if path:
                            # RIP route poisoning: ba'zan suboptimal path tanlash
                            if random.random() < 0.05:
                                # Counting-to-infinity simulyatsiyasi
                                path = path + [path[-1]]  # Extra hop
                            paths[(src_h, dst_h)] = path

    # ══════════════════════════════════════════════
    #  OSPF — Link-state, Dijkstra, area-based cost
    # ══════════════════════════════════════════════
    elif routing_mode == "ospf":
        areas = _assign_ospf_areas(topo)

        # OSPF cost-weighted Dijkstra
        for src_sw in topo["switches"]:
            src_area = areas.get(src_sw, 0)

            # Cost graph
            _, prev = _dijkstra_weighted(graph, src_sw, _compute_ospf_cost)

            for src_h, s_sw in host_sw.items():
                if s_sw != src_sw:
                    continue
                for dst_h, d_sw in host_sw.items():
                    if s_sw == d_sw:
                        paths[(src_h, dst_h)] = [src_sw]
                        continue
                    path = _reconstruct_path(prev, src_sw, d_sw)
                    if path:
                        dst_area = areas.get(d_sw, 0)
                        # Inter-area: area 0 orqali o'tishi kerak
                        if src_area != dst_area and src_area != 0 and dst_area != 0:
                            # ABR (Area Border Router) orqali yo'naltirish
                            has_backbone = any(areas.get(s, -1) == 0 for s in path)
                            if not has_backbone:
                                # Backbone orqali suboptimal path qo'shish
                                backbone_sws = [s for s, a in areas.items() if a == 0]
                                if backbone_sws:
                                    abr = backbone_sws[0]
                                    p1 = _reconstruct_path(prev, src_sw, abr)
                                    _, prev2 = _dijkstra_weighted(graph, abr, _compute_ospf_cost)
                                    p2 = _reconstruct_path(prev2, abr, d_sw)
                                    if p1 and p2:
                                        path = p1 + p2[1:]  # ABR orqali

                        # Stub area: default route faqat
                        if areas.get(d_sw, 0) == 2:
                            pass  # Normal Dijkstra path ishlaydi

                        # Timer-driven staleness: SPF hasn't reconverged yet
                        if random.random() < _OSPF_STALE_PROB:
                            path = path + [path[-1]]

                        paths[(src_h, dst_h)] = path

    # ══════════════════════════════════════════════
    #  IS-IS — Link-state, wide metric, L1/L2 hierarchy
    # ══════════════════════════════════════════════
    elif routing_mode == "isis":
        levels = _assign_isis_levels(topo)

        for src_sw in topo["switches"]:
            src_level = levels.get(src_sw, "L1")

            # IS-IS metric-weighted Dijkstra
            _, prev = _dijkstra_weighted(graph, src_sw, _compute_isis_metric)

            for src_h, s_sw in host_sw.items():
                if s_sw != src_sw:
                    continue
                for dst_h, d_sw in host_sw.items():
                    if s_sw == d_sw:
                        paths[(src_h, dst_h)] = [src_sw]
                        continue

                    dst_level = levels.get(d_sw, "L1")
                    path = _reconstruct_path(prev, src_sw, d_sw)
                    if path:
                        # L1→L2 hierarchy: L1 router L1L2 orqali L2 ga o'tadi
                        if src_level == "L1" and dst_level == "L2":
                            # L1L2 redistribution point topish
                            l1l2_routers = [s for s, l in levels.items() if l == "L1L2"]
                            if l1l2_routers:
                                # Eng yaqin L1L2 ni topish
                                best_rp = None
                                best_cost = float("inf")
                                for rp in l1l2_routers:
                                    p = _reconstruct_path(prev, src_sw, rp)
                                    if p and len(p) < best_cost:
                                        best_cost = len(p)
                                        best_rp = rp
                                if best_rp:
                                    _, prev2 = _dijkstra_weighted(graph, best_rp, _compute_isis_metric)
                                    p1 = _reconstruct_path(prev, src_sw, best_rp)
                                    p2 = _reconstruct_path(prev2, best_rp, d_sw)
                                    if p1 and p2:
                                        path = p1 + p2[1:]

                        # Timer-driven staleness: LSP hasn't been refreshed yet
                        if random.random() < _ISIS_STALE_PROB:
                            path = path + [path[-1]]

                        paths[(src_h, dst_h)] = path

    # ══════════════════════════════════════════════
    #  EIGRP — Composite metric (BW + Delay), DUAL
    # ══════════════════════════════════════════════
    elif routing_mode == "eigrp":
        variance = EIGRP_CONFIG["variance"]

        for src_sw in topo["switches"]:
            # EIGRP composite metric
            dist, prev = _dijkstra_weighted(graph, src_sw, _compute_eigrp_metric)

            # Successor (best path) va Feasible Successor (backup)
            for src_h, s_sw in host_sw.items():
                if s_sw != src_sw:
                    continue
                for dst_h, d_sw in host_sw.items():
                    if s_sw == d_sw:
                        paths[(src_h, dst_h)] = [src_sw]
                        continue

                    best_path = _reconstruct_path(prev, src_sw, d_sw)
                    if not best_path:
                        continue

                    # EIGRP unequal-cost load balancing
                    # Feasible successor: metric <= variance * successor metric
                    best_metric = dist.get(d_sw, float("inf"))
                    all_candidates = _bfs_all_paths(graph, src_sw).get(d_sw, [])

                    feasible = [best_path]
                    for cand in all_candidates:
                        # Candidate metric
                        cand_metric = 0
                        for i in range(len(cand) - 1):
                            for neighbor, params in graph.get(cand[i], []):
                                if neighbor == cand[i + 1]:
                                    cand_metric += _compute_eigrp_metric(params)
                                    break
                        # Feasibility condition: metric < variance * best
                        if cand_metric <= variance * best_metric and cand != best_path:
                            feasible.append(cand)
                            if len(feasible) >= 4:
                                break

                    # Traffic share: best path 60%, feasible 40%
                    if len(feasible) > 1 and random.random() < 0.4:
                        paths[(src_h, dst_h)] = random.choice(feasible[1:])
                    else:
                        paths[(src_h, dst_h)] = best_path

    # ══════════════════════════════════════════════
    #  BGP — AS-PATH, local-pref, MED, communities
    # ══════════════════════════════════════════════
    elif routing_mode == "bgp":
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

                # BGP best path selection
                scored = []
                for p in all_p:
                    score, as_path = _bgp_path_score(topo, p)
                    scored.append((score, p, as_path))
                scored.sort(key=lambda x: x[0])

                best = scored[0]
                path = best[1]

                # Route dampening: unstable routes suppress
                suppress, penalty = _bgp_route_dampening(random.randint(0, 3))
                if suppress and len(scored) > 1:
                    path = scored[1][1]  # Backup path

                # iBGP vs eBGP path selection
                dst_as = topo["switches"][dst_sw]["as"]
                if src_as == dst_as:
                    # iBGP — prefer IGP metric (shortest)
                    path = min(all_p, key=len)
                else:
                    # eBGP — prefer shortest AS-PATH
                    pass  # Already selected by score

                # MED-based tiebreaker: 20% chance of alternative
                if len(scored) > 1 and random.random() < 0.2:
                    path = scored[1][1]

                paths[(src_h, dst_h)] = path

    # ══════════════════════════════════════════════
    #  ECMP — Equal-Cost Multi-Path
    # ══════════════════════════════════════════════
    elif routing_mode == "ecmp":
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
                        paths[(src_h, dst_h)] = candidates

    # ══════════════════════════════════════════════
    #  SPF — Generic Shortest Path First (delay-weighted)
    # ══════════════════════════════════════════════
    elif routing_mode == "spf":
        for sw in topo["switches"]:
            _, prev = compute_dijkstra(graph, sw, "delay")
            for h_name, h_info in topo["hosts"].items():
                target_sw = h_info["switch"]
                if target_sw == sw:
                    # Same-switch host pair: pre-existing gap left this pair
                    # out entirely (compute_dijkstra has nothing to route to
                    # itself). Mirror the [src_sw]-only path every other mode
                    # uses for co-located hosts.
                    for src_h, src_sw in host_sw.items():
                        if src_sw == sw and src_h != h_name:
                            paths[(src_h, h_name)] = [sw]
                    continue
                path = _reconstruct_path(prev, sw, target_sw)
                if path:
                    for src_h, src_sw in host_sw.items():
                        if src_sw == sw:
                            paths[(src_h, h_name)] = path

    # ══════════════════════════════════════════════
    #  Policy — AS preference based
    # ══════════════════════════════════════════════
    elif routing_mode == "policy":
        for src_h, src_sw in host_sw.items():
            src_as = topo["switches"][src_sw]["as"]
            for dst_h, dst_sw in host_sw.items():
                if src_sw == dst_sw:
                    paths[(src_h, dst_h)] = [src_sw]
                    continue
                all_p = _bfs_all_paths(graph, src_sw).get(dst_sw, [])
                if not all_p:
                    continue
                scored = []
                for p in all_p:
                    as_set = set(topo["switches"].get(s, {}).get("as", 0) for s in p)
                    cross_as = len(as_set)
                    score = cross_as * 10 + len(p)
                    scored.append((score, p))
                scored.sort()
                if len(scored) > 1 and random.random() < 0.3:
                    paths[(src_h, dst_h)] = scored[1][1]
                else:
                    paths[(src_h, dst_h)] = scored[0][1]

    # ══════════════════════════════════════════════
    #  Static — Admin-defined shortest paths
    # ══════════════════════════════════════════════
    elif routing_mode == "static":
        # Static routing: admin bir marta qo'lda yozgan, inverse-bandwidth
        # bo'yicha og'irlashtirilgan Dijkstra (eng semiz linkni tanlaydi,
        # kechikishga qaramaydi) -- l2_learn'ning hop-count BFS'idan tubdan
        # farqli siyosat. Har bir topologiya uchun bir marta hisoblanadi va
        # keshlanadi (haqiqiy admin ham har paketda qayta hisoblamaydi).
        sig = _topology_signature(topo)
        prev_by_src = _static_prev_cache.get(sig)
        if prev_by_src is None:
            prev_by_src = {}
            for src_sw in topo["switches"]:
                _, prev = _dijkstra_weighted(graph, src_sw, _compute_static_weight)
                prev_by_src[src_sw] = prev
            _static_prev_cache[sig] = prev_by_src

        for src_sw in topo["switches"]:
            prev = prev_by_src[src_sw]
            for src_h, s_sw in host_sw.items():
                if s_sw != src_sw:
                    continue
                for dst_h, d_sw in host_sw.items():
                    if s_sw == d_sw:
                        paths[(src_h, dst_h)] = [src_sw]
                        continue
                    path = _reconstruct_path(prev, src_sw, d_sw)
                    if path:
                        paths[(src_h, dst_h)] = path
        for src_h, src_sw in host_sw.items():
            for dst_h, dst_sw in host_sw.items():
                if src_h != dst_h and src_sw == dst_sw:
                    paths[(src_h, dst_h)] = [src_sw]

    # ══════════════════════════════════════════════
    #  Hybrid — Real internetga o'xshash: AS ichida OSPF (IGP),
    #  AS'lar orasida BGP (EGP), teng narxli yo'llarda ECMP
    # ══════════════════════════════════════════════
    elif routing_mode == "hybrid":
        as_of_switch = {sw: info.get("as", 0) for sw, info in topo["switches"].items()}

        def _subgraph_for_as(as_id):
            sg = {}
            for u, neighbors in graph.items():
                if as_of_switch.get(u) != as_id:
                    continue
                sg[u] = [(v, p) for v, p in neighbors if as_of_switch.get(v) == as_id]
            return sg

        as_subgraphs = {as_id: _subgraph_for_as(as_id) for as_id in set(as_of_switch.values())}

        def _path_ospf_cost(path):
            cost = 0
            for i in range(len(path) - 1):
                for v, params in graph.get(path[i], []):
                    if v == path[i + 1]:
                        cost += _compute_ospf_cost(params)
                        break
            return cost

        for src_h, src_sw in host_sw.items():
            src_as = as_of_switch.get(src_sw, 0)
            for dst_h, dst_sw in host_sw.items():
                if src_sw == dst_sw:
                    paths[(src_h, dst_h)] = [src_sw]
                    continue

                dst_as = as_of_switch.get(dst_sw, 0)

                if src_as == dst_as:
                    # Intra-AS — IGP (OSPF-uslubidagi cost-weighted Dijkstra),
                    # faqat shu AS switchlari orqali
                    sg = as_subgraphs.get(src_as, {})
                    _, prev = _dijkstra_weighted(sg, src_sw, _compute_ospf_cost)
                    path = _reconstruct_path(prev, src_sw, dst_sw)
                    if not path:
                        # AS bo'lingan bo'lsa — to'liq grafga qaytish
                        _, prev_full = _dijkstra_weighted(graph, src_sw, _compute_ospf_cost)
                        path = _reconstruct_path(prev_full, src_sw, dst_sw)
                    if path:
                        # ECMP — teng narxli yo'llar orasida tanlash
                        all_p = _bfs_all_paths(sg or graph, src_sw).get(dst_sw, [])
                        if all_p:
                            best_cost = _path_ospf_cost(path)
                            tied = [p for p in all_p if _path_ospf_cost(p) == best_cost]
                            if len(tied) > 1:
                                path = random.choice(tied)
                        paths[(src_h, dst_h)] = path
                else:
                    # Inter-AS — EGP (BGP-uslubidagi AS-PATH/local-pref tanlash)
                    all_p = _bfs_all_paths(graph, src_sw).get(dst_sw, [])
                    if not all_p:
                        continue
                    scored = [(_bgp_path_score(topo, p)[0], p) for p in all_p]
                    scored.sort(key=lambda x: x[0])
                    best_score = scored[0][0]
                    # ECMP — teng BGP-skorli yo'llar orasida tanlash
                    tied = [p for s, p in scored if s == best_score]
                    paths[(src_h, dst_h)] = random.choice(tied) if len(tied) > 1 else scored[0][1]

    # ══════════════════════════════════════════════
    #  L2 Learn — Default BFS flooding
    # ══════════════════════════════════════════════
    elif routing_mode == "l2_learn":
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
        for src_h, src_sw in host_sw.items():
            for dst_h, dst_sw in host_sw.items():
                if src_h != dst_h and src_sw == dst_sw:
                    paths[(src_h, dst_h)] = [src_sw]

    else:
        raise ValueError(
            f"Noma'lum routing_mode: {routing_mode!r}. Valid modes: "
            "l2_learn, rip, ospf, isis, eigrp, bgp, ecmp, spf, policy, static, hybrid"
        )

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

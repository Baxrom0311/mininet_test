"""Simulyatsiya topologiyalari: switchlar, hostlar, linklar, access-linklar."""

TOPOLOGIES = {
    # ═══ 1. THREE AS - kichik, 3 AS ═══
    "three_as": {
        "nat_private_subnet": "192.168.60.0/24",
        "switches": {
            "s1": {"as": 100, "role": "core",    "area": 0, "isis_level": "L2"},
            "s2": {"as": 100, "role": "border",  "area": 1, "isis_level": "L1L2"},
            "s3": {"as": 200, "role": "border",  "area": 1, "isis_level": "L1L2"},
            "s4": {"as": 200, "role": "servers", "area": 2, "isis_level": "L1"},
            "s5": {"as": 300, "role": "border",  "area": 1, "isis_level": "L1L2"},
            "s6": {"as": 300, "role": "access",  "area": 2, "isis_level": "L1"},
        },
        "hosts": {
            "dns1":   {"switch": "s1", "ip": "10.0.1.1/8", "role": "server"},
            "web1":   {"switch": "s4", "ip": "10.0.4.1/8", "role": "server"},
            "web2":   {"switch": "s4", "ip": "10.0.4.2/8", "role": "server"},
            "vid1":   {"switch": "s4", "ip": "10.0.4.3/8", "role": "server"},
            "api1":   {"switch": "s3", "ip": "10.0.3.1/8", "role": "server"},
            # Residential/mobile — NAT orqasidagi xususiy manzillar
            "fib1":   {"switch": "s6", "ip": "192.168.60.11/24", "role": "client"},
            "fib2":   {"switch": "s6", "ip": "192.168.60.12/24", "role": "client"},
            "dsl1":   {"switch": "s6", "ip": "192.168.60.13/24", "role": "client"},
            "lte1":   {"switch": "s5", "ip": "192.168.60.21/24", "role": "client"},
            "lte2":   {"switch": "s5", "ip": "192.168.60.22/24", "role": "client"},
            "cab1":   {"switch": "s5", "ip": "192.168.60.23/24", "role": "client"},
            "nat_gw": {"switch": "s5", "ip": "10.0.5.9/8", "role": "gateway"},
        },
        "links": {
            ("s1","s2"): {"bw": 50,  "delay": "3ms",  "loss": 0.01, "jitter": "1ms",   "queue": 80},
            ("s3","s4"): {"bw": 80,  "delay": "0.5ms","loss": 0.005,"jitter": "0.2ms", "queue": 120},
            ("s5","s6"): {"bw": 20,  "delay": "2ms",  "loss": 0.05, "jitter": "1ms",   "queue": 40},
            ("s2","s3"): {"bw": 40,  "delay": "8ms",  "loss": 0.02, "jitter": "2ms",   "queue": 60},
            ("s2","s5"): {"bw": 30,  "delay": "12ms", "loss": 0.05, "jitter": "3ms",   "queue": 50},
            # Short hop but bandwidth-starved (legacy/oversubscribed link) --
            # deliberately makes the fewest-hop route NOT the fattest-pipe
            # route, so 'static' (bw-weighted) genuinely diverges from
            # 'l2_learn' (hop-count) on this topology. See routing.py fix #1.
            ("s1","s3"): {"bw": 6,   "delay": "5ms",  "loss": 0.01, "jitter": "1ms",   "queue": 100},
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
            "nat_gw": {"bw": 20, "delay": "2ms",  "loss": 0},
        },
    },

    # ═══ 2. FIVE AS - realistik internet ═══
    "five_as": {
        "nat_private_subnet": "192.168.50.0/24",  # nat_gw shu yerda joylashgan
        "switches": {
            "s1": {"as": 100, "role": "tier1_core",         "area": 0, "isis_level": "L2"},    # Tier-1 ISP core
            "s2": {"as": 200, "role": "tier2_border",       "area": 1, "isis_level": "L1L2"},  # Tier-2 ISP
            "s3": {"as": 200, "role": "tier2_access",       "area": 2, "isis_level": "L1"},
            "s4": {"as": 300, "role": "cdn_edge",           "area": 1, "isis_level": "L1L2"},  # CDN
            "s5": {"as": 300, "role": "cdn_origin",         "area": 2, "isis_level": "L1"},
            "s6": {"as": 400, "role": "enterprise_core",    "area": 1, "isis_level": "L1L2"},  # Enterprise
            "s7": {"as": 400, "role": "enterprise_access",  "area": 2, "isis_level": "L1"},
            "s8": {"as": 500, "role": "residential_agg",    "area": 1, "isis_level": "L1L2"},  # Residential ISP
            "s9": {"as": 500, "role": "residential_access", "area": 2, "isis_level": "L1"},
        },
        "hosts": {
            "root1":  {"switch": "s1", "ip": "10.1.0.1/8",  "role": "server"},  # DNS root
            "isp_gw": {"switch": "s2", "ip": "10.2.0.1/8",  "role": "server"},  # ISP gateway
            "cdn1":   {"switch": "s4", "ip": "10.4.0.1/8",  "role": "server"},  # CDN edge
            "cdn2":   {"switch": "s4", "ip": "10.4.0.2/8",  "role": "server"},
            "origin": {"switch": "s5", "ip": "10.5.0.1/8",  "role": "server"},  # Origin server
            "corp1":  {"switch": "s7", "ip": "10.7.0.1/8",  "role": "client"},  # Enterprise
            "corp2":  {"switch": "s7", "ip": "10.7.0.2/8",  "role": "client"},
            # Residential/mobile — NAT orqasidagi xususiy (RFC1918) manzillar.
            # Tashqariga chiqishda nat_gw ularni o'z ommaviy IP'siga tarjima qiladi.
            "home1":  {"switch": "s9", "ip": "192.168.50.11/24", "role": "client"},
            "home2":  {"switch": "s9", "ip": "192.168.50.12/24", "role": "client"},
            "mob1":   {"switch": "s8", "ip": "192.168.50.21/24", "role": "client"},
            "mob2":   {"switch": "s8", "ip": "192.168.50.22/24", "role": "client"},
            # NAT gateway — bitta interfeys (s8), ustida ikkita manzil:
            # ommaviy (bu "ip", 10.x.x.x) + xususiy (192.168.50.1, kod orqali
            # setup_nat_gateway()da qo'shiladi). "gateway" roli — TrafficGen
            # uni client/server sifatida tanlamaydi, faqat NAT/forward qiladi.
            "nat_gw": {"switch": "s8", "ip": "10.8.0.9/8", "role": "gateway"},
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
            # Backup: Enterprise <-> CDN direct peering (private leased line, same
            # city pair as the s6-s4 backup above). Deliberately high-bw/high-delay
            # -- inverted vs. s6-s4's low-bw/high-delay and vs. this topology's
            # general bw-delay correlation -- so bandwidth-optimizing protocols
            # (OSPF/EIGRP/ISIS, which chase the fat single hop) and delay-optimizing
            # ones (SPF, which chases the lower cumulative latency of the longer
            # multi-hop routes) genuinely disagree on the best path. See fix #5.
            ("s7","s5"): {"bw": 90,  "delay": "30ms", "loss": 0.01,  "jitter": "2ms",   "queue": 90},
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
            "nat_gw": {"bw": 30, "delay": "1ms",  "loss": 0},
        },
    },

    # ═══ 3. DATACENTER - Fat-tree ═══
    "datacenter": {
        "switches": {
            "s1": {"as": 100, "role": "core1", "area": 0, "isis_level": "L2"},
            "s2": {"as": 100, "role": "core2", "area": 0, "isis_level": "L2"},
            "s3": {"as": 100, "role": "agg1",  "area": 1, "isis_level": "L1L2"},
            "s4": {"as": 100, "role": "agg2",  "area": 1, "isis_level": "L1L2"},
            "s5": {"as": 100, "role": "tor1",  "area": 2, "isis_level": "L1"},   # Top of Rack
            "s6": {"as": 100, "role": "tor2",  "area": 2, "isis_level": "L1"},
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
        "nat_private_subnet": "192.168.70.0/24",
        "switches": {
            "s1": {"as": 100, "role": "core",          "area": 0, "isis_level": "L2"},
            "s2": {"as": 100, "role": "distribution1", "area": 1, "isis_level": "L1L2"},
            "s3": {"as": 100, "role": "distribution2", "area": 1, "isis_level": "L1L2"},
            "s4": {"as": 100, "role": "access_bldg_a", "area": 2, "isis_level": "L1"},
            "s5": {"as": 100, "role": "access_bldg_b", "area": 2, "isis_level": "L1"},
            # DMZ: externally-reachable servers, modeled as NSSA (external routes
            # injected without full backbone LSA flooding).
            "s6": {"as": 100, "role": "dmz",           "area": 3, "isis_level": "L1"},
            # ISP gateway: boundary to the outside AS -- redistribution point.
            "s7": {"as": 200, "role": "isp_gateway",   "area": 3, "isis_level": "L2"},
        },
        "hosts": {
            "www":   {"switch": "s6", "ip": "10.0.6.1/8", "role": "server"},
            "mail":  {"switch": "s6", "ip": "10.0.6.2/8", "role": "server"},
            "db":    {"switch": "s1", "ip": "10.0.1.1/8", "role": "server"},
            # Kampus klient qurilmalari — NAT orqasidagi xususiy manzillar
            "pc1":   {"switch": "s4", "ip": "192.168.70.11/24", "role": "client"},
            "pc2":   {"switch": "s4", "ip": "192.168.70.12/24", "role": "client"},
            "pc3":   {"switch": "s5", "ip": "192.168.70.13/24", "role": "client"},
            "pc4":   {"switch": "s5", "ip": "192.168.70.14/24", "role": "client"},
            "wifi1": {"switch": "s4", "ip": "192.168.70.21/24", "role": "client"},
            "wifi2": {"switch": "s5", "ip": "192.168.70.22/24", "role": "client"},
            "inet":  {"switch": "s7", "ip": "10.0.7.1/8", "role": "server"},
            "nat_gw": {"switch": "s2", "ip": "10.0.2.9/8", "role": "gateway"},
        },
        "links": {
            ("s1","s2"): {"bw": 50, "delay": "0.5ms","loss": 0.005,"jitter": "0.1ms","queue": 100},
            ("s1","s3"): {"bw": 50, "delay": "0.5ms","loss": 0.005,"jitter": "0.1ms","queue": 100},
            ("s2","s4"): {"bw": 20, "delay": "1ms",  "loss": 0.01, "jitter": "0.3ms","queue": 50},
            ("s3","s5"): {"bw": 20, "delay": "1ms",  "loss": 0.01, "jitter": "0.3ms","queue": 50},
            ("s1","s6"): {"bw": 30, "delay": "0.2ms","loss": 0.002,"jitter": "0.05ms","queue":80},
            ("s1","s7"): {"bw": 15, "delay": "10ms", "loss": 0.05, "jitter": "3ms",  "queue": 30},
            # Short hop but bandwidth-starved (aging distribution cross-link)
            # -- same rationale as three_as's s1-s3 retune: makes the
            # fewest-hop route NOT the fattest-pipe route so 'static' and
            # 'l2_learn' genuinely diverge here too. See routing.py fix #1.
            ("s2","s3"): {"bw": 4,  "delay": "0.3ms","loss": 0.003,"jitter": "0.1ms","queue": 60},
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
            "nat_gw": {"bw": 20, "delay": "1ms", "loss": 0},
        },
    },
}

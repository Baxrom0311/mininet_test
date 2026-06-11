"""
Barcha konfiguratsiya bir joyda.
5 ta AS, har xil link turlari, trafik profillari.
"""

# ─── ONOS ────────────────────────────────────────────────
ONOS_IMAGE = "onosproject/onos:2.7.0"
ONOS_CONTAINER = "onos"
ONOS_API = "http://127.0.0.1:8181/onos/v1"
ONOS_USER = "karaf"
ONOS_PASS = "karaf"
ONOS_OF_PORT = 6653
ONOS_APPS = [
    "org.onosproject.openflow",
    "org.onosproject.fwd",
    "org.onosproject.proxyarp",
    "org.onosproject.hostprovider",
    "org.onosproject.lldpprovider",
]

# ─── sFlow-RT ────────────────────────────────────────────
SFLOW_RT_IMAGE = "sflow/sflow-rt"
SFLOW_RT_CONTAINER = "sflow-rt"
SFLOW_RT_API = "http://127.0.0.1:8008"
SFLOW_COLLECTOR_PORT = 6343
SFLOW_SAMPLING_RATE = 64
SFLOW_POLLING_INTERVAL = 10

# ─── Data Collection ─────────────────────────────────────
DATA_DIR = "/data"
PCAP_DIR = f"{DATA_DIR}/pcap"
STATS_DIR = f"{DATA_DIR}/stats"
FLOWS_DIR = f"{DATA_DIR}/flows"
DATASET_DIR = f"{DATA_DIR}/datasets"
ONOS_STATS_DIR = f"{DATA_DIR}/onos_logs"
SFLOW_DATA_DIR = f"{DATA_DIR}/sflow_data"

ONOS_POLL_INTERVAL = 5       # ONOS stats har 5 sekundda
SFLOW_POLL_INTERVAL = 10     # sFlow data har 10 sekundda
IMPAIRMENT_CHANGE_MIN = 15   # Dinamik o'zgarish min interval (sek)
IMPAIRMENT_CHANGE_MAX = 60   # Dinamik o'zgarish max interval (sek)

# ─── Link profillari (real internet turlari) ─────────────
LINK_PROFILES = {
    "tier1_core": {
        "bw": 1000,        # Mbps
        "delay": "2ms",
        "jitter": "0.2ms",
        "loss": 0.001,     # %
        "max_queue_size": 500,
    },
    "tier1_border": {
        "bw": 500,
        "delay": "5ms",
        "jitter": "0.5ms",
        "loss": 0.005,
        "max_queue_size": 400,
    },
    "isp_core": {
        "bw": 500,
        "delay": "4ms",
        "jitter": "0.5ms",
        "loss": 0.01,
        "max_queue_size": 300,
    },
    "isp_border": {
        "bw": 200,
        "delay": "8ms",
        "jitter": "1ms",
        "loss": 0.02,
        "max_queue_size": 250,
    },
    "cdn_internal": {
        "bw": 10000,
        "delay": "0.3ms",
        "jitter": "0.05ms",
        "loss": 0.0001,
        "max_queue_size": 1000,
    },
    "enterprise_core": {
        "bw": 1000,
        "delay": "1ms",
        "jitter": "0.1ms",
        "loss": 0.005,
        "max_queue_size": 300,
    },
    "enterprise_access": {
        "bw": 100,
        "delay": "1ms",
        "jitter": "0.2ms",
        "loss": 0.01,
        "max_queue_size": 200,
    },
    "residential_backbone": {
        "bw": 1000,
        "delay": "3ms",
        "jitter": "0.3ms",
        "loss": 0.005,
        "max_queue_size": 400,
    },
    "access_fiber": {
        "bw": 100,
        "delay": "5ms",
        "jitter": "1ms",
        "loss": 0.05,
        "max_queue_size": 150,
    },
    "access_dsl": {
        "bw": 20,
        "delay": "25ms",
        "jitter": "5ms",
        "loss": 0.5,
        "max_queue_size": 80,
    },
    "access_lte": {
        "bw": 30,
        "delay": "40ms",
        "jitter": "15ms",
        "loss": 1.0,
        "max_queue_size": 60,
    },
    "access_cable": {
        "bw": 50,
        "delay": "15ms",
        "jitter": "3ms",
        "loss": 0.2,
        "max_queue_size": 100,
    },
    # Inter-AS linklar
    "peering_tier1_tier1": {
        "bw": 500,
        "delay": "15ms",
        "jitter": "2ms",
        "loss": 0.01,
        "max_queue_size": 300,
    },
    "peering_tier1_cdn": {
        "bw": 10000,
        "delay": "3ms",
        "jitter": "0.2ms",
        "loss": 0.001,
        "max_queue_size": 800,
    },
    "peering_isp_enterprise": {
        "bw": 200,
        "delay": "8ms",
        "jitter": "1.5ms",
        "loss": 0.02,
        "max_queue_size": 200,
    },
    "peering_isp_residential": {
        "bw": 1000,
        "delay": "10ms",
        "jitter": "1ms",
        "loss": 0.01,
        "max_queue_size": 350,
    },
}

# ─── AS ta'riflari ───────────────────────────────────────
# Har bir AS: turi, switchlar, hostlar, ichki linklar
# IP sxemasi: 10.<AS-id>.<switch-ichki-id>.<host>/24

AS_DEFINITIONS = {
    100: {
        "name": "TransitCore",
        "type": "tier1_transit",
        "description": "Tier-1 transit provayder (magistral)",
        "switches": ["s1", "s2", "s3", "s4", "s5"],
        "hosts": {
            "s1": [("h_t1_mon1", "10.100.1.1/16")],      # monitoring server
            "s5": [("h_t1_dns1", "10.100.5.1/16")],       # root DNS
        },
        "internal_links": [
            # Core full mesh
            ("s1", "s2", "tier1_core"),
            ("s1", "s3", "tier1_core"),
            ("s2", "s3", "tier1_core"),
            # Core to border
            ("s1", "s4", "tier1_border"),
            ("s2", "s5", "tier1_border"),
            ("s3", "s4", "tier1_border"),
            ("s3", "s5", "tier1_border"),
        ],
    },
    200: {
        "name": "RegionalISP",
        "type": "regional_isp",
        "description": "Regional ISP (shaharararo)",
        "switches": ["s6", "s7", "s8", "s9", "s10"],
        "hosts": {
            "s7": [("h_isp_cache1", "10.200.7.1/16")],    # cache server
            "s8": [("h_isp_dns1", "10.200.8.1/16")],      # local DNS
        },
        "internal_links": [
            ("s6", "s7", "isp_core"),
            ("s7", "s8", "isp_core"),
            ("s6", "s8", "isp_core"),
            ("s7", "s9", "isp_border"),
            ("s8", "s10", "isp_border"),
            ("s6", "s9", "isp_border"),    # redundant path
        ],
    },
    300: {
        "name": "CloudCDN",
        "type": "content_provider",
        "description": "Content/Cloud provayder (serverlar)",
        "switches": ["s11", "s12", "s13", "s14"],
        "hosts": {
            "s12": [
                ("h_cdn_web1", "10.30.12.1/16"),           # web server
                ("h_cdn_web2", "10.30.12.2/16"),           # web server 2
            ],
            "s13": [
                ("h_cdn_video1", "10.30.13.1/16"),         # video streaming
                ("h_cdn_video2", "10.30.13.2/16"),         # video streaming 2
            ],
            "s14": [
                ("h_cdn_api1", "10.30.14.1/16"),           # API server
                ("h_cdn_db1", "10.30.14.2/16"),            # database
                ("h_cdn_dns1", "10.30.14.3/16"),           # authoritative DNS
            ],
        },
        "internal_links": [
            ("s11", "s12", "cdn_internal"),
            ("s11", "s13", "cdn_internal"),
            ("s12", "s13", "cdn_internal"),
            ("s12", "s14", "cdn_internal"),
            ("s13", "s14", "cdn_internal"),
        ],
    },
    400: {
        "name": "Enterprise",
        "type": "enterprise",
        "description": "Korporativ tarmoq (ofis)",
        "switches": ["s15", "s16", "s17", "s18"],
        "hosts": {
            "s16": [("h_ent_srv1", "10.40.16.1/16")],     # internal server
            "s17": [
                ("h_ent_pc1", "10.40.17.1/16"),            # office PC
                ("h_ent_pc2", "10.40.17.2/16"),
                ("h_ent_voip1", "10.40.17.3/16"),          # VoIP phone
            ],
            "s18": [
                ("h_ent_pc3", "10.40.18.1/16"),
                ("h_ent_pc4", "10.40.18.2/16"),
                ("h_ent_voip2", "10.40.18.3/16"),
            ],
        },
        "internal_links": [
            ("s15", "s16", "enterprise_core"),
            ("s16", "s17", "enterprise_access"),
            ("s16", "s18", "enterprise_access"),
            ("s15", "s17", "enterprise_core"),    # redundant
        ],
    },
    500: {
        "name": "ResidentialISP",
        "type": "residential_isp",
        "description": "Uy foydalanuvchilari ISP",
        "switches": ["s19", "s20", "s21", "s22", "s23", "s24"],
        "hosts": {
            "s22": [
                ("h_res_dsl1", "10.50.22.1/16"),           # DSL user
                ("h_res_dsl2", "10.50.22.2/16"),
            ],
            "s23": [
                ("h_res_fib1", "10.50.23.1/16"),           # fiber user
                ("h_res_fib2", "10.50.23.2/16"),
                ("h_res_fib3", "10.50.23.3/16"),
            ],
            "s24": [
                ("h_res_lte1", "10.50.24.1/16"),           # LTE user
                ("h_res_lte2", "10.50.24.2/16"),
                ("h_res_cab1", "10.50.24.3/16"),           # cable user
                ("h_res_cab2", "10.50.24.4/16"),
            ],
        },
        "internal_links": [
            ("s19", "s20", "residential_backbone"),
            ("s19", "s21", "residential_backbone"),
            ("s20", "s21", "residential_backbone"),   # redundant
            ("s20", "s22", "access_dsl"),
            ("s20", "s23", "access_fiber"),
            ("s21", "s24", "access_lte"),             # LTE + cable on same agg
        ],
    },
}

# ─── AS'lar arasi linklar ────────────────────────────────
INTER_AS_LINKS = [
    # (switch_a, switch_b, profile)
    # AS100 <-> AS200 (Tier-1 to Regional ISP)
    ("s4", "s6", "peering_tier1_tier1"),
    ("s5", "s10", "peering_tier1_tier1"),     # redundant peering

    # AS100 <-> AS300 (Tier-1 to CDN - same datacenter)
    ("s3", "s11", "peering_tier1_cdn"),

    # AS200 <-> AS400 (ISP to Enterprise)
    ("s9", "s15", "peering_isp_enterprise"),

    # AS200 <-> AS500 (ISP to Residential ISP)
    ("s10", "s19", "peering_isp_residential"),

    # AS100 <-> AS500 (Tier-1 to Residential - backup)
    ("s4", "s19", "peering_tier1_tier1"),
]

# ─── Trafik profillari ───────────────────────────────────
# Real internet trafik aralashmasi
TRAFFIC_PROFILES = {
    "web_browsing": {
        "protocol": "tcp",
        "port": 80,
        "pattern": "bursty",         # Pareto distributed
        "avg_rate_kbps": 500,
        "duration_sec": 30,
        "weight": 0.30,              # trafikdagi ulushi
    },
    "https": {
        "protocol": "tcp",
        "port": 443,
        "pattern": "bursty",
        "avg_rate_kbps": 2000,
        "duration_sec": 60,
        "weight": 0.25,
    },
    "video_streaming": {
        "protocol": "udp",
        "port": 8080,
        "pattern": "constant",       # CBR
        "avg_rate_kbps": 5000,
        "duration_sec": 120,
        "weight": 0.20,
    },
    "dns_queries": {
        "protocol": "udp",
        "port": 53,
        "pattern": "poisson",
        "avg_rate_kbps": 10,
        "duration_sec": 300,
        "weight": 0.07,
    },
    "voip": {
        "protocol": "udp",
        "port": 5060,
        "pattern": "constant",       # G.711 CBR
        "avg_rate_kbps": 64,
        "duration_sec": 180,
        "weight": 0.03,
    },
    "ssh_interactive": {
        "protocol": "tcp",
        "port": 22,
        "pattern": "bursty",
        "avg_rate_kbps": 20,
        "duration_sec": 120,
        "weight": 0.03,
    },
    "bulk_transfer": {
        "protocol": "tcp",
        "port": 20,
        "pattern": "constant",       # elephant flow
        "avg_rate_kbps": 10000,
        "duration_sec": 60,
        "weight": 0.07,
    },
    "email_smtp": {
        "protocol": "tcp",
        "port": 25,
        "pattern": "bursty",
        "avg_rate_kbps": 100,
        "duration_sec": 10,
        "weight": 0.03,
    },
    "gaming": {
        "protocol": "udp",
        "port": 27015,
        "pattern": "constant",
        "avg_rate_kbps": 100,
        "duration_sec": 300,
        "weight": 0.02,
    },
}

# ─── Dinamik impairment ssenariylari ────────────────────
IMPAIRMENT_SCENARIOS = {
    "congestion_burst": {
        "description": "Qisqa muddatli congestion (5-15 sek)",
        "delay_add_ms": (10, 50),
        "loss_add_pct": (1.0, 5.0),
        "jitter_add_ms": (5, 20),
        "duration_sec": (5, 15),
        "probability": 0.3,           # har bir interval uchun ehtimollik
    },
    "link_degradation": {
        "description": "Sekin link yomonlashuvi (30-120 sek)",
        "delay_add_ms": (5, 20),
        "loss_add_pct": (0.5, 3.0),
        "jitter_add_ms": (2, 10),
        "duration_sec": (30, 120),
        "probability": 0.2,
    },
    "micro_burst": {
        "description": "Juda qisqa burst (1-3 sek)",
        "delay_add_ms": (20, 100),
        "loss_add_pct": (3.0, 10.0),
        "jitter_add_ms": (10, 50),
        "duration_sec": (1, 3),
        "probability": 0.15,
    },
    "route_flap": {
        "description": "Link o'chib-yonish (3-10 sek)",
        "link_down_sec": (3, 10),
        "probability": 0.05,
    },
}

# ─── Host rolelari (trafik gen uchun) ────────────────────
# Qaysi hostlar server, qaysilari client
SERVER_HOSTS = [
    "h_cdn_web1", "h_cdn_web2",      # web serverlar
    "h_cdn_video1", "h_cdn_video2",   # video serverlar
    "h_cdn_api1",                      # API server
    "h_cdn_dns1",                      # DNS
    "h_t1_dns1",                       # root DNS
    "h_isp_cache1",                    # cache
    "h_ent_srv1",                      # enterprise server
]

CLIENT_HOSTS = [
    "h_res_dsl1", "h_res_dsl2",
    "h_res_fib1", "h_res_fib2", "h_res_fib3",
    "h_res_lte1", "h_res_lte2",
    "h_res_cab1", "h_res_cab2",
    "h_ent_pc1", "h_ent_pc2", "h_ent_pc3", "h_ent_pc4",
    "h_ent_voip1", "h_ent_voip2",
]

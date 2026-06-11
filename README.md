# Real Internet Simulation v3

Mininet + SDN (os-ken/Ryu) yordamida real internet trafikini simulyatsiya qilish va ML/AI uchun dataset generatsiya qilish.

## Xususiyatlari

- **4 topologiya**: three_as (3 AS, 6 switch), five_as (5 AS, 9 switch), datacenter (Fat-tree, 6 switch), campus (7 switch)
- **4 routing algoritm**: L2 MAC learning, SPF (Dijkstra), ECMP (load balancing), Policy (BGP-like)
- **13 trafik turi**: web, video, dns, bulk, voip, ssh, gaming, email, iot, https, streaming, p2p, cloud
- **8 anomaliya turi**: port_scan, syn_flood, udp_flood, dns_amplify, slowloris, ping_sweep, brute_force, data_exfil
- **Real protokollar**: DNS resolution (cache, TTL, recursive), HTTP GET/POST, TCP connection states, Adaptive Bitrate Streaming
- **10 impairment turi**: packet loss, delay spike, bandwidth limit, link flap, congestion, packet reorder, buffer bloat, MTU blackhole, duplicate, jitter spike
- **Diurnal trafik pattern**: vaqtga asoslangan yuklama (kunduzi ko'p, tunda kam)
- **Path tracing**: Dijkstra/BFS asosida hop-by-hop marshrut

## Tizim talablari

- Ubuntu 22.04+ (yoki Docker)
- Python 3.10+
- Mininet 2.3+
- Open vSwitch 2.17+
- 1 GB+ RAM (2 GB tavsiya)

## O'rnatish

### Docker bilan (tavsiya etiladi)

```bash
# Build
docker build -t internet-sim .

# Run (--privileged kerak Mininet uchun)
docker run --privileged --rm -v $(pwd)/output:/data internet-sim \
    --topology five_as --routing spf --duration 300

# Boshqa topologiya
docker run --privileged --rm -v $(pwd)/output:/data internet-sim \
    --topology datacenter --routing ecmp --duration 180

# Interactive CLI
docker run --privileged --rm -it -v $(pwd)/output:/data internet-sim \
    --topology campus --routing spf --cli
```

### To'g'ridan-to'g'ri serverda

```bash
# O'rnatish
sudo bash install_light.sh

# yoki qo'lda
sudo apt install -y mininet openvswitch-switch python3-os-ken iperf3 hping3 tcpdump
pip install -r requirements.txt

# Ishga tushirish
sudo python3 light_simulation.py --topology five_as --routing spf --duration 300
```

## Ishlatish

### Asosiy buyruqlar

```bash
# Default (three_as + l2_learn, 180 soniya)
sudo python3 light_simulation.py

# 5 AS topologiya, SPF routing, 10 daqiqa
sudo python3 light_simulation.py --topology five_as --routing spf --duration 600

# Datacenter, ECMP load balancing
sudo python3 light_simulation.py --topology datacenter --routing ecmp

# Kampus, BGP-like policy routing
sudo python3 light_simulation.py --topology campus --routing policy --duration 300

# Mininet CLI (debug uchun)
sudo python3 light_simulation.py --topology three_as --cli

# Faqat dataset build (oldingi run dan)
python3 light_simulation.py --dataset-only

# Trafik yoki impairment o'chirish
sudo python3 light_simulation.py --no-traffic --no-impairments
```

### Topologiya vizualizatsiya

```bash
# PNG rasm generatsiya qilish (sudo kerak emas)
python3 light_simulation.py --topology five_as --routing spf --visualize
python3 light_simulation.py --topology three_as --routing ecmp --visualize
python3 light_simulation.py --topology datacenter --routing ecmp --visualize
python3 light_simulation.py --topology campus --routing policy --visualize
```

## Topologiyalar

### three_as (3 Autonomous System)
```
AS 100 (ISP Core)     AS 200 (Servers)      AS 300 (Users)
  [s1]──────────────────[s3]──[s4]            [s5]──[s6]
   │   core    border    │   servers    border │   access
   └────────[s2]─────────┘              ───────┘
             border                     
   dns1       │        api1,web1,web2   lte1,lte2,cab1
              └────────vid1             fib1,fib2,dsl1
```

### five_as (5 AS - Realistic Internet)
```
AS 100: s1 (Tier-1 core) ── root DNS
AS 200: s2-s3 (Tier-2 ISP) ── ISP gateway
AS 300: s4-s5 (CDN) ── cdn1, cdn2, origin
AS 400: s6-s7 (Enterprise) ── corp1, corp2
AS 500: s8-s9 (Residential) ── home1, home2, mob1, mob2
```

### datacenter (Fat-tree)
```
     [s1 core1]  [s2 core2]
      /    \      /    \
  [s3 agg1] [s4 agg2]
     |           |
  [s5 tor1]  [s6 tor2]
  srv1-3      srv4-6
```

### campus (University/Enterprise)
```
        [s7 ISP]
          |
  [s6 DMZ]──[s1 core]──[s2 dist1]──[s4 bldg_a]
  www,mail    db    |              pc1,pc2,wifi1
                  [s3 dist2]──[s5 bldg_b]
                              pc3,pc4,wifi2
```

## Dataset chiqishi

Har bir run `/data/datasets/` da 12 ta CSV fayl yaratadi:

| Fayl | Tarkib |
|------|--------|
| `flow_records.csv` | Asosiy trafik oqimlari (src/dst IP, port, bytes, RTT, path) |
| `flow_stats.csv` | OpenFlow switch statistikasi |
| `ping_results.csv` | ICMP ping natijalari |
| `iperf_results.csv` | iperf3 throughput o'lchovlari |
| `traceroute_hops.csv` | Hop-by-hop marshrut ma'lumotlari |
| `link_stats.csv` | Link bandwidth, delay, loss, queue |
| `impairment_events.csv` | Network impairment hodisalari |
| `topology_snapshot.csv` | Topologiya tuzilishi |
| `dns_queries.csv` | DNS so'rovlari (cache, TTL, recursive) |
| `http_transactions.csv` | HTTP GET/POST tranzaksiyalari |
| `anomaly_events.csv` | Hujum trafik hodisalari |
| `connection_states.csv` | TCP holatlari (ESTABLISHED, TIME_WAIT, ...) |

### Namuna ma'lumotlar

```python
import pandas as pd

flows = pd.read_csv("datasets/flow_records.csv")
print(flows.columns.tolist())
# ['timestamp', 'src', 'dst', 'src_ip', 'dst_ip', 'sport', 'dport',
#  'proto', 'bytes_sent', 'bytes_recv', 'rtt_ms', 'jitter_ms',
#  'retransmits', 'cwnd', 'path', 'hops', 'as_path', 'traffic_type',
#  'loss_pct', 'bw_mbps', 'tcp_cc', 'topology', 'routing', ...]

dns = pd.read_csv("datasets/dns_queries.csv")
anomaly = pd.read_csv("datasets/anomaly_events.csv")
```

## Barcha kombinatsiyalarni ishga tushirish

```bash
for topo in three_as five_as datacenter campus; do
    for route in l2_learn spf ecmp policy; do
        echo "=== $topo + $route ==="
        sudo python3 light_simulation.py \
            --topology $topo --routing $route --duration 300
        # Datasetni saqlash
        mkdir -p results/${topo}_${route}
        cp /data/datasets/*.csv results/${topo}_${route}/
    done
done
```

## Arxitektura

```
light_simulation.py (yagona fayl)
├── TOPOLOGIES dict          - 4 ta topologiya definitsiyasi
├── TRAFFIC_MIX              - 13 trafik turi profillari
├── ANOMALY_MIX              - 8 hujum turi profillari
├── IMPAIRMENT_EVENTS        - 10 impairment turi
├── SDNController class      - os-ken OpenFlow controller
│   ├── L2 learning
│   ├── SPF (Dijkstra)
│   ├── ECMP (hash-based)
│   └── Policy (BGP-like)
├── TrafficGen class         - Trafik generatsiyasi
│   ├── _iperf_loop()        - TCP/UDP throughput
│   ├── _ping_loop()         - ICMP latency
│   ├── _http_loop()         - Real HTTP traffic
│   ├── _dns_loop()          - DNS resolution
│   ├── _anomaly_loop()      - Attack traffic
│   ├── _connection_state_loop() - TCP state tracking
│   └── _adaptive_bitrate_loop() - Video ABR
├── Impairments class        - Network impairment injection
├── build_dataset()          - JSONL -> CSV/Parquet
└── visualize_topology()     - PNG topology map
```

## Litsenziya

MIT

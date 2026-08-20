# Real Internet Simulation v3

Mininet + SDN (os-ken/Ryu) yordamida real internet trafikini simulyatsiya qilish va ML/AI uchun dataset generatsiya qilish.

## Xususiyatlari

- **4 topologiya**: three_as (3 AS, 6 switch), five_as (5 AS, 9 switch), datacenter (Fat-tree, 6 switch), campus (7 switch)
- **11 routing algoritm**: L2 MAC learning, RIP v2, OSPF, IS-IS, EIGRP, BGP, ECMP, SPF (Dijkstra), Policy (BGP-like), Static, Hybrid (intra-AS OSPF + inter-AS BGP + ECMP)
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

### Git LFS kerak (klonlashdan oldin)

Repo'dagi dataset CSV fayllari (`results/**/*.csv`) **Git LFS** orqali saqlanadi
(`.gitattributes`ga qarang). LFS o'rnatilmagan holda klonlansa, CSV o'rniga kichik "pointer"
fayllari tushadi va `combine_datasets.py`/ML skriptlari xato beradi. Shuning uchun klonlashdan
**oldin** LFS'ni o'rnating:

```bash
# Debian/Ubuntu
sudo apt install -y git-lfs
git lfs install

# so'ng klonlang — CSV'lar avtomatik yuklanadi
git clone <repo-url>

# Agar LFS'siz allaqachon klonlagan bo'lsangiz:
git lfs install && git lfs pull
```

### Docker bilan (tavsiya etiladi)

```bash
# Build
docker build -f docker/Dockerfile -t internet-sim .

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
pip install -r docker/requirements.txt

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

Har bir run `/data/datasets/` da har biri uchun `.csv` va `.parquet` juftlik yaratadi
(`dataset_builder.py`ning `build_dataset()` funksiyasi belgilaydi), plyus `metadata.json`.
Fayl faqat mos JSONL manbasida qatordan bo'lsagina yoziladi — masalan NAT'siz topologiyada
(`datacenter`) `nat_translations.csv` umuman paydo bo'lmaydi.

| Fayl | Tarkib |
|------|--------|
| `transport_events.csv` | Kontroller tomonidan qayd etilgan har paket (TCP/UDP/ICMP/ARP) metama'lumoti |
| `flow_stats.csv` | OpenFlow oqim (flow) statistikasi, davriy so'ralgan |
| `port_stats.csv` | Switch port statistikasi (rx/tx bayt/paket/drop, hisoblangan bytes_per_sec) |
| `rtt.csv` | Tasodifiy host juftliklari orasidagi o'lchangan ICMP RTT |
| `traffic_log.csv` | Generatsiya qilingan trafik (HTTP/iperf/DNS/anomaliya va h.k.) hodisa logi |
| `impairments.csv` | Qo'llanilgan tarmoq nosozliklari (congestion, link flap, jitter spike, ...) |
| `path_traces.csv` | Har host juftligi uchun nazariy (topologiya asosidagi) va real (ping) yo'l/RTT/loss |
| `hop_details.csv` | `path_traces`dan chiqarilgan, har hop uchun bitta qator (link bw/delay/loss/jitter) |
| `dns_queries.csv` | Ko'p bosqichli DNS so'rovlari (root/TLD/authoritative, cache hit/TTL) |
| `http_transactions.csv` | HTTP GET/POST tranzaksiyalari |
| `anomaly_events.csv` | Hujum trafik hodisalari (port_scan, syn_flood, ...) |
| `connection_states.csv` | TCP holatlari (ESTABLISHED, TIME_WAIT, ...) |
| `nat_translations.csv` | NAT gateway'dagi conntrack tarjimalari (faqat NAT'li topologiya + run muvaffaqiyatli conntrack o'qisa — pastdagi "Ma'lum cheklovlar"ga qarang) |

### Namuna ma'lumotlar

```python
import pandas as pd

events = pd.read_csv("datasets/transport_events.csv")
print(events.columns.tolist())

paths = pd.read_csv("datasets/path_traces.csv")
dns = pd.read_csv("datasets/dns_queries.csv")
anomaly = pd.read_csv("datasets/anomaly_events.csv")
```

## Ma'lum cheklovlar

- **NAT tarjimalari Docker ichida ishonchsiz**: `NATMonitor` NAT gateway hostida
  `/proc/net/nf_conntrack`ni o'qib `nat_translations.csv` yaratadi. Docker ichida
  (`--privileged` bilan ham) bu fayl ba'zan umuman yaratilmaydi — repo'dagi
  `results/combined/` (Docker orqali yig'ilgan) shunga misol. Har bir run uchun bu faylning
  borligini alohida tekshiring, undan qat'iy tarzda tayanmang.
- **STP convergence**: halqali topologiyalarda (masalan `five_as`) OVS spanning-tree
  yaqinlashishi kerak, aks holda `pingAll`/QoS/NAT sozlash bosqichida vaqtinchalik yo'qotish
  ko'rinishi mumkin. `light_simulation.py` kutish vaqtini halqalar soniga qarab
  moslashtiradi (`docs/CLAUDE.md`dagi "Known limitations" bo'limiga qarang) — bu tuzatish
  jonli Mininet muhitida qayta tasdiqlanmagan.

## Barcha kombinatsiyalarni ishga tushirish

Barcha topologiya x 11 routing rejim (va istalgan sonli seed) kombinatsiyalarini
`run_campaign.sh` ketma-ket ishga tushiradi va har run'ning **butun** `datasets/` papkasini
(`metadata.json` bilan birga) `results/<topo>_<mode>_seed<s>/datasets/` ga ko'chiradi — bu aynan
`combine_datasets.py` kutgan layout:

```bash
# Default: 4 topologiya x 11 rejim x seed 0, har biri 300s
sudo bash run_campaign.sh

# Faqat five_as, 3 ta seed bilan (ilmiy takrorlanuvchanlik uchun)
sudo bash run_campaign.sh --topologies "five_as" --seeds "0 1 2" --duration 300

# Nima ishlashini oldindan ko'rish (hech narsa ishga tushmaydi)
bash run_campaign.sh --dry-run
```

So'ng birlashtirish:

```bash
python3 results/combine_datasets.py                    # barcha topologiya x rejim
python3 results/combine_datasets.py --topology five_as # faqat five_as* run'lar
```

`combine_datasets.py` `results/<topo>_<mode>[_seed<N>]/datasets/` papkalaridagi bir xil nomli
CSV'larni birlashtiradi va har qatorga provenance ustunlarini qo'shadi (`routing`, `topology`,
`seed`, `run_id`, `source_dir`) — natijadagi qatorni qaysi run yaratganini kuzatish mumkin.
Chiqish: `results/combined/*.csv`.

## Arxitektura

`light_simulation.py` — CLI/`main()` orkestratori (~270 qator); haqiqiy mantiq repo ildizidagi
alohida modullarga bo'lingan (barcha Mininet/os-ken importlari lazy — faqat funksiya ichida):

```
light_simulation.py    - CLI + main() orkestratori
├── config.py           - DATA_DIR, CONTROLLER_PORT
├── topologies.py        - TOPOLOGIES: 4 ta topologiya definitsiyasi
├── routing.py            - compute_paths(): 11 ta routing rejimi
├── network_build.py       - Mininet topologiya, QoS/DiffServ qdisc, NAT gateway/monitor
├── controller.py           - os-ken/Ryu SDN controller subprocess (start_controller)
├── traffic_gen.py           - TrafficGen: HTTP/iperf/DNS/anomaliya/TCP CC/ABR
├── impairments.py            - Impairments: 10 ta dinamik nosozlik turi
├── collector.py                - Collector: tcpdump + RTT o'lchov
├── path_tracer.py                - PathTracer: nazariy + real yo'l kuzatuvi
├── dataset_builder.py             - build_dataset(): JSONL -> CSV/Parquet
├── visualize.py                    - visualize_topology(): PNG topology map
└── netutil.py                       - parse_ping(), graph_diameter() (umumiy yordamchilar)
```

Eski (endi ishlatilmaydigan) simulyator avlodlari `legacy/`da arxivlangan — batafsil
`docs/CLAUDE.md`ga qarang. Docker fayllari (`Dockerfile`, `docker-entrypoint.sh`,
`requirements.txt`) `docker/` papkasida, lekin build konteksti hamon repo ildizi
(`docker build -f docker/Dockerfile -t internet-sim .`).

## Litsenziya

MIT

# NetLab — Mininet asosidagi vizual tarmoq laboratoriyasi

NetLab — tarmoqni sichqoncha bilan quradigan, keyin **haqiqiy Linux tarmoq
stekida** ishga tushiradigan ilova. Packet Tracer / OPNET uslubidagi vosita, lekin
qurilmalar ichida haqiqiy dasturlar ishlaydi: router'larda FRRouting, switch'larda
Open vSwitch yoki o'zingiz yozgan P4 dastur, SDN qurilmalarda o'zingiz yozgan
kontroller ilovasi ishlaydi.

```
Brauzer (UI) ──WebSocket/REST──► lab/server.py (FastAPI) ──► driver
                                                             ├─ real: Mininet + OVS + FRR + BMv2 + os-ken
                                                             └─ demo: Mininet'siz soddalashtirilgan simulyatsiya
```

## Ishga tushirish

### Linux / WSL2 (haqiqiy tarmoq)

```bash
sudo bash lab/install_lab.sh --all
```

```bash
sudo lab/.venv/bin/python -m lab.netlab --driver real
```

So'ng brauzerda `http://127.0.0.1:8080/` ni oching. WSL2'da Windows brauzeridan
ham shu manzil ishlaydi (WSL localhost forwarding). Agar ishlamasa,
`--host 0.0.0.0` bilan ishga tushiring: server avtomatik token yaratib, kirish
URL'ini ekranga chiqaradi.

**Yadro imkoniyatlari avtomatik aniqlanadi** (`/api/info` → `capabilities`):

| Holat | Nima qilinadi |
|---|---|
| `openvswitch` yadro moduli yo'q (WSL2 standart yadrosi, konteynerlar) | OVS **userspace datapath** (`datapath=netdev`) ishlatiladi; veth'larda TX checksum offload `ethtool -K … tx off` bilan o'chiriladi (aks holda TCP/UDP o'tmaydi, faqat ping o'tadi) |
| `sch_netem` yo'q | kabel `bw` (HTB) va `queue` (HTB ostida `pfifo limit`) ishlaydi, `delay/jitter/loss` e'tiborsiz qoldiriladi va bir marta ogohlantiriladi |
| userspace OVS port navbati | port socket'i navbatdagi paketlar uchun sndbuf sarflaydi (standart ~94 paket) — `net.core.wmem_default` 16 MB ga oshiriladi va OVS bir marta qayta ishga tushiriladi |
| OVS daemon ishlamayapti (systemd yo'q) | `ovs-ctl start` bilan avtomatik ishga tushiriladi |
| os-ken skripti boshqa Python versiyasiga tegishli | import qila oladigan interpreter (`python3.12 -m os_ken.cmd.manager` …) avtomatik tanlanadi |

Majburlash: `NETLAB_OVS_DATAPATH=kernel|user`.

Haqiqiy tarmoq integratsiya testi (root kerak; FRR/os-ken/BMv2 bo'lmasa tegishli qism o'tkazib yuboriladi):

```bash
sudo python3 -m lab.tests.real_integration            # l2 ospf rip emu queue conv vlan natdhcp apps qos bgp stp fw fw_emu mcast vrrp lag failures topogen sdn p4
```

### Istalgan kompyuter (demo rejim, Mininet'siz)

```bash
pip install -r lab/requirements.txt
```

```bash
python3 -m lab.netlab --driver demo --open
```

Demo rejim UI'ni o'rganish va topologiya chizish uchun. Unda L2/L3 forwarding
soddalashtirilgan (ping/traceroute, routing jadvallari, paket animatsiyasi), lekin
haqiqiy FRR/P4/SDN kodi bajarilmaydi.

Alohida oynada ochish uchun (pywebview kerak): `python3 -m lab.netlab --window`.

## Interfeys (React + shadcn/ui)

UI `lab/ui/` da: Vite, React 18, TypeScript, Tailwind va shadcn/ui komponentlari
(Radix primitivlari). Palitra oq/qora (neutral), panellar yengil "glass" effektli,
kun va tun mavzusi bor (yuqori o'ng burchakdagi tugma). Yig'ilgan versiya
`lab/web-react/` da repo'ga qo'shilgan, shuning uchun foydalanish uchun **Node.js
shart emas** — Python serveri uni `/` manzilida beradi. Eski (vanilla JS) interfeys
vaqtincha `/classic` manzilida qoladi.

UI'ni o'zgartirish:

```bash
cd lab/ui && npm install
```

```bash
npm run dev       # http://127.0.0.1:5173 (backend 8080 da ishlab tursin: /api va /ws proksi)
```

```bash
npm run build     # tiplar tekshiruvi + lab/web-react ga yig'ish (commit qiling)
```

Tuzilma: `src/lib/` — API, WebSocket hodisalari, zustand store, Wi-Fi modeli;
`src/components/ui/` — shadcn komponentlari; `src/features/` — kanvas, panellar va
oynalar (qurilma, P4, SDN, ssenariy, natijalar); `src/components/charts.tsx` —
chiziqli grafik va issiqlik xaritasi.

## Qurilmalar

| Qurilma | Ichida nima ishlaydi | "Ichiga kirish" |
|---|---|---|
| **PC / Server** | Linux network namespace | bash: `ip`, `ping`, `iperf3`, `tcpdump`, `curl` ... |
| **Switch (OVS)** | Open vSwitch bridge. *standalone* — OVS o'zi L2 learning (+STP), *sdn* — siz tanlagan kontroller boshqaradi | bash + `flows`, `fdb`, `ports`, `stp`; Jadvallar: FDB, OpenFlow flow'lar |
| **P4 Switch** | BMv2 `simple_switch` — data-plane'ni **o'zingiz dasturlaysiz** (P4_16, v1model) | `simple_switch_CLI` (table_add, counter_read, register_read ...); Hardware: pipeline diagrammasi, BMv2 paket trace |
| **Router (FRR)** | Namespace + zebra/staticd + tanlangan daemonlar (ospfd, bgpd, ripd, isisd, ospf6d, eigrpd, pimd, ldpd, bfdd) | `vtysh` — Cisco IOS'ga o'xshash CLI (`configure terminal`, `router ospf`, `show ip route`) |
| **SDN Router** | OVS bridge + **siz yozgan** kontroller ilovasi (ARP, LPM, TTL, flow o'rnatish) | Jadvallar: flow'lar; kontroller logi |
| **Wi-Fi AP** | Simsiz kirish nuqtasi (802.11g/n/ac/ax, 2.4/5 GHz, 20–160 MHz, uzatish quvvati). Host/server/router unga simsiz ulanadi | Sozlama: radio parametrlari + stansiyalar jadvali (masofa, RSSI, SNR, MCS) |

**Kabel (fizik qatlam)** parametrlari: tezlik (HTB), kechikish, jitter, yo'qotish
(netem) va navbat uzunligi. Ular tarmoq ishlayotganda ham darhol qo'llanadi.
Kabelni uzish/ulash ham mumkin (link down/up).

**Hardware bo'limi**: old panelda portlar (link va activity LED'lari, tezlik),
interfeys navbatlari (`tc -s qdisc`), P4 uchun pipeline (Parser → Ingress
match-action → Egress → Deparser), jadvallar, counter/register'lar va BMv2
trace logi.

## Ish oqimlari

**FRR (o'zingiz konfiguratsiya qilasiz)**
1. Router qo'shing, kabel ulang. **Sozlama** bo'limida interfeys IP'larini yozing va daemonlarni (masalan `ospfd`) belgilang.
2. **Startup-config** maydoniga FRR konfiguratsiyasini yozing yoki tarmoq ishlayotganda **CLI → vtysh** orqali jonli sozlang.
3. "Joriy running-config'ni olish" → **Saqlash**. Konfiguratsiya loyiha fayliga yoziladi va keyingi ishga tushirishda qo'llanadi.

**P4 (switch "hardware"ini o'zingiz yozasiz)**
1. Chap paneldagi **P4 dasturlar → +** (shablon: `l2_switch` yoki `ipv4_router`).
2. Muharrirda kodni yozing. **Tekshirish (p4c)** kompilyatsiya qiladi va pipeline'ni chizadi.
3. P4 switch → **Sozlama**: dasturni tanlang, runtime buyruqlarini yozing (`table_add ...`), so'ng **Kompilyatsiya + yuklash**. Dastur tarmoqni to'xtatmasdan almashtiriladi (`swap_configs`).
4. MAC manzillar deterministik: `00:00:00:<id hi>:<id lo>:<port>`. Ular Sozlama oynasida ko'rsatiladi.

**SDN (routingni o'zingiz yozasiz)**
1. **SDN kontrollerlar → +** (shablon: `l2_learning` yoki `l3_router`).
2. Switch'ni *sdn* rejimiga o'tkazing yoki SDN Router qo'shing va kontrollerni tanlang.
3. Kontroller `NETLAB_CONFIG` faylidan har bir qurilmaning portlari, IP'lari va statik marshrutlarini o'qiydi. UI'da biror narsa o'zgarsa, fayl avtomatik yangilanadi.
4. Kodni o'zgartirib **Saqlash + qayta ishga tushirish** tugmasini bosing — switchlar avtomatik qayta ulanadi.

## Ssenariylar, dvigatellar va natijalar (OPNET ish oqimi)

OPNET'dagi asosiy ish oqimi **Ssenariy → Simulyatsiya → Natijalar → Solishtirish**.
NetLab'da ham xuddi shunday:

1. Chap panel → **Ssenariylar → +**. Trafik ilovalarini qo'shing:
   - **UDP** — qat'iy tezlik (video, VoIP);
   - **TCP** — bor sig'imni oladi (fayl uzatish);
   - **Ping** — RTT o'lchovi.

   **Vaqtli hodisalar** ham qo'shish mumkin: kabel uzilishi yoki tiklanishi, kabel parametrlarining o'zgarishi.
2. **Dvigatel**ni tanlab, **Ishga tushirish** tugmasini bosing.
3. **Natijalar** oynasida o'tkazuvchanlik, kechikish, RTT, jitter, yo'qotish va kabel yuklamasini vaqt bo'yicha ko'rasiz. Bir nechta natijani belgilasangiz, ular bitta grafikda solishtiriladi. Xulosa jadvalini CSV qilib yuklab olish mumkin.

| Dvigatel | Nima | Qachon |
|---|---|---|
| **Analitik** | Oqim modeli: UDP qat'iy tezlikda, TCP max-min adolat + Mathis formulasi, navbat M/M/1. ~0.1 s | Tez "nima bo'ladi?" tahlili, katta topologiyalar |
| **ns-3** | Diskret-hodisali simulyatsiya (OPNET bilan bir xil tamoyil): paket darajasida, virtual vaqtda, deterministik (seed) | Ilmiy natija, takrorlanuvchan tajriba, real vaqtdan tez |
| **Mininet** | Haqiqiy emulyatsiya: iperf3/ping haqiqiy Linux TCP/UDP, FRR, OVS, P4, SDN orqali o'tadi | Protokol va konfiguratsiyani "haqiqatda" tekshirish |

**Kabel statistikasi** (OPNET'dagi *queue size* / *dropped packets*): har natijada
`links.<kabel>` — soniyalik nuqtalar, uchala dvigatelda bir xil maydonlar bilan:

| Maydon | Ma'nosi | Analitik | ns-3 | Mininet |
|---|---|---|---|---|
| `ab_bps` / `ba_bps` | yo'nalish bo'yicha yuklama | oqim modeli | qurilma TX navbati | `/proc/net/dev` |
| `util` | max(ab, ba) / bw | ✓ | ✓ | ✓ |
| `queue_ab` / `queue_ba` | jo'natuvchi tomondagi navbat (paket) | M/M/1 (1500 B) | pfifo qdisc + qurilma navbati | `tc -s qdisc` root qlen |
| `drops_ab` / `drops_ba` | shu soniyada navbat to'lib tashlangan paketlar | ortiqcha UDP / 1000 B | qdisc + qurilma drops | `tc` drops (userspace OVS'da switch kirdi-chiqdi bo'yicha tuzatilgan) |

**Qurilma statistikasi va konvergensiya** (OPNET *node statistics*, *convergence
duration*) — analitik va Mininet dvigatellarida:

- `nodes.<qurilma>` — har L3 qurilma (host/server/router) uchun soniyalik nuqtalar:
  `routes` (jadvaldagi marshrutlar soni), `fwd_pps` (tranzit paketlar), `rx_pps`/`tx_pps`,
  `noroute` (marshrut topilmay tashlangan), `discards` (IP/navbat tashlanishlari).
  Mininet'da `/proc/<pid>/net/{route,snmp,netstat}` dan o'qiladi (subprocess'siz).
- `route_events` — `{t, dev, added, removed}`: qaysi router qachon qaysi marshrutni
  oldi yoki yo'qotdi.
- `convergence` — har topologiya hodisasi uchun `converged_s`: hodisadan keyingi oxirgi
  marshrut o'zgarishigacha bo'lgan vaqt va qatnashgan qurilmalar.

ns-3 da ham bor: `Ipv4L3Protocol` trace'lari (UnicastForward/Rx/Tx/Drop) C++ ichida
(cppyy) sanaladi, marshrutlar `PrintRoutingTable` dan olinadi.

Masalan, `rip_ring` da r1-r2 kabeli uzilganda (standart taymerlar): analitik 15 s,
real FRR 19 s, ns-3 RIPv2 34 s (oxirgi o'zgarishgacha); OSPF (`ospf_lab`, hello=1 s) —
soniya ichida.

### RIP taymerlari

Router `config.rip_timers = {"update": 10, "timeout": 60, "garbage": 40}` (yoki
startup'dagi `timers basic 10 60 40`). Uchala dvigatel bir xil qiymatni oladi:

| | Qanday qo'llanadi | update=10 da natija (`rip_ring`) |
|---|---|---|
| Mininet (FRR) | `vtysh … router rip / timers basic U T G` | konvergensiya 4 s |
| ns-3 | `RipHelper` `UnsolicitedRoutingUpdate`/`TimeoutDelay`/`GarbageCollectionDelay` | trafik uzilishi 4 s |
| Analitik | muqobil yo'l keyingi periodik update'da: o'rtacha `update/2` (`rip_convergence_s` bilan almashtirish mumkin) | 5 s |

Parametrik tadqiqot: `{"kind": "router", "router": "r1", "all": true, "field": "rip_update"}`
— konvergensiya vaqti vs update taymeri (analitik / ns-3).

Kabelning `queue` parametri (paket) uchala dvigatelda navbat chegarasi: ns-3'da L3
tomonda `PfifoFastQueueDisc(MaxSize=queue)`, Mininet'da netem `limit` yoki (netem
bo'lmasa) HTB ostidagi `pfifo limit`. Jonli tarmoqda `stats` hodisasi ham
`queue_ab/queue_ba/qdrops` maydonlarini beradi.

### Ilova modellari: VoIP, HTTP, FTP (OPNET "Application Config")

| Tur | Maydonlar | Natija (`apps.<id>` va `flows.<id>`) |
|---|---|---|
| `voip` | `codec`: `g711` / `g729` | ikki yo'nalishli RTP (50 paket/s); har soniya **MOS** (ITU-T G.107 E-model: kechikish + 2×jitter bufer + kodek, yo'qotish → Ie_eff), `avg_mos`, `min_mos`, `quality` |
| `http` | `page` (masalan `500KB`), `think` (s) | har `think` soniyada sahifa (server→mijoz TCP); `pages_done/pages`, `avg_page_s`, `max_page_s`, `items[]` |
| `ftp` | `file` (`20MB`), `direction` (`download`/`upload`) | `transfer_s`, `avg_rate_bps`, `completed` |

`src` — mijoz, `dst` — server. Ilovalar dvigatelga oddiy udp/tcp ga yoyilib beriladi
(`lab/apps.py`); tcp `bytes` (cheklangan uzatish) uchala dvigatelda bor: analitikda
qadam ichida hodisaga asoslangan (uzatish tugasa ulushi boshqalarga o'tadi, ulanish +
slow start RTT'lari), ns-3'da `BulkSend MaxBytes` + PacketSink trace (aniq tugash vaqti),
Mininet'da `iperf3 -n`.

`ospf_lab` (10 Mbit/s r1-r2), VoIP + HTTP (500 KB / 3 s) + FTP (5 MB) birga:

| | VoIP MOS | HTTP sahifa | FTP 5 MB |
|---|---|---|---|
| Analitik | 4.12 | 0.75 s | 5.5 s |
| ns-3 | 4.08 | 0.76 s | 4.7 s |
| Mininet (real, netem'siz) | 4.39 | 1.05 s (iperf3 boshqaruv ulanishi ham) | 5.1 s |

Tadqiqot maydonlari: `app.codec`, `app.page`, `app.think`, `app.file`.

### QoS: navbat intizomi va DSCP

Kabel `params.qdisc`: `fifo` (standart), `prio` (qat'iy ustuvorlik), `fq_codel`
(oqimlar bo'yicha adolat + CoDel AQM). Ilova `dscp` (0..63); `voip` standart EF=46.
Yuqori sinf — DSCP >= 40 (EF, CS5..CS7).

| Dvigatel | prio | fq_codel |
|---|---|---|
| Analitik | yuqori sinf UDP birinchi xizmat qilinadi; navbati faqat o'z sinfi (M/M/1) | navbat ~5 ms (CoDel target) |
| ns-3 | `PrioQueueDisc` (EF → socket priority 4 → band 0) | `FqCoDelQueueDisc` |
| Mininet | HTB ierarxiyasi: 5:10 (prio 0, rate=bw) / 5:20 (prio 1) + u32 DSCP filtr — `sch_prio` shart emas | `fq_codel` (kernelda bo'lsa; bo'lmasa fifo + ogohlantirish) |

Mininet'da TOS: `iperf3 -S`, `ping -Q`. Tadqiqot maydoni: `link.qdisc`.

`ospf_lab`, l4 (10 Mbit/s) da VoIP + FTP 20 MB + 9 Mbit/s video — VoIP MOS:

| | fifo | prio | fq_codel |
|---|---|---|---|
| Analitik | 3.02 | 4.39 | 4.39 |
| ns-3 | 1.66 | 4.38 | 4.36 |

Real Mininet (10 Mbit/s kabel, 14 Mbit/s UDP yuklama): fifo MOS 1.96 → prio 4.28.

Eslatma: analitik modelda TCP navbati endi faqat tor joyda (to'yingan hop) hisoblanadi.

### Survivability: nosozlik ta'siri va N-1 sig'im rejasi

OPNET'dagi "Failure Impact" tahlili: har bir **yakka** nosozlik (bitta kabel yoki bitta
qurilma — uning barcha kabellari) uchun ssenariy ilovalari **barqaror holatda**
(protokollar yaqinlashgandan keyin) analitik model bilan qayta hisoblanadi va asosiy
holat bilan solishtiriladi. Marshrutlash, VLAN, NAT, firewall, STP, LAG, VRRP, BGP va
multicast — analitik dvigatelning o'zidan (qayta yozilmagan).

```bash
python3 -m lab.survivability lab/examples/rip_ring.json uzilish          # jadval
python3 -m lab.survivability lab/examples/vrrp_lab.json zaxira both --json
```

API: `POST /api/analysis/survivability` `{"project": {...}, "scenario": "uzilish", "targets": "links"|"devices"|"both", "include_hosts": false}`.

Har bir holat (`cases[]`, kritiklik bali bo'yicha kamayish tartibida):

| Maydon | Ma'nosi |
|---|---|
| `broken` | uzilgan oqimlar (yo'l yo'q / 100% yo'qotish) va sababi |
| `broken_demand_pct` | uzilgan talab ulushi (asosiy o'tkazuvchanlik bo'yicha) |
| `rerouted` | yo'li o'zgargan (boshqa yo'lga o'tgan) oqimlar |
| `degraded` | o'tkazuvchanligi 5% dan ko'p tushgan oqimlar |
| `udp_loss`, `overloaded` | yo'qotishi > 1% bo'lgan UDP oqimlar, util ≥ 0.95 bo'lib qolgan kabellar |
| `mos` | VoIP MOS o'zgarishi (asosiy → nosozlik) |
| `convergence` | taxminiy tiklanish vaqti: RIP `update/2`, STP 30 s / RSTP 1 s, VRRP Master_Down_Interval, LAG failover |
| `score` | kritiklik bali (uzilgan talab + ortiqcha yuk + degradatsiya + MOS tushishi) |

- `spof` — yagona nosozlik nuqtalari (nosozligi kamida bitta oqimni uzadigan kabel/qurilma).
- `baseline.down_flows` — asosiy holatda ham ishlamaydigan oqimlar (masalan firewall bloklagan) —
  nosozlik aybi sifatida hisoblanmaydi.
- `capacity_plan` — har kabel/yo'nalish: asosiy va eng yomon (N-1) inelastik yuklama (UDP, ping,
  VoIP, multicast), qaysi nosozlikda, `need_mbps = yuklama / 0.8`; `upgrade: true` — kabel
  tezligi yetmaydi. Elastik TCP har qanday sig'imni to'ldirgani uchun rejaga kirmaydi
  (`worst_tcp_mbps` — ma'lumot uchun). `upgrades` — faqat oshirish kerak bo'lganlari.

Hostlar/serverlar standartda nishon emas (`include_hosts`). FTP/HTTP ilovalari elastik TCP
sifatida baholanadi. Tahlil tez: namunalarda 0.01–0.15 s.

Misollar:
- `ospf_lab`: r1-r2 (l4), r1, r2, s1, s2 — SPOF (zaxira yo'l yo'q).
- `rip_ring`: l2 uzilsa hech narsa uzilmaydi, oqimlar r3 orqali (`RIP 15 s`); l3 ning eng yomon
  yuklamasi l2 nosozligida.
- `lag_lab`: bond a'zosi uzilsa uzilish yo'q, TCP o'tkazuvchanligi yarmiga tushadi.
- `vrrp_lab`: r1 nosozligida oqimlar r2 orqali (`VRRP 3.5 s`), r3 — SPOF.

### Nosozlik va tiklanish (MTBF / MTTR, mavjudlik)

OPNET'dagi "Failure/Recovery" kabi: ssenariyga `failures` qo'shiladi — kabel va qurilmalar
tasodifiy (lekin seed bo'yicha takrorlanadigan) vaqtlarda ishdan chiqadi va tiklanadi.

```json
"failures": {"seed": 1, "distribution": "exponential",
             "links":   {"mtbf_s": 120, "mttr_s": 15, "targets": ["l2", "l3"]},
             "devices": {"mtbf_s": 600, "mttr_s": 60, "targets": ["r2"]}}
```

- `distribution`: `exponential` (standart; ish/nosozlik davrlari eksponensial) yoki `fixed`
  (aniq MTBF/MTTR — tushuntirish va tekshirish uchun qulay).
- `targets`: ro'yxat yoki `"all"` (qurilmalarda host/server'siz).
- Qurilma nosozligi — uning barcha kabellari uziladi. Bir kabelni ikki sabab (kabel o'zi va
  qurilma) uzsa, kabel ikkalasi ham tugaguncha uzilgan qoladi.
- Nosozliklar oddiy `link_down` / `link_up` hodisalariga aylantiriladi (`source: "failure"`,
  `cause: [...]`), shuning uchun **uchala dvigatel** (analitik, ns-3, Mininet) ularni bir xil
  bajaradi, routing/STP/VRRP/BGP konvergensiyasi ham odatdagidek ishlaydi (`lab/failures.py`).

Natijada `availability`:

| Maydon | Ma'nosi |
|---|---|
| `flows.<id>.availability_pct` | oqim faol bo'lgan soniyalardan trafik o'tgan soniyalar ulushi (ping — javob kelgan soniyalar); http — yuklangan sahifalar, ftp — fayl yakunlandi |
| `flows.<id>.outages` | uzilishlar: boshlanish, tugash, davomiylik |
| `links.<id>` | ishlamay turgan vaqt (s, %), nosozliklar soni, oraliqlar |
| `theoretical.<nishon>` | A = MTBF / (MTBF + MTTR) |
| `mean_flow_availability`, `min_flow_availability` | oqimlar bo'yicha o'rtacha / eng yomon |

Parametrik tadqiqot: `{"kind": "failure", "group": "links", "field": "mtbf_s" | "mttr_s"}` yoki
`{"kind": "failure", "field": "seed"}` (bir nechta seed — Monte-Karlo o'rtachasi).

Namuna: `reliability_lab` — OSPF halqasi (zaxira yo'l) va zaxirasiz r3–srv2 kabeli:

| `ishonchlilik` (600 s, MTBF 120 s, MTTR 15 s; nazariy 88.9%) | pc1→srv1 (zaxirali) | pc1→srv2 (zaxirasiz) | Vaqt |
|---|---|---|---|
| Analitik | 93.3% | 85.3% | ~1 s |
| ns-3 | 93.8% | 86.1% | ~22 s |

Tekshiruvlar: qat'iy MTBF 100 / MTTR 10 (3600 s) — 91.1% (nazariy 90.9%); eksponensial,
6 seed o'rtachasi 92.1%; MTTR 5 / 20 / 60 s → 96.9 / 89.6 / 72.5%. Real Mininet
(MTBF 10 / MTTR 5, qat'iy): kabel 10–15 va 25–30 s da haqiqatan uziladi, ping mavjudligi
73.7% (kabel ishlagan vaqt 75%).

Cheklovlar: analitik dvigatel hodisalarni 1 s qadamda qo'llaydi (1 s dan qisqa uzilish
ko'rinmasligi mumkin); mavjudlik 1 s aniqlikda; nosozlik hodisasi foydalanuvchining shu kabel
uchun qo'lda yozgan hodisasini bekor qilishi mumkin (masalan, nosozlik tugashi `link_up`).

```bash
python3 -m lab.tests.smoke_failures
```

#### N-2 (juft nosozliklar), SRLG va kirish kabellari filtri

`analyze(..., order=2)` (API: `"order": 2`, CLI: `--n2`) — N-1 holatlariga qo'shimcha barcha
nishon juftliklari baholanadi (`n2` maydoni):

- **kesish:** ikkala nishon ham asosiy holatdagi va birorta N-1 holatdagi oqim yo'llarida
  ishlatilmasa — juftlik hech narsani o'zgartira olmaydi; qurilma va uning o'z kabeli
  juftligi — bitta qurilma nosozligi bilan bir xil. Ikkalasi ham o'tkazib yuboriladi (`pruned`);
- `max_cases` (standart 2000) — undan ko'p juftlik bo'lsa birinchilari baholanadi, `truncated: true`;
- `vulnerabilities` — **faqat juft nosozlikda** uziladigan oqimlar (har qanday yakka nosozlikka chidaydi);
- `min_cut` — har oqim uchun minimal kesim: `1` (SPOF), `2` (faqat juftlikda), `">=3"`
  (N-2 dan ham omon qoladi), `null` (asosiy holatda ishlamaydi — masalan firewall). Kesim
  baholangan nishonlar to'plami bo'yicha (`exclude_access` bilan host kabellari hisobga olinmaydi);
- `n2.capacity_plan` / `n2.upgrades` — eng yomon juft nosozlik bo'yicha sig'im rejasi (N-1 rejasidan alohida).

**SRLG** (umumiy xavf guruhi — bitta kanal/trassadagi kabellar): loyiha `"srlg": {"trassa1": ["l3", "l7"]}`
yoki so'rovda `srlg` — har guruh bitta nosozlik holati (`kind: "srlg"`), `srlg_broken` — oqim
qaysi guruhlarda uziladi.

**`exclude_access`** — host/server kabellari nishonlardan chiqariladi (SPOF ro'yxati ularsiz
qisqaroq; uchi host bo'lgan har bir kabel — bu kabel uzilsa, aslida faqat shu host uziladi).

```bash
python3 -m lab.survivability lab/examples/rip_ring.json uzilish links --n2 --exclude-access
python3 -m lab.survivability lab/examples/rip_ring.json uzilish links --srlg '{"trassa": ["l2", "l3"]}'
```

`rip_ring` (kirish kabellarisiz): N-1 da SPOF yo'q, lekin `l2 + l3` yoki `l2 + l4` birga uzilsa
pc1→srv1 uziladi — min kesim 2. Tezlik (N-2, barcha kabellar): kampus 41 kabel / 820 juftlik
~14 s, halqa 50 kabel / 1225 juftlik ~21 s; `exclude_access` bilan 2.4 s va 5.6 s.

### Katta topologiyalar: generator va tezlik

`lab/topogen.py` (OPNET "Rapid Configuration") — bir buyruq bilan katta tarmoq:

```bash
python3 -m lab.topogen campus --core 2 --dist 6 --access 12 --hosts 5 --flows 40 > lab/projects/kampus.json
```

Turlar: `ring` (halqa + diagonallar), `star` (markaz + filiallar), `tree` (ikkilik daraxt),
`campus` (core / distribution / access). Barcha routerlarda OSPF — real FRR uchun `startup`
ham yaratiladi (`network ... area 0`, router-router kabellarda point-to-point, hello 1 s).
API: `POST /api/topogen {"kind": "ring", "routers": 20, "hosts": 5, "flows": 40}`.

Analitik model `Net` keshlari bilan (kabel indeksi, L2 reach, marshrutlar, LPM, forward;
holat o'zgarmagan qadamlarda `Net` qayta ishlatiladi) — natijalar bayt darajasida bir xil:

| Topologiya | Avval | Hozir |
|---|---|---|
| 50 qurilma, 20 oqim, 40 s | 7.5 s | ~0.2 s |
| 280 qurilma (40 router), 100 oqim, 30 s | 12.4 s (faqat kabel indeksi va L2/marshrut keshlari bilan; to'liq keshsiz o'lchanmagan) | 0.9 s |
| Kampus 80 qurilma — survivability (N-1, links+devices) | — | 3.4 s |

Real Mininet: generatsiya qilingan 6 routerli halqa — OSPF ~9 s da yaqinlashadi, kabel
uzilganda muqobil yo'l ~1 s (`real_integration topogen`).

### Hisobotlar: HTML va CSV eksport (OPNET "Report")

`lab/report.py` — bitta fayldan iborat, tashqi resurssiz HTML hisobot (CSS va SVG grafiklar
ichida; yorug'/qorong'i mavzu avtomatik, palitra UI bilan bir xil) hamda CSV to'plami.

| So'rov | Natija |
|---|---|
| `GET /api/runs/{loyiha}/{run_id}/report.html` | bitta natija: xulosa, oqimlar, ilovalar (MOS / sahifa / fayl), mavjudlik, grafiklar (o'tkazuvchanlik, kechikish, yo'qotish, MOS, kabel yuklamasi), kabellar (max yuklama/navbat/tashlangan), qurilmalar, konvergensiya, marshrut o'zgarishlari, hodisalar |
| `GET /api/runs/{loyiha}/{run_id}/export.zip` | `flows.csv`, `flow_summary.csv`, `links.csv`, `nodes.csv`, `apps.csv`, `availability.csv`, `convergence.csv`, `route_events.csv`, `events.csv`, `results.json` |
| `POST /api/reports/compare` `{"project", "run_ids": [...]}` | natijalarni solishtirish (umumiy oqimlar bo'yicha jadval va grafiklar) |
| `GET /api/studies/{loyiha}/{study_id}/report.html` | parametrik tadqiqot: 1D — metrika vs parametr grafiklari, 2D — issiqlik xaritalari |
| `POST /api/analysis/survivability/report` (survivability bilan bir xil tana) | SPOF, kritiklik reytingi, N-1 sig'im rejasi |

Buyruq qatori:

```bash
python3 -m lab.report lab/results/ospf_lab/<run_id>.json -o hisobot.html
python3 -m lab.report lab/results/ospf_lab/ -o solishtirish.html          # papkadagi barcha natijalar
python3 -m lab.report lab/results/ospf_lab/ --study -o tadqiqot.html
python3 -m lab.report lab/results/ospf_lab/<run_id>.json --zip -o natija.zip
```

Barcha foydalanuvchi matnlari (nomlar, izohlar) escape qilinadi; token himoyasi hisobotlarga ham
tegishli. Hisobot hajmi odatda 5–30 KB.

### ns-3 tezligi: katta topologiyalar

ns-3 — paket darajasidagi simulyatsiya: vaqt deyarli to'liq C++ ichida (paketlar soni ×
hop'lar), Python tomoni (namuna olish, marshrutlar) sezilarsiz. Profil (gdb namunalari):
TCP/IP stek, navbatlar, bridge — "yagona sekin joy" yo'q. Shuning uchun:

**Aniq rejim (standart) — natija o'zgarmaydi**, faqat bizning qo'shimcha yuklamamiz olib tashlandi:

- FlowMonitor faqat ilova uchlarida (har hop'dagi probe — paket tegi, hash — behuda edi);
- qurilma hisoblagichlari: `Tx` trace o'rniga `SendOutgoing` + `UnicastForward` (ns-3 `Tx`
  trace'ga ulanilsa har hop'da paketni nusxalab sarlavha qo'shadi), `std::map` o'rniga vektor;
- `RateErrorModel` yo'qotish 0 bo'lganda o'chiriladi (har paketda tasodifiy son chizilmaydi).

Tezlashuv kichik (~0–8%, ns-3 ning o'zi asosiy vaqt). Barcha 21 namuna ssenariysida va
generatsiya qilingan ring10/campus'da natijalar (oqimlar, kabellar, qurilmalar, route_events,
konvergensiya) BAYT darajasida bir xil (stp_lab'da route_events tartibi bir soniya ichida
hash'ga bog'liq edi — endi saralangan).

**Tezkor rejim (ixtiyoriy, taxminiy)**: ssenariyda `"ns3_fast": true` (yoki
`NETLAB_NS3_FAST=1`) — jumbo MTU 9000 / MSS 8948, navbatlar BAYT bo'yicha teng qoldiriladi
(paket soni ×1500/9000). TCP paketlari ~6 baravar kam → 4–5 baravar tez. UDP oqimlari bir xil;
TCP oqimlarining umumiy o'tkazuvchanligi yaqin, lekin bir-biri bilan bo'lishishi (adolat)
va paket darajasidagi kechikish/jitter farq qiladi. Wi-Fi bo'lsa qo'llanmaydi. Natijada
`ns3_fast: true` va izoh bo'ladi.

O'lchovlar (30 s, bitta link_down; 4 yadro, bir vaqtda 2–4 ish — taxminiy):

| Topologiya | Asos | Aniq (yangi) | Tezkor (`ns3_fast`) | Tezkor: TCP jami / UDP |
|---|---|---|---|---|
| ring 10 router, 30 host, 20 oqim | 290 s | 267 s | 54 s (5.4x) | −0.2% / <0.1% |
| ring 20 router, 100 host, 40 oqim | 567 s | — | 107 s (5.3x) | — |
| campus 2/4/8, 40 host, 40 oqim | 356 s | 366 s | 88 s (4.0x) | +0.7% / <2% |
| ring 40 router, 200 host, 100 oqim | ~2600 s (taxm.: 36 daq. da 83%) | — | 404 s (~6x) | — |

Tezkor rejimda alohida TCP oqimlari ulushi o'zgaradi (median 8–15%, eng ko'p 93%) — umumiy
yuklama/sig'im tahlili uchun, oqim darajasidagi aniq natija uchun emas.

**Parallel tadqiqotlar**: ns-3 dvigatelida parametrik tadqiqot variantlari bir vaqtda
ishga tushiriladi (har biri alohida jarayon): `NETLAB_NS3_PARALLEL=N`, standart — yadrolar
soni / 2 (1..4). Misol: 4 variantli tadqiqot (har biri ~19 s) 2 parallel bilan 39 s.
Har ns-3 jarayoni katta topologiyada 1–3 GB xotira olishi mumkin (ring 40: ~2.8 GB).
Analitik (GIL) va Mininet (bitta jonli tarmoq) ketma-ket qoladi.

Test: `python3 -m lab.tests.smoke_ns3_perf` (tezkor rejim; `NETLAB_NS3_PERF_EXACT=1` — aniq ham).

### Ikki parametrli tadqiqot (issiqlik xaritasi)

Tadqiqot bo'limida ikkinchi parametr ham tanlanishi mumkin. Shunda ikki parametr
qiymatlarining barcha kombinatsiyalari ishga tushiriladi (ko'pi bilan 49 ta). Natijalar
oynasida **issiqlik xaritasi** chiziladi: x o'qi — 1-parametr, y o'qi — 2-parametr,
katak — tanlangan o'lchov va oqim. Rang ketma-ket bir rangli shkalada: kichik
qiymatlar fonga yaqin, kattalari to'q (tun mavzusida esa teskari). Har bir katakda qiymat
yozilgan, sichqoncha bilan ustiga borsangiz tooltip chiqadi. Misol: `ospf_lab` →
`asosiy`, x = `l4.bw` (1…100), y = `l4.queue` (10…1000).

### Parametrik tadqiqot (OPNET "parametric study")

Ssenariy oynasining pastki qismida bitta parametr tanlanadi:
- kabel: tezlik, kechikish, loss yoki navbat;
- ilova: tezlik yoki paket o'lchami.

So'ng qiymatlar ro'yxati yoziladi (masalan `1,2,5,10,20,50,100`). Har bir qiymat
uchun ssenariy tanlangan dvigatelda alohida ishga tushadi. Natijalar oynasida
**"Parametr grafigi"** chiziladi: x o'qi — parametr qiymati (diapazon keng bo'lsa,
logarifmik), y o'qi — tanlangan o'lchovning o'rtacha qiymati, har bir oqim uchun
alohida chiziq.

Masalan, `ospf_lab` → `asosiy` ssenariysida r1–r2 kabeli tezligini 1→100 Mbit/s
gacha o'zgartirsangiz, quyidagilar ko'rinadi:
- 5 Mbit/s dan past tezlikda UDP video kabelni to'liq egallaydi va TCP umuman o'tolmaydi;
- tezlik oshgani sari FTP chiziqli o'sadi;
- kechikish soniyalardan millisekundlarga tushadi.

### Routing konvergensiyasi: RIP va OSPF

`rip_ring` namunasi — uchburchak topologiya: r1–r2 asosiy yo'l, r1–r3–r2 zaxira yo'l.
`uzilish` ssenariysida t=10 s da asosiy kabel uziladi:

| Routing | ns-3 | Analitik |
|---|---|---|
| RIP (`ripd`) | ns-3 RIPv2: ~24 s trafik yo'q, zaxira yo'l keyingi periodik update'da (≤30 s) o'rganiladi | `update/2` (`rip_timers`, standart 15 s) yoki `rip_convergence_s` — eski yo'llar ishlatiladi |
| OSPF (`ospfd`) | SPF darhol qayta hisoblanadi — uzilish yo'q | uzilish yo'q |

Daemonni routerning Sozlama oynasida almashtirib (`ripd` ↔ `ospfd`), ikkala holatni
ishga tushiring va natijalarni bitta grafikda solishtiring.

ns-3 o'rnatish (Mininet shart emas, Windows'da WSL yoki Linux kerak):

```bash
pip install ns3
```

Bitta topologiya va bitta ssenariyni uchala dvigatelda ishga tushirib, natijalarni
solishtirish mumkin. Masalan, `ospf_lab` → `uzilish` ssenariysida analitik model
va ns-3 bir xil o'tkazuvchanlikni (~5 Mbit/s video, kabel uzilganda 0) beradi,
lekin kechikish va TCP tiklanishi farq qiladi. Bu modellarning chegarasini ko'rsatadi.

**ns-3 modellashtirishdagi soddalashtirishlar:**
- `ripd` yoqilgan routerlar ns-3'ning haqiqiy RIPv2 protokolida ishlaydi. Qolgan routerlarning yo'llarini analitik dvigatel bilan **bir xil** SPF modeli (hop soni) hisoblaydi va ns-3'ga statik marshrut sifatida o'rnatadi. Kabel uzilganda yoki stansiya qamrovdan chiqqanda yo'llar darhol qayta hisoblanadi (OSPF'ning sekundlik konvergensiyasi modellashtirilmaydi). ns-3'ning o'z global routing'i ishlatilmaydi: ns-3.44'da u ikki holatda yiqiladi — `IpForward=false` hostda va umumiy Wi-Fi kanaliga ulangan bir nechta AP bo'lganda.
- Jitter parametri modellashtirilmaydi.
- P4 switch ns-3'da oddiy L2 bridge, SDN router esa oddiy L3 router sifatida ishlaydi (P4 va SDN mantiqi faqat Mininet dvigatelida bajariladi).
- TCP Cubic, MSS 1448 (Linux'ga yaqin qilib sozlangan).

## Mininet va OPNET: farqi nimada

OPNET (Riverbed Modeler) — **diskret-hodisali simulyator**. U vaqtni virtual
hisoblaydi va har bir protokolning *modelini* bajaradi. Mininet esa
**emulyator**: haqiqiy Linux yadrosi va haqiqiy dasturlar real vaqtda ishlaydi.

| | OPNET / ns-3 (simulyatsiya) | Mininet (emulyatsiya) |
|---|---|---|
| Vaqt | virtual — real vaqtdan tez yoki sekin | real vaqt |
| Masshtab | minglab tugun | yuzlab (host CPU chegarasi) |
| Takrorlanuvchanlik | to'liq (seed) | yo'q (OS jadvallashtiruvchisi) |
| Protokollar | modellar (aniqligi model sifatiga bog'liq) | haqiqiy kod (FRR, Linux TCP, OVS) |
| Simsiz / fizik qatlam | bor | yo'q (Mininet-WiFi alohida) |
| Konfiguratsiya / CLI | yo'q | haqiqiy (`vtysh`, `ovs-ofctl`, P4 runtime) |

Xulosa: **faqat Mininet bilan OPNET'ni to'liq takrorlab bo'lmaydi.** Shuning uchun
NetLab gibrid qilib qurilgan: bitta loyiha modeli, uchta dvigatel. Mininet —
Packet Tracer tomoni (haqiqiy qurilmalar, CLI, hardware). ns-3 — OPNET tomoni
(DES, masshtab, statistika, solishtirish).

## VLAN, NAT va DHCP

### VLAN (802.1Q)

Switch portlari — `config.ports` (kalit — port raqami):

```json
"ports": {"1": {"mode": "access", "vlan": 10},
          "3": {"mode": "trunk", "allowed": [10, 20], "native": 1}}
```

- **access** — tegsiz kadrlar shu VLAN'da; tegli kadr tashlanadi.
- **trunk** — `allowed` (bo'sh = hammasi) tegli o'tadi; `native` berilsa tegsiz kadrlar shu VLAN'ga.
- sozlanmagan port — OVS standarti (barcha VLAN'lar tegli, tegsiz = ichki VLAN 0).
- Router/host subinterfeysi (router-on-a-stick): `"r1-eth1.10": {"ip": "10.0.10.1/24"}`
  (nom ≤ 15 belgi — Linux cheklovi).

| Dvigatel | Qanday |
|---|---|
| Mininet | OVS `tag` / `trunks` / `vlan_mode` (jonli o'zgartirish ham), subinterfeys — `ip link add … type vlan` (kernelda `8021q` kerak; bo'lmasa aniq xato) |
| Analitik / demo | L2 BFS 802.1Q qoidalari bilan (izolyatsiya, trunk, inter-VLAN routing) |
| ns-3 | access VLAN — har VLAN uchun alohida bridge; trunk/subinterfeys — tushunarli xato |

Namuna: `vlan_lab` — ikki switch orasida trunk, r1 router-on-a-stick, pc5 boshqa VLAN'da
(izolyatsiya). Inter-VLAN trafik trunk kabelidan ikki marta o'tadi (`links.l4` ab va ba).

### DHCP

Server (router/server) — `config.dhcp_server`:

```json
"dhcp_server": {"pools": [{"iface": "r1-eth1", "start": "10.0.1.100", "end": "10.0.1.150",
                           "gateway": "10.0.1.1", "lease": 3600}]}
```

Mijoz — interfeys IP'si `"dhcp"`. Manzillar deterministik taqsimlanadi (qurilma id
tartibida, bo'sh birinchi manzil; `lab/services.py: resolve_dhcp`) — analitik va ns-3
shu manzillarni statik ishlatadi, Mininet esa haqiqiy **dnsmasq** (MAC bo'yicha
rezervatsiya — natija model bilan bir xil) va **dhclient** (namespace ichida, o'z
skripti bilan — `/etc/resolv.conf` ga tegmaydi). Mijoz server bilan bitta L2 domenda
(VLAN hisobga olinadi) bo'lishi kerak; relay yo'q. Inspect → "DHCP lizinglari".

### NAT (masquerade)

Router `config.nat = {"outside": "r1-eth2"}` — boshqa interfeyslardan outside orqali
chiqadigan paketlar manbasi outside IP'ga almashtiriladi (Mininet: `iptables -t nat …
MASQUERADE`). Analitik/demo modelda javob yo'li avval NAT routerning outside IP'siga,
keyin ichki manzilga boradi (tashqi router ichki subnetni bilmasa ham ishlaydi);
tashqaridan ichkariga yangi ulanish — marshrut bo'lmasa o'tmaydi. ns-3'da NAT yo'q
(ogohlantirish). Inspect → iptables va conntrack.

Namuna: `nat_dhcp_lab` — pc1/pc2 DHCP oladi, r1 NAT bilan provayderga (isp) chiqaradi.
Real sinov: srv1 so'rovlarni `203.0.113.2` (r1 outside) dan ko'radi; NAT o'chirilsa
javoblar qaytmaydi.

Router `config.routes` (statik marshrutlar) Mininet'da kernel marshruti sifatida ham
qo'llanadi (zebra uni ko'radi).

## Routing, L2 va xavfsizlik: BGP, STP, Firewall

### BGP: avtonom tizimlar (eBGP / iBGP)

Router `daemons` ichida `bgpd` va tuzilgan `config.bgp`:

```json
"bgp": {"asn": 65001, "router_id": "2.2.2.2",
        "neighbors": [{"ip": "10.12.0.1", "remote_as": 65001, "next_hop_self": true},
                      {"ip": "203.0.113.2", "remote_as": 65002}],
        "networks": ["10.1.0.0/24"], "redistribute": ["connected", "ospf", "static"]}
```

- **sessiya** — ikkala tomon bir-birini (IP + `remote_as`) ko'rsatsa UP. eBGP — qo'shni
  to'g'ridan-to'g'ri ulangan subnetda; iBGP — IGP/statik marshrut bilan yetadi.
- **e'lon** — `networks` (prefiks RIB'da bo'lsa, FRR import-check kabi) + `redistribute`.
- **eBGP** — AS_PATH oldiga o'z AS'i, AS halqasi rad etiladi; **iBGP** — faqat lokal/eBGP
  marshrutlar uzatiladi (route reflector yo'q), next-hop o'zgarmaydi (`next_hop_self` bo'lmasa).
- tanlash: qisqa AS_PATH > eBGP > iBGP; AD: eBGP 20, OSPF 110, RIP 120, iBGP 200.
- IGP (OSPF/RIP) AS chegarasidan o'tmaydi (ikkala routerda har xil `asn` bo'lsa).

| Dvigatel | Qanday |
|---|---|
| Mininet | FRR `bgpd`; `config.bgp` → `vtysh -f` (`router bgp`, `no bgp ebgp-requires-policy`, `neighbor … timers connect 5`, `address-family ipv4 unicast`). Inspect: `show bgp summary`, `show ip bgp` |
| Analitik / demo | `lab/bgp.py` path-vector modeli; uzilishda marshrut darhol olib tashlanadi, tiklanganda `bgp_convergence_s` (standart 2 s) dan keyin qaytadi. Demo terminal: `show bgp summary`, `show ip bgp` |
| ns-3 | BGP yo'q — model hisoblagan marshrutlar statik o'rnatiladi (hodisada darhol qayta hisoblanadi) |

Namuna: `bgp_lab` — AS65001 (r1, r2: OSPF + iBGP, pc1) va AS65002 (r3, srv1), r2–r3 eBGP.
Ssenariy `ebgp_uzilish`: t=10 s da eBGP kabeli uziladi, t=20 s da tiklanadi.

| | uzilishda marshrut olib tashlanishi | tiklanish konvergensiyasi | video uzilishi |
|---|---|---|---|
| Real FRR (Mininet) | 0.2 s (interfeys DOWN → sessiya darhol tushadi) | 2.0 s | 11 s |
| Analitik | darhol | 2.0 s | 12 s |
| ns-3 (statik) | darhol | darhol | 10 s |

Testlar: `python3 -m lab.tests.smoke_bgp` (demo + analitik + ns-3),
`sudo python3 -m lab.tests.real_bgp` (real FRR).

Cheklovlar: modelda IGP qo'shnisining barcha interfeys subnetlari o'rganiladi (OSPF `network`
bayonotlari tahlil qilinmaydi); BGP atributlari (local-pref, MED, community), route reflector,
multipath va policy (route-map) modellashtirilmaydi — real FRR'da startup orqali yozish mumkin.

### STP / RSTP (switch halqalari)

Switch `config`:

```json
"stp": true, "stp_mode": "stp", "stp_priority": 4096,
"ports": {"2": {"stp_cost": 100}}
```

- `stp_mode`: `stp` (802.1D, standart) yoki `rstp` (802.1w).
- `stp_priority`: 0..61440, 4096 ga karrali (standart 32768); eng kichik (priority, id) — root.
- `ports.<n>.stp_cost`: port yo'l narxi; bo'lmasa kabel tezligidan (802.1D-1998: 10 Mbit/s
  → 100, 100 Mbit/s → 19, 1 Gbit/s → 4, 10 Gbit/s → 2). Real driver bu narxni OVS'ga
  yozadi (veth o'zini "10 Gbit/s" deb ko'rsatadi).

Model (`lab/stp.py`): root bridge, root port, designated/alternate portlar; alternate —
blocking. STP'siz switchlar halqa hosil qilsa — **broadcast storm**: domen ishlamaydi,
ping/traceroute sababni ko'rsatadi (`L2 halqa (STP o'chiq): broadcast storm [l1, l2, l3]`),
ns-3 aniq xato beradi. Inspect → "STP (spanning tree)" (rol/holat, bridge/root id);
real driverda `ovs-appctl stp/show` (yoki `rstp/show`) va model bashorati yonma-yon.

| Dvigatel | Qanday | Konvergensiya (`stp_lab`, faol kabel uzilganda) |
|---|---|---|
| Mininet (OVS) | `stp_enable` / `rstp_enable`, `stp-priority`, `stp-system-id` = device_mac(id, 0), `stp-path-cost` | STP ~23 s, RSTP < 1 s (o'lchangan) |
| Analitik | blocking → forwarding o'tish `hold`: 802.1D 2×forward_delay = 30 s (bilvosita nosozlik +max_age 20 s), RSTP 1 s; `stp_convergence_s` — qo'lda | 30 s / 1 s |
| ns-3 | BridgeNetDevice'da STP yo'q: model daraxti bo'yicha blocking segment ma'lumot uchun yopiladi (ErrorModel), hodisada qayta hisoblanadi; MAC yozuvlari 2 s (TCN FDB tozalashi o'rniga) | 30 s / 1 s |

Port holati o'zgarishlari `route_events` ga (`dev` = switch, `port2 root/forwarding`)
yoziladi — `convergence` (OPNET "convergence duration") L2 uchun ham ko'rinadi.

Eslatma: 802.1D da kabel **tiklanganda** ham uzilish bo'ladi — yangi root port 30 s
listening/learning kutadi, eski root port esa darhol blocking bo'ladi (RSTP'da ~1 s).

Namuna: `stp_lab` — s1 (root, priority 4096), s2, s3 uchburchak; s3:2 blocking.
Ssenariy `uzilish`: t=10 s da l2 (s1-s3) uziladi, t=50 s da tiklanadi.

Testlar: `python3 -m lab.tests.smoke_stp` (demo + analitik + ns-3),
`sudo flock /tmp/netlab-real.lock python3 -m lab.tests.real_stp` (real OVS STP va RSTP).

### Firewall / ACL (stateful, iptables)

Router, host yoki server `config.firewall`:

```json
"firewall": {"default": "drop",
             "rules": [
               {"chain": "forward", "action": "accept", "state": "established"},
               {"chain": "forward", "action": "accept", "in": "r1-eth1"},
               {"chain": "forward", "action": "accept", "proto": "tcp", "dst": "10.0.2.10/32", "dport": 80},
               {"chain": "forward", "action": "reject", "proto": "icmp", "in": "r1-eth3", "dst": "10.0.1.0/24"}]}
```

- **Semantika — iptables bilan bir xil:** zanjir (`input` / `forward` / `output`) ichida
  birinchi mos qoida; mos qoida bo'lmasa — siyosat. `default` routerda `forward`,
  host/serverda `input` zanjiriga tegishli; aniq siyosat: `"policy": {"input": "drop"}`.
- Maydonlar: `proto` (`any` / `tcp` / `udp` / `icmp`), `src`/`dst` (subnet), `dport`/`sport`
  (faqat tcp/udp), `in`/`out` (interfeys), `state` (`new` / `established`).
- **Stateful:** `established` qoidasi javob paketlarini o'tkazadi — "ichkaridan tashqariga
  ruxsat, tashqaridan yangi ulanish yo'q". Bu qoida bo'lmasa javoblar ham tashlanadi.
- **`reject` va `drop`:** `reject` — TCP uchun RST, boshqalar uchun ICMP host-prohibited
  (ping darhol "Destination Host Prohibited" deydi); `drop` — jim tashlash (timeout).

| Dvigatel | Qanday |
|---|---|
| Mininet | qurilma namespace'ida o'z zanjirlari `NETLAB-IN/FWD/OUT` (INPUT/FORWARD/OUTPUT dan sakrash); NAT (`nat` jadvali) alohida qoladi; jonli o'zgartirish — zanjirlar qayta yoziladi. Inspect → **har qoida hisoblagichi** (paket/bayt, `iptables -L -v`) |
| Analitik / demo | paket yo'li bo'yicha: manba OUTPUT, oraliq routerlar FORWARD, manzil INPUT; NAT'dan keyingi hoplarda manba almashtiriladi; tcp/ping javobi `established` sifatida tekshiriladi (`lab/firewall.py`) |
| ns-3 | iptables yo'q — model bloklagan oqim ishga tushirilmaydi (natijada 100% yo'qotish, `blocked` sababi bilan) |

**Portlar.** Ilovaga `dport` bering (`{"type": "tcp", "dport": 80, ...}`); bo'lmasa port
`5201 + ilova tartib raqami` (Mininet'dagi iperf3 porti bilan bir xil). Mininet'da iperf3
server/klient aynan shu portda ishlaydi; bir (server, port) dagi bir nechta TCP ilova
(masalan HTTP sahifalari) bitta doimiy iperf3 server'ni bo'lishadi. Eslatma: iperf3 UDP
testi shu portga TCP boshqaruv ulanishini ham ochadi — real tarmoqda UDP ilova uchun shu
portda TCP ham ruxsat etilgan bo'lishi kerak.

Namuna: `firewall_lab` — LAN (pc1, pc2), DMZ veb-server (srv1:80), Internet (inet).
Ssenariy `trafik`: `web_in` (inet → srv1:80) va LAN oqimlari o'tadi, `ssh_in` (:22)
tashlanadi, `ping_in` (inet → pc1) reject. Real Mininet sinovi: TCP :80 47 Mbit/s, :22
"Connection timed out", hisoblagichlar (established qoidasi 37 404 paket, default drop 6),
jonli qoida qo'shish va olib tashlash.

```bash
python3 -m lab.tests.smoke_fw
```

```bash
sudo python3 -m lab.tests.real_fw
```

### IP multicast: IGMP + PIM-SM

Router — `daemons` ichida `pimd` va ixtiyoriy `config.pim`:

```json
"daemons": ["ospfd", "pimd"],
"pim": {"rp": "10.0.0.1", "interfaces": ["r1-eth1", "r1-eth2"]}
```

`interfaces` berilmasa — IP'li barcha interfeyslarda `ip pim` + `ip igmp`. Unicast
routing (OSPF/statik) bo'lishi shart (RPF). Switch — `config.igmp_snooping: true`
(OVS `mcast_snooping`): kadrlar faqat a'zo portlarga va PIM router portlariga boradi;
o'chiq bo'lsa butun L2 domenga (VLAN/STP hisobga olinadi) flood.

Ilova turi `mcast` (`dst` — qabul qiluvchilar ro'yxati):

```json
{"id": "tv", "type": "mcast", "src": "srv1", "group": "239.1.1.1",
 "dst": ["pc1", "pc3"], "rate": "4Mbps", "size": 1000, "start": 1, "stop": 29}
```

Natija: `apps.tv` — `reached` / `reached_list`, `avg_loss_pct`, `per_receiver`
(har qabul qiluvchi tezligi, yo'qotishi, kechikishi); `flows.tv.<qabul qiluvchi>` —
soniyalik seriya; `links` — har kabelda **bitta nusxa** (qabul qiluvchilar soniga
ko'paytirilmaydi).

| Dvigatel | Qanday |
|---|---|
| Analitik / demo | daraxt = manbadan har qabul qiluvchigacha eng qisqa unicast yo'llar birlashmasi (SPT); yo'ldagi router'da pimd/PIM interfeysi bo'lmasa — shoxcha uziladi; umumiy kabel yuklamasi bir marta; snooping'siz segmentlarda flood yuklamasi; yuklangan shoxchadagina yo'qotish |
| ns-3 | PIM yo'q — model daraxti bo'yicha statik `AddMulticastRoute` (hodisalarda qayta o'rnatiladi), bitta OnOff → guruh, qabul qiluvchilarda PacketSink (SeqTsSize: kechikish, yo'qotish); ns-3 bridge'da IGMP snooping yo'q (flood) |
| Mininet | FRR pimd (`ip pim rp`, `ip pim`, `ip igmp`), `lab/engines/mcast_tool.py` yuboruvchi/qabul qiluvchi (IP_ADD_MEMBERSHIP → IGMP report → PIM join), TTL 16; inspect: `show ip pim neighbor`, `show ip mroute`, `show ip igmp groups`, `show ip pim rp-info`, switch'da `mdb/show` |

Namuna `mcast_lab`: srv1 → r1 (RP) → r2 (s1: pc1 a'zo, pc2 a'zo emas) va r3 (s2: pc3),
r2-r3 zaxira kabel, OSPF + PIM-SM. Real sinov: PIM qo'shnilar va RP tayyor, pc1/pc3
4.0 Mbit/s oladi, pc2 ga yetmaydi (snooping), r1-r2 kabelida 4.17 Mbit/s (bitta nusxa);
r1-r3 uzilganda pc3 shoxchasi ~1 s ichida r2-r3 orqali tiklanadi (l4: 0 → 4.17 Mbit/s).

Cheklovlar: model faqat SPT (RP orqali umumiy daraxt va SPT'ga o'tish vaqti
modellanmaydi); analitikda PIM qayta qurilishi darhol; IGMP snooping domeni — segmentdagi
barcha switchlarda yoqilgan bo'lsa; ns-3'da snooping yo'q.

### VRRP: gateway zaxirasi (first-hop redundancy)

Router `config.vrrp` (bir xil `vrid` + `vip` — bitta guruh):

```json
"vrrp": [{"iface": "r1-eth1", "vrid": 1, "vip": "10.0.1.254/24",
          "priority": 120, "preempt": true, "advert_ms": 1000}]
```

Hostlar gateway sifatida VIP'ni oladi. Master — kabeli ishlayotgan routerlar ichida eng
yuqori `priority` (teng bo'lsa — interfeys IP'si kattasi). `vrrpd` daemon avtomatik qo'shiladi.

| Dvigatel | Qanday |
|---|---|
| Mininet (FRR) | `vrrpd` (VRRPv3) + router namespace'ida macvlan `vrrp4-<port>-<vrid>` (MAC `00:00:5e:00:01:<vrid>`, VIP bilan; backup'da vrrpd uni protodown qiladi). `arp_ignore=1` — backup ota interfeysi VIP uchun ARP javob bermaydi. Inspect: `show vrrp` + model bashorati |
| Analitik / demo | master saylovi; master kabeli uzilsa VIP egasiz, backup `Master_Down_Interval` = 3×advert + (256−priority)/256×advert (standart ~3.6 s) dan keyin master; preempt — darhol (FRR 8.4 kabi). `route_events` / `convergence` da ko'rinadi |
| ns-3 | VRRP yo'q — model vaqt jadvali bo'yicha VIP master interfeysiga qo'shimcha manzil sifatida ko'chiriladi, ARP keshlari tozalanadi (gratuitous ARP ekvivalenti) |

Ssenariy sozlamalari: `vrrp_convergence_s` (failover vaqtini qo'lda), `vrrp_preempt_s`
(preempt kechikishi, standart 0).

Namuna: `vrrp_lab` — r1 (120) va r2 (100) LAN'da 10.0.1.254 ni bo'lishadi, r3 orqali
srv1. `zaxira` ssenariysi: t=10 s r1 LAN kabeli uziladi, t=20 s tiklanadi.

| | failover (r2 master) | trafik uzilishi | preempt |
|---|---|---|---|
| Analitik | 3.6 s | 4 s | 0 s, uzilishsiz |
| ns-3 | 3.6 s | ~3 s | uzilishsiz |
| Mininet (real FRR) | 3.2–3.4 s | ~8 s* | 0.03 s, uzilishsiz |

\* realda javob yo'lida OSPF ham yaqinlashadi: r3 ECMP marshrutining r1 tarmog'i LAN
dead-interval (4 s) tugaguncha qoladi — modelda IGP konvergensiyasi darhol deb olinadi.

Yo'l-yo'lakay: model IGP'si (OSPF/RIP BFS) endi kabeli uzilgan interfeys subnetini
e'lon qilmaydi (real FRR kabi).

Real test: `sudo flock /tmp/netlab-real.lock python3 -m lab.tests.real_vrrp`;
smoke: `python3 -m lab.tests.smoke_vrrp`.

### Link aggregation (802.3ad LACP / bond)

Ikki switch orasidagi bir nechta parallel kabelni bitta mantiqiy kanalga birlashtirish.
Switch `config.bonds` (qo'shni switch'da xuddi shu kabellarning ikkinchi uchlari bilan
mos bond bo'lishi shart):

```json
"bonds": {"bond1": {"ports": [3, 4], "mode": "balance-tcp", "lacp": "active"}}
```

- `mode`: `balance-tcp` (oqim — L4 hash), `balance-slb` (manba MAC), `active-backup`
  (faqat birlamchi a'zo, qolganlari zaxira).
- `lacp`: `active` / `passive` / `off` (ikkala tomon passive yoki faqat bir tomon off —
  validatsiya xatosi).
- Faqat switch <-> switch: host/router bonding qo'llab-quvvatlanmaydi (konteyner/WSL
  kernelida Linux bonding moduli bo'lmasligi mumkin; OVS bond'i switch'lar orasida ishlaydi).

| Dvigatel | Qanday |
|---|---|
| Mininet | a'zo portlar `ovs-vsctl add-bond <sw> <sw>-<bond> … bond_mode=… lacp=… lacp-time=fast` bilan bitta OVS portiga aylantiriladi (ofport va tc saqlanadi). Inspect: `ovs-appctl bond/show`, `lacp/show` + model jadvali |
| Analitik / demo | bond — bitta L2 qo'shnichilik; har oqim deterministik hash bo'yicha BITTA a'zodan o'tadi (kabel statistikasi a'zo bo'yicha). A'zo uzilsa — `bond_failover_s` (standart 0.5 s) davomida unga hash qilingan oqimlar yo'qoladi, keyin qolgan a'zolarga qayta taqsimlanadi |
| ns-3 | bonding yo'q — bond bitta vakil a'zo kabel orqali yuradi, tezligi = ishlaydigan a'zolar yig'indisi (qolganlari yopiq); oqim bo'yicha hash yo'q, statistika vakil kabelda |

**STP bilan:** OVS STP/RSTP yoqilgan bridge'da bond porti "disabled" rolda qoladi va
hech qachon forwarding'ga o'tmaydi (real sinovda tekshirildi). Shuning uchun bond'li
switch'da STP avtomatik o'chiriladi (ogohlantirish bilan) — model ham shunday hisoblaydi.
Bond'ning parallel a'zolari halqa hisoblanmaydi (bitta qirra); bond'siz parallel kabellar
STP bilan — bittasi blocking, STP'siz — broadcast storm. Bond'li switch'da boshqa zaxira
yo'llar halqa hosil qilmasin.

Namuna: `lag_lab` — s1–s2 2×100 Mbit/s bond (balance-tcp, LACP), t=10 s da a'zo `l5` uziladi.

| | 2 oqim (oldin) | `l5` uzilgandan keyin |
|---|---|---|
| Analitik | 200 Mbit/s (har a'zo ~100) | 100 Mbit/s |
| ns-3 | 200 Mbit/s | ~3 s uzilish (MAC qayta o'rganish), keyin 100 Mbit/s |
| Mininet (real, userspace OVS) | 4 parallel oqim: 134 Mbit/s (bitta oqim 95); LACP `negotiated` | trafik darhol davom etadi |

Real sinovda balance-tcp hash oqimlarni a'zolarga notekis taqsimladi (masalan 75 MB / 31 MB) —
ikkita oqim ham bitta a'zoga tushishi mumkin (hash to'qnashuvi).

## Wi-Fi (simsiz tarmoq)

Kabel asbobi (`C`) bilan Wi-Fi AP'ni va host/server/router'ni ketma-ket bosing —
kabel avtomatik **simsiz** bo'ladi (kanvasda nuqtali chiziq bilan ko'rsatiladi).
Kanvasdagi masofa haqiqiy metrga aylanadi: masshtab loyiha sozlamasida
(`meters_per_px`, standart 1 px = 0.25 m) va kanvasning chap pastki burchagida.
Qurilmani sudrasangiz, kabel yorlig'idagi MCS, tezlik va masofa darhol yangilanadi.
AP atrofidagi doiralar: tashqi — aloqa chegarasi, ichki — yuqori tezlik zonasi
(SNR ≥ 25 dB).

Radio modeli (`lab/wifi.py`):
- signal susayishi: log-distance, n = 3;
- SNR asosida MCS tanlanadi;
- aloqa chegarasi: RSSI ≥ −82 dBm.

Model ns-3 o'lchovlariga kalibrlangan. 802.11ax, 20 MHz, 20 dBm uchun:

| Masofa | 10 m | 20 m | 30 m | 40 m | 50 m | 70 m |
|---|---|---|---|---|---|---|
| ns-3 (UDP) | 113.8 | 92.1 | 69.6 | 62.2 | 42.6 | 0 |
| Model | 114.7 | 91.8 | 68.8 | 61.9 | 41.3 | 0 |

| Dvigatel | Wi-Fi qanday modellashtiriladi |
|---|---|
| ns-3 | To'liq: YansWifiPhy, CSMA/CA, umumiy efir, A-MPDU, IdealWifiManager, joylashuv kanvasdan |
| Analitik | Har bir stansiya tezligi = model taxmini; AP efir vaqti stansiyalar o'rtasida bo'linadi (efir vaqti bo'yicha adolat), TCP ACK uchun ~9% qo'shimcha efir |
| Mininet | Radio yo'q: AP = OVS switch, Wi-Fi kabeli = model tezligidagi TCLink (umumiy efir emulyatsiya qilinmaydi) |

### Bir nechta AP va kanal xalaqiti

AP sozlamasida **kanal** tanlanadi. Ro'yxatdagi kanallar diapazon va kenglikka mos
keladi, masalan 5 GHz / 20 MHz uchun 36, 40, 44 va hokazo. Bir xil (yoki chastotasi
ustma-ust tushadigan) kanaldagi, bir-birini eshitadigan AP'lar **umumiy efirni
bo'lishadi**:
- ns-3'da barcha AP'lar bitta radio muhitda ishlaydi. Bir kanaldagilar CSMA/CA orqali
  navbat kutadi va to'qnashadi, boshqa kanaldagilar esa bir-biriga ta'sir qilmaydi.
- Analitik dvigatelda bunday AP'lar bitta efir vaqti guruhiga birlashtiriladi.

`wifi_kampus` namunasi — 60 m oraliqdagi ikki AP. Standart holatda ap1 36-kanalda, ap2 40-kanalda:

| AP kanallari | ns-3 (ikki TCP yuklash, Mbit/s) | Analitik |
|---|---|---|
| 36 / 36 (bir kanal) | 39.4 + 36.3 | 42.1 + 42.1 |
| 36 / 40 (turli kanal) | 79.3 + 80.8 | 84.2 + 84.2 |

Tadqiqot oynasida **"Wi-Fi ap2 · kanal"** parametrini `36,40` qiymatlari bilan
ishga tushirsangiz, shu farqni bitta grafikda ko'rasiz.

### Harakatlanuvchi stansiyalar

Ssenariy hodisasi **"Harakat"**: qurilma `t` soniyadan boshlab `davomiylik` soniya
ichida (x, y) nuqtaga to'g'ri chiziq bo'ylab yuradi. Manzilni 📍 tugmasi bilan
to'g'ridan-to'g'ri kanvasdan tanlash mumkin. Ssenariy oynasi ochiq turganda kanvasda
harakat yo'nalishi strelka bilan ko'rsatiladi.
- **ns-3:** pozitsiya har 0.5 s da yangilanadi; MCS va to'qnashuvlar real hisoblanadi.
- **Analitik:** har soniyada radio taxmini qayta hisoblanadi.
- **Mininet:** har soniyada TCLink tezligi qayta o'rnatiladi, qamrovdan chiqsa link uziladi.

`wifi_kampus` → `yurish`: mob1 ap1 dan uzoqlashadi, video tezligi MCS bo'yicha
bosqichma-bosqich tushadi:
- ns-3: 60 → 57 → 53 → 41 → 27 → 13 → 0 Mbit/s;
- analitik: 60 → 55 → 41 → 27 → 0 Mbit/s.

Aloqa uzilgach, ping ham to'xtaydi.

### Roaming (AP almashish)

Bir xil **SSID, diapazon va kanal**ga ega AP'lar bitta ESS hisoblanadi. Harakatlanuvchi
stansiya joriy AP qamrovidan chiqqanda eng kuchli signalli AP'ga o'tadi ("yopishqoq
klient" — AP'ni faqat aloqa uzilgach almashtiradi). Wi-Fi kabeli stansiya ulangan
**boshlang'ich** AP'ni bildiradi, keyingi almashishlar avtomatik.
- **ns-3:** StaWifiMac beacon'lar yo'qolgach o'zi skanerlab, qayta ulanadi. Har
  soniyada stansiyaning BSSID'i o'qiladi va AP almashsa, natijaga `roam` hodisasi
  yoziladi (grafikda "⇄ mob1→ap2" belgisi).
- **Analitik:** aloqa uzilgach 1 s uzilish, so'ng eng kuchli AP'ga o'tadi.
- **Mininet:** roaming yo'q (radio emulyatsiya qilinmaydi).

ns-3 faqat o'z kanalida skanerlaydi, shuning uchun ESS'dagi AP'lar bir kanalda bo'lishi
kerak. `wifi_roaming` → `yurish` ssenariysi: mob1 100 m oraliqdagi ap1 dan ap2 ga yuradi
(20 Mbit/s video).

| | Uzilish | Roaming ap1 → ap2 | Video oldin / keyin |
|---|---|---|---|
| ns-3 | t = 27 s | t = 28 s (RTT 32 ms, keyin 4 ms) | 20 / 20 Mbit/s |
| Analitik | t = 26 s | t = 27 s | 20 / 20 Mbit/s |

**ns-3'da kuzatilgan holat.** Bir kanaldagi qo'shni AP bor va downlink yuklamasi kanal
sig'imiga yaqin bo'lsa, stansiya bilan aloqa butunlay uzilib qolishi mumkin.
`wifi_kampus` misolida (mob1 ap1 dan 35 m, ap2 dan 46 m; ikkala AP 36-kanalda):
- 30 Mbit/s — normal o'tadi;
- 40 Mbit/s — oqim faqat ~4-soniyada boshlanadi;
- 50+ Mbit/s — umuman o'tmaydi.

Ap2 boshqa kanalda bo'lsa yoki umuman bo'lmasa, 60 Mbit/s ham normal o'tadi. Bu real
Wi-Fi'dagi to'qnashuv/qayta uzatish qulashi bo'lishi ham, ns-3 modelining artefakti
ham bo'lishi mumkin. Shuning uchun bunday natijalarni analitik dvigatel bilan solishtiring.

`wifi_ofis` namunasi: AP, uch noutbuk (23 / 50 / 64 m) va simli server.
`yuklama` ssenariysi Wi-Fi'ning "performance anomaly" hodisasini ko'rsatadi:
uzoqdagi sekin stansiya efirni uzoq band qiladi va yaqindagi stansiyani ham
sekinlashtiradi. Tadqiqot uchun **Wi-Fi AP · uzatish quvvati** parametrini tanlab,
masalan `5,10,15,20,25` dBm qiymatlarida uzoqdagi stansiya tezligini ko'ring.

## Namunalar (Ochish → Namunalar)

- `ospf_lab`: ikki LAN, ikki FRR router, OSPF area 0. Ssenariylar: `asosiy`, `uzilish`, `tez_kabel`.
- `rip_ring`: uch router uchburchak, RIPv2. `uzilish` ssenariysi — konvergensiya tahlili.
- `wifi_ofis`: Wi-Fi AP, uch noutbuk turli masofada, simli server. Ssenariylar: `yolgiz`, `yuklama`.
- `wifi_kampus`: ikki AP (36 va 40-kanal), switch, server, harakatlanuvchi mob1. Ssenariylar: `xalaqit`, `yurish`.
- `wifi_roaming`: bitta ESS'dagi ikki AP; mob1 ap1 dan ap2 ga yuradi va roaming qiladi.
- `p4_l2_lab`: uchta host, P4 L2 switch, `dmac` jadvali.
- `sdn_router_lab`: SDN switch + SDN router (`l3_router` ilovasi).
- `vlan_lab`: VLAN 10/20, trunk, router-on-a-stick, izolyatsiya. Ssenariy: `vlanlar`.
- `nat_dhcp_lab`: DHCP server + NAT (masquerade) + provayder. Ssenariy: `internet`.
- `bgp_lab`: AS65001 (OSPF + iBGP) va AS65002, eBGP r2-r3. Ssenariy: `ebgp_uzilish`.
- `stp_lab`: uch switch halqasi (STP, s1 root), bloklangan port va konvergensiya.
- `firewall_lab`: LAN, DMZ veb-server (:80), internet; stateful firewall qoidalari.
- `mcast_lab`: IGMP + PIM-SM (RP r1), 239.1.1.1 oqimi pc1 va pc3 ga, IGMP snooping.
- `vrrp_lab`: r1 (prio 120) / r2 (prio 100) virtual gateway 10.0.1.254, failover va preempt.
- `lag_lab`: s1–s2 orasida 2×100 Mbit/s bond (balance-tcp, LACP), a'zo kabel uzilishi.
- `reliability_lab`: OSPF halqa + zaxirasiz r3–srv2 kabeli; MTBF/MTTR nosozliklari (`ishonchlilik` 600 s, `qisqa` 60 s).

## Klaviatura

`V` — tanlash, `C` — kabel, `D` — o'chirish, `Del` — tanlanganni o'chirish,
`Enter` — qurilmaga kirish, `F` — hammasini ko'rsatish, `Esc` — rejimni bekor qilish,
`Ctrl+S` — saqlash. Qurilmaga ikki marta bosish — ichiga kirish.

- **`Ctrl+K` — buyruqlar palitrasi.** Qurilma (nomi yoki IP bo'yicha), ssenariy, P4/SDN
  dastur va namuna loyihalarni qidiradi. Undan qurilma qo'shish, tarmoqni ishga
  tushirish/to'xtatish, saqlash, mavzuni almashtirish mumkin. `↑↓` — tanlash, `Enter` —
  bajarish.
- **`Ctrl+Z` / `Ctrl+Shift+Z` (yoki `Ctrl+Y`) — bekor qilish / qaytarish** (100 qadamgacha).
  Qurilma va kabel qo'shish/o'chirish, ko'chirish, sozlamalar, ssenariy tahrirlari tarixga
  yoziladi. Tez ketma-ket yozish bitta qadamga birlashtiriladi. Tarmoq ishlayotganda
  o'chirilgan, chunki jonli tarmoq holati bilan farqlanib qoladi.

## Xavfsizlik

Real driver qurilmalarga **root** terminal beradi. Server standart holatda faqat
`127.0.0.1` da tinglaydi. Boshqa manzilda (`--host 0.0.0.0`) ishga tushirilsa,
token majburiy. Token URL'ini begonalarga bermang.

## Fayllar

| Yo'l | Vazifa |
|---|---|
| `netlab.py` | CLI: driver tanlash, uvicorn, `--window` |
| `server.py` | REST API, `/ws/events` (paket/statistika/log), `/ws/term/<qurilma>` (PTY) |
| `model.py` | Loyiha modeli, validatsiya, port/MAC qoidalari, kontroller konfiguratsiyasi |
| `drivers/mininet_driver.py` | Real driver: jonli qo'shish/o'chirish, FRR, BMv2, os-ken, tcpdump, `/proc/<pid>/net/dev` statistikasi |
| `drivers/demo_driver.py` | Demo driver (Mininet'siz) |
| `engines/analytic.py` | Analitik (oqim) dvigateli |
| `engines/ns3_runner.py` | Loyiha → ns-3 tarjimasi, alohida jarayonda; FlowMonitor har 1 s da o'lchanadi |
| `engines/emulation.py` | Mininet dvigateli: iperf3/ping + hodisalar ishlab turgan tarmoqda |
| `engines/manager.py` | Ishga tushirishlar, progress hodisalari, `results/<loyiha>/` ga saqlash |
| `templates/p4/`, `templates/sdn/` | P4 va SDN shablonlari |
| `examples/` | Namunaviy loyihalar |
| `projects/` | Sizning loyihalaringiz (gitignore) |
| `ui/` | React + TypeScript + shadcn/ui manbasi (Vite) |
| `web-react/` | Yig'ilgan UI (serverning `/` manzili) — `cd ui && npm run build` |
| `web/` | Klassik UI (vanilla JS, `/classic`). Dizayn tokenlari `studio/web/css` dan |

## Cheklovlar va keyingi qadamlar

- Real driver Ubuntu'da Mininet 2.3, OVS 2.17+, FRR 8+ va p4lang paketlari bilan ishlash uchun yozilgan. Bu repo'da u faqat statik tekshirilgan (CI'da Mininet yo'q). Birinchi ishga tushirishda xato chiqsa, **Hodisalar** panelidagi logni yuboring.
- Router qurilmasini tarmoq ishlayotganda qayta nomlab bo'lmaydi.
- Mininet dvigateli (`engines/emulation.py`) faqat statik tekshirilgan — birinchi real ishga tushirishda xato chiqishi mumkin.
- Wi-Fi: qo'shni (ustma-ust tushmaydigan) kanallarning qisman xalaqiti va roaming'da kanallararo skanerlash modellashtirilmaydi.
- Rejada: LTE/5G, harakatlanuvchi stansiyalar, bir nechta parametrli tadqiqot (2D), ns-3'da kabel yuklamasi statistikasi, VLAN/trunk, pcap yuklab olish.

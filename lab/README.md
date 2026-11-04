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
| RIP (`ripd`) | ns-3 RIPv2: ~24 s trafik yo'q, zaxira yo'l keyingi periodik update'da (≤30 s) o'rganiladi | `rip_convergence_s` (standart 15 s) — eski yo'llar ishlatiladi |
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

## Klaviatura

`V` — tanlash, `C` — kabel, `D` — o'chirish, `Del` — tanlanganni o'chirish,
`Enter` — qurilmaga kirish, `F` — hammasini ko'rsatish, `Esc` — bekor qilish,
`Ctrl+S` — saqlash. Qurilmaga ikki marta bosish — ichiga kirish.

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
| `web/` | UI (vanilla JS). Dizayn tokenlari `studio/web/css` dan olinadi. `web/vendor/xterm` — xterm.js (MIT) |

## Cheklovlar va keyingi qadamlar

- Real driver Ubuntu'da Mininet 2.3, OVS 2.17+, FRR 8+ va p4lang paketlari bilan ishlash uchun yozilgan. Bu repo'da u faqat statik tekshirilgan (CI'da Mininet yo'q). Birinchi ishga tushirishda xato chiqsa, **Hodisalar** panelidagi logni yuboring.
- Router qurilmasini tarmoq ishlayotganda qayta nomlab bo'lmaydi.
- Mininet dvigateli (`engines/emulation.py`) faqat statik tekshirilgan — birinchi real ishga tushirishda xato chiqishi mumkin.
- Wi-Fi: qo'shni (ustma-ust tushmaydigan) kanallarning qisman xalaqiti va roaming'da kanallararo skanerlash modellashtirilmaydi.
- Rejada: LTE/5G, harakatlanuvchi stansiyalar, bir nechta parametrli tadqiqot (2D), ns-3'da kabel yuklamasi statistikasi, VLAN/trunk, pcap yuklab olish.

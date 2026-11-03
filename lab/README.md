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

ns-3 o'rnatish (Mininet shart emas, Windows'da WSL yoki Linux kerak):

```bash
pip install ns3
```

Bitta topologiya va bitta ssenariyni uchala dvigatelda ishga tushirib, natijalarni
solishtirish mumkin. Masalan, `ospf_lab` → `uzilish` ssenariysida analitik model
va ns-3 bir xil o'tkazuvchanlikni (~5 Mbit/s video, kabel uzilganda 0) beradi,
lekin kechikish va TCP tiklanishi farq qiladi. Bu modellarning chegarasini ko'rsatadi.

**ns-3 modellashtirishdagi soddalashtirishlar:**
- Dinamik routing (OSPF/IS-IS/BGP) o'rniga global SPF ishlatiladi; kabel uzilganda yo'llar darhol qayta hisoblanadi.
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

## Namunalar (Ochish → Namunalar)

- `ospf_lab`: ikki LAN, ikki FRR router, OSPF area 0. Ssenariylar: `asosiy`, `uzilish`, `tez_kabel`.
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
- Rejada: parametrik tadqiqot (bir parametrni diapazonda o'zgartirib, avtomatik ishga tushirish), ns-3'da OSPF/RIP modellari va simsiz (Wi-Fi/LTE) qurilmalar, ns-3'da kabel yuklamasi statistikasi, VLAN/trunk, pcap yuklab olish.

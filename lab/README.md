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

## Namunalar (Ochish → Namunalar)

- `ospf_lab`: ikki LAN, ikki FRR router, OSPF area 0.
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
| `templates/p4/`, `templates/sdn/` | P4 va SDN shablonlari |
| `examples/` | Namunaviy loyihalar |
| `projects/` | Sizning loyihalaringiz (gitignore) |
| `web/` | UI (vanilla JS). Dizayn tokenlari `studio/web/css` dan olinadi. `web/vendor/xterm` — xterm.js (MIT) |

## Cheklovlar va keyingi qadamlar

- Real driver Ubuntu'da Mininet 2.3, OVS 2.17+, FRR 8+ va p4lang paketlari bilan ishlash uchun yozilgan. Bu repo'da u faqat statik tekshirilgan (CI'da Mininet yo'q). Birinchi ishga tushirishda xato chiqsa, **Hodisalar** panelidagi logni yuboring.
- Router qurilmasini tarmoq ishlayotganda qayta nomlab bo'lmaydi.
- Rejada: VLAN/trunk portlar, iperf3 trafik generatori UI'dan, ssenariylar (vaqt bo'yicha hodisalar), natijalarni yozib olish va solishtirish (OPNET uslubidagi tahlil), pcap'ni yuklab olish.

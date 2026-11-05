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

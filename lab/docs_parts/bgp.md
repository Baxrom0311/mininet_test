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

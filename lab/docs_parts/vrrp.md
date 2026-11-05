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

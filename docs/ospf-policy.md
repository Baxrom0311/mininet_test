# IPv4 OSPF network va passive-interface modeli

Demo, analitik va ns-3 IPv4 marshrutlari endi `config.startup` ichidagi OSPF
interfeys tanlash qoidalariga amal qiladi. Bu FRR konfiguratsiyasini umumiy model
uchun o‘qiydi; real driverga yuboriladigan matnni o‘zgartirmaydi.

`bgp_lab`dagi aniq tuzatish: r2 faqat `10.12.0.0/30`ni OSPF’da e’lon qiladi.
Uning `203.0.113.0/30` tashqi subneti r1’da OSPF yo‘li bo‘lib ko‘rinmaydi.
`10.2.0.0/24` esa next-hop-self orqali iBGP yo‘li bo‘lib qoladi, pc1 → srv1 ishlaydi.

## Qo‘llab-quvvatlanadigan startup qatorlari

```text
router ospf
 network 10.12.0.0/30 area 0
 network 10.1.0.0/24 area 0.0.0.0
 passive-interface default
 no passive-interface r1-eth2
!
```

- `network PREFIX area AREA` interfeysning **IP manzilini** tanlaydi. Masalan,
  `network 10.1.0.1/32 area 0` 10.1.0.1/24 interfeysini tanlaydi va haqiqiy /24
  subnetini e’lon qiladi. Tarmoq qatori yangi sun’iy prefiks yaratmaydi.
- Passive interfeys subneti e’lon qilinadi; unda qo‘shnichilik tuzilmaydi.
  `passive-interface IFNAME`, `passive-interface default` va ularning `no`
  shakllari qo‘llanadi. Interfeysning aniq qiymati defaultdan ustun.
- `no network PREFIX area AREA` shu prefiks/area juftligini bekor qiladi.
- `interface IFNAME` ostidagi `ip ospf area AREA` ham tanlashni yoqadi. Aniq
  interfeys area’si `network` qatorlaridan ustun; aks holda eng uzun mos network
  prefiksi tanlanadi. `no ip ospf area AREA` aniq interfeys qiymatini olib tashlaydi.
- Area butun 0..4294967295 yoki dotted IPv4 ko‘rinishida bo‘ladi; `0` va
  `0.0.0.0` bir xil. Qo‘shnichilik uchun ikkala tomon area’si teng, OSPF faol va
  ikkala interfeys ham non-passive bo‘lishi kerak.
- BGP/RIP kabi boshqa `router` bloklaridagi network qatorlari OSPF tanlashiga
  kiritilmaydi. Bo‘sh qatorlar, `!`/`#` izohlari o‘tkaziladi; exit/end scope’ni yopadi.

`router ospf` bor, lekin hech qanday interfeys tanlanmagan bo‘lsa, uzoq prefikslar
o‘rganilmaydi. Connected va statik yo‘llar bundan mustaqil ishlaydi.

Eski daemon-only loyihalar uchun moslik saqlanadi: startup’da umuman IPv4 OSPF
tanlash konfiguratsiyasi bo‘lmasa, `ospfd` routerining barcha interfeyslari area 0
deb olinadi. Bu legacy model taxmini; daemonni yoqishning o‘zi real FRR’da network
qatorlarini yaratadi degani emas.

## Cheklov va validatsiya

Bu IPv4 default OSPF process uchun per-area hop SPF. Hello/LSA/DR/BFD, cost metric,
ABR/inter-area summary, redistribution va route-map qo‘shilmagan. Har area alohida
qidiriladi; boshqa area prefiksi o‘zboshimchalik bilan tarqatilmaydi. IPv6 OSPFv3
IPv4 startup network/passive qatorlari bilan filtrlanmaydi; oldingi model saqlangan.
RIP/IS-IS va statik yo‘llar o‘z mexanizmlaridan foydalanadi.

Qo‘llanadigan network/passive/area sintaksisi xato bo‘lsa project validation xatoni
router va startup qator raqami bilan ko‘rsatadi. Valid real FRR process-ID/VRF
konfiguratsiyasi umumiy project validation’da shu model cheklovi sabab rad etilmaydi;
ammo model uni default processga tekislab hisoblamaydi, `UnsupportedOspf` bilan
aniq to‘xtaydi. Bu valid FRR konfiguratsiyasining barcha buyruqlari tekshirilganini
anglatmaydi: real konfiguratsiya qabulini FRR tekshiradi.

```bash
python -m unittest lab.tests.test_ospf_policy -v
python -m unittest lab.tests.smoke_ospf_policy -v
# Dedicated root host, real FRR acceptance (Claude):
sudo flock /tmp/netlab-real.lock python3 -m lab.tests.real_bgp
```

Model fixture’lari sintetik. Paket testi haqiqiy ns-3 UDP paketlari bilan network
exclusion, passive transit, uzilish/tiklanish va iBGP next-hop’ni tekshiradi.
Rejadagi I bandining **real FRR taqqoslashi Claude tomonidan keyin bajariladi**;
`real_bgp` testiga r1’da e’lon qilinmagan transit prefiksi yo‘qligini tekshiruvchi
assertion qo‘shilgan. Model/ns-3 natijasi real protokol acceptance o‘rniga ishlatilmaydi.

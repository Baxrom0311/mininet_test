# OSPF interfeys siyosati regressiyasi — 2026-11-06

Branch: `fix/ospf-network-passive`, baza `main` `207ba52`.
Rejadagi I bandining model/ns-3 qismi. A/C va OSPF kechikishi branchlaridan
mustaqil; katta driver refaktori yoki real FRR konfiguratsiyasi tahriri yo‘q.

| Tekshiruv | Natija |
|---|---|
| Umumiy backend regressiyasi | **97 test passed**, 10.34 s (82 mavjud + 15 yangi) |
| OSPF policy, IPv6, IS-IS model/ns-3 | **19 test passed**, 106.71 s, 7 ns-3 subprocess |
| Asl demo/analitik smoke to‘plamlari | **12 suite passed** |
| Barcha misol loyihalari | `validate_project` xatosiz |
| Python AST, workflow YAML, lokal Markdown havolalari, diff whitespace | **passed** |

Yangi model regressiyalari network IP-selection va haqiqiy subnet e’loni,
passive LAN/transit/default/exception, no-network, area mosligi, interface-area,
strict empty OSPF block, legacy daemon-only mosligi, boshqa router contextlari,
mixed RIP/OSPF label, startup type/syntax va valid real VRF’ni modelga
tekislamaslikni tekshiradi. IPv4 qoidalari IPv6 OSPFv3’ga qo‘llanmagan.

Uchta yangi ns-3 jarayonida:

1. E’lon qilinmagan LAN uchun UDP `tx_pkts > 0`, `rx_pkts == 0`; parallel ruxsatli
   oqim >400 kbit/s ishlaydi. Bu firewall sabab oldindan o‘chirilgan sintetik oqim emas.
2. Bir tomondagi passive transit muqobil yo‘lga majbur qiladi; qolgan faol transit
   uzilganda trafik yo‘qoladi, tiklanganda qaytadi. Passive qoidasi e’tiborsiz
   qolganida bevosita yo‘l ishlardi va ushbu tekshiruv yiqilardi.
3. `bgp_lab`: pc1 → 203.0.113.2 uchun qabul qilingan paket yo‘q; pc1 → srv1
   iBGP/next-hop-self orqali >400 kbit/s ishlaydi.

Qolgan to‘rtta ns-3 jarayoni oldingi IPv4/IPv6 TCP/UDP/echo va IS-IS packet
regressiyalaridir. ns-3 `3.44.post0`, cppyy `3.5.0`, Python 3.12 ishlatilgan.
Asl 12 suite’da `NETLAB_SKIP_NS3=1` bo‘lgan: demo, BGP, STP, firewall, multicast,
VRRP, LAG, survivability, failures, performance, report, N-2/SRLG.

```bash
python -m unittest lab.tests.test_ospf_policy -v
CXX=g++ python -m unittest lab.tests.smoke_ospf_policy lab.tests.smoke_ipv6 lab.tests.smoke_isis -v
```

`real_bgp`ga shu e’lon qilinmagan prefiksning r1 kernel RIB’ida yo‘qligi uchun
yangi assertion qo‘shilgan; bu real assertion ushbu sessiyada bajarilmagan.
Haqiqiy FRR bilan yangi taqqoslash bajarilmagan; bu Claude zimmasidagi I acceptance
va D real to‘plamiga qoladi. Remote GitHub CI ushbu sessiyada ishga tushirilmagan.
Qoidalar va cheklovlar: [ospf-policy.md](../ospf-policy.md).

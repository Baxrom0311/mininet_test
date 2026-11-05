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

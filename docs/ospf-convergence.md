# OSPF/OSPFv3 vaqt modeli

Analitik va ns-3 ssenariylarida `ospf_convergence_s` bilan OSPF marshrutining
o‘rnatilishini kechiktirish mumkin. Standart **0 s** — oldingi darhol SPF xulqi.
Faqat 0..3600 oralig‘idagi chekli JSON soni qabul qilinadi.

```json
{
  "duration": 30,
  "ip_version": 4,
  "ospf_convergence_s": 1.7,
  "apps": [],
  "events": [{"t": 10, "action": "link_down", "link": "l1"}]
}
```

Bu fragmentni `l1` mavjud loyiha ssenariysiga qo‘shing. IPv4 uchun `ospfd`, IPv6
uchun `ospf6d` ishlatiladi. Haqiqiy Mininet/FRR bu parametrni ishlatmaydi;
uning konvergensiyasi FRR konfiguratsiyasi va kernel holatiga bog‘liq.

## Forwarding semantikasi

- Link holati haqiqatan o‘zgarsa (`link_down` yoki `link_up`), oxirgi o‘rnatilgan
  OSPF yo‘llari berilgan muddat davomida saqlanadi. Jismoniy kabel darhol uziladi;
  eski yo‘l o‘sha kabel orqali o‘tsa, paketlar yo‘qoladi.
  ns-3 o‘chgan interfeysga gateway yo‘lini o‘rnatmaydi (IPv6 source-address
  selection abortini oldini olish uchun); trafik uchun u baribir yaroqsiz.
- Kutish tugaganda yangi topologiyadagi SPF yo‘llari o‘rnatiladi. Kabel tiklanganda
  ishlab turgan muqobil yo‘l kutish davomida ham ishlaydi.
- Ketma-ket o‘zgarishlar kutish muddatini oxirgi hodisadan boshlab qayta hisoblaydi;
  oraliq SPF natijasi jadvalga muddatidan oldin tushmaydi.
- Takroriy `link_down`/`link_up` joriy holat bilan bir xil bo‘lsa muddat uzaymaydi.
  Boshidanoq uzilgan kabellar uchun qo‘shimcha boshlang‘ich kutish yo‘q.
- Connected va statik yo‘llar joriy holatga mos; OSPF bo‘lmagan protokollarga bu
  parametr kechikish qo‘shmaydi. `link_params` va harakat bu taymerni boshlamaydi.

Bu OSPF Hello/LSA/DR/BFD paketlarini simulyatsiya qilmaydi; OSPFv3 uchun ham xuddi
shu forwarding taxmini qo‘llanadi. IS-IS modeli o‘zgarmaydi. SPF hop soni va mavjud
demo marshrut tanlash qoidalaridan foydalanadi; taymer cost/area modelini qo‘shmaydi.

Analitik dvigatel 1 s qadamlarda ishlaydi: 10 s hodisa + 1.7 s kutish yangi yo‘lni
12 s qadamida ishlatadi. ns-3 kutish tugashini alohida vaqt nuqtasida qo‘llaydi;
`sample` faqat statistikani yig‘ish oralig‘ini belgilaydi. `route_events` va
`convergence` jadval o‘zgarishlarini namuna vaqtida kuzatadi, taymerning ichki aniq
hodisa vaqti yoki ping uzilishining o‘rnini bosmaydi. Natija `notes`ida tanlangan
kechikish va bu model ekani ko‘rsatiladi. N-1/N-2 survivability tahlili barqaror
holatni hisoblaydi, `convergence` maslahatida shu parametrni ko‘rsatadi.

## Kalibratsiya

[Real hisobot](validation/real-env-2026-11-06.md)dagi OSPF `route_update_s`
medianasi ~1.74 s, lekin atigi ikki yaroqli takror bor. Bu carrier-down hodisasi,
hello/dead 1/4 s va o‘sha FRR/topologiyaga tegishli. Umumiy standartni 1.7 s ga
almashtirish uchun yetarli asos yo‘q.

`lab.calibration` endi tasdiqlangan `ospf,route_update_s,s` kuzatuvlaridan
`ospf_convergence_s` taklif qiladi. `traffic_gap_s` shu timerga aylantirilmaydi;
taklif avtomatik qo‘llanmaydi. CSV’dagi eski `model=0` natijalari tarixiy o‘lchovdir,
fayllar qayta yozilmaydi. Bir xil topologiya va failure turida ko‘proq takror yig‘ib,
taklifni alohida ssenariyda tekshirish kerak.

```bash
python -m unittest lab.tests.test_ospf_convergence lab.tests.test_calibration -v
python -m unittest lab.tests.smoke_ospf -v
```

Birinchi to‘plam sun’iy model fixture’larini ishlatadi; ikkinchisi ns-3 paketlarini
alohida jarayonlarda IPv4/IPv6 bilan sinaydi. Bu real FRR protokol sinovi emas.

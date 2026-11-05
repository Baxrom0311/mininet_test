### ns-3 tezligi: katta topologiyalar

ns-3 — paket darajasidagi simulyatsiya: vaqt deyarli to'liq C++ ichida (paketlar soni ×
hop'lar), Python tomoni (namuna olish, marshrutlar) sezilarsiz. Profil (gdb namunalari):
TCP/IP stek, navbatlar, bridge — "yagona sekin joy" yo'q. Shuning uchun:

**Aniq rejim (standart) — natija o'zgarmaydi**, faqat bizning qo'shimcha yuklamamiz olib tashlandi:

- FlowMonitor faqat ilova uchlarida (har hop'dagi probe — paket tegi, hash — behuda edi);
- qurilma hisoblagichlari: `Tx` trace o'rniga `SendOutgoing` + `UnicastForward` (ns-3 `Tx`
  trace'ga ulanilsa har hop'da paketni nusxalab sarlavha qo'shadi), `std::map` o'rniga vektor;
- `RateErrorModel` yo'qotish 0 bo'lganda o'chiriladi (har paketda tasodifiy son chizilmaydi).

Tezlashuv kichik (~0–8%, ns-3 ning o'zi asosiy vaqt). Barcha 21 namuna ssenariysida va
generatsiya qilingan ring10/campus'da natijalar (oqimlar, kabellar, qurilmalar, route_events,
konvergensiya) BAYT darajasida bir xil (stp_lab'da route_events tartibi bir soniya ichida
hash'ga bog'liq edi — endi saralangan).

**Tezkor rejim (ixtiyoriy, taxminiy)**: ssenariyda `"ns3_fast": true` (yoki
`NETLAB_NS3_FAST=1`) — jumbo MTU 9000 / MSS 8948, navbatlar BAYT bo'yicha teng qoldiriladi
(paket soni ×1500/9000). TCP paketlari ~6 baravar kam → 4–5 baravar tez. UDP oqimlari bir xil;
TCP oqimlarining umumiy o'tkazuvchanligi yaqin, lekin bir-biri bilan bo'lishishi (adolat)
va paket darajasidagi kechikish/jitter farq qiladi. Wi-Fi bo'lsa qo'llanmaydi. Natijada
`ns3_fast: true` va izoh bo'ladi.

O'lchovlar (30 s, bitta link_down; 4 yadro, bir vaqtda 2–4 ish — taxminiy):

| Topologiya | Asos | Aniq (yangi) | Tezkor (`ns3_fast`) | Tezkor: TCP jami / UDP |
|---|---|---|---|---|
| ring 10 router, 30 host, 20 oqim | 290 s | 267 s | 54 s (5.4x) | −0.2% / <0.1% |
| ring 20 router, 100 host, 40 oqim | 567 s | — | 107 s (5.3x) | — |
| campus 2/4/8, 40 host, 40 oqim | 356 s | 366 s | 88 s (4.0x) | +0.7% / <2% |
| ring 40 router, 200 host, 100 oqim | ~2600 s (taxm.: 36 daq. da 83%) | — | 404 s (~6x) | — |

Tezkor rejimda alohida TCP oqimlari ulushi o'zgaradi (median 8–15%, eng ko'p 93%) — umumiy
yuklama/sig'im tahlili uchun, oqim darajasidagi aniq natija uchun emas.

**Parallel tadqiqotlar**: ns-3 dvigatelida parametrik tadqiqot variantlari bir vaqtda
ishga tushiriladi (har biri alohida jarayon): `NETLAB_NS3_PARALLEL=N`, standart — yadrolar
soni / 2 (1..4). Misol: 4 variantli tadqiqot (har biri ~19 s) 2 parallel bilan 39 s.
Har ns-3 jarayoni katta topologiyada 1–3 GB xotira olishi mumkin (ring 40: ~2.8 GB).
Analitik (GIL) va Mininet (bitta jonli tarmoq) ketma-ket qoladi.

Test: `python3 -m lab.tests.smoke_ns3_perf` (tezkor rejim; `NETLAB_NS3_PERF_EXACT=1` — aniq ham).

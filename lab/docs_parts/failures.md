### Nosozlik va tiklanish (MTBF / MTTR, mavjudlik)

OPNET'dagi "Failure/Recovery" kabi: ssenariyga `failures` qo'shiladi — kabel va qurilmalar
tasodifiy (lekin seed bo'yicha takrorlanadigan) vaqtlarda ishdan chiqadi va tiklanadi.

```json
"failures": {"seed": 1, "distribution": "exponential",
             "links":   {"mtbf_s": 120, "mttr_s": 15, "targets": ["l2", "l3"]},
             "devices": {"mtbf_s": 600, "mttr_s": 60, "targets": ["r2"]}}
```

- `distribution`: `exponential` (standart; ish/nosozlik davrlari eksponensial) yoki `fixed`
  (aniq MTBF/MTTR — tushuntirish va tekshirish uchun qulay).
- `targets`: ro'yxat yoki `"all"` (qurilmalarda host/server'siz).
- Qurilma nosozligi — uning barcha kabellari uziladi. Bir kabelni ikki sabab (kabel o'zi va
  qurilma) uzsa, kabel ikkalasi ham tugaguncha uzilgan qoladi.
- Nosozliklar oddiy `link_down` / `link_up` hodisalariga aylantiriladi (`source: "failure"`,
  `cause: [...]`), shuning uchun **uchala dvigatel** (analitik, ns-3, Mininet) ularni bir xil
  bajaradi, routing/STP/VRRP/BGP konvergensiyasi ham odatdagidek ishlaydi (`lab/failures.py`).

Natijada `availability`:

| Maydon | Ma'nosi |
|---|---|
| `flows.<id>.availability_pct` | oqim faol bo'lgan soniyalardan trafik o'tgan soniyalar ulushi (ping — javob kelgan soniyalar); http — yuklangan sahifalar, ftp — fayl yakunlandi |
| `flows.<id>.outages` | uzilishlar: boshlanish, tugash, davomiylik |
| `links.<id>` | ishlamay turgan vaqt (s, %), nosozliklar soni, oraliqlar |
| `theoretical.<nishon>` | A = MTBF / (MTBF + MTTR) |
| `mean_flow_availability`, `min_flow_availability` | oqimlar bo'yicha o'rtacha / eng yomon |

Parametrik tadqiqot: `{"kind": "failure", "group": "links", "field": "mtbf_s" | "mttr_s"}` yoki
`{"kind": "failure", "field": "seed"}` (bir nechta seed — Monte-Karlo o'rtachasi).

Namuna: `reliability_lab` — OSPF halqasi (zaxira yo'l) va zaxirasiz r3–srv2 kabeli:

| `ishonchlilik` (600 s, MTBF 120 s, MTTR 15 s; nazariy 88.9%) | pc1→srv1 (zaxirali) | pc1→srv2 (zaxirasiz) | Vaqt |
|---|---|---|---|
| Analitik | 93.3% | 85.3% | ~1 s |
| ns-3 | 93.8% | 86.1% | ~22 s |

Tekshiruvlar: qat'iy MTBF 100 / MTTR 10 (3600 s) — 91.1% (nazariy 90.9%); eksponensial,
6 seed o'rtachasi 92.1%; MTTR 5 / 20 / 60 s → 96.9 / 89.6 / 72.5%. Real Mininet
(MTBF 10 / MTTR 5, qat'iy): kabel 10–15 va 25–30 s da haqiqatan uziladi, ping mavjudligi
73.7% (kabel ishlagan vaqt 75%).

Cheklovlar: analitik dvigatel hodisalarni 1 s qadamda qo'llaydi (1 s dan qisqa uzilish
ko'rinmasligi mumkin); mavjudlik 1 s aniqlikda; nosozlik hodisasi foydalanuvchining shu kabel
uchun qo'lda yozgan hodisasini bekor qilishi mumkin (masalan, nosozlik tugashi `link_up`).

```bash
python3 -m lab.tests.smoke_failures
```

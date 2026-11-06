# Kalibratsiya dalillarini sanash

`lab.calibration` model xatosi bilan namuna sonini alohida ko‘rsatadi. Bir yoki
ikki mos kelgan kuzatuv 10 takror talabini bajarmaydi. Standart `min_samples=1`
eski CLI xulqini saqlaydi; E/G uchun chegarani aniq bering:

```bash
python -m lab.calibration /tmp/measurements.csv --min-samples 10 \
  --require-metric ospf:route_update_s:s \
  --require-metric rip:route_recovery_s:s \
  --output /tmp/comparison.json
```

`--require-metric` takrorlanadi; `protocol:metric:unit` kichik harfga va chetdagi
bo‘shliqlarsiz ko‘rinishga keltiriladi. Talab qilingan guruh CSV’da bo‘lmasa ham
summary’da `samples=0` va bo‘sh median bilan ko‘rinadi. Boshqa guruhning mosligi
shu yo‘qolgan guruhni yashirmaydi. `--format markdown` jadvalda `2/10`, `0/10`
kabi sanashni ko‘rsatadi. JSON maydonlari:

| Maydon | Ma’nosi |
|---|---|
| `samples` | Bir xil protocol/metric/unit uchun takrorlanmagan verified source ID soni |
| `input_samples` | Shu guruhdagi barcha verified satrlar soni |
| `duplicate_samples` | Sanashdan chiqarilgan aniq nusxalar |
| `sufficient_samples` | `samples >= min_samples` |
| `within_bounds` | Barcha tekshirilgan kuzatuvlar o‘z toleransida; namuna yo‘q bo‘lsa `null` |
| `mixed_model_values` | Guruhdagi model bashoratlari har xil |
| `override_eligible` | Qo‘llanadigan seconds timeri, yetarli son va bir xil model qiymati bor |

Exit kodlari:

- **0 — passed**: barcha guruhlarda namuna soni yetarli va barcha kuzatuvlar toleransda.
- **1 — failed**: son yetarli, lekin kamida bir kuzatuv toleransdan tashqarida.
- **2 — unverified**: tekshirilgan kuzatuv yo‘q va alohida metric talabi berilmagan.
  Argument/CSV xatosi ham avvalgidek 2 qaytaradi va xatoni stderr’da yozadi.
- **3 — insufficient**: kamida bitta guruh uchun takrorlar yetmaydi yoki talab
  qilingan metric butunlay yo‘q. Bu holat fit xatosidan oldin ko‘rsatiladi;
  `within_bounds` orqali mavjud kuzatuvlardagi tafovut baribir ko‘rinadi.

## Nusxa va manba qoidasi

CSV shabloni o‘zgarmagan. `source` haqiqiy host/run/test/rep identifikatori
bo‘lishi kerak. Bir xil `(protocol, metric, unit, source)` va bir xil numeric
qiymatlar qayta kiritilsa, observation saqlanadi, ammo median va sanashga yana
qo‘shilmaydi. `duplicate_of` birinchi kuzatuvning 1 dan boshlangan data-row
raqamini ko‘rsatadi. Shu manbaga boshqa model/real/tolerance qiymati yozilsa
vosita zid satrlarni ko‘rsatib to‘xtaydi. Turli model variantlarini alohida
hisobotlarda baholang.

Turli source ID’lar mustaqil tajribalarni **isbotlamaydi**. Bir xil topologiya,
failure yo‘nalishi, taymer va probe intervali borligini o‘lchovchi tekshiradi.
Down/up kuzatuvlarini bir metric guruhiga yoki turli sharoitlarni bitta medianaga
aralashtirmang. Har xil model qiymati bo‘lgan guruh uchun pooled timer override
taklif qilinmaydi; har bir row fit tekshiruvi saqlanadi. `traffic_gap_s` hamon
protokol timeriga aylantirilmaydi.

## E/G uchun real yig‘ish

Dedicated root hostda mavjud collector OSPF link-down va RIP recovery uchun:

```bash
sudo python -m lab.benchmarks.real_calibration --only ospf rip --reps 10 \
  --output /tmp/measurements.csv
```

Keyin yuqoridagi compare buyrug‘ini bajaring. Collector yaroqsiz takrorlarni
chiqarsa, natija 10 tadan kam bo‘lib `insufficient` qaytadi. Mavjud collector
OSPF link-up uchun alohida metric yig‘maydi; E bandining bu qismi hali alohida
o‘lchov talab qiladi. Uni ham qamrab olish uchun alohida metric, masalan
`--require-metric ospf:route_restore_s:s`, talab qilish mumkin — yo‘q ma’lumot
avtomatik to‘ldirilmaydi va bu metric timer override’iga aylantirilmaydi.

Saqlangan [real hisobot](validation/real-env-2026-11-06.md) hamda xom CSV/JSON
o‘zgartirilmagan. Undagi 2–3 namuna `--min-samples 10` bilan yetarli hisoblanmaydi.
Yangi real o‘lchov yoki standart timer retuning bu branchda bajarilmagan.

```bash
python -m unittest lab.tests.test_calibration lab.tests.test_calibration_evidence -v
```

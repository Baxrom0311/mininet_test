# Kampaniya hisobotining takroriy hisoblarini kamaytirish

`perf/campaign-analysis` branchi `feat/experiment-campaigns` (`4b686b1`) ustida.
Maqsad: bir kampaniyada umumiy baseline’ni har variant uchun qayta tahlil qilish
xarajatini olib tashlash, hisobot qiymatlari va validatsiyasini saqlash.

## O‘lchangan bottleneck

Boshlang‘ich cProfile: 60 flow, 60 s analytic model, original baseline + 4 variant,
har birida 3 replication (15 run). Bitta report 13 summarize va 39 window analyze
chaqirig‘i qilgan. Profiling bilan jami 2.214 s; uning 1.844 s qismi summarize’da.
Bu profiler overhead’i bor natija, quyidagi oddiy wall-time bilan teng emas.

Avval har cohort uchun summary, keyin har mustaqil compare uchun ikkala summary
qayta hisoblangan. C ta condition uchun 3C−2 summary va (3C−2)×replication window
analysis bo‘lgan. Endi bitta request ichida C ta summary va C×replication window
analysis qoladi. Runtime/flow identity va normalized design ham shu request ichida
qayta ishlatiladi. Raqamlar va CI formulasi o‘zgarmagan.

## Request-local scope

Private `_ComparisonInput` faqat shu analysis request’iga tegishli, JSON’dan
alohida o‘qilgan recordlar bilan ishlaydi. Provenance hash, seed, runtime, flow,
window va missing-data qoidalari bajariladi. Tayyor context HTTP input sifatida
qabul qilinmaydi, manager/global keshda saqlanmaydi. Caller-owned recordlar
hisoblash davomida read-only hisoblanadi.

Keyingi so‘rov fayllarni yana o‘qiydi va tekshiradi. Edited/deleted/repaired record
keyingi natijada ko‘rinadi; uzun muddatli stale cache yoki disk cache yo‘q. Xato
natijasini shu request ichida qayta ishlatishda exception turi/args saqlanadi,
traceback/source-series frame’lari saqlanmaydi. Contextning weakref orqali bo‘shashi
normal va guard-error holatlarida test qilingan. Full report qiymatlari oldingi
mustaqil pairwise yo‘l bilan bir xil; ichki shared summary’lar read-only ishlatiladi.

## Ixtiyoriy ixcham JSON

```text
GET /api/campaigns/my_lab/CAMPAIGN_ID/report.json?compact=true
```

Parametrsiz yoki compact=false javob avvalgi full schema’da. HTML va ZIP eksport
ham full qoladi. compact=true javobda representation=cohort_references bo‘ladi;
comparison baseline/candidate summary’sining katta metrics/run_windows/notes
maydonlari takrorlanmaydi. Summary metadata va summary_ref saqlanadi:

```json
{
  "experiment_id": "BASELINE_ID",
  "summary_ref": "/experiments/0/summary"
}
```

summary_ref — shu JSON dokumenti ichidagi JSON pointer; tashqi URL yoki boshqa
projectga murojaat emas. To‘liq cohort summary experiments massivida bir marta
qoladi. Paired metriclar, samples, CI, guard/errors va config diff o‘zgarmaydi.
Error comparison’lar null result/error ko‘rinishida qoladi. compact invalid bool
qiymati 422 beradi, project scope va token qoidalari saqlanadi.

## Takrorlanadigan benchmark

```bash
python -m lab.benchmarks.campaign_analysis --flows 60 --duration 60 --variants 4 --repetitions 3 --output /tmp/campaign-analysis.json
```

Fixture haqiqiy analytic engine bilan yaratiladi; generation vaqtga kiritilmaydi.
Oldingi orchestration’ni takrorlovchi reference har cohortni va har pairni mustaqil
validatsiyalab hisoblaydi. Optimized va reference bir xil completed fayllarni bir
processda o‘qiydi, experiments/comparisons qiymatlari digest orqali tengligi
tekshiriladi. Bu real Mininet/ns-3 throughput yoki HTTP network latency benchmarki emas.

Shu instance, Python 3.12.14, Linux x86_64, 15 run, jami 8 841 618 bayt natija JSON:

| Amal | Reference | Request-local |
|---|---:|---:|
| Report vaqti, 3 o‘lchov mediani | 440.13 ms | 259.65 ms |
| Cohort summary chaqirig‘i | 13 | 5 |
| Window analysis chaqirig‘i | 39 | 15 |
| Runtime guard chaqirig‘i | 24 | 15 |

Shu fixture’da 1.70× tezlashdi. Full JSON 3 094 728 bayt, compact JSON 1 920 045 bayt
(37.96% kichik). Hajmlar UTF-8, compression’dan oldin. OS cache, CPU, disk va
ma’lumot hajmi natijaga ta’sir qiladi; boshqa topology yoki host uchun kafolat emas.
Xom o‘lchov: [validation/campaign-analysis-benchmark.json](validation/campaign-analysis-benchmark.json).

## Tekshiruv

```bash
python -m unittest lab.tests.test_campaign_analysis -v
python -m unittest lab.tests.smoke_campaigns -v
```

6 yangi test bilan backend to‘plami 82 test: reference ekvivalentligi, computation
counts, guard-failure reuse/revalidation, edited/deleted inputs freshness,
compact pointer/auth/default API va normal/error context lifetime. Packet campaign
suite IPv4/IPv6’da full va compact metriclar tengligini ham tekshiradi (8 ns-3 subprocess).

Lokal natija: 82 backend testi va 8 haqiqiy ns-3 subprocessli IPv4/IPv6 campaign
sinovi o‘tdi. Remote GitHub CI bu lokal tekshiruvdan alohida; hali bajarildi deb belgilanmagan.

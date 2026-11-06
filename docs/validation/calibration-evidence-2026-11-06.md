# Kalibratsiya dalillari regressiyasi — 2026-11-06

Branch: `fix/calibration-evidence`, baza `main` `b2296ae` (PR #18–#21 birlashtirilgan).
E/G’dagi qo‘shimcha real o‘lchovlar uchun hisobot vositasi; yangi real o‘lchov emas.

- **17 calibration testi passed**: 5 mavjud va 12 yangi evidence/CLI testi.
- **113 backend testi passed**, 10.97 s: joriy `main`ning runtime/queue/experiments/
  campaign/analysis/OSPF-delay/real-lock regressiyalari va 12 yangi test.
- Python sintaksisi, CI YAML parse, lokal Markdown havolalari va `git diff --check`
  passed. Remote CI ushbu sessiyada ishga tushirilmagan.

Yangi testlar aniq nusxalarning count/medianaga ta’sir qilmasligi, zid provenance
rad etilishi, source normalization, required metric yo‘qligi, unverified satrlar,
yetarli son bilan fit xatosining ajratilishi, mixed-model override cheklovi,
CLI JSON/Markdown hamda exit 0/1/2/3 holatlarini tekshiradi. Fixture’lar sintetik;
ulardan haqiqiy protokol konstantasi chiqarilmagan.

Saqlangan haqiqiy CSV’da:

| Metric | Takror | Talab | Fit |
|---|---:|---:|---|
| OSPF route_update_s | 2 | 10 | toleransdan tashqarida |
| RIP route_recovery_s | 3 | 10 | joriy toleransda |

`--min-samples 10` bilan status **insufficient**, exit **3**, timer override
takliflari bo‘sh. Boshqa saqlangan metriclarda ham 3 tadan namuna bor va ular
10 talabiga yetmaydi. Xom CSV hash’i tekshiruvdan oldin va keyin teng;
tarixiy JSON medianalari, sample sonlari, xatolari va fit statuslari standart
taqqoslashda o‘zgarishsiz qolishi regressiya bilan tekshirildi.

```bash
python -m unittest lab.tests.test_calibration lab.tests.test_calibration_evidence -v
python -m lab.calibration docs/validation/real-calibration-2026-11-06.csv \
  --min-samples 10 --require-metric ospf:route_update_s:s \
  --require-metric rip:route_recovery_s:s --format markdown
```

Ikkinchi buyruqda exit 3 kutiladi. [Qo‘llanma](../calibration-evidence.md) manba
identifikatorlari mustaqillik yoki bir xil tajriba sharoitini isbotlamasligini,
hamda OSPF link-up uchun alohida o‘lchov hali kerakligini tushuntiradi.
Timer standartlari va real collector kodi o‘zgartirilmagan.

### Hisobotlar: HTML va CSV eksport (OPNET "Report")

`lab/report.py` — bitta fayldan iborat, tashqi resurssiz HTML hisobot (CSS va SVG grafiklar
ichida; yorug'/qorong'i mavzu avtomatik, palitra UI bilan bir xil) hamda CSV to'plami.

| So'rov | Natija |
|---|---|
| `GET /api/runs/{loyiha}/{run_id}/report.html` | bitta natija: xulosa, oqimlar, ilovalar (MOS / sahifa / fayl), mavjudlik, grafiklar (o'tkazuvchanlik, kechikish, yo'qotish, MOS, kabel yuklamasi), kabellar (max yuklama/navbat/tashlangan), qurilmalar, konvergensiya, marshrut o'zgarishlari, hodisalar |
| `GET /api/runs/{loyiha}/{run_id}/export.zip` | `flows.csv`, `flow_summary.csv`, `links.csv`, `nodes.csv`, `apps.csv`, `availability.csv`, `convergence.csv`, `route_events.csv`, `events.csv`, `results.json` |
| `POST /api/reports/compare` `{"project", "run_ids": [...]}` | natijalarni solishtirish (umumiy oqimlar bo'yicha jadval va grafiklar) |
| `GET /api/studies/{loyiha}/{study_id}/report.html` | parametrik tadqiqot: 1D — metrika vs parametr grafiklari, 2D — issiqlik xaritalari |
| `POST /api/analysis/survivability/report` (survivability bilan bir xil tana) | SPOF, kritiklik reytingi, N-1 sig'im rejasi |

Buyruq qatori:

```bash
python3 -m lab.report lab/results/ospf_lab/<run_id>.json -o hisobot.html
python3 -m lab.report lab/results/ospf_lab/ -o solishtirish.html          # papkadagi barcha natijalar
python3 -m lab.report lab/results/ospf_lab/ --study -o tadqiqot.html
python3 -m lab.report lab/results/ospf_lab/<run_id>.json --zip -o natija.zip
```

Barcha foydalanuvchi matnlari (nomlar, izohlar) escape qilinadi; token himoyasi hisobotlarga ham
tegishli. Hisobot hajmi odatda 5–30 KB.

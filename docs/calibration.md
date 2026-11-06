# Model va real o‘lchovlarni taqqoslash

Real root hostda olingan tekshiruvlar va xom CSV/JSON
[2026-11-06 hisobotida](validation/real-env-2026-11-06.md) saqlangan. Quyidagi
qiymatlar o‘sha hisobotdan; bu cloud draftida yangi FRR o‘lchovi bajarilmagan.
Oldingi STP ~23 s taxmini o‘sha tekshiruv bilan tuzatilgan.

| Hodisa | Hozirgi model | Saqlangan real hisobot | Takror |
|---|---|---|---|
| STP direct link, forwarding | 2 × forward_delay = 30 s | median 29.7 s | 3 |
| VRRP master down | RFC MDI (~3.6 s, priority/advertga bog‘liq) | median 3.52 s | 3 |
| OSPF route update | standart 0 s; ixtiyoriy `ospf_convergence_s` | median 1.74 s | 2 |
| 20/30 router peak RSS | model taxmini kiritilmagan | 1.11/1.63 GB | har hajmga 1 |

VRRP master saylovi bilan end-to-end trafik uzilishini aralashtirmang: qaytish
OSPF yo‘li, neighbor resolution va probe interval ham uzilishga qo‘shiladi.
`recovery_probe_s` protokolning aniq ichki konvergensiya vaqti emas.

Real hostdagi takroriy o‘lchovlarni CSV’da saqlang:

```text
protocol,metric,unit,model,real,source,verified,abs_tolerance,rel_tolerance
```

`real` ustuniga faqat haqiqiy o‘lchovni, `source`ga host/run/test identifikatorini
yozing. `verified=true` — o‘lchovni taqdim etuvchining deklaratsiyasi; vosita
dalilning haqiqiyligini mustaqil tekshirmaydi. Bir xil metric/unit uchun takroriy
namunalar median va eng katta xato bilan jamlanadi.

```bash
python3 -m lab.calibration /tmp/measurements.csv --output /tmp/comparison.json
python3 -m lab.calibration /tmp/measurements.csv --format markdown --output /tmp/comparison.md
```

Qabul chegarasi: `abs(model-real) <= abs_tolerance + rel_tolerance * abs(real)`.
Exit 0 — barcha tekshirilgan kuzatuvlar chegarada; 1 — tafovut bor; 2 — tasdiqlangan
real kuzatuv yo‘q. NaN/Inf/manfiy raqamlar rad etiladi. Standartlar o‘zgarmaydi.

Faqat mos metric uchun ko‘rib chiqiladigan ssenariy override taklif qilinadi:
STP `port_forwarding_s`, VRRP `master_down_s`, RIP `route_recovery_s`, BGP
`session_recovery_s`, OSPF `route_update_s` → `ospf_convergence_s`.
`traffic_gap_s` protokol timeriga aylantirilmaydi. OSPF standart qiymati 0 s;
[OSPF vaqt modeli](ospf-convergence.md) cheklovlarini o‘qing.
Taklifni qabul qilishdan oldin modelni ayni topologiya,
failure turi, probe intervali va trafik bilan qayta sinang.

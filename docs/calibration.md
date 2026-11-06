# Model va real o‘lchovlarni taqqoslash

Real root hostda olingan tekshiruvlar va xom CSV/JSON
[2026-11-06 hisobotida](validation/real-env-2026-11-06.md) saqlangan. Quyidagi
qiymatlar o‘sha hisobotdan; bu cloud draftida yangi FRR o‘lchovi bajarilmagan.

STP uchun avvalgi emulyatsiya binlaridan olingan taxmin tuzatilgan.

| Hodisa | `main` modeli | Saqlangan real hisobot | Takror |
|---|---|---|---|
| STP direct link, forwarding | 2 × forward_delay = 30 s | median 29.7 s | 3 |
| VRRP master down | RFC MDI (~3.6 s, priority/advertga bog‘liq) | median 3.52 s | 3 |
| OSPF route update | standart 0 s, ixtiyoriy `ospf_convergence_s` | median 1.74 s | 2 |
| RIP (update 10) route recovery | 5 s | median 7.97 s | 3 |
| RSTP direct link, forwarding | 1 s | median 0.002 s | 3 |
| BGP session recovery | 2 s | median 1.23 s | 3 |
| 20/30 router peak RSS | model taxmini kiritilmagan | 1.11/1.63 GB | har hajmga 1 |

O‘lchov bazasi `648fbd8` (PR #12); artefaktlar PR #15 bilan qo‘shilgan.
Bu PR #13 va keyingi kodning real acceptance natijasi emas. OSPF/RIP uchun
kamida 10 takrorli qayta o‘lchov rejalangan. RSTP/BGP standartlari uchta namuna
asosida avtomatik almashtirilmaydi; joriy toleransda ular hisobotda o‘tgan.


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
E/G uchun `--min-samples 10` va takroriy `--require-metric` ishlating:
[namuna soni, dublikatlar va exit kodlari](calibration-evidence.md).
Exit 0 — barcha tekshirilgan kuzatuvlar chegarada; 1 — tafovut bor; 2 — tasdiqlangan
real kuzatuv yo‘q; 3 — so‘ralgan namuna soni yoki metric yetishmaydi.
NaN/Inf/manfiy raqamlar rad etiladi. Standartlar o‘zgarmaydi.

Faqat mos metric uchun ko‘rib chiqiladigan ssenariy override taklif qilinadi:
STP `port_forwarding_s`, VRRP `master_down_s`, RIP `route_recovery_s`, BGP
`session_recovery_s`, OSPF `route_update_s` → `ospf_convergence_s`.
`traffic_gap_s` protokol timeriga aylantirilmaydi. OSPF standart qiymati 0 s;
[OSPF vaqt modeli](ospf-convergence.md) cheklovlarini o‘qing.
Taklifni qabul qilishdan oldin modelni ayni topologiya,
failure turi, probe intervali va trafik bilan qayta sinang.

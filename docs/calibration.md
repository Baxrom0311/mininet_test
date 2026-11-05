# Model va real o‘lchovlarni taqqoslash

Bu cloud instance’da root/FRR yo‘q; yangi real protokol yoki resurs o‘lchovi yo‘q.
Oldingi suhbatdagi STP ~23 s va VRRP ~8 s qiymatlari foydalanuvchi yuborgan
hisobotdan olingan, bu sessiyada qayta tasdiqlanmagan. Ular model konstantalarini
avtomatik almashtirishga asos qilinmadi.

| Hodisa | Hozirgi model | Tashqi hisobot | Bu sessiyadagi real tekshiruv |
|---|---|---|---|
| STP direct link, forwarding | 2 × forward_delay = 30 s | ~23 s | blocked |
| VRRP master down | RFC MDI (~3.6 s, priority/advertga bog‘liq) | end-to-end trafikda ~8 s | blocked |
| OSPF route update | analitik SPF darhol | alohida o‘lchov kerak | blocked |
| 20+ router startup/CPU/RSS | taxmin kiritilmagan | yangi benchmark kerak | blocked |

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
`session_recovery_s`. `traffic_gap_s` yoki OSPF uchun mavjud bo‘lmagan timer
uydirib qo‘shilmaydi. Taklifni qabul qilishdan oldin modelni ayni topologiya,
failure turi, probe intervali va trafik bilan qayta sinang.

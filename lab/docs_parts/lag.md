### Link aggregation (802.3ad LACP / bond)

Ikki switch orasidagi bir nechta parallel kabelni bitta mantiqiy kanalga birlashtirish.
Switch `config.bonds` (qo'shni switch'da xuddi shu kabellarning ikkinchi uchlari bilan
mos bond bo'lishi shart):

```json
"bonds": {"bond1": {"ports": [3, 4], "mode": "balance-tcp", "lacp": "active"}}
```

- `mode`: `balance-tcp` (oqim — L4 hash), `balance-slb` (manba MAC), `active-backup`
  (faqat birlamchi a'zo, qolganlari zaxira).
- `lacp`: `active` / `passive` / `off` (ikkala tomon passive yoki faqat bir tomon off —
  validatsiya xatosi).
- Faqat switch <-> switch: host/router bonding qo'llab-quvvatlanmaydi (konteyner/WSL
  kernelida Linux bonding moduli bo'lmasligi mumkin; OVS bond'i switch'lar orasida ishlaydi).

| Dvigatel | Qanday |
|---|---|
| Mininet | a'zo portlar `ovs-vsctl add-bond <sw> <sw>-<bond> … bond_mode=… lacp=… lacp-time=fast` bilan bitta OVS portiga aylantiriladi (ofport va tc saqlanadi). Inspect: `ovs-appctl bond/show`, `lacp/show` + model jadvali |
| Analitik / demo | bond — bitta L2 qo'shnichilik; har oqim deterministik hash bo'yicha BITTA a'zodan o'tadi (kabel statistikasi a'zo bo'yicha). A'zo uzilsa — `bond_failover_s` (standart 0.5 s) davomida unga hash qilingan oqimlar yo'qoladi, keyin qolgan a'zolarga qayta taqsimlanadi |
| ns-3 | bonding yo'q — bond bitta vakil a'zo kabel orqali yuradi, tezligi = ishlaydigan a'zolar yig'indisi (qolganlari yopiq); oqim bo'yicha hash yo'q, statistika vakil kabelda |

**STP bilan:** OVS STP/RSTP yoqilgan bridge'da bond porti "disabled" rolda qoladi va
hech qachon forwarding'ga o'tmaydi (real sinovda tekshirildi). Shuning uchun bond'li
switch'da STP avtomatik o'chiriladi (ogohlantirish bilan) — model ham shunday hisoblaydi.
Bond'ning parallel a'zolari halqa hisoblanmaydi (bitta qirra); bond'siz parallel kabellar
STP bilan — bittasi blocking, STP'siz — broadcast storm. Bond'li switch'da boshqa zaxira
yo'llar halqa hosil qilmasin.

Namuna: `lag_lab` — s1–s2 2×100 Mbit/s bond (balance-tcp, LACP), t=10 s da a'zo `l5` uziladi.

| | 2 oqim (oldin) | `l5` uzilgandan keyin |
|---|---|---|
| Analitik | 200 Mbit/s (har a'zo ~100) | 100 Mbit/s |
| ns-3 | 200 Mbit/s | ~3 s uzilish (MAC qayta o'rganish), keyin 100 Mbit/s |
| Mininet (real, userspace OVS) | 4 parallel oqim: 134 Mbit/s (bitta oqim 95); LACP `negotiated` | trafik darhol davom etadi |

Real sinovda balance-tcp hash oqimlarni a'zolarga notekis taqsimladi (masalan 75 MB / 31 MB) —
ikkita oqim ham bitta a'zoga tushishi mumkin (hash to'qnashuvi).

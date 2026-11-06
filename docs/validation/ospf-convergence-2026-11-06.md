# OSPF vaqt modeli regressiyasi — 2026-11-06

Baza: `main` `207ba52` (PR #17). Branch: `feat/ospf-convergence`.
Bu hisobotdagi yangi natijalar model va ns-3 tekshiruvlaridir; yangi root/FRR yoki
real kernel IPv6 o‘lchovi bajarilmagan.

| Tekshiruv | Natija |
|---|---|
| Backend unit/API regressiyasi | **91 test passed** (10.74 s) |
| IPv6, IS-IS, OSPF model/ns-3 to‘plami | **19 test passed** (88.65 s, 6 ns-3 subprocess) |
| Yakuniy OSPF fractional-event regressiyasi | **passed**, IPv4/IPv6 2 subprocess (29.20 s) |
| Asl smoke to‘plamlari | demo, BGP, STP, VRRP, N-1, N-2/SRLG, report, performance — **passed** |
| Python AST va `git diff --check` | **passed** |

91 test ichida 8 yangi OSPF model testi va 1 yangi calibration testi bor.
OSPF regressiyasi manfiy/NaN/Inf/bool/string/katta integer qiymatlarni rad etishni,
standart 0 s mosligini, IPv4/IPv6 uzilish va muqobil yo‘lga o‘tishni, statik/connected
yo‘llarni, takroriy/ketma-ket hodisalarni va survivability maslahatini tekshiradi.

Paket sinovida 0.5 s sampling, hodisa `t=4.2500001`, OSPF kechikishi `2.125 s`:
6 s gacha trafik yo‘q, `6..6.5 s` binida qisman trafik, 7 s da to‘liq trafik bor.
Takroriy down hodisasi timer muddatini uzaytirmaydi. Link up paytida ishlab turgan
muqobil yo‘l uzilmaydi; keyin afzal yo‘l tiklanadi. Bu jonli OSPF paketlari emas.

Yangi sinovda aniqlanib tuzatilgan ikki muammo:

- IPv6 `SetDown` manzillarni tozalagach o‘sha interfeysga eski gateway marshrutini
  qo‘yish ns-3 `SourceAddressSelection` assertiga olib kelgan. O‘chgan interfeysga
  gateway marshruti qo‘yilmaydi; trafik kutish tugaguncha tashlanadi.
- Event vaqtini 6 xonagacha yaxlitlash hodisani keyingi namuna vaqtiga surishi
  mumkin edi. Event mark endi asl vaqtni saqlaydi; yangi fractional regressiya
  ikkala IP oilasida buni tekshiradi.

Saqlangan real CSV qayta taqqoslanganda OSPF taklifi `1.7443 s`; standart **0 s**
o‘zgarmagan. CSV/JSON tarixiy xom real o‘lchovlar tahrirlanmagan.

## Qayta bajarish

```bash
python -m unittest lab.tests.test_runtime_safety ml.test_labeling lab.tests.test_calibration lab.tests.test_ospf_convergence lab.tests.test_traffic lab.tests.test_run_queue lab.tests.test_experiments lab.tests.test_result_index lab.tests.test_analysis_windows lab.tests.test_experiment_comparison lab.tests.test_campaigns lab.tests.test_campaign_analysis -q
CXX=g++ python -m unittest lab.tests.smoke_ospf lab.tests.smoke_ipv6 lab.tests.smoke_isis -v
```

Asl smoke to‘plamlarida `NETLAB_SKIP_NS3=1` qo‘llangan; ularning paket qismlari
ushbu smoke natijasiga kirmaydi. Paketlar yuqoridagi alohida ns-3 suite’da
Python 3.12, ns-3 `3.44.post0`, cppyy `3.5.0` bilan tekshirilgan.
CI ro‘yxatiga yangi testlar qo‘shilgan; remote CI ushbu sessiyada ishga tushirilmagan.

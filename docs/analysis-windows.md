# Warm-up va vaqt oralig‘i bo‘yicha tahlil

`feat/analysis-windows` branchi `perf/run-result-index` (`9b16071`) ustiga qurilgan.
Tayyor natijani qayta tahlil qiladi; simulyatsiyani qayta bajarmaydi, saqlangan
series/summary yoki UI dizaynini o‘zgartirmaydi.

## API

```text
GET /api/runs/my_lab/RUN_ID/window?start_s=5&end_s=25
GET /api/experiments/my_lab/EXPERIMENT_ID?start_s=5&end_s=25
GET /api/experiments/my_lab/EXPERIMENT_ID/report.html?start_s=5&end_s=25
GET /api/experiments/my_lab/EXPERIMENT_ID/export.zip?start_s=5&end_s=25
```

`start_s` simulyatsiya boshidan hisoblanadi: 5 s warm-up uchun 5. `end_s` tahlil
oxiri. Bittasi berilmasa 0 yoki run duration ishlatiladi. Chegaralar finite va
`0 <= start_s < end_s <= duration` bo‘lishi kerak. Noto‘g‘ri diapazon/NaN/Inf 400,
parse bo‘lmaydigan query 422 beradi. Single-run endpoint completed analytic/ns3
natijasini talab qiladi; pending/error va Mininet qo‘llanmaydi. Token/origin
himoyasi saqlanadi. Experiment’da ikkala parametr ham berilmasa avvalgi engine
summary asosidagi statistika saqlanadi.

## Estimator va coverage

`t` bin oxirini bildiradi. Yangi analytic/ns-3 flow nuqtalarida aniq `t_start`,
`t_end` bor; VoIP/multicast/HTTP/FTP agregatsiyasi mos chegaralarni saqlaydi.
Yangi analytic natijada qadam `sample_s` orqali saqlanadi va `scenario.sample`ga
mos (default 1 s). Eski analytic natijada `sample_s` yo‘q bo‘lsa qadam hamon 1 s;
uning inputidagi sample qayta talqin qilinmaydi. ns-3 qadam `scenario.sample`.
Run oxiridagi
qisqa bin haqiqiy davomiyligi bilan hisoblanadi. Flow active vaqti bilan so‘ralgan
oyna kesishmasiga **butunlay kirgan** binlar tanlanadi. Boundary binlar taxminiy
bo‘linmaydi. Masalan `[1,2]`, `[2,3]`, `[3,3.5]` uchun `[1,3.5]` tanlovi og‘irliklari
1, 1 va 0.5 s; `[1.2,3.2]` esa faqat `[2,3]`ni olib, 1 s trim ko‘rsatadi.

Formula: `sum(value * bin_duration) / sum(valid_bin_duration)`. Throughput, delay,
jitter, RTT va loss vaqt bilan og‘irlangan bin o‘rtachalaridir. Loss umumiy tx/rx
packet ratio yoki delay umumiy packet-weighted mean deb talqin qilinmaydi: mavjud
schema bu oyna uchun packet hisoblagichlarini saqlamaydi. To‘liq oyna ham asl
engine summary bilan teng chiqishi shart emas.

`run_windows` ichida sample_s/duration, selected start/end, expected/observed bins,
expected_s, trimmed_s, legacy_intervals va error bor. Har metrikada value,
covered_s, samples va complete bor. Null/non-finite qiymat yoki yo‘q bin zero
bilan almashtirilmaydi; partial mean descriptiv qiymat sifatida qoladi. Inactive
flow’da sample yo‘q. HTTP/FTP analytic think-time/idle davrlarida series binlari
bo‘lmasa coverage incomplete bo‘lishi mumkin; avtomatik zero to‘ldirilmaydi.

Har running window mean’i bitta mustaqil namuna; binlar replication hisoblanmaydi.
95% Student t CI barcha replication done, metrika binlari to‘liq va per-flow
selected oynalar bir xil bo‘lgandagina beriladi. missing/incomplete/window_mismatch
CI yo‘qligini tushuntiradi. Tajribadagi complete ijro tugaganini bildiradi, metrika
coverage’ini emas. [experiments.md](experiments.md)dagi statistik shartlar saqlanadi.
Warm-up cutoff avtomatik steady-state detektori emas.

## Eski natijalar va ns-3 tuzatishi

Aniq chegaralar bo‘lmasa engine grid va eski t’dan faqat bitta intervalni aniqlash
mumkin bo‘lganda foydalaniladi; eski millisekund rounding’i hisobga olinadi va
legacy_intervals=true ko‘rsatiladi. Yo‘q bin keyingi nuqtagacha cho‘zilmaydi.
Duplicate, off-grid yoki noaniq interval flow error beradi va CI chiqmaydi.
Eski ns-3 overshoot yoki bir xil timestampga tushgan juda qisqa tail kabi natijalar
uchun yangi run kerak bo‘lishi mumkin. Raw natija o‘zgarmaydi.

Oldingi ceil(duration/sample) stop duration’dan oshishi mumkin edi. Endi stop aynan
duration’da va qisqa tail alohida olinadi. Sampling mark’lari set orqali tanlanadi;
fractional qadamni olti kasrga yumaloqlab grid testi bilan tashlab yuborish yo‘q.
Timing grid float toleransi 1e-8; simulatorning o‘z resolution’i ham amal qiladi.
Firewall bloklangan sintetik flow ham shu interval jadvalini ishlatadi.
scenario.sample son va 0.001..3600 s; duration/sample <= 100000. Bu faqat sample
limitidir, barcha topology/flow kombinatsiyalari RAM’ga sig‘ishi kafolati emas.
Yangi analytic hisob ham `scenario.sample`dan foydalanadi. Kvantlash va
moslik qoidalari: [analytic-sampling.md](analytic-sampling.md).

## Eksport va tekshiruv

Experiment ZIP’da raw natijalar, experiment.json/HTML bilan birga
experiment_statistics.csv (mean/CI/window) va window_coverage.csv (run/flow/metric
coverage va diagnostika) bor. Aniq bin chegaralari results.json’da; eski flows.csv
display timestamp formati saqlanadi. Oyna va estimator schema’si experiment.json’da.

```bash
python -m unittest lab.tests.test_analysis_windows -v
python -m unittest lab.tests.smoke_experiments -v
```

9 yangi test weighted reference, fractional tail, trim, gap/null/nonfinite, legacy
ambiguity, app interval propagation, sampling validation, CI va API HTML/CSV/ZIP
hamda result immutability’ni tekshiradi. Backend regressiya to‘plami 52 test.
Packet suite IPv4/IPv6 uchun 3.5 s duration, 0.4 s sample, 9 bin, oxirgi 0.3 s tail,
selected-window coverage va aynan bir xil replay’ni tekshiradi.

Lokal natija: 52 backend regressiyasi, 19 IPv6/IS-IS/replication model va haqiqiy
ns-3 testi o‘tdi. Asl demo va report smoke suite ham analitik rejimda o‘tdi.
Bu real Mininet/FRR yoki kernel IPv6 validation’i emas.

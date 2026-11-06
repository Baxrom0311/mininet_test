# Juftlangan baseline/candidate tajribalar

`feat/paired-experiment-comparison` branchi `feat/analysis-windows` (`470ca17`)
ustiga qurilgan. Tayyor runlarni o‘zgartirmasdan ikkita tajriba effektini hisoblaydi.
API, standalone HTML/CSV/ZIP va offline CLI qo‘shildi; UI dizayni o‘zgarmagan.

## API

Bir loyihada baseline konfiguratsiya bilan `POST /api/experiments`, keyin candidate
config bilan yana yuboring. Ikkalasida bir xil seed va repetitions bo‘lsin.

```text
POST /api/experiments/compare
POST /api/experiments/compare/report.html
POST /api/experiments/compare/export.zip
```

Uchalasining JSON tanasi bir xil:

```json
{
  "project": "my_lab",
  "baseline_id": "BASELINE_ID",
  "candidate_id": "CANDIDATE_ID",
  "start_s": 5,
  "end_s": 25
}
```

start_s/end_s ixtiyoriy: berilmasa engine summary, berilsa
[analysis-windows.md](analysis-windows.md)dagi duration-weighted estimator.
Token/origin himoyasi va project scope saqlanadi. Unknown experiment 404,
mos kelmagan input/seed/runtime/window 400. Endpoint yangi run yaratmaydi.

## Juftlash va moslik

Juftlar list tartibi bo‘yicha emas, `(seed, rng_run)` orqali topiladi. Replication
count, fixed seed, engine (analytic/ns3), project, IP family, duration, sampling
qadam va runtime/source manifest bir xil bo‘lishi shart. Runtime tekshiruvi
Python/package/switch va Git/source identifikatsiyasini qamraydi. Kod yoki engine
versiyasini almashtirish bu turdagi paired comparison’ga kirmaydi. Bir xil flow ID
uchun type, src/dst va active start/stop ham mos bo‘ladi.

Ikkalasida failures bo‘lsa juft running failure generator seed’i ham mos keladi.
Bir tomonga failures qo‘shish, link/routing/topology/rate kabi condition o‘zgarishlari
diff’da ko‘rsatiladi. Mos seed metadata topology/model o‘zgargach aynan bir xil packet
trayektoriyasi yoki stream assignment bo‘lishini kafolatlamaydi. Config diff nima
o‘zgarganini ko‘rsatadi, sababiy effektni isbotlamaydi. Bu versiya paired Student t
usulini ishlatadi; boshqa seed’li mustaqil kohort uchun boshqa test avtomatik tanlanmaydi.

Run identity’lari takror ishlatilmaydi. Pending/error/cancelled/interrupted/deleted
run bo‘lsa mavjud mos juftlardan descriptiv qiymatlar ko‘rinadi, CI yashiriladi.
Non-finite/missing metric va incomplete window ham o‘sha metrika CI’ini yashiradi.
Failed runlarni chiqarib tashlab yakuniy CI berilmaydi. Faqat bir tomonda mavjud
flow/metric rows missing pairs bilan chiqishi mumkin. complete runlar ijrosi
tugaganini bildiradi, barcha metrics to‘liqligini emas.

## Statistika va birliklar

Har juft uchun `d_i = candidate_i − baseline_i`:

- baseline_mean/candidate_mean aynan delta hisoblangan bir xil juftlar o‘rtachasi;
- delta_mean, delta_stddev, `mean(d) ± t(n−1,.975) × sd(d)/sqrt(n)`;
- relative_change_pct = `100 × delta_mean / baseline_mean`, faqat point estimate;
- pairs/missing_pairs/incomplete_pairs/window_mismatch va ci_excludes_zero.

Baseline nol yoki relative natija non-finite bo‘lsa relative_change_pct null.
Ratio uchun CI yo‘q. Throughput delta bps, delay/jitter/RTT delta ms, loss delta
percentage points: 10% → 12% loss uchun +2 percentage points va relative +20%.
preferred_direction throughput uchun higher, qolganlari lower; bu avtomatik tavsiya
va model aniqligi dalili emas.

CI faqat barcha kutilgan juftlar done, finite, to‘liq/mos coverage bilan beriladi.
Bir juft — bitta namuna; vaqt binlari mustaqil replication hisoblanmaydi. Pairs
orasida mustaqillik va delta sample mean taxminan normal bo‘lishi shartlari bor.
Intervallar per metric, multiple-comparison tuzatishi yo‘q. ci_excludes_zero p-value
yoki butun hisobot bo‘yicha umumiy hukm emas. Nol kenglik model/real tarmoq aniqligini
isbotlamaydi.

## Audit va eksport

design_diff selected scenario/topology o‘zgarishlarini JSON pointer bilan beradi;
seed/substream/failure seed va ishlatilmagan boshqa scenarios chiqariladi. Ko‘pi
bilan 100 diff; katta qiymatda preview/SHA-256 va truncation flag. To‘liq inputlar
ZIP’da saqlanadi. Pointer’dagi ~0 tilde, ~1 slash escape’i.

ZIP: comparison.json, report.html, paired_statistics.csv, paired_samples.csv,
run_map.csv, raw CSV/results.json va har running replay input/manifestlari.
run_map.csv role bilan numeric input folderni bog‘laydi. Replay snapshoti hali yo‘q
pending/error runlarda input_directory bo‘sh va replay_inputs_available `False`
bo‘ladi. samples har juft qiymati, farqi va incomplete sababini ko‘rsatadi.
HTML escape qiladi va tashqi resurs talab qilmaydi.

Har alohida experiment ZIP’idagi results.json massivini offline solishtirish:

```bash
python -m lab.experiment_comparison baseline/results.json candidate/results.json -o effect.json
python -m lab.experiment_comparison baseline/results.json candidate/results.json --start-s 5 --end-s 25 --format html -o effect.html
python -m lab.experiment_comparison baseline/results.json candidate/results.json --format zip -o effect.zip
```

CLI invalid inputda exit 2. Moslik qoidalari archived runlarga ham qo‘llanadi.
Oldingi comparison_key hash formati saqlangan; 470ca17’dagi fixture hash testi bor.

## Tekshiruv

```bash
python -m unittest lab.tests.test_experiment_comparison -v
python -m unittest lab.tests.smoke_experiment_comparison -v
```

11 yangi unit/integration test bilan backend to‘plami 63 test: reference CI, pair
ordering, failed/partial/missing holatlar, runtime/clock/endpoint/failure-seed guard,
percent/zero/overflow va loss units, window, diff/escape, CLI, API export/auth va
archived fingerprint. Packet suite IPv4/IPv6 uchun 2+2 replicationdan bandwidth
conditionlarni solishtiradi (jami 8 ns-3 subprocess). Bu Mininet/FRR benchmarki emas;
uning privileged validation talabi saqlanadi.

Lokal natija: 63 backend testi o‘tdi. Haqiqiy ns-3 IPv4/IPv6 paired bandwidth
sinovi ham o‘tdi: 8 alohida subprocess, ikkala oilada to‘liq juftlar, window coverage
va musbat throughput delta CI tekshirildi. GitHub workflow natijasi bu lokal
tekshiruvlardan alohida; hali remote CI bajarildi deb belgilanmagan.

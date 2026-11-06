# Baseline va variantlar kampaniyasi

`feat/experiment-campaigns` branchi `feat/paired-experiment-comparison` (`2bbdade`)
ustiga qurilgan. Bitta so‘rov original baseline va parametr variantlari uchun
bir xil seed/substream replicationlarini rejalaydi. Mavjud executor ishlaydi;
qo‘shimcha orchestrator thread yoki alohida cheksiz queue yaratilmaydi.

## Preview va start

```text
POST /api/campaigns/preview
POST /api/campaigns
```

Ikkalasining tanasi bir xil:

```json
{
  "project": {"name": "my_lab", "devices": {}, "links": {}, "scenarios": {}},
  "scenario": "normal",
  "engine": "ns3",
  "param": {"kind": "link", "link": "l1", "field": "bw"},
  "values": [1, 2, 5],
  "repetitions": 3,
  "seed": 42,
  "start_s": 5,
  "end_s": 25
}
```

project o‘rniga to‘liq valid loyiha yuboring. Baseline original loyiha; values faqat
variantlarni bildiradi. Natijada baseline/v01/v02/v03 shartlari va 12 run bo‘ladi.
Har condition o‘z experiment_id va run_ids’iga ega. Preview normalized config
farqlarini ham ko‘rsatadi, lekin campaign/run metadata saqlamaydi va ish boshlamaydi;
preview’dagi UUID’lar vaqtinchalik. Start to‘liq tekshiruvni qayta bajaradi va yangi
ID’lar qaytaradi. Caller project obyekti joyida o‘zgarmaydi.

Engine analytic yoki ns3. values 1..8 scalar string/finite number; boolean qabul
qilinmaydi. repetitions 2..25 (default 3), fixed seed default 1, umumiy run <=100.
start_s/end_s ixtiyoriy, [analysis-windows.md](analysis-windows.md) qoidalarida.
Kampaniya normal run/study’lar bilan 256 pending/running limit va NETLAB_RUN_WORKERS
(default 2) executorni baham ko‘radi. Bu RAM sig‘ishi kafolati emas.

Parametr operatori mavjud study operatori bilan bir xil: link (bw/delay/loss/queue/
qdisc), app (rate/size/codec/page/think/file), ap (tx_power/channel), router
(rip_update/rip_timeout, optional all), failure (mtbf_s/mttr_s, optional group).
Failure seed’ini sweep qilish mumkin emas: uni campaign seed va replication index
belgilaydi. Har o‘zgartirilgan variant va har replication ish boshlanishidan oldin
model validatsiyasidan o‘tadi. Kamida bitta variant noto‘g‘ri bo‘lsa hech biri yuborilmaydi.

Baseline avval, keyin values tartibida queue’ga qo‘yiladi. Worker soniga qarab
conditionlar ijrosi ustma-ust kelishi mumkin; bu strict serial completion emas.
Barcha model nusxalari alohida. Mininet bu kampaniyaga kirmaydi.

## Holat va cancel

```text
GET  /api/campaigns/my_lab?limit=100&offset=0
GET  /api/campaigns/my_lab/CAMPAIGN_ID
POST /api/campaigns/my_lab/CAMPAIGN_ID/cancel
```

List manifest metadatasini qaytaradi; submission holati job ijrosining live status’i
emas. Bitta campaign GET’i per-condition counts, rejalangan runlar holati, progress,
missing/invalid run ID va complete beradi. Status polling full project snapshot yoki
series JSON’larini decode qilmaydi. Natijalar katalogini stat qilish avvalgi indeks
qoidasi bo‘yicha O(fayllar soni) qoladi.

Status queued/running/done/failed/cancelled/interrupted/incomplete/submission_error.
complete faqat barcha rejalangan runlar done va submission submitted bo‘lsa true.
Progress terminal error/cancelled/interrupted’ni ham tugagan ish sifatida sanaydi;
progress=1 muvaffaqiyatni bildirmaydi. Per-run authoritative state ham ko‘rsatiladi.
Status yozuvi ham ishlamasa, shu queue instance’i yo‘qotgan pending ishlar
orphaned_run_ids’da chiqadi; local ish qolmasa campaign interrupted ko‘rsatiladi.
Boshqa live instance ishlari shu taxmin bilan interrupted deb belgilanmaydi.

Cancel faqat shu campaign membership’i mos runlarga ta’sir qiladi. Queued runlar
bekor qilinadi, running runlar cooperative token oladi; boshqa campaign/oddiy job
saqlanadi. cancel_requested urinishlar soni, not_cancelled’da boshqa live worker
kabi boshqarib bo‘lmaydigan joblar qaytadi. Allaqachon tugagan campaign cancel’i noop.
Cancelled queued loyiha payload’i workerlar band bo‘lsa ham darhol registry’dan
chiqariladi; executor navbatida faqat kichik run ID qoladi.

## Tranzaksiya va restart

Bitta run, replication batch va campaign uchun umumiy run_submission yo‘li bor.
Barcha queued row’lar va campaign manifest bir SQLite tranzaksiyada saqlanadi.
Commit bo‘lmaguncha worker submit qilinmaydi. Workerlar scheduling gate’da kutadi;
barcha future’lar va submitted marker muvaffaqiyatli yozilmaguncha engine boshlanmaydi.
Scheduler xatosida barcha batch tokenlari to‘xtatiladi, unscheduled/cancelled row’lar
terminal bo‘ladi va campaign submission_error qayd etiladi.

Plan campaigns jadvalida, metadata uchun alohida kichik summary ustuni bilan
runs.sqlite3 ichida saqlanadi. JSON series odatdagi per-run fayllarda. Server
restartida o‘lgan owner’ning pending runlari interrupted bo‘ladi; kampaniya o‘zi
replay/resume qilinmaydi. Bitta results katalogida bitta server; PID reuse cheklovi
oldingi queue recovery kabi qoladi. Disk xatosi cleanup status yozuviga ham to‘sqinlik
qilishi mumkin; shu instance’dagi reja admitted qolib, local future bo‘lmasa interrupted
ko‘rsatiladi. SQLite/JSON orasida ikki faylli tranzaksiya yo‘q.

Kutilmagan pre-result worker xatosi active/future/token/payloadni yetim qoldirmaydi.
Tayyor committed JSON esa callback/index xatosi sabab error bilan almashtirilmaydi.
Reconcile mavjud natijadan metadatani tiklaydi. Kod nusxasi pin qilinmaydi; ish
vaqtida source/runtime o‘zgarsa paired comparison buni aniqlab CI’ni rad etishi mumkin.

## Hisobot va eksport

```text
GET /api/campaigns/my_lab/CAMPAIGN_ID/report.json
GET /api/campaigns/my_lab/CAMPAIGN_ID/report.html
GET /api/campaigns/my_lab/CAMPAIGN_ID/export.zip
```

Report baseline va har variantning summary’si hamda baseline’ga nisbatan paired
farqlarni hisoblaydi. Bitta cohort missing/corrupt/incompatible bo‘lsa uning error’i
ko‘rinadi; boshqa variantlar ham ko‘rib chiqiladi. Missing runlar chiqarib tashlanib
final CI berilmaydi. Membership mismatches foreign runni cancel yoki comparison’ga
qo‘shmaydi. analysis_complete hisoblashlar mavjudligini bildiradi; har metrika
coverage va CI eligibility alohida tekshiriladi.

95% interval har comparison/metric uchun alohida. Multiple-comparison correction,
omnibus test yoki avtomatik winner yo‘q. [experiment-comparison.md](experiment-comparison.md)
qoidalari, fixed seed/runtime/flow identity talablari va time-window semantikasi saqlanadi.

ZIP: plan.json (original project/runtime), campaign.json, report.html, run_status.csv,
effects.csv, comparison_errors.csv, per-comparison HTML va experiments/baseline.zip,
experiments/v01.zip va hokazo. Ichki arxivlar mavjud experiment eksport formatida;
ularning results.json’i offline paired CLI bilan ishlaydi. Butunlay o‘chirilgan
cohort uchun unavailable.txt bor. Plan/condition keys tekshiriladi; buzilgan path
arxiv nomiga o‘tmaydi. HTML qiymatlarni escape qiladi, tashqi resurs kerak emas.
UI dizayni o‘zgarmagan.

## Tekshiruv

```bash
python -m unittest lab.tests.test_campaigns -v
python -m unittest lab.tests.smoke_campaigns -v
```

13 yangi test: preview/preflight/limits, actual analytic effects/API/auth/nested ZIP,
metadata-only restart polling, real child-process interrupted recovery, DB rollback,
scheduler va unexpected-worker errors, committed result preservation, cancel scope,
queued payload weakref release, missing/incompatible data va unsafe condition path.
Persistent status-write va cancel-save failure ham tekshiriladi. Asosiy backend
to‘plami 76 test. Packet suite IPv4/IPv6 uchun baseline+variant,
ikkitadan replication va fractional window bilan jami 8 haqiqiy ns-3 subprocessni
tekshiradi. Real Mininet/FRR resurs benchmarki alohida privileged muhitni talab qiladi.

Lokal tekshiruv: 76 backend regressiyasi o‘tdi. IPv4/IPv6 campaign packet suite
8 haqiqiy ns-3 subprocess bilan o‘tdi; paired bandwidth effekti va window coverage
tasdiqlandi. Asl demo/report smoke suite ham analitik rejimda o‘tdi. Remote CI
natijasi lokal tekshiruvdan alohida; hali GitHub workflow bajarildi deb belgilanmagan.

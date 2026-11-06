# Takroriy tajribalar va qayta bajarish

`feat/experiment-statistics` branchi `docs/architecture` ustiga qurilgan. Backend
API va mustaqil hisobotlar qo‘shildi; UI maydonlarini foydalanuvchi alohida ishlaydi.

## Tajribani boshlash

`POST /api/experiments` tanasi:

```json
{
  "project": {"name": "my_lab", "devices": {}, "links": {}, "scenarios": {}},
  "scenario": "normal",
  "engine": "ns3",
  "repetitions": 10,
  "seed": 42
}
```

`project` o‘rniga valid to‘liq loyihani yuboring. Javobda `experiment_id`, barcha
`run_ids` va `repetitions` keladi. Analytic va ns3 dvigatellari ishlaydi; default
analytic, 10 takror, seed 1. Takrorlar 2..25, seed musbat integer va oxirgi
failure seed bilan birga 2147483647 chegarasiga sig‘ishi shart. Boolean/float
qiymatlar integer sifatida qabul qilinmaydi. Jonli Mininet tarmog‘ini mustaqil
boshlang‘ich holatga qaytarish yo‘q, shuning uchun bu endpoint Mininet’ni rad etadi.

ns-3 tavsiyasiga mos: `scenario.seed` o‘zgarmaydi, `scenario.rng_run` 1..N bo‘ladi;
runner `RngSeedManager.SetSeed` va `SetRun`ni obyektlar yaratilishidan oldin chaqiradi.
MTBF/MTTR `failures.seed` esa `seed + replication_index` bo‘ladi. Kirishdagi shu
maydonlar tajriba uchun almashtiriladi; foydalanuvchi loyiha obyekti o‘zgarmaydi.
Analitik dvigatelda stoxastik failures bo‘lmasa takrorlar bir xil natija berishi tabiiy.

Barcha runlar mavjud chegaralangan executor va SQLite registridan foydalanadi.
Queue’da butun tajriba uchun joy yetmasa, runlar qo‘shilishidan oldin rad etiladi.
I/O yoki jarayon halokati paytidagi yozuvlar yagona tranzaksiya emas; natijada kam
run saqlansa hisobot buni incomplete deb ko‘rsatadi. Server qayta startda
interrupted runlarni o‘z-o‘zidan qayta bajarmaydi. Har run odatdagi cancel
endpointi orqali to‘xtatiladi. Qayta boshlash uchun yangi tajriba yuboriladi.

## Hisobot va statistik ma’no

- `GET /api/experiments/{project}/{experiment_id}` — JSON holat/statistika.
- Shu URL’ga `/report.html` — tashqi resurssiz HTML.
- Shu URL’ga `/export.zip` — barcha natijalar, CSV, JSON statistika va HTML.

Har flow uchun throughput, delay, jitter, loss va RTT ko‘rsatkichlari mavjud
bo‘lsa: namuna soni, yetishmagan namuna soni, o‘rtacha, **sample** standart
og‘ish va ikki tomonlama 95% Student t intervali beriladi:

`mean ± t(df=n−1, 0.975) × sample_stddev / sqrt(n)`.

Bitta running summary qiymati bitta namuna hisoblanadi. Vaqt qatoridagi binlar
mustaqil takror sifatida sanalmaydi; bu hisobot warm-up filtrlashni qo‘shmaydi,
dvigatelning mavjud summary semantikasini saqlaydi. `n < 2` bo‘lsa interval yo‘q.
Tajribaning biror runi pending/error/cancelled/interrupted/deleted bo‘lsa, barcha
intervallar yashiriladi. Muayyan metrika yetishmasa yoki non-finite bo‘lsa, o‘sha
metrikaning intervali berilmaydi. Mavjud yakunlangan runlarning descriptiv mean/SD
qiymatlari baribir ko‘rinadi; bu muvaffaqiyatli runlarni tanlab CI chiqarish emas.

Taqqoslashda topology, scenario (seed/substreamlardan tashqari), engine va
runtime manifest bir xil bo‘lishi tekshiriladi. Mos kelmagan/tampered inputlar
400 bilan rad etiladi. Alohida takror seed/substream juftlari va indekslari
takrorlanmasligi kerak. Protokol va hodisa modeli xatosi bu intervalga kirmaydi.
Nol kenglik deterministik natijani bildiradi, real tarmoq aniqligini isbotlamaydi.
Mustaqillik va sample mean taxminan normal bo‘lishi statistik shartdir; ayniqsa
kichik N yoki kuchli asimmetriyada interval ehtiyotkor talqin qilinadi. Intervallar
har metrika uchun alohida; ko‘p metrikani birgalikda tekshirishga tuzatish yo‘q.

## Eksport tarkibi va replay

Yangi oddiy runlarning mavjud `/api/runs/{project}/{run_id}/export.zip` eksporti
ham qayta bajarish fayllarini oladi. Bitta run uchun ZIP ildizida, ko‘p run uchun
`runs/01/`, `runs/02/`, ... ichida:

| Fayl | Mazmun |
|---|---|
| `project.json` | Shu run ishlatgan loyiha va asl tanlangan ssenariy nusxasi |
| `run-request.json` | `POST /api/runs` uchun tayyor project/scenario/engine tanasi |
| `effective-input.json` | failures/apps expand’dan keyingi aniq hodisalar va transport oqimlari |
| `manifest.json` | Schema, SHA-256, Python/package versiyalar, engine switch, Git commit/diff holati va lab Python source hash |

Experiment ZIP’da qo‘shimcha `experiment.json` va `report.html` bor. Mavjud
`results.json` va CSV formatlari saqlangan. Eski runlar topology snapshotga ega
bo‘lmagani uchun ularga soxta manifest yaratilmaydi.

Mos source/runtime o‘rnatilgan serverda eksportdagi `run-request.json`ni
`POST /api/runs`ga yuboring (token bo‘lsa odatdagi autentifikatsiya bilan).
`manifest.runtime.switches.NETLAB_NS3_FAST` qiymatini ham moslang; u process
environmentidan keladi va loyiha JSON’ining bir qismi emas. To‘g‘ridan-to‘g‘ri
ns-3 forwarding replay uchun `python -m lab.engines.ns3_runner effective-input.json out.json`
ishlaydi; bu yo‘l API’dagi apps/failures postprocess xulosalarini bajarmaydi.

Manifest butun OS/container yoki FRR/OVS binarlarini arxivlamaydi. Bir xil Git
commitning o‘zi yetarli emas: dirty diff hash, `lab_python_sha256`, paketlar va
switchlarni ham tekshiring. Source hash lab’dagi Python fayllarini, shu jumladan
untracked modullarni qamraydi; node_modules/results/.venv/generated UI chiqariladi.
Git diff hash faqat tracked o‘zgarishlar uchun; diff matni yoki kod ZIP’ga qo‘shilmaydi.
Input hash tasodifiy o‘zgartirishni aniqlaydi, kriptografik imzo emas. Manifest
process environmentini, token yoki Git remote credentiallarini yig‘maydi. Loyiha
nusxasi esa foydalanuvchi bergan konfiguratsiyani saqlaydi; eksportni ulashishda
shu loyiha tarkibini hisobga oling.

## Tekshiruv

```bash
python -m unittest lab.tests.test_experiments lab.tests.test_run_queue -v
python -m unittest lab.tests.smoke_experiments -v
```

Birinchi to‘plam statistik reference, non-finite/overflow, incomplete/xato
namunalar, input isolation, turli MTBF/MTTR hodisalari, SQLite metadata, API eksport
va analitik replay’ni tekshiradi. Ikkinchisi haqiqiy queued ns-3 jarayonlarida
IPv4/IPv6 uchun ikkitadan takror va bittadan aynan bir xil packet-metric replay
qiladi (jami 6 jarayon). Bu real Mininet/FRR testi emas.

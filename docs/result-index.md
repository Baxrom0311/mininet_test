# Katta natijalar tarixi: indeks va sahifalash

`perf/run-result-index` branchi `feat/experiment-statistics` (`521fb5b`) ustiga qurilgan.
Oldingi server har `GET /api/runs/{project}` so‘rovida shu loyihadagi barcha JSON
natijalarini, shu jumladan flow/link/node vaqt qatorlarini to‘liq o‘qirdi. Endi
ro‘yxat uchun SQLite’da kerakli metadata va fayl signaturalari saqlanadi.

## API

Parametrsiz so‘rov avvalgi `{"runs": [...]}` formatini saqlaydi. Barcha natijalarni
qaytaradi, ammo o‘zgarmagan JSON series’larni qayta o‘qimaydi.

```text
GET /api/runs/my_lab?limit=20&offset=0
GET /api/runs/my_lab?limit=20&status=done&engine=ns3
GET /api/runs/my_lab?experiment_id=EXPERIMENT_ID
GET /api/runs/my_lab?study_id=STUDY_ID
```

Parametr berilganda javobda `runs`, filtrdan keyingi `total`, `limit` va `offset`
bo‘ladi. `limit` 1..200, default 100; `offset` manfiy bo‘lmagan integer. Filtrlar
birgalikda AND bilan ishlaydi. `status`: queued/running/cancelling/done/error/
cancelled/interrupted/unknown; `engine`: analytic/ns3/mininet. Study/experiment
ID’lari run ID qoidalariga mos bo‘lishi kerak. Noto‘g‘ri diapazon yoki filtr 400,
FastAPI integerga aylantira olmaydigan query qiymati 422 beradi.

Tartib: `created DESC, run_id DESC`; bir xil vaqtli natijalar ham barqaror
joylashadi. Bitta javobning count va page qiymatlari bitta SQLite read snapshotdan
olinadi. Alohida so‘rovlar orasida yangi runlar qo‘shilsa, offset sahifalari siljishi
mumkin; bu cursor yoki tarixni muzlatib qo‘yish mexanizmi emas.

`status` filtri SQLite’dagi haqiqiy ijro holatiga qo‘llanadi. Mavjud UI polling’ini
saqlash uchun local active run javobda `status=running` va `queue_state=queued|running|cancelling`
ko‘rinishida beriladi. Live progress active snapshotdan olinadi. Terminal runlarda
status to‘g‘ridan-to‘g‘ri qaytariladi. Ro‘yxatda series yoki topology snapshot yo‘q.

Experiment va study reportlari ham metadata filtridan foydalanadi: to‘liq JSON
faqat tanlangan tajribaga tegishli runlar uchun ochiladi. Yangi UI dizayni qo‘shilmadi.

## Fayllar va registr uyg‘unligi

Yangi run yozilganda JSON avval atomik almashtiriladi; uning metadata va
`(mtime_ns, size)` signaturasi indeksga yoziladi. Shu serverdagi reconcile, natija
yozish va o‘chirish umumiy index lock bilan muvofiqlashadi. Pending run nomini
o‘zgartirish qisman result JSON yaratmaydi, va yangi nom ijro tugagach saqlanadi.

Ro‘yxat so‘rovi katalogni ko‘rib/stat qilib chiqadi. Eski yoki tashqaridan qo‘shilgan
JSON bir marta o‘qilib indekslanadi. Signatura o‘zgarsa metadata yangilanadi;
oldin indekslangan fayl o‘chirilsa registr yozuvi ham olib tashlanadi. Registry’da
bor, hali JSON’i yo‘q queued/interrupted yozuvlar saqlanadi. Corrupt JSON, noto‘g‘ri
metadata yoki filename bilan mos kelmagan run_id ro‘yxatga kiritilmaydi; mavjud
eskirgan indeks yozuvi chiqariladi. Bir xil legacy ID turli loyihalarda alohida
saqlanadi: registr kaliti endi `(project, run_id)`.

Tashqi dastur fayl hajmi va mtime’ini aynan saqlab o‘zgartirsa, avtomatik tekshiruv
buni aniqlay olmaydi. Majburiy yangilash:

```text
POST /api/runs/my_lab/reindex
```

Javob: `scanned`, `updated`, `removed`, `invalid` sonlari. Endpoint mavjud
token/origin himoyasidan foydalanadi. `invalid` joriy tekshiruvda topilgan buzilgan
fayllarni, shu jumladan signaturasi o‘zgarmagan avvalgi xatolarni ham sanaydi.
Majburiy reindex buzilgan fayllarning keshini ham chetlab o‘tib, qayta parse qiladi.
GET result/export asl JSON ma’lumotini ishlatadi.

### Buzilgan natijalar diagnostikasi

```text
GET /api/results/my_lab/invalid?limit=100&offset=0
```

Javob `total`, `limit`, `offset`, `files` maydonlaridan iborat. Har bir faylda
`run_id`, `mtime_ns`, `size` (bayt) va ko‘pi bilan 500 belgili `error` bor.
`files` run ID bo‘yicha tartiblanadi; count va sahifa bitta read snapshotdan olinadi.
`limit` 1..1000 (default 100), `offset` 0..1000000 (default 0). Diapazon xatosi
400, integerga aylantirib bo‘lmaydigan query 422 beradi. Mavjud token himoyasi
amal qiladi. Alohida `/api/results` prefiksi run ID’si `invalid` bo‘lgan eski
natijani olishga xalaqit bermaydi.

Deterministik JSON/metadata xatosi SQLite’dagi alohida `invalid_results` jadvalida
`(project, run_id)` va `(mtime_ns, size)` bilan saqlanadi. Ro‘yxat va diagnostika
so‘rovlari o‘zgarmagan buzilgan JSON’ni, server qayta ishga tushganda ham, qayta
o‘qimaydi. Fayl signaturasi o‘zgarsa qayta tekshiriladi; tuzatilgan natija
indeksga qaytganda diagnostika tranzaksiya ichida tozalanadi. O‘chirilgan faylning
diagnostikasi ham keyingi reconcile’da yo‘qoladi. Fayllarning o‘zi ko‘chirilmaydi
yoki o‘chirilmaydi. Queued/running/cancelling worker metadata’si tashqaridan
qo‘yilgan buzilgan fayl sabab olib tashlanmaydi.
Bu fayl keyinchalik o‘chirilsa ham faqat diagnostika tozalanadi, faol worker
metadata’si saqlanadi.

Vaqtinchalik permission/read xatolari signatura keshiga kiritilmaydi: ruxsat
tuzatilishi fayl hajmi yoki mtime’ini o‘zgartirmasligi mumkin. Ular reindex
hisobotining `invalid` soniga kiradi, ammo diagnostika sahifasi faqat parse yoki
metadata tekshiruvidan o‘tmagan fayllarning oxirgi qaydini ko‘rsatadi. Indeksga
yozish xatosi buzilgan JSON sifatida yashirilmaydi; so‘rovga xato qaytariladi.

Bu optimallashtirishda katalog/stat ishi hanuz **O(natijalar soni)**. Sahifalash
response va SQL metadata o‘qishini chegaralaydi; million faylli katalog uchun O(1)
yechim emas. Birinchi katta legacy import index lockni uzoqroq ushlab turishi
mumkin. To‘liq report/ZIP yoki bitta result olish o‘sha resultning series’ini o‘qiydi.

## Eski bazani yangilash

Eski olti ustunli `runs` jadvali birinchi ochilishda tranzaksiya ichida yangi
schema’ga ko‘chadi. Metadata, owner va vaqtlar saqlanadi; migratsiya xatosida jadval
almashtirilmaydi. SQLite backup API orqali birinchi yangilashdan oldin
`runs.sqlite3.pre-index.bak` yaratiladi; u WAL’dagi committed ma’lumotni ham oladi.
Mavjud backup ustiga yozilmaydi. JSON natija fayllari bu backup ichiga kirmaydi.

Eski kod yangi composite-key schema’ga yozishga mos emas. Downgrade kerak bo‘lsa,
serverni to‘xtatib mos backup/separate results katalogidan foydalaning va yangi
natijalarni ham saqlang. Bitta results katalogida bitta server ishlatish talabi va
PID reuse recovery cheklovi saqlanadi. Disk xatosida JSON/SQLite orasida to‘liq
ikki faylli tranzaksiya yo‘q; keyingi reconcile mavjud JSON’ni qayta indekslaydi.

## O‘lchov va regressiyalar

```bash
python -m lab.benchmarks.result_index --runs 100 --points 10000 --output /tmp/result-index.json
python -m unittest lab.tests.test_result_index -v
```

Shu cloud instance’dagi **sun’iy natijalar tarixi**: 100 ta JSON, har birida 10 000
nuqta, jami 64 904 890 bayt. Bu Mininet yoki ns-3 tarmoq unumdorligi o‘lchovi emas.
Bir xil fayllar bilan eski va yangi ro‘yxat kodlari shu processda solishtirildi:

| Amal | Vaqt | To‘liq JSON o‘qish |
|---|---:|---:|
| Dastlabki indekslash | 773.7 ms | 100 |
| Eski ro‘yxat, median 3 run | 503.2 ms | har so‘rovda 100 |
| Indekslangan ro‘yxat, median 3 run | 1.67 ms | 0 |
| Indekslangan 20 qatorli page, median 3 run | 1.46 ms | 0 |

Shu fixture’da warm list taxminan 302 marta tezroq bo‘ldi. OS cache, disk, fayl soni
va metadata hajmi natijaga ta’sir qiladi; bu boshqa history yoki host uchun kafolat
emas. Xom o‘lchov: [validation/result-index-benchmark.json](validation/result-index-benchmark.json).

10 ta yangi test: katta JSON’ni warm/restart polling’da qayta o‘qimaslik, pagination
va actual-phase filtrlar, legacy migration/rollback/backup, tashqi edit/delete/
corruption, majburiy reindex, concurrent saves, label persistence, project izolyatsiyasi
va faqat tanlangan experiment JSON’larini ochish. Asosiy backend to‘plami jami
43 testga yetdi. Asl demo va report smoke suite ham analitik rejimda tekshirildi.
Yangi indeks bilan haqiqiy ns-3 replication/replay testi ham o‘tdi: IPv4 va IPv6
uchun ikkitadan takror va bittadan aynan bir xil packet-metric replay (jami 6 jarayon).

# Real emulyatsiya worker lifecycle’i

`Mininet` dvigatelidagi vaqtli launch, link hodisalari va stdout readerlari
`EmulationWorkers` orqali bitta running resurslari sifatida boshqariladi.
Bu Mininet/FRR konfiguratsiyasini yoki oqim hisoblash formulasini o‘zgartirmaydi.

Oldin link update xatosi faqat logga yozilgan: tarmoqdagi nosozlik qo‘llanmagan
bo‘lsa ham run `done` deb saqlanishi mumkin edi. Endi hodisa, mobility refresh,
launch yoki parser istisnosi run xatosiga aylanadi. Manager uni mavjud `error`
natijasi orqali saqlaydi; cancellation holati queue qoidalari bilan boshqariladi.

- Kelajakdagi callbacklar stop event bilan bekor qilinadi.
- Popen va uning tracking ro‘yxatiga qo‘shilishi bir qulf ostida. Shutdown o‘sha
  ikkisi orasida processni yo‘qotib qo‘ymaydi; yopilgach spawn rad etiladi.
- Allaqachon boshlangan callback kechroq spawn’ga kelsa, yangi child yaratilmaydi.
- Child’lar avval terminate qilinadi, umumiy kutish budjetidan keyin qolganlari
  kill qilinadi va yana wait bajariladi. Scheduled callback va readerlar join
  qilinadi, so‘ng stdout pipe’lar yopiladi.
- Natija series’i reader va EOF callbacklari tugagach yig‘iladi; oxirgi buffered
  metric natijani yig‘ish bilan parallel qolmaydi.
- Asosiy callback/cancel istisnosi cleanup xatosi bilan almashtirilmaydi;
  ikkilamchi xato exception note’da, manager saqlagan run JSON’ida esa
  `error_notes` maydonida qoladi.

Python driver callback’ini tashqaridan majburan to‘xtatib bo‘lmaydi. Join budjetida
qaytmagan callback aniqlansa cleanup xatosi qaytadi; run muvaffaqiyatli deb
belgilanmaydi. Bunday holatda real host va callback’ni tekshirish kerak.
Bu mexanizm har qanday bloklangan native command’ni kafolatli bekor qiladi
degan da’vo yo‘q.

Link hodisasida `t` so‘ralgan vaqt sifatida saqlanadi; yangi `actual_t` esa driver
chaqiruvi tugagandagi elapsed vaqtni ko‘rsatadi. Host scheduling va command
kechikishi bor, shuning uchun ular farq qilishi mumkin. Mavjud convergence
hisoblash qoidasi o‘zgartirilmagan. `move` hodisalari hali har soniyada qo‘llanadi.

Bu o‘zgarish har bir ilovaning nonzero process exit kodini avtomatik umumiy run
xatosi deb hisoblamaydi: masalan ping loss yoki failure ssenariysidagi uzilgan TCP
bunday talqinni talab qilmaydi. Ushbu ish boshqaruv/worker istisnolari va resurs
egalik davriga tegishli. Mavjud soniyalik bin va fractional timing cheklovlari
ham alohida ish bo‘lib qoladi.

```bash
python -m unittest lab.tests.test_emulation_workers -v
```

Yangi testlar sintetik driver/proc counterlar va haqiqiy lokal Python child’lardan
foydalanadi: signal, wait/kill, reader drain, spawn/close poygasi va hodisa/mobility
failure. Root/Mininet/FRR ishga tushirilmaydi. Haqiqiy acceptance Claude real
hostda `real_integration` (`emu`, `conv`, `failures`, `fw_emu`) orqali bajaradi;
shu testlarning assertionlari o‘zgartirilmagan.

# Audit topilmalari qayta tekshirildi

Base: `feat/netlab-ipv6-scale-review` (`300f7e4`), undan oldin yangi `main`
(`7c6e317`). Topilmalar shu kodda ham mavjud edi; oldingi hisobotdagi da’volar
avtomatik yangi bazaga ko‘chirilmadi.

## Tuzatishlar

- CLI localhost uchun ham token yaratadi. HTTP mutations va ikkala WebSocket
  bir xil origin tekshiruvini ishlatadi. Tokenli embedding uchun ham origin
  tekshiruvi saqlanadi. Origin’ni yubormaydigan non-browser client token bilan ishlaydi.
- UUID’li run/study ID, xavfsiz active snapshot va expansion xatolarini saqlash.
  Study va oddiy run bir vaqtda Mininet emulyatsiyasini bajara olmaydi.
- Root simulyator xatoni cleanup’dan keyin qayta ko‘taradi, bekor qilish nonzero
  exit qaytaradi. Dataset qurish xatosi ham muvaffaqiyat deb ko‘rsatilmaydi.
- DATA_DIR env’dan barcha modullarga import vaqtida bir xil olinadi; campaign
  `--data-dir` qiymatini bolaga uzatadi.
- Root `--routing` aniq nazariy deb belgilanadi; L2 forwarding va PacketIn
  ma’nosi README/metadata/CSV’da ajratildi. Haqiqiy FRR routing qo‘shildi degan
  da’vo yo‘q. Model va real o‘lchovni aralashtirmang.
- Studio per-run katalog, exit marker va process group ishlatadi. Eski global
  metadata muvaffaqiyat mezoni emas; timeout error; Stop signal yuboradi.
  Windows → WSL buyruq quote’i markazlashtirildi. SSH noma’lum host kalitini
  rad etadi; import arxivi datasets ichidagi oddiy fayllar bilan cheklangan.
- ML artefaktlarida model prefiksi bor; sweep targetlar ro‘yxati, haqiqiy ijro
  oynasi va run chegarasi label hisobiga kiradi. Hujum buyruqlari timeout ostida
  tugashi kutiladi; bu testlarda hech qanday hujum trafiki yuborilmadi.
- UI live request xatosidan keyin authoritative devices/linksni qayta oladi;
  aloqa yo‘q bo‘lsa oldingi holatga qaytadi. Local scenario/code tahrirlari saqlanadi.

## Validation

`python -m unittest lab.tests.test_runtime_safety ml.test_labeling -v`: **14 test o‘tdi**.
origin HTTP/WS, localhost token, 10 parallel start/10 result, expansion error,
startup error, custom DATA_DIR, Studio stale marker/nonzero exit/Stop va label
matching va generated remote shell exit marker tekshiruvlari. Asl demo smoke
to‘plami ham qayta o‘tdi. UI bundle’langan store ustida rad etilgan device/config
so‘rovlari va disconnected rollback sinovi o‘tdi. Typecheck/build o‘tdi, committed
`lab/web-react` bundle yangilandi.

Haqiqiy Windows SSH serverga ulanish, masofaviy signal va Mininet tarmog‘i bu
muhitda sinovdan o‘tmadi. Studio lifecycle testlari transport stubi bilan;
generated remote shell protokoli tekshiriladi. ML to‘liq treningi bajarilmadi.

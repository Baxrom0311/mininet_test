# Emulyatsiya worker regressiyasi — 2026-11-06

Branch: `fix/emulation-worker-lifecycle`, baza `main` `62cf721` (PR #22–#23 bilan).

- **141 backend testi passed**, 14.83 s: mavjud 128 + 13 yangi lifecycle testi.
- Sintetik drayverda to‘liq `emulation.run` link-update va mobility xatolarini
  qaytaradi; ular log ichida yashirilib, muvaffaqiyatli natija bo‘lib qolmaydi.
- Deterministik thread rendezvous orqali ikkita race tekshirildi: close Popen
  bilan registration orasida child’ni yo‘qotmaydi; boshlangan callback close’dan
  keyin yangi child yarata olmaydi.
- Haqiqiy lokal Python child’larida final stdout drain/EOF, SIGTERM’dan keyin
  SIGKILL fallback, wait va pipe close tekshirildi.
- Asosiy istisno cleanup xatosi bilan almashtirilmaydi; ikkilamchi note manager
  saqlagan error run JSON’iga `error_notes` sifatida chiqishi tekshirildi.
- O‘zgartirilgan eski queue testida faqat private `_run` stub argumentlari yangi
  resource owner’ga moslandi; terminate/wait/stop assertionlari saqlandi.
- Python sintaksisi, workflow YAML parse va `git diff --check` passed.

```bash
python -m unittest lab.tests.test_emulation_workers -v
```

Bu natijalar cloud’dagi sintetik driver va lokal subprocess testlaridir. Ular
foydalanuvchining 64 GB RAM/i9 14-avlod/RTX 3050 hostidagi resurs o‘lchovi emas.
Yangi real Mininet/FRR acceptance bajarilmadi; Claude `real_integration` orqali
`emu`, `conv`, `failures`, `fw_emu` holatlarini qayta tekshirishi kerak.
Remote CI ushbu sessiyada ishga tushirilmagan. [Lifecycle qoidalari](../emulation-lifecycle.md).

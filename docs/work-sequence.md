# Branchlar va ish ketma-ketligi

Foydalanuvchi PR va merge’ni o‘zi bajaradi. Agent main’ga push qilmaydi va environment
publish qilmaydi. Keyingi branchlar oldingi branch ustiga quriladi; PR base’ini shunga
mos tanlang yoki oldingi PR merge qilingach yangi main ustiga rebase qiling.

1. `feat/netlab-ipv6-scale-review` — `300f7e4`, `67ba546`: IPv6 model/ns-3, katta real FRR
   benchmark vositasi, address-family kodini ajratish. Push qilindi. Real FRR
   o‘lchovi root/Mininet/FRR yo‘qligi sabab bloklangan; model va ns-3 testlari o‘tdi.
2. `fix/review-runtime-safety` — audit topilmalari: origin/auth, run identity,
   xato exit kodi, Studio lifecycle, ML artefakt/label, DATA_DIR va UI sync.
   `1563fd6`: 14 regressiya testi, demo suite va React typecheck/build o‘tdi; push qilindi.
3. `feat/isis` — model/FRR konfiguratsiyasi, namuna va smoke/real testlar.
   6 test (shu jumladan IPv4/IPv6 ns-3) o‘tdi; real test root/isisd sabab blocked.
4. `feat/calibration` — real o‘lchov importi va model/real jadvali; yangi real
   sonlarni root muhitsiz taxmin qilib kiritmaslik.
5. `feat/traffic-matrix` — CSV va gravity traffic, API, 1000 oqimli tekshiruv.
6. `feat/run-queue` — SQLite run metadata, chegaralangan ijro, cancel, restart recovery.
7. `feat/p4-bmv2` — mavjud P4 real testini qat’iy prerequisite/skip holati bilan
   tayyorlash. O‘rnatish va real validation root/BMv2/p4c yo‘qligiga bog‘liq.
8. `ci/real-mininet` — alohida qo‘lda/tungi real job; CI ishlagan deb faqat GitHub
   natijasi mavjud bo‘lsa aytish.
9. `docs/architecture` — dvigatellar, kesh, expand/postprocess, API va flock qoidalari.
10. UI’ning yangi feature maydonlari — yuborilgan reja bo‘yicha foydalanuvchi ishlaydi.
    Agent mavjud UI’dagi tasdiqlangan sync xatosidan tashqari dizaynni o‘zgartirmaydi.

Asl audit `233054b`da bajarilgan. Checkout endi PR #3 bilan kelgan `7c6e317` ustida;
topilmalar shu bazada qayta tekshirilmoqda. Oldingi tashqi hisobotni yangi main’ning
test natijasi sifatida talqin qilmang.

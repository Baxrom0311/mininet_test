# Ssenariy muharriri

Chap paneldagi ssenariyni oching. Yuqoridagi **Sozlamalar**, **Ilovalar**,
**Nosozliklar**, **Hodisalar** va **Bajarish** tugmalari tegishli bo‘limga olib boradi.
Tor oynada formalar bitta ustunga moslashadi; bo‘limlar navigatsiyasini yon tomonga
surish mumkin. Oynani sarlavhadagi tugma bilan kattalashtirish/qaytarish mumkin.

## Trafik

**Ilova qo‘shish** kamida ikkita host/server/router bo‘lganda ishlaydi. Har bir
ilova alohida kartada: kartani ochib tahrirlash, nusxalash yoki o‘chirish mumkin.
ID noyob bo‘lishi kerak. Nusxa yangi ID oladi. Loyiha undo/redo tarixi bu amallarni
ham saqlaydi.

| Tur | Tahrirlanadigan maxsus maydonlar |
| --- | --- |
| UDP | tezlik, paket hajmi |
| TCP | ixtiyoriy tezlik chegarasi, paket hajmi |
| Ping | paket hajmi, ping oralig‘i |
| VoIP | G.711/G.729 kodeki |
| HTTP | sahifa hajmi (`100KB`, `1MB`), so‘rov oralig‘i |
| FTP | fayl hajmi, server → mijoz yoki mijoz → server yo‘nalishi |
| Multicast | IPv4 guruhi, host/server qabul qiluvchilari, tezlik, paket hajmi |

Barcha turlarda manba/qabul qiluvchi, boshlanish/tugash, nom va DSCP (0–63) bor.
HTTP/FTP uchun manba — mijoz, qabul qiluvchi — server. Multicast manbasini
o‘zgartirish uni qabul qiluvchilar ro‘yxatidan chiqaradi. Guruh manzili multicast
diapazonida bo‘lishi, routerlarda esa PIM sozlangan bo‘lishi kerak. IPv6 multicast
hozircha backend tomonidan qo‘llanmaydi; tekshiruv uni ishga tushirishga yo‘l bermaydi.

Ilova turini almashtirish avvalgi turga oid maxsus maydonlarni olib tashlaydi va
yangi tur uchun standart qiymatlar qo‘yadi. ID, vaqt, nom, DSCP va boshqa umumiy
maydonlar saqlanadi. Import qilingan loyihalarda multicast `dst` massivlari va
legacy qabul qiluvchi yozuvlari o‘qiladi. Qurilma nomini o‘zgartirish multicast
qabul qiluvchilari, harakat hodisalari va aniq tanlangan qurilma nosozliklariga
ham qo‘llanadi.

## Vaqt va nosozliklar

- Davomiylik: 1–3600 soniya; namuna oralig‘i: 0.001–3600 soniya. Backend har bir
  ssenariyda 100 mingdan ko‘p namuna oralig‘iga yo‘l qo‘ymaydi.
- IP versiyasi: IPv4 yoki IPv6. Kerakli manzillar qurilma sozlamalarida bo‘lishi kerak.
- Seed, RNG run va ns-3 tez rejimi alohida ochiladigan bo‘limda. Tez rejimdagi
  MTU 9000 paket metrikalariga ta’sir qiladi; Wi-Fi bo‘lsa qo‘llanmaydi.
- **Nosozlik va tiklanish** modeli kabel va qurilmalar uchun alohida MTBF/MTTR,
  seed, eksponensial yoki qat’iy taqsimot bilan sozlanadi. Nishonlarni alohida yoki
  `all` bilan tanlash mumkin. Qurilmalarda `all` host/serverlarni kiritmaydi; aniq
  tanlovda esa ular ham mavjud. Oxirgi guruh o‘chirilsa model ham o‘chiriladi.
- Vaqtli hodisalar kabel uzilishi/tiklanishi, tezlik/kechikish/yo‘qotish/navbat
  o‘zgarishi va qurilma harakatini qo‘llaydi. Harakat manzilini kanvasdan tanlash mumkin.

## Tekshirish va bajarish

**Tekshirish** `/api/scenario/preflight` orqali xatolar, dvigatel mavjudligi va
mosligi, yoyilgan oqimlar/hodisalar sonini ko‘rsatadi. Bu tugma ishni navbatga
qo‘ymaydi. Ssenariy, loyiha yoki dvigatel o‘zgarsa oldingi tekshiruv natijasi
tozalanadi. Noto‘g‘ri son yoki bo‘sh majburiy maydonga brauzer fokus beradi;
maydon ochiladigan panel ichida bo‘lsa panel ham ochiladi.

**Ishga tushirish** avval tekshiradi, keyin ishni yuboradi. Takroriy bosish ayni
yuborishni ko‘paytirmaydi. Navbat holati REST orqali ham kuzatiladi: ish WebSocket
xabari kelishidan oldin tugagan bo‘lsa ham oyna osilib qolmaydi. Holatni o‘qish
xatosi, qayta urinish, jarayon va bekor qilish tugmasi ko‘rinadi. Natija tayyor
bo‘lsa natijalar oynasi ochiladi; xatoli/bekor qilingan ish holati shu yerda qoladi.

Parametrik tadqiqotda yangi ilova maydonlari (kodek, sahifa, so‘rov oralig‘i, fayl,
multicast tezligi/hajmi), nosozlik MTBF/MTTR/seed va mos dvigatellarda RIP taymerlari
ham tanlanadi. Bir yoki ikki parametr, ko‘pi bilan 49 kombinatsiya. `g711,g729`,
`100KB,1MB` kabi matnlar saqlanadi; manfiy va eksponent sonlar o‘qiladi. Bo‘sh
elementlar (`1,,2`) qabul qilinmaydi. Ikki o‘qda ayni parametr tanlanmaydi.
Variantlar serverda ish boshlanishidan oldin tekshiriladi.

Muvaffaqiyatsiz variant butun tadqiqotga “tayyor” deb ko‘rsatilmaydi. Uning xatosi
va natijani ochish tugmasi qoladi. Yangi ish oldingisining yakunlangan holatini
meros olmaydi. Loyiha nomi o‘zgarsa, eski loyiha ishi yangi loyiha oynasiga chiqmaydi;
avval yuborilgan ish esa backendda eski loyiha ostida qoladi.

## Tekshirish buyruqlari

```bash
npm --prefix lab/ui test
npm --prefix lab/ui run build
```

Brauzer testi uchun birinchi terminalda, repo ildizidan:

```bash
python -m lab.ui.tests.scenario_server
```

Bu demo backendni `127.0.0.1:8766` da, vaqtinchalik loyiha va natijalar katalogi
bilan ochadi. Foydalanuvchining saqlangan ma’lumotlariga tegmaydi. Test fixture’da
ushlab turiladigan ish va ataylab yiqiladigan variant bor; qolgan ishlar haqiqiy
analitik dvigatelda bajariladi. Playwright va brauzer alohida o‘rnatilgan bo‘lishi kerak.

```bash
NETLAB_PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
NETLAB_BROWSER=/absolute/path/to/chrome \
npm --prefix lab/ui run test:scenario
```

`NETLAB_SCENARIO_PORT` (server), `NETLAB_SCENARIO_URL` (test),
`NETLAB_SCENARIO_FIXTURE` (ikkalasi) va `NETLAB_UI_ARTIFACTS` (skrinshotlar) ixtiyoriy.
Standart skrinshot katalogi `/tmp/netlab-scenario-browser`. Toza takrorlash uchun
fixture serverini qayta ishga tushiring. Yig‘ilgan `lab/web-react` ham commit qilinadi.

[O‘lchangan tekshiruvlar va skrinshotlar](validation/ui-scenario-editor-2026-11-08.md).

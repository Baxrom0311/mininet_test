# Natijalar oynasi

React interfeysida **Natijalar va solishtirish** tugmasini bosing. Bu oyna analitik,
ns-3 va Mininet natijalarini bir xil API orqali o‘qiydi.

- Tarix serverdan 50 tadan olinadi. Dvigatel va holat filtrlari barcha natijalarga,
  matnli qidiruv esa joriy sahifaga qo‘llanadi. Belgilash kataklari bilan 20 tagacha
  natijani solishtirish mumkin; nomini bosish faqat o‘sha natijani tanlaydi.
- **Grafiklar**: throughput, delay, RTT, jitter, loss va kabel yuklamasi. Oqim va
  kabelni tanlash mumkin. Grafikda ko‘pi bilan oltita chiziq bor; ko‘proq natija
  tanlansa, cheklov oynada yoziladi. Xulosa va eksport barcha tanlangan natijalarni oladi.
- **Ilovalar**: VoIP MOS, HTTP sahifa yuklanishi, FTP uzatish natijasi va multicast
  qabul qiluvchilari. Ushbu ilova modellari bo‘lmagan eski natijada bo‘sh holat chiqadi.
- **Tarmoq sifati**: mavjudlik, uzilishlar, konvergensiya, kabel navbatlari,
  tashlangan paketlar va qurilma ko‘rsatkichlari.
- **Hodisalar**: ssenariy amallari va marshrut qo‘shilishi/olib tashlanishi.
- HTML hisobotlar, bitta natijaning to‘liq ZIP arxivi va xulosaning CSV eksporti bor.
  CSV UTF-8 BOM bilan yoziladi; vergul, qo‘shtirnoq va yangi qatorlar saqlanadi.
- 1D tadqiqotda parametr qiymatlari kategoriya sifatida ko‘rsatiladi; 2D da issiqlik
  xaritasi chiqadi. Xatoli variantlar holat jadvalida qoladi, grafikda ularning
  o‘rniga qiymat to‘qib chiqarilmaydi. Barcha variantlar yakunlanishi bilan ularning
  barchasi muvaffaqiyatli tugashi alohida ko‘rsatiladi.
- Faol ishni bekor qilish mumkin. Tayyor natijani o‘chirishdan oldin tasdiq so‘raladi.
  **O‘qilmagan natijalar** buzilgan arxivlarni ko‘rsatadi; **Qayta tekshirish** indeksni
  yangilaydi. Bu amal arxivlarni o‘chirmaydi.

O‘lchanmagan qiymat `—` bilan, haqiqiy nol `0` bilan chiqadi. Mavjudlik faqat uni
hisoblagan natijalarda mavjud; kabel navbati va paket tashlash kabi maydonlar
dvigatelga bog‘liq. UI mavjud hisob-kitoblarni namoyish qiladi.

Oynani kattalashtirish/qaytarish, sarlavhasidan sudrash va burchagidan o‘lchash
mumkin. Ekran o‘zgarsa boshqaruvlar uning ichida qoladi. Tor oynada tarix paneli
ochib-yopiladi. Bo‘limlar klaviaturaning chap/o‘ng, Home va End tugmalari bilan almashadi.

## Yuklash va katta grafiklar

Tarix faol ishlar borida 3 soniyada, qolgan holatda 15 soniyada yangilanadi.
Natija tafsilotlari ko‘pi bilan uchta parallel so‘rovda olinadi. Tanlov yoki loyiha
almashganda eski so‘rovlar bekor qilinadi. Xatolar va qayta urinish tugmasi ko‘rinadi.

Har bir chiziqning SVG geometriyasi 1200 tagacha nuqtaga qisqartiriladi: vaqt
bo‘laklarining boshi/oxiri va minimum/maksimumi saqlanadi. Yo‘q qiymat uzilishlari
chiziq bilan ulanmaydi. Tooltip tanlangan ko‘rsatilgan vaqtning asl qiymatini o‘qiydi;
barcha xom namunalarga alohida hover nuqtasi berilmaydi. Xom natija va ZIP arxivi
qisqartirilmaydi. Brauzer tanlangan natijaning to‘liq JSON ma’lumotini hali ham oladi;
bu o‘zgarish serverdan oqimli ma’lumot olishni joriy qilmaydi.

## Tekshirish

```bash
npm --prefix lab/ui ci --no-audit --no-fund
npm --prefix lab/ui test
npm --prefix lab/ui run build
```

Yig‘ilgan `lab/web-react` fayllarini ham commit qiling. CI yuqoridagi tekshiruvlarni
va yig‘ilgan fayllarning manbaga mosligini tekshiradi.

Ixtiyoriy to‘liq brauzer tekshiruvi uchun Playwright va Chrome/Chromium kerak.
Playwright loyiha ishlab chiqarish bog‘liqligi emas; alohida o‘rnatilgan modulni
`NETLAB_PLAYWRIGHT_MODULE` orqali ko‘rsatish mumkin. Birinchi terminalda, repo ildizidan:

```bash
python -m lab.ui.tests.server
```

Bu faqat `127.0.0.1:8765` da ishlaydi, vaqtinchalik natijalar va loyihalar katalogini
yaratadi. Haqiqiy analitik ilovalar, 1D/2D tadqiqotlar, xatoli variant, buzilgan
arxiv, 51 ta kichik tarix yozuvi va sintetik 100 ming nuqtali signalni tayyorlaydi.
Foydalanuvchining saqlangan ma’lumotlarini o‘qimaydi yoki o‘zgartirmaydi. Test
bekor qilish va o‘chirishni ham bajaradi: **har bir takrorlashdan oldin shu serverni
to‘xtatib, qayta ishga tushiring**.

Ikkinchi terminalda:

```bash
NETLAB_PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
NETLAB_BROWSER=/absolute/path/to/chrome \
npm --prefix lab/ui run test:browser
```

Playwright o‘z brauzeri bilan o‘rnatilgan bo‘lsa `NETLAB_BROWSER` shart emas.
`NETLAB_UI_PORT` (server), `NETLAB_UI_URL` (test), `NETLAB_UI_FIXTURE` (ikkalasi)
va `NETLAB_UI_ARTIFACTS` (skrinshotlar) orqali manzillarni o‘zgartirish mumkin.
Standart skrinshot katalogi `/tmp/netlab-ui-browser`.

O‘lchangan tekshiruvlar: [2026-11-08 natijalari](validation/ui-results-workspace-2026-11-08.md).

# Network Studio — vizual boshqaruv ilovasi

Cisco DNA Center / Packet Tracer uslubidagi **desktop ilova** (alohida oyna,
brauzer emas): topologiyani vizual qurish, simulyatsiyani serverda ishga
tushirish, yig'ilgan ma'lumotni jonli ko'rish va tahlil qilish — hammasi bitta
oynada, glass/neon dizaynda, **kun-tun mavzu** bilan.

pywebview (native WebKit oynasi) + HTML/CSS/JS frontend + Python backend. Barcha
mavjud simulyator modullari (`routing`, `topologies`, `analytics.data`) qayta
ishlatiladi — Studio ularning ustidagi vizual qatlam.

## O'rnatish

```bash
pip install -r studio/requirements.txt
```
(macOS'da pyobjc avtomatik o'rnatiladi. Agar `tkinter` kabi Tk kerak bo'lmasa —
Studio Tk ishlatmaydi, faqat WebKit.)

## Ishga tushirish

```bash
python3 studio/studio.py
# yoki repo tubidan:
python3 -m studio.studio
```

## Ekranlar

- **Boshqaruv paneli** — KPI plitalar, dataset tanlash, mini-grafiklar, so'nggi runlar.
- **Topologiya quruvchi** — sichqoncha bilan switch/host qo'shish, ulash, sozlash,
  tekshirish va saqlash. Saqlangan topologiya `custom_topologies/`ga yoziladi va
  `--topology <nom>` orqali real simulyatsiyada ishlatiladi.
- **Analitika** — Routing / Anomaliya / DNS / QoS grafiklari + erkin SQL so'rov (DuckDB).
- **Simulyatsiya** — topologiya/rejim/duration/seed tanlab, **serverda** (SSH orqali)
  ishga tushirish, jonli progress + log, natijani import qilish.
- **Sozlamalar** — SSH server (host/user/parol/WSL distro/repo yo'li), standart
  qiymatlar, mavzu. "Ulanishni tekshirish" tugmasi.

## Muhim

- **Simulyatsiya serverda ishlaydi** — Mininet haqiqiy Linux kernel talab qiladi,
  shuning uchun Studio Mac'da ishlaydi-yu, `light_simulation.py`ni WSL/Ubuntu
  serverda SSH orqali ishga tushiradi. Server ma'lumotlari Sozlamalarda.
- `studio/config.json` (SSH paroli bo'lishi mumkin) **git'ga yozilmaydi** (gitignore).
- Barcha frontend offline — tashqi kutubxona/CDN yo'q.

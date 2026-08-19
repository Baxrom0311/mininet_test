# Analitika — desktop ilova

Tugagan simulyatsiya run'laridan qolgan CSV dataset'larni (`results/combined/` yoki har
bir `results/five_as_<mode>/datasets/`) DuckDB orqali so'rab, Tkinter oynasida vizual
tahlil qiladigan mustaqil desktop ilova. Simulyatorning o'zidan (`light_simulation.py` va
uning modullaridan) butunlay ajratilgan — ularga hech qanday o'zgarish kiritmaydi.

## O'rnatish

```bash
brew install python-tk@3.14   # yoki sizning Python versiyangizga mos paket
pip install -r analytics/requirements.txt
```

## Ishga tushirish

```bash
python3 analytics/app.py
python3 analytics/app.py --data results/five_as_ospf/datasets   # boshqa dataset papkasi
```

## Tab'lar

- **Xulosa** — har dataset necha qator, routing rejimlar taqsimoti
- **Topologiya** — 4 ta tayyor topologiyani (`topologies.py`) grafik ko'rinishda
- **Topologiya qurish** — sichqoncha bilan yangi topologiya qurish (switch/host/link
  qo'shish, parametrlarini sozlash), validatsiya qilib `custom_topologies/*.json`ga
  saqlash. Saqlangan topologiya `topologies.py`ning avtomatik loader'i orqali darhol
  `light_simulation.py --topology <nom>`da ishlatiladigan bo'ladi (WSL/Ubuntu serverda,
  chunki bu Mac'da Mininet yo'q — ilova simulyatsiyani o'zi ishga tushirmaydi, faqat
  to'g'ri buyruqni ko'rsatadi).
- **Routing solishtiruvi** — 11 routing rejimini RTT/hop bo'yicha solishtiruvchi grafiklar
- **Anomaliyalar** — hujum hodisalari turlari va kunlik tarqalishi
- **DNS** — root/TLD/authoritative bosqichlari kechikish taqsimoti
- **QoS/Impairments** — DSCP/navbat bandi taqsimoti, nosozlik turlari
- **SQL so'rov** — DuckDB'ga to'g'ridan-to'g'ri erkin SQL yozish (istalgan jadval ustida)

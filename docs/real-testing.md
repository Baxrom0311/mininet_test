# Real testlarning umumiy qulfi

Mininet cleanup host bo‘yicha umumiy. Barcha `lab.tests.real_*` CLI’lari (11 ta)
hamda `lab.benchmarks.real_scale` va `real_calibration` `main()` orqali avtomatik
`/tmp/netlab-real.lock` exclusive qulfini oladi. Import qilingan `t_*` funksiyalari
chaqiruvchining scope’ida ishlaydi: integration suite bitta qulfni boshidan oxirgi
`driver.stop()`gacha ushlaydi.

```bash
sudo timeout 600 python3 -m lab.tests.real_integration l2 ospf fw
sudo timeout 180 python3 -m lab.tests.real_isis --output /tmp/isis.json
sudo timeout 600 python3 -m lab.benchmarks.real_scale --routers 10 20 --output /tmp/scale.json
sudo python3 -m lab.benchmarks.real_calibration --reps 10 --output /tmp/calibration.csv
```

Katta benchmarkning checkpoint, sampler xatosi va uzilishdagi hisobot qoidalari:
[scale-benchmark.md](scale-benchmark.md).

Avvalgi wrapper ham ishlaydi:

```bash
sudo flock /tmp/netlab-real.lock python3 -m lab.tests.real_isis --output /tmp/isis.json
```

Yordamchi Linux `/proc/self/fdinfo` orqali shu inode uchun meros qolgan exclusive
`FLOCK` descriptor borligini tekshiradi va uni duplicate qiladi. Kontekst tugaganda
faqat o‘z descriptorini yopadi; tashqi qulfni `LOCK_UN` bilan ochmaydi. Qulf fayli
o‘chirilmaydi yoki kesilmaydi. Bir thread ichida nested scope mumkin, boshqa thread
va mustaqil jarayon band qulfni chetlab o‘tolmaydi. Helper descriptorlari exec’ga
o‘tmaydi, oddiy fork child’da ham yopiladi.

Band bo‘lsa kutib qolmaydi: `real network busy: ...` xabari va **exit 1** qaytadi.
Avvalgi test/server tugagach qayta ishga tushiring. Prerequisite tekshiruvlarining
**exit 2** holati saqlangan; kernel qulf testi real tarmoq passed degani emas.
Symlink yoki oddiy fayl bo‘lmagan lock path qabul qilinmaydi.

`flock --close` merosiy descriptorni yopadi; shared `flock --shared` esa exclusive
himoya emas. Ikkalasi ham qayta ishlatilmaydi va band qulf sifatida to‘xtaydi.
Oddiy `flock file command` yoki `flock --no-fork file command` ishlating.
Linux `/proc` ko‘rinmasa tashqi qulfni qayta ishlatish imkoni yo‘q; oddiy to‘g‘ridan
ishga tushirishda kernel `flock` ishlashda davom etadi.

Jonli real server qulfni test yordamchisidan olmaydi; uni tashqaridan himoyalang:

```bash
sudo flock /tmp/netlab-real.lock python3 -m lab.netlab --driver real
```

Qulfga amal qilmaydigan boshqa Mininet jarayonlari ajratilmaydi; dedicated host
kerak. [Real hisobot](validation/real-env-2026-11-06.md) tarixiy natijalarni beradi.

```bash
python3 -m unittest lab.tests.test_real_lock -v
```

Bu testlar root yoki Mininet ishlatmaydi: haqiqiy kernel qulfi, tashqi util-linux
wrapper, nested/parallel jarayonlar, thread/fork, exception cleanup va barcha 13
CLI kirish nuqtasining band holatdagi xulqini tekshiradi.

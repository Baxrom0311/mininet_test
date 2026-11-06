# Collector regressiyasi — 2026-11-06

Branch: `fix/calibration-collector`, baza `main` `b2296ae`. Yangi real FRR o‘lchovi yo‘q.

- **120 backend testi passed**, 11.99 s: mavjud 101 + 19 yangi collector/probe testi.
- Yakuniy provenance qo‘shimchasidan so‘ng **19 collector testi qayta passed**,
  0.73 s. `method=event_before_command_v2` CSV va ledger’da saqlanishi ham tekshirildi.
- OSPF down/up ketma-ketligi sintetik drayver bilan tekshirildi: up’dan avval
  alternate route tayyor bo‘lishi shart; yangi metriclar bir-biriga aralashmaydi.
- Haqiqiy lokal Python subprocesslarda exception, baseline yo‘qligi va SIGTERM’ni
  inkor qiladigan process sinovlari o‘tdi: kill’dan keyin ham wait, reader join,
  stdout close tekshirildi. Bular ping/FRR protokol o‘lchovi emas.
- Fake collector’da missing/nonfinite/duplicate metric, exception’dan keyingi
  success, cleanup failure, interrupt, UUID source va CSV SHA-256 tekshirildi.
  Chala to‘plamda CLI exit 1, root yo‘qligida exit 2 saqlangan.

```bash
python -m unittest lab.tests.test_calibration_collector -v
```

Yangi kod namespace/FRR hostda ishlatilmadi, remote CI ishga tushirilmadi.
Timer konstantalari va avvalgi real CSV/JSON artefaktlar tahrirlanmadi.
Claude real hostda [collector qo‘llanmasi](../calibration-collector.md) asosida
OSPF down/up va RIP uchun 10 tadan kuzatuv yig‘ishi kerak. `collection=passed`
faqat metrikalar to‘liq yig‘ilganini bildiradi; fit va retuning alohida review.

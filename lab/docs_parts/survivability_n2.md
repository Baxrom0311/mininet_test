#### N-2 (juft nosozliklar), SRLG va kirish kabellari filtri

`analyze(..., order=2)` (API: `"order": 2`, CLI: `--n2`) — N-1 holatlariga qo'shimcha barcha
nishon juftliklari baholanadi (`n2` maydoni):

- **kesish:** ikkala nishon ham asosiy holatdagi va birorta N-1 holatdagi oqim yo'llarida
  ishlatilmasa — juftlik hech narsani o'zgartira olmaydi; qurilma va uning o'z kabeli
  juftligi — bitta qurilma nosozligi bilan bir xil. Ikkalasi ham o'tkazib yuboriladi (`pruned`);
- `max_cases` (standart 2000) — undan ko'p juftlik bo'lsa birinchilari baholanadi, `truncated: true`;
- `vulnerabilities` — **faqat juft nosozlikda** uziladigan oqimlar (har qanday yakka nosozlikka chidaydi);
- `min_cut` — har oqim uchun minimal kesim: `1` (SPOF), `2` (faqat juftlikda), `">=3"`
  (N-2 dan ham omon qoladi), `null` (asosiy holatda ishlamaydi — masalan firewall). Kesim
  baholangan nishonlar to'plami bo'yicha (`exclude_access` bilan host kabellari hisobga olinmaydi);
- `n2.capacity_plan` / `n2.upgrades` — eng yomon juft nosozlik bo'yicha sig'im rejasi (N-1 rejasidan alohida).

**SRLG** (umumiy xavf guruhi — bitta kanal/trassadagi kabellar): loyiha `"srlg": {"trassa1": ["l3", "l7"]}`
yoki so'rovda `srlg` — har guruh bitta nosozlik holati (`kind: "srlg"`), `srlg_broken` — oqim
qaysi guruhlarda uziladi.

**`exclude_access`** — host/server kabellari nishonlardan chiqariladi (SPOF ro'yxati ularsiz
qisqaroq; uchi host bo'lgan har bir kabel — bu kabel uzilsa, aslida faqat shu host uziladi).

```bash
python3 -m lab.survivability lab/examples/rip_ring.json uzilish links --n2 --exclude-access
python3 -m lab.survivability lab/examples/rip_ring.json uzilish links --srlg '{"trassa": ["l2", "l3"]}'
```

`rip_ring` (kirish kabellarisiz): N-1 da SPOF yo'q, lekin `l2 + l3` yoki `l2 + l4` birga uzilsa
pc1→srv1 uziladi — min kesim 2. Tezlik (N-2, barcha kabellar): kampus 41 kabel / 820 juftlik
~14 s, halqa 50 kabel / 1225 juftlik ~21 s; `exclude_access` bilan 2.4 s va 5.6 s.

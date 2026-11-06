# Real kalibratsiya yig‘uvchisi

E/G uchun dedicated root Mininet/FRR hostda:

```bash
sudo python -m lab.benchmarks.real_calibration --only ospf ospf-up rip --reps 10 \
  --output /tmp/measurements.csv
```

Har case va takrorda yangi topologiya ko‘tariladi va `driver.stop()` bajariladi.
`ospf-up` avval kabelni uzadi, muqobil yo‘l va ping tayyorligini kutadi, keyin
kabelni qayta ulab afzal next-hop qaytishini o‘lchaydi. Case’lar aralashtirilmaydi:

| Case | Protokol holati | Trafik |
|---|---|---|
| `ospf` | `ospf,route_update_s` — link-down muqobil next-hop | `traffic_gap_s` |
| `ospf-up` | `ospf,route_restore_s` — link-up afzal next-hop | `traffic_gap_up_s` |
| `rip` | `rip,route_recovery_s` — link-down muqobil next-hop | `traffic_gap_s` |

Link-up vaqtida muqobil yo‘l ishlayotgani uchun trafik uzilishi nolga yaqin
bo‘lishi mumkin; bu route restore vaqti ham nol degani emas. Up metric avtomatik
`ospf_convergence_s` override’iga aylantirilmaydi. Standart model taymerlari
o‘zgartirilmagan.

## Natija va exit kodi

CSV’ning avvalgi ustunlari saqlanadi. Yonida `/tmp/measurements.csv.collection.json`
yoziladi; `--report PATH` bilan boshqa nom berish mumkin. U taqqoslash JSON’i
uchun ishlatiladigan `measurements.json`ni bosib yozmaydi.

Ledger har takrorda case, rep, status, haqiqiy metric qiymati yoki yo‘qligi,
istisno/cleanup xatosi, wall time, UUID bilan run ID va CSV SHA-256’ini saqlaydi.
CSV va JSON alohida atomic replace bilan yoziladi; ikkalasi bitta transaction
emas. Interrupt/kill’dan keyin JSON statusi va CSV hash mosligini tekshiring.
Oddiy `KeyboardInterrupt`da cleanup va `interrupted` holati saqlanadi;
SIGKILL’da oxirgi saqlangan holat `running` bo‘lishi mumkin.

- **Exit 0**: barcha so‘ralgan case/takrorlar va ularning metrikalari yig‘ilgan.
- **Exit 1**: kamida bir exception, yo‘q metric yoki cleanup xatosi bor. Mavjud
  yaroqli satrlar saqlanadi; yo‘q qiymat CSV’da nol bilan almashtirilmaydi.
- **Exit 2**: root yo‘q yoki argument xato (`reps` 1..1000; case’lar takrorlanmasin).

Cleanup yiqilsa keyingi topologiya boshlanmaydi. Collection `passed` — model real
natijaga mos degani emas; moslikni alohida `lab.calibration` baholaydi.

`fix/calibration-evidence` imkoniyatlari ham birlashtirilgach son va mavjudlikni
tekshirish uchun:

```bash
python -m lab.calibration /tmp/measurements.csv --min-samples 10 \
  --require-metric ospf:route_update_s:s \
  --require-metric ospf:route_restore_s:s \
  --require-metric rip:route_recovery_s:s --output /tmp/comparison.json
```

## Vaqt va probe shartlari

Protokol vaqti monotonic clock bilan **link command oldidan** boshlab o‘lchanadi;
command va keyingi kuzatish kechikishi ham kiradi. Bu FRR ichidagi aniq LSA/SPF
vaqti emas. Oldingi collector polling’ni commanddan keyin boshlagan. Yangi
source va ledger `method=event_before_command_v2`ni ko‘rsatadi; eski va yangi
usul natijalarini bitta medianaga aralashtirmang.

Ping `-D`, interval 0.1 s. Hodisadan oldin kamida ikki javob va oxirgi 1 s ichida
yangi javob talab qilinadi. OSPF/RIP next-hop’i hodisadan oldin ham tekshiriladi.
BGP link-up ataylab uzilgan tarmoqda boshlanadi: ping process startup output’i
kutiladi, ishlamaydigan kabeldan pre-event reply talab qilinmaydi.

`traffic_gap_s` — eventni qamrab oluvchi va undan keyingi tugallangan reply gap’lari
orasidagi eng kattasi minus probe intervali. Yakunda javobsiz tail qolsa (oxirgi
reply 1 s dan eski), natija `None` va takror `incomplete` bo‘ladi. Bu uzilishning
aniq ichki davomiyligi emas; probe fazasi va rejalashtirish xatosi bor.
Duplicate ICMP sequence’lar sanalmaydi. Orqaga sakragan wall clock yoki kuzatuv
oynasi davomida wall/monotonic farqi 0.5 s dan oshsa ping metrikalari yaroqsiz.

Ping process context manager’da boshqariladi: terminate, timeoutda kill, har ikki
holatda wait, reader join va pipe close. Link command/poll xatosida ham shu cleanup
ishlaydi. Yangi helperlar `calibration_probes.py` va `calibration_collection.py`da;
katta Mininet/ns-3 driver refaktorlari bu ishga kirmaydi.

```bash
python -m unittest lab.tests.test_calibration_collector -v
```

Bu testlar sintetik drayver/trace va haqiqiy lokal Python subprocesslardan
foydalanadi. Root, FRR yoki haqiqiy namespace o‘lchovi bajarilmaydi. Yangi real
acceptance va 10 ta down/up/RIP kuzatuvlarini Claude real hostda yig‘adi.

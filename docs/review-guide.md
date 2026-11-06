# Review va merge tartibi

Barcha ishlar `main`dan tashqarida. Branchlar ketma-ket ustma-ust qurilgan.
Har bir oldingi PR main’ga kirgach keyingisini yangilangan main bilan taqqoslang
yoki rebase qiling. Quyidagi base branch delta review uchun; asosiy merge target
main bo‘lishi kerak. Agent PR ochmagan, merge yoki environment publish qilmagan.

| Tartib | Branch | Delta base | Commit |
|---:|---|---|---|
| 1 | `feat/netlab-ipv6-scale-review` | `main` (`7c6e317`) | `300f7e4`, `67ba546` |
| 2 | `fix/review-runtime-safety` | `feat/netlab-ipv6-scale-review` | `1563fd6` |
| 3 | `feat/isis` | `fix/review-runtime-safety` | `5ff1cfc` |
| 4 | `feat/calibration` | `feat/isis` | `0e5fcf4` |
| 5 | `feat/traffic-matrix` | `feat/calibration` | `45f9ce1` |
| 6 | `feat/run-queue` | `feat/traffic-matrix` | `c3d0c50` |
| 7 | `feat/p4-bmv2` | `feat/run-queue` | `d276af0` |
| 8 | `ci/real-mininet` | `feat/p4-bmv2` | `59c5580` |
| 9 | `docs/architecture` | `ci/real-mininet` | `08bbbe4` |
| 10 | `feat/experiment-statistics` | `docs/architecture` | `521fb5b` |
| 11 | `perf/run-result-index` | `feat/experiment-statistics` | indexed history/pagination branchi |

## Tekshirilgan imkoniyatlar

- `perf/run-result-index`: yangi 10 test, jami 43 backend regressiyasi. Asl demo/report
  smoke suite va haqiqiy ns-3 IPv4/IPv6 replication/replay ham o‘tdi.
  100 × 10 000 nuqtali 64.9 MB sun’iy history’da warm
  ro‘yxat median 503.2 ms’dan 1.67 ms’ga tushdi; o‘zgarmagan full JSON o‘qishlar
  100’dan 0’ga tushdi. [result-index.md](result-index.md) cold import narxi,
  migration backup/downgrade va O(fayllar soni) stat cheklovini tushuntiradi.

- `feat/experiment-statistics`: 33 unit/integration test o‘tdi (oldingi 27 + 6 yangi
  experiment testi). Haqiqiy ns-3 replication/replay testi IPv4 va IPv6 uchun jami
  6 alohida jarayon bilan o‘tdi; aynan bir input qayta bajarilganda flow metrikalari
  teng. Eski report va failures smoke suite ham yangi branchda o‘tdi.
  [experiments.md](experiments.md) API, statistika shartlari va eksport cheklovlarini beradi.

- Yangi funksional to‘plam: **27 test o‘tdi** (runtime/auth, label, calibration,
  traffic va queue). Queue testlari cheklangan parallelizm, queued/running cancel,
  silent child cleanup va dead-process recovery’ni tekshiradi.
- IPv6/IS-IS to‘plami: **18 test o‘tdi**, shu jumladan alohida haqiqiy ns-3
  jarayonlarida IPv4/IPv6 TCP/UDP/echo, outage/recovery va IS-IS statik forwarding.
  Bu FRR protokol paketlari yoki kernel IPv6 sinovi emas.
- Yangi queued API bilan asl demo suite va ns-3 STP suite o‘tdi.
- Qolgan 11 ta asl demo/analytic smoke to‘plami ham final branchda o‘tdi:
  BGP, STP, firewall, multicast, VRRP, LAG, survivability, failures, performance,
  report va N-2. Bu qayta tekshiruvda ns-3 qismlari alohida skip qilingan.
- UI typecheck/build va failure reconciliation sinovi o‘tdi; committed UI bundle
  yangilangan. Oxirgi TypeScript typecheck ham o‘tdi.
- 118 tracked Python fayli AST sintaksisi va shell skriptlar sintaksisi tekshirildi.
- 1000-flow CSV import + 2 soniyalik analitik ssenariy 0.064–0.079 s atrofida
  bajarildi. Bu real 1000-flow emulator benchmarki emas.

## Tashqi blockerlar va qolgan acceptance

| Ish | Holat | Kerakli keyingi dalil |
|---|---|---|
| Real 20+ router FRR CPU/RSS/startup | Benchmark vositasi tayyor, local blocked | Dedicated root host yoki manual real CI JSON/log |
| Real IS-IS adjacency/recovery | Konfiguratsiya/test tayyor, local blocked | FRR isisd bilan 6 adjacency Up va recovery natijasi |
| Kalibratsiya konstantalarini moslash | Compare vositasi tayyor, defaults o‘zgarmagan | Bir xil metric/unit/topologiyadagi takroriy real o‘lchovlar |
| P4 template compile va BMv2 forwarding | Strict gate tayyor, local blocked | p4c, BMv2, Mininet/root bilan haqiqiy passed natija |
| Real Mininet GitHub CI | Workflow YAML tekshirilgan | Merge’dan keyingi haqiqiy GitHub job natijasi |
| UI yangi protocol/statistics editorlari | Yuborilgan taqsimot bo‘yicha foydalanuvchida | UI implementation va browser acceptance |

## OPNETga yaqinlashish uchun keyingi mezonlar

Real o‘lchov va kalibratsiya yopilmasdan aniqlikni oshirdik deb aytmaslik kerak.
Keyingi bosqichda bir xil seed/config/tool versiyalari bilan eksport qilinadigan
reproducibility paketi, takroriy runlar uchun confidence interval, analytic modelning
1 soniyalik diskretlash xatosi va protokol/traffic-gap metrikalarining farqi alohida
baholanishi kerak. Model route/SPF bilan real control-plane protokolini aniq ajratish
saqlanadi. Hozirgi branchlar OPNETning barcha imkoniyatlarini takrorlaydi degan
da’vo qilinmaydi.

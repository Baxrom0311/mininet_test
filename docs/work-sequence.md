# Branchlar va ish ketma-ketligi

Foydalanuvchi PR va merge’ni o‘zi bajaradi. Agent main’ga push qilmaydi va environment
publish qilmaydi. Quyidagi 1–15 branchlar tarixiy stack bo‘lib `main`ga kirgan
(oxirgi merge PR #16). Joriy baza `207ba52` (PR #17). Yangi ishlar alohida branchda.
Real FRR va kalibratsiya dalillari [2026-11-06 hisobotida](validation/real-env-2026-11-06.md)
saqlangan: PR #12 bazasi, PR #15 artefaktlari; keyingi kodning real sinovi alohida ish.

1. `feat/netlab-ipv6-scale-review` — `300f7e4`, `67ba546`: IPv6 model/ns-3, katta real FRR
   benchmark vositasi, address-family kodini ajratish. Push qilindi. Real FRR
   model va ns-3 testlari o‘tdi. Keyingi root host sinovida 10/20/30 router natijalari
   yuqoridagi real hisobotga qo‘shildi.
2. `fix/review-runtime-safety` — audit topilmalari: origin/auth, run identity,
   xato exit kodi, Studio lifecycle, ML artefakt/label, DATA_DIR va UI sync.
   `1563fd6`: 14 regressiya testi, demo suite va React typecheck/build o‘tdi; push qilindi.
3. `feat/isis` — model/FRR konfiguratsiyasi, namuna va smoke/real testlar.
   6 test (shu jumladan IPv4/IPv6 ns-3) o‘tdi; keyingi real FRR sinovida config/timer
   xatolari tuzatilib, passed natija yuqoridagi real hisobotga yozildi.
4. `feat/calibration` — real o‘lchov importi va model/real jadvali; yangi real
   sonlarni root muhitsiz taxmin qilib kiritmaslik.
   CSV compare/median/tolerance va qo‘llab-quvvatlangan timer takliflari tayyor;
   4 test o‘tdi. Real retuning yangi o‘lchov yo‘qligi sabab bajarilmadi.
5. `feat/traffic-matrix` — CSV va gravity traffic, API, 1000 oqimli tekshiruv.
   Import/API va 5 test tayyor. 1000 oqim, 2 s analitik ssenariy import bilan
   0.079 s oldi (shu instance’da; umumiy barcha topologiyalar uchun kafolat emas).
6. `feat/run-queue` — SQLite run metadata, chegaralangan ijro, cancel, restart recovery.
   4 yangi test o‘tdi: ijro chegarasi, queued/running cancel, silent child cleanup,
   dead-worker recovery. Asl demo/analytic API suite ham o‘tdi.
   Birgalikdagi 27 test va queued API orqali haqiqiy ns-3 STP suite ham o‘tdi.
7. `feat/p4-bmv2` — mavjud P4 real testini qat’iy prerequisite/skip holati bilan
   tayyorlash. O‘rnatish va real validation root/BMv2/p4c yo‘qligiga bog‘liq.
   Strict compile/runtime gate qo‘shildi. Real hostda ham p4c/BMv2 yo‘qligi qayd
   etilgan; muvaffaqiyatli forwarding dalili hali yo‘q. Tizim apt manbalari o‘zgartirilmadi.
8. `ci/real-mininet` — alohida qo‘lda/tungi real job; CI ishlagan deb faqat GitHub
   natijasi mavjud bo‘lsa aytish.
   Ubuntu 24.04 workflow qo‘shildi: nightly/manual L2/OSPF/firewall/IS-IS,
   manual optional 10/20-router benchmark va saqlanadigan log/JSON artefaktlari.
   YAML lokal tekshirildi; GitHub’da hali bajarilmagan.
9. `docs/architecture` — dvigatellar, kesh, expand/postprocess, API va flock qoidalari.
   `docs/architecture.md` yozildi; source’dagi route’lar bilan tekshirildi.
   `docs/review-guide.md` branch/commit tartibi va acceptance holatini jamlaydi.
   Final branch: 27 yangi test, 18 IPv6/IS-IS testi, asl demo va qolgan 11 smoke
   suite o‘tdi. Keyinchalik real suite, IS-IS, scale va kalibratsiya natijalari
   yuqoridagi real hisobot bilan to‘ldirildi.
10. `feat/experiment-statistics` — independent replications, Student t 95% CI,
    per-run input/runtime manifest va qayta bajarish eksporti. `docs/architecture`
    ustiga qurildi. 33 unit/integration test, eski report/failures suite va haqiqiy
    ns-3 IPv4/IPv6 uchun 6 jarayonli replication/replay sinovi o‘tdi.
    Qo‘llanma: `docs/experiments.md`. Bu real Mininet o‘lchovlarini almashtirmaydi.
11. `perf/run-result-index` — persistent metadata/file-signature indeksi, bounded
    pagination va filtrlar, legacy schema migration/backup, selected experiment
    report reads va pending label persistence. `feat/experiment-statistics` ustida.
    10 yangi test bilan backend to‘plami 43 test; 64.9 MB sun’iy history’da warm
    list 503.2 ms → 1.67 ms, 0 full JSON reads. `docs/result-index.md`ga qarang.
    Haqiqiy ns-3 IPv4/IPv6 replication/replay va asl demo/report suite ham o‘tdi.
12. `feat/analysis-windows` — warm-up/cutoff bo‘yicha archived result tahlili,
    duration-weighted full-bin statistika va coverage diagnostikasi, HTML/CSV/ZIP.
    ns-3 fractional stop/sampling tuzatishi va app interval propagation.
    `perf/run-result-index` ustida; 9 yangi test bilan backend to‘plami 52 test.
    Qo‘llanma: `docs/analysis-windows.md`.
    52 backend va 19 IPv6/IS-IS/replication testi o‘tdi; haqiqiy ns-3’da fractional
    sample/tail va replay tekshirildi. Asl demo/report suite ham o‘tdi.
13. `feat/paired-experiment-comparison` — baseline/candidate paired Student t,
    config diff, raw-pair audit, HTML/CSV/ZIP va offline CLI. `feat/analysis-windows`
    ustida. 11 yangi test bilan 63 backend testi o‘tdi; IPv4/IPv6 paired bandwidth
    sinovi 8 haqiqiy ns-3 subprocess bilan o‘tdi. `docs/experiment-comparison.md`.
14. `feat/experiment-campaigns` — original baseline va 1..8 variant uchun paired
    replication kampaniyasi, preview/status/cancel/restart va nested ZIP/report.
    Shared transactional submission, eager cancelled-payload release va orphan
    worker cleanup. `feat/paired-experiment-comparison` ustida; `docs/campaigns.md`.
    76 backend testi, 8 subprocessli haqiqiy IPv4/IPv6 ns-3 campaign va asl demo/report
    suite o‘tdi. Storage-write failure’da orphan/foreign-owner farqi ham tekshirildi.
15. `perf/campaign-analysis` — request-local prepared cohort reuse va ixtiyoriy
    compact JSON. `feat/experiment-campaigns` ustida. 60 flow/15 run benchmark’da
    440.13 ms → 259.65 ms; compact JSON 37.96% kichik. 6 yangi test, jami 82.
    `docs/campaign-analysis-performance.md` va xom benchmark JSON’ga qarang.
    82 backend testi hamda IPv4/IPv6 full/compact metric equivalence sinovi
    8 haqiqiy ns-3 subprocess bilan o‘tdi.
16. UI’ning yangi feature maydonlari — yuborilgan reja bo‘yicha foydalanuvchi ishlaydi.
    Agent mavjud UI’dagi tasdiqlangan sync xatosidan tashqari dizaynni o‘zgartirmaydi.

Asl audit `233054b`da bajarilgan; eski yozuvlardagi test sonlari o‘sha branch holatiga
tegishli. B → D ishlarida real GitHub workflow va keyingi `main` uchun natija yig‘iladi.
E/G qo‘shimcha OSPF/RIP takrorlarini, H esa yetarli dalil bo‘lsa timer review’ni kutadi.
F implementatsiyasi `feat/ospf-convergence` (`abc12a3`) branchida; bu docs branchiga
qo‘shilmagan. UI foydalanuvchida, katta modul refaktorlari real acceptance’dan keyin.

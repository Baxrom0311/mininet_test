# IPv6 va real scale tekshiruvi

Joriy real natijalar: [2026-11-06 hisobot](real-env-2026-11-06.md),
[10/20/30 router xom JSON](real-scale-2026-11-06.json).
20/30 routerda peak process RSS 1.11/1.63 GB, barcha kutilgan OSPF qo‘shnilari
Full. Umumiy OVS RSS alohida; maksimal sig‘im aniqlanmagan.
Real hisobot bazasi `648fbd8` (PR #12), quyidagi model sinovlari esa oldingi
branch tarixidir. Kernel IPv6 real sinovi hali bajarilmagan.

Branch bazasi: `origin/main` — `7c6e317` (PR #3 merge). Oldingi `233054b`
checkoutdagi o‘zgarishlar shu bazaga moslashtirildi; STP, multicast, firewall,
VRRP, LAG, failures, survivability va ns-3 tezlik kodlari saqlandi.

## Bajarilgan

- `python -m unittest lab.tests.smoke_ipv6 -v`: 12 test. O‘n bittasi model/demo/
  validation/resource sampler, bittasi ikki alohida ns-3 jarayonida IPv4 va IPv6
  TCP/UDP/echo, link uzilishi/tiklanishi, route events va forwarding counterlarini
  tekshiradi. Hech qaysi assertion o‘chirilmadi.
- Yangilangan `main`ning 12 ta demo/analitik smoke to‘plami o‘tdi:
  demo, BGP, STP, firewall, multicast, VRRP, LAG, survivability, failures,
  performance, report va N-2 survivability (`NETLAB_SKIP_NS3=1`).
- STP, multicast, VRRP va firewall smoke to‘plamlari ns-3 bilan ham o‘tdi.
- 20-router zanjir modelida 16 hopdan uzun yo‘l va qaytish gatewaylari tekshirildi.
- ns-3.44.post0, Python 3.12, cppyy 3.5.0 ishlatildi. `CC=gcc CXX=g++` bilan
  wheel build qilindi, TLS/checksum tekshiruvi o‘chirilmagan.
- IPv6 forwardingdagi haqiqiy yo‘qotish sababi ns-3 manbasi bilan tekshirildi:
  `2001:db8::/32` ataylab tashlanadi. Generator ULA manzillarini ishlatadi.
- `Ipv6Interface::SetDown()` manzillarni tozalaydi: link tiklanganda ULA manzilni
  qayta qo‘shish packet recovery testi bilan tasdiqlandi.

## Real sinovning holati

Asl cloud probe root/Mininet/FRR mavjud bo‘lmagani sabab o‘lchov bajara olmagan.
Keyinchalik alohida root hostda 10/20/30 router o‘lchovlari bajarilgan va yuqoridagi
artefaktlarda saqlangan. Avvalgi cloud prerequisite tekshiruvi shu real natijani
bekor qilmaydi; model/ns-3 natijasi ham real FRR o‘lchovi o‘rniga ishlatilmaydi.

Real hostda README buyruqlarini bajaring va hosil bo‘lgan JSON’ni ko‘rib chiqing.
Sampled process RSS, shared OVS RSS va cgroup cheklovlarini alohida talqin qiling;
20 routerdan o‘tish katta topologiyalarda cheksiz masshtablashni anglatmaydi.

## Kod soddalashtirildi

- `lab/addressing.py` oilani tanlash/copy/filter qoidalarini bir joyda ushlaydi.
- `lab/engines/ns3_addressing.py` stack, manzil, endpoint, route va link-state
  farqlarini ajratadi. ns-3 trafik, queue va feature kodlari takrorlanmadi.
- Analitik modelda boshlang‘ich koordinatalar va mutable run holati alohida
  nusxalarda saqlandi. Deepcopy’ni olib tashlash Wi-Fi harakatini buzishi
  regressiya tekshiruvida aniqlandi va bu optimizatsiya qaytarildi.
- Real benchmark unit test yoki demo natijasini real o‘lchov deb ko‘rsatmaydi.

Haqiqiy IPv6 kernel/FRR sinovi va GUI’ni qo‘lda sinash bu bosqichda bajarilmadi.

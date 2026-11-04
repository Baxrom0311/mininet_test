### STP / RSTP (switch halqalari)

Switch `config`:

```json
"stp": true, "stp_mode": "stp", "stp_priority": 4096,
"ports": {"2": {"stp_cost": 100}}
```

- `stp_mode`: `stp` (802.1D, standart) yoki `rstp` (802.1w).
- `stp_priority`: 0..61440, 4096 ga karrali (standart 32768); eng kichik (priority, id) — root.
- `ports.<n>.stp_cost`: port yo'l narxi; bo'lmasa kabel tezligidan (802.1D-1998: 10 Mbit/s
  → 100, 100 Mbit/s → 19, 1 Gbit/s → 4, 10 Gbit/s → 2). Real driver bu narxni OVS'ga
  yozadi (veth o'zini "10 Gbit/s" deb ko'rsatadi).

Model (`lab/stp.py`): root bridge, root port, designated/alternate portlar; alternate —
blocking. STP'siz switchlar halqa hosil qilsa — **broadcast storm**: domen ishlamaydi,
ping/traceroute sababni ko'rsatadi (`L2 halqa (STP o'chiq): broadcast storm [l1, l2, l3]`),
ns-3 aniq xato beradi. Inspect → "STP (spanning tree)" (rol/holat, bridge/root id);
real driverda `ovs-appctl stp/show` (yoki `rstp/show`) va model bashorati yonma-yon.

| Dvigatel | Qanday | Konvergensiya (`stp_lab`, faol kabel uzilganda) |
|---|---|---|
| Mininet (OVS) | `stp_enable` / `rstp_enable`, `stp-priority`, `stp-system-id` = device_mac(id, 0), `stp-path-cost` | STP ~23 s, RSTP < 1 s (o'lchangan) |
| Analitik | blocking → forwarding o'tish `hold`: 802.1D 2×forward_delay = 30 s (bilvosita nosozlik +max_age 20 s), RSTP 1 s; `stp_convergence_s` — qo'lda | 30 s / 1 s |
| ns-3 | BridgeNetDevice'da STP yo'q: model daraxti bo'yicha blocking segment ma'lumot uchun yopiladi (ErrorModel), hodisada qayta hisoblanadi; MAC yozuvlari 2 s (TCN FDB tozalashi o'rniga) | 30 s / 1 s |

Port holati o'zgarishlari `route_events` ga (`dev` = switch, `port2 root/forwarding`)
yoziladi — `convergence` (OPNET "convergence duration") L2 uchun ham ko'rinadi.

Eslatma: 802.1D da kabel **tiklanganda** ham uzilish bo'ladi — yangi root port 30 s
listening/learning kutadi, eski root port esa darhol blocking bo'ladi (RSTP'da ~1 s).

Namuna: `stp_lab` — s1 (root, priority 4096), s2, s3 uchburchak; s3:2 blocking.
Ssenariy `uzilish`: t=10 s da l2 (s1-s3) uziladi, t=50 s da tiklanadi.

Testlar: `python3 -m lab.tests.smoke_stp` (demo + analitik + ns-3),
`sudo flock /tmp/netlab-real.lock python3 -m lab.tests.real_stp` (real OVS STP va RSTP).

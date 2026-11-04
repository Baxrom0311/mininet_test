### Firewall / ACL (stateful, iptables)

Router, host yoki server `config.firewall`:

```json
"firewall": {"default": "drop",
             "rules": [
               {"chain": "forward", "action": "accept", "state": "established"},
               {"chain": "forward", "action": "accept", "in": "r1-eth1"},
               {"chain": "forward", "action": "accept", "proto": "tcp", "dst": "10.0.2.10/32", "dport": 80},
               {"chain": "forward", "action": "reject", "proto": "icmp", "in": "r1-eth3", "dst": "10.0.1.0/24"}]}
```

- **Semantika — iptables bilan bir xil:** zanjir (`input` / `forward` / `output`) ichida
  birinchi mos qoida; mos qoida bo'lmasa — siyosat. `default` routerda `forward`,
  host/serverda `input` zanjiriga tegishli; aniq siyosat: `"policy": {"input": "drop"}`.
- Maydonlar: `proto` (`any` / `tcp` / `udp` / `icmp`), `src`/`dst` (subnet), `dport`/`sport`
  (faqat tcp/udp), `in`/`out` (interfeys), `state` (`new` / `established`).
- **Stateful:** `established` qoidasi javob paketlarini o'tkazadi — "ichkaridan tashqariga
  ruxsat, tashqaridan yangi ulanish yo'q". Bu qoida bo'lmasa javoblar ham tashlanadi.
- **`reject` va `drop`:** `reject` — TCP uchun RST, boshqalar uchun ICMP host-prohibited
  (ping darhol "Destination Host Prohibited" deydi); `drop` — jim tashlash (timeout).

| Dvigatel | Qanday |
|---|---|
| Mininet | qurilma namespace'ida o'z zanjirlari `NETLAB-IN/FWD/OUT` (INPUT/FORWARD/OUTPUT dan sakrash); NAT (`nat` jadvali) alohida qoladi; jonli o'zgartirish — zanjirlar qayta yoziladi. Inspect → **har qoida hisoblagichi** (paket/bayt, `iptables -L -v`) |
| Analitik / demo | paket yo'li bo'yicha: manba OUTPUT, oraliq routerlar FORWARD, manzil INPUT; NAT'dan keyingi hoplarda manba almashtiriladi; tcp/ping javobi `established` sifatida tekshiriladi (`lab/firewall.py`) |
| ns-3 | iptables yo'q — model bloklagan oqim ishga tushirilmaydi (natijada 100% yo'qotish, `blocked` sababi bilan) |

**Portlar.** Ilovaga `dport` bering (`{"type": "tcp", "dport": 80, ...}`); bo'lmasa port
`5201 + ilova tartib raqami` (Mininet'dagi iperf3 porti bilan bir xil). Mininet'da iperf3
server/klient aynan shu portda ishlaydi; bir (server, port) dagi bir nechta TCP ilova
(masalan HTTP sahifalari) bitta doimiy iperf3 server'ni bo'lishadi. Eslatma: iperf3 UDP
testi shu portga TCP boshqaruv ulanishini ham ochadi — real tarmoqda UDP ilova uchun shu
portda TCP ham ruxsat etilgan bo'lishi kerak.

Namuna: `firewall_lab` — LAN (pc1, pc2), DMZ veb-server (srv1:80), Internet (inet).
Ssenariy `trafik`: `web_in` (inet → srv1:80) va LAN oqimlari o'tadi, `ssh_in` (:22)
tashlanadi, `ping_in` (inet → pc1) reject. Real Mininet sinovi: TCP :80 47 Mbit/s, :22
"Connection timed out", hisoblagichlar (established qoidasi 37 404 paket, default drop 6),
jonli qoida qo'shish va olib tashlash.

```bash
python3 -m lab.tests.smoke_fw
```

```bash
sudo python3 -m lab.tests.real_fw
```

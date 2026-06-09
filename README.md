# Ryu/os-ken + Mininet SDN lab

Maqsad: 9 ta OpenFlow switch, har switch ortida kichik LAN hostlar, hammasi Ryu/os-ken controller orqali boshqariladi. Controller transport layer metadata yozadi:

- TCP/UDP source va destination portlar
- TCP flag, sequence, acknowledgement qiymatlari
- IP source/destination
- switch DPID, kirish porti, chiqish porti
- flow packet/byte counters

Ubuntu 24.04 da `ryu` paketi o'rniga `os-ken` mavjud. `os-ken` Ryu'ning fork'i va controller API'lari shu lab uchun bir xil.

## O'rnatish

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-os-ken
```

Eski Mininet holatini tozalash:

```bash
sudo mn -c
```

## Avtomatik test

```bash
sudo python3 main.py --mode test
```

Bu buyruq:

1. `ryu_transport_controller.py` ni `osken-manager` yoki `ryu-manager` bilan ishga tushiradi.
2. 9 ta switch va 18 ta hostli Mininet topologiyani yaratadi.
3. `pingAll` bilan L2 reachability tekshiradi.
4. TCP HTTP traffic va UDP datagram yuboradi.
5. Controller yozgan transport metadata borligini tekshiradi.

## Interaktiv CLI

```bash
sudo python3 main.py --mode cli
```

CLI ichida:

```text
nodes
net
h1 ping h18
h18 python3 -c "import urllib.request; print(urllib.request.urlopen('http://10.0.1.1:8000').read(80))"
exit
```

## Log fayllar

Transport packet-in eventlari:

```bash
sudo tail -f /tmp/ryu_transport_events.jsonl
```

Flow counter statistikasi:

```bash
sudo tail -f /tmp/ryu_flow_stats.jsonl
```

## Topologiya

Backbone:

```text
          s1
       /  |  \
      s2  s3  s4
     / \  / \   \
    s5 s6 s7 s8 s9
```

Har bir switchga default 2 tadan host ulanadi:

```text
s1: h1, h2
s2: h3, h4
...
s9: h17, h18
```

Hostlar `10.0.<switch>.<host>/16` IP formatida yaratiladi.

## Parametrlar

Host sonini oshirish:

```bash
sudo python3 main.py --mode test --hosts-per-switch 3
```

Link bandwidth/delay o'zgartirish:

```bash
sudo python3 main.py --mode test --bandwidth 10 --delay 10ms
```

Controller'ni alohida terminalda ishga tushirish:

```bash
osken-manager --ofp-tcp-listen-port 6633 ryu_transport_controller.py
sudo python3 main.py --mode cli --external-controller
```

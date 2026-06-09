# Mininet Python API lab

Bu loyiha Mininet'ni CLI orqali emas, Python API orqali o'rganish uchun kichik lab.

Topologiya:

```text
h1 ---\
       s1 ===== s2
h2 ---/          \--- h3
                 \--- h4
```

## Ishga tushirish

Mininet root huquqi talab qiladi:

```bash
sudo python3 main.py --mode demo
```

## Rejimlar

Avtomatik demo:

```bash
sudo python3 main.py --mode demo
```

Avtomatik test:

```bash
sudo python3 main.py --mode test
```

Interaktiv Mininet CLI:

```bash
sudo python3 main.py --mode cli
```

CLI ichida sinab ko'rish uchun:

```text
nodes
net
h1 ping h4
iperf h1 h4
exit
```

## Link parametrlarini o'zgartirish

```bash
sudo python3 main.py --mode demo --bandwidth 5 --delay 20ms
```

`--bandwidth` Mbps qiymatida, `--delay` esa Mininet delay formatida beriladi.

## Muammo bo'lsa

Eski Mininet holatini tozalash:

```bash
sudo mn -c
```

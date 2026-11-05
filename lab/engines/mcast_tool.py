"""Multicast UDP yuboruvchi/qabul qiluvchi (Mininet namespace ichida, mnexec orqali).

    python3 -m lab.engines.mcast_tool send <group> <port> <rate_bps> <size> <duration> [ttl]
    python3 -m lab.engines.mcast_tool recv <group> <port> <duration> [iface_ip]

Paket: 8 bayt seq + 8 bayt yuborilgan vaqt (time.time(), bitta mashina — umumiy soat) + to'ldiruvchi.
recv har soniyada bitta satr chiqaradi:
    STAT <unix_sec> <bytes> <pkts> <lost> <avg_delay_ms> <jitter_ms>
(yo'qotish — seq bo'shliqlari; birinchi paketgacha bo'lganlar hisoblanmaydi).
recv guruhga qo'shilganda kernel IGMP report yuboradi -> pimd daraxtni quradi.
"""

import socket
import struct
import sys
import time

HDR = struct.Struct("!Qd")


def send(group, port, rate, size, duration, ttl=16):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, int(ttl))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
    size = max(int(size), HDR.size)
    pad = b"\0" * (size - HDR.size)
    interval = size * 8 / float(rate)
    t0 = time.time()
    seq = 0
    nxt = t0
    while time.time() - t0 < float(duration):
        now = time.time()
        if now < nxt:
            time.sleep(min(nxt - now, 0.01))
            continue
        try:
            s.sendto(HDR.pack(seq, time.time()) + pad, (group, int(port)))
        except OSError:
            pass                    # marshrut yo'q / ENOBUFS — paket yo'qoldi
        seq += 1
        nxt += interval
        if nxt < time.time() - 0.5:     # ortda qolsak — tutib olmaymiz (portlash bo'lmasin)
            nxt = time.time()
    print(f"SENT {seq}", flush=True)


def recv(group, port, duration, iface_ip="0.0.0.0"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    s.bind(("", int(port)))
    mreq = struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton(iface_ip))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    s.settimeout(0.2)
    print("JOINED", flush=True)
    t_end = time.time() + float(duration)
    sec = int(time.time())
    b = n = lost = 0
    dsum = jsum = 0.0
    last_seq = last_d = None
    while time.time() < t_end:
        try:
            data = s.recv(65535)
        except socket.timeout:
            data = None
        now = time.time()
        if int(now) != sec:
            print(f"STAT {sec} {b} {n} {lost} {dsum / n * 1000 if n else -1:.3f} {jsum / max(n - 1, 1) * 1000 if n > 1 else -1:.3f}",
                  flush=True)
            sec, b, n, lost, dsum, jsum = int(now), 0, 0, 0, 0.0, 0.0
        if not data or len(data) < HDR.size:
            continue
        seq, ts = HDR.unpack_from(data)
        d = max(now - ts, 0.0)
        if last_seq is not None and seq > last_seq + 1:
            lost += seq - last_seq - 1
        if last_seq is None or seq > last_seq:
            last_seq = seq
        if last_d is not None:
            jsum += abs(d - last_d)
        last_d = d
        b += len(data)
        n += 1
        dsum += d


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "send":
        send(a[1], a[2], float(a[3]), int(a[4]), float(a[5]), int(a[6]) if len(a) > 6 else 16)
    elif a and a[0] == "recv":
        recv(a[1], a[2], float(a[3]), a[4] if len(a) > 4 else "0.0.0.0")
    else:
        print(__doc__)
        sys.exit(2)

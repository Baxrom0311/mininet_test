"""Kichik, sof-Python tarmoq yordamchi funksiyalari.

Bu modulda Mininet/os-ken'ga bog'liq HECH NARSA yo'q (faqat matn tahlili va
grafda BFS) — shuning uchun bir nechta modul (collector.py, path_tracer.py,
light_simulation.py) uni xavfsiz import qilishi va Mininet o'rnatilmagan
muhitda ham unit-test qilishi mumkin. `Collector._parse_ping` kabi boshqa
modulning "private" metodiga to'g'ridan-to'g'ri murojaat qilish o'rniga,
umumiy funksiyalar shu yerda bir joyda saqlanadi.
"""

import threading
from collections import deque

# ── Per-node buyruq lock'lari (thread-safe Node.cmd) ──
# Mininet Node.cmd() thread-safe EMAS: har node bitta interaktiv shell'ni
# (sendCmd/waitOutput) bo'lishadi. Bir nechta fon-thread bitta node'da bir
# vaqtda cmd chaqirsa ichki "assert not self.waiting" ishga tushadi va buyruq
# jimgina yo'qoladi. Har node uchun BITTA lock ushlaymiz (nomi bo'yicha) va
# faqat shu bitta node.cmd chaqiruvi davomida ushlaymiz — hech qachon ikki
# node lock birga ushlanmaydi, shuning uchun deadlock bo'lishi mumkin emas.
_node_locks = {}
_node_locks_guard = threading.Lock()


def _get_node_lock(name):
    with _node_locks_guard:
        lock = _node_locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _node_locks[name] = lock
        return lock


def locked_cmd(node, cmd_str):
    """`node.cmd(cmd_str)` chaqiruvini shu node uchun serializatsiya qiladi.

    Ko'p background thread (traffic_gen, impairments, collector, path_tracer,
    NATMonitor) bitta Node obyektida bir vaqtda cmd chaqiradi — Node.cmd()
    thread-safe bo'lmagani uchun bu poyga buyruqlarni yo'qotadi. Bu yordamchi
    har node uchun bitta lock orqali chaqiruvlarni ketma-ketlashtiradi.
    """
    lock = _get_node_lock(getattr(node, "name", None) or id(node))
    with lock:
        return node.cmd(cmd_str)


def parse_ping(output):
    """`ping -c N ...` buyrug'i chiqishini tahlil qiladi: packet loss %
    va min/avg/max/mdev RTT (ms) qiymatlarini ajratib oladi.

    Muvaffaqiyatsiz/unreachable holatlarda "min/avg/max" qatori bo'lmaydi —
    natijada faqat (yoki hech qanday) loss_pct maydoni qaytariladi.
    """
    result = {}
    for line in output.split("\n"):
        if "packet loss" in line:
            for part in line.split(","):
                if "packet loss" in part:
                    try:
                        result["loss_pct"] = float(part.strip().split("%")[0])
                    except ValueError:
                        pass
        if "min/avg/max" in line:
            try:
                vals = line.split("=")[1].strip().split("/")
                result["rtt_min"] = float(vals[0])
                result["rtt_avg"] = float(vals[1])
                result["rtt_max"] = float(vals[2])
                if len(vals) > 3:
                    result["rtt_mdev"] = float(vals[3].split()[0])
            except (ValueError, IndexError):
                pass
    return result


def graph_diameter(switches, links):
    """Switch-darajasidagi grafning diametri: barcha juftlik (src, dst)
    orasidagi eng qisqa yo'llarning ENG UZUNI (hop sonida), BFS orqali.

    `switches` — nomlar iterable'i (masalan topo["switches"] dict/kalitlar).
    `links` — (s1, s2) juftlik kalitli dict yoki shunday tuple'lar iterable'i
    (masalan topo["links"]).

    STP convergence vaqtini haqiqiy topologiya o'lchamiga moslashtirish
    uchun ishlatiladi (light_simulation.py) — halqalar soni yagona emas,
    BPDU'ning eng uzoq yo'l bo'ylab tarqalish vaqti ham ahamiyatli.
    """
    graph = {s: [] for s in switches}
    for (s1, s2) in links:
        graph.setdefault(s1, []).append(s2)
        graph.setdefault(s2, []).append(s1)

    diameter = 0
    for src in switches:
        dist = {src: 0}
        queue = deque([src])
        while queue:
            node = queue.popleft()
            for neighbor in graph.get(node, []):
                if neighbor not in dist:
                    dist[neighbor] = dist[node] + 1
                    queue.append(neighbor)
        if dist:
            diameter = max(diameter, max(dist.values()))
    return diameter

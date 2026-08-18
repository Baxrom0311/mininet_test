"""Dinamik tarmoq nosozliklari: congestion, link flap, packet reorder,
buffer bloat, MTU blackhole, duplicate, jitter spike."""

import json
import os
import random
import threading
import time

from config import DATA_DIR

# Dinamik impairmentlar
IMPAIRMENT_EVENTS = [
    {"name": "congestion",    "prob": 0.25, "delay": (20, 80),  "loss": (2, 8),   "dur": (5, 25)},
    {"name": "micro_burst",   "prob": 0.15, "delay": (40, 150), "loss": (5, 15),  "dur": (1, 5)},
    {"name": "link_degrade",  "prob": 0.15, "delay": (10, 40),  "loss": (1, 5),   "dur": (15, 60)},
    {"name": "link_flap",     "prob": 0.08, "delay": (0, 0),    "loss": (0, 0),   "dur": (2, 8)},
    {"name": "route_change",  "prob": 0.05, "delay": (5, 20),   "loss": (0, 2),   "dur": (3, 10)},
    {"name": "packet_reorder","prob": 0.10, "delay": (0, 0),    "loss": (0, 0),   "dur": (5, 20)},
    {"name": "buffer_bloat",  "prob": 0.10, "delay": (50, 300), "loss": (0, 1),   "dur": (10, 40)},
    {"name": "mtu_blackhole", "prob": 0.04, "delay": (0, 0),    "loss": (0, 0),   "dur": (5, 15)},
    {"name": "duplicate",     "prob": 0.03, "delay": (0, 0),    "loss": (0, 0),   "dur": (3, 10)},
    {"name": "jitter_spike",  "prob": 0.10, "delay": (10, 50),  "loss": (0, 1),   "dur": (5, 15)},
]


class Impairments:
    def __init__(self, net):
        self.net = net
        self._running = False
        self._thread = None
        self._log = os.path.join(DATA_DIR, "stats/impairment_log.jsonl")

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self):
        sw_links = [l for l in self.net.links
                     if l.intf1.node.name.startswith("s") and l.intf2.node.name.startswith("s")]
        while self._running:
            time.sleep(random.randint(8, 30))
            if not self._running:
                break
            link = random.choice(sw_links)
            intf = link.intf1
            node = intf.node
            src, dst = link.intf1.node.name, link.intf2.node.name
            event = random.choice(IMPAIRMENT_EVENTS)
            if random.random() > event["prob"]:
                continue
            dur = random.randint(event["dur"][0], event["dur"][1])
            ename = event["name"]

            if ename == "link_flap":
                self._log_event("link_flap_start", src, dst, dur)
                try:
                    link.intf1.node.cmd(f"ip link set {link.intf1.name} down")
                    link.intf2.node.cmd(f"ip link set {link.intf2.name} down")
                except Exception:
                    continue
                def restore(l=link, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try:
                        l.intf1.node.cmd(f"ip link set {l.intf1.name} up")
                        l.intf2.node.cmd(f"ip link set {l.intf2.name} up")
                    except Exception:
                        pass
                    self._log_event("link_flap_end", s, ds, 0)
                threading.Thread(target=restore, daemon=True).start()

            elif ename == "packet_reorder":
                # Real paket tartibsizligi — tc netem reorder
                reorder_pct = random.randint(5, 25)
                gap = random.randint(3, 10)
                self._log_event("packet_reorder", src, dst, dur,
                               reorder_pct=reorder_pct, gap=gap)
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} parent 1:30 handle 30: netem "
                             f"delay 10ms reorder {reorder_pct}% gap {gap}")
                except Exception:
                    continue
                def restore_reorder(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} parent 1:30 handle 30: netem delay 0ms")
                    except Exception: pass
                    self._log_event("packet_reorder_end", s, ds, 0)
                threading.Thread(target=restore_reorder, daemon=True).start()

            elif ename == "buffer_bloat":
                # Buffer bloat — katta queue delay, past loss
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                self._log_event("buffer_bloat", src, dst, dur, delay_add=delay_add)
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} parent 1:30 handle 30: netem "
                             f"delay {delay_add}ms {delay_add//4}ms distribution normal")
                except Exception:
                    continue
                def restore_bloat(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} parent 1:30 handle 30: netem delay 0ms")
                    except Exception: pass
                    self._log_event("buffer_bloat_end", s, ds, 0)
                threading.Thread(target=restore_bloat, daemon=True).start()

            elif ename == "mtu_blackhole":
                # MTU blackhole — katta paketlar yo'qoladi
                self._log_event("mtu_blackhole", src, dst, dur, mtu=576)
                try:
                    node.cmd(f"ip link set {intf.name} mtu 576")
                except Exception:
                    continue
                def restore_mtu(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"ip link set {i} mtu 1500")
                    except Exception: pass
                    self._log_event("mtu_blackhole_end", s, ds, 0)
                threading.Thread(target=restore_mtu, daemon=True).start()

            elif ename == "duplicate":
                # Paket duplikatsiyasi
                dup_pct = random.randint(1, 10)
                self._log_event("duplicate", src, dst, dur, duplicate_pct=dup_pct)
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} parent 1:30 handle 30: netem "
                             f"duplicate {dup_pct}%")
                except Exception:
                    continue
                def restore_dup(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} parent 1:30 handle 30: netem delay 0ms")
                    except Exception: pass
                    self._log_event("duplicate_end", s, ds, 0)
                threading.Thread(target=restore_dup, daemon=True).start()

            elif ename == "jitter_spike":
                # Kuchli jitter — VoIP/gaming uchun yomon
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                jitter = delay_add * random.uniform(0.5, 1.5)
                corr = random.randint(20, 50)
                self._log_event("jitter_spike", src, dst, dur,
                               delay_add=delay_add, jitter=round(jitter, 1), correlation=corr)
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} parent 1:30 handle 30: netem "
                             f"delay {delay_add}ms {int(jitter)}ms {corr}%")
                except Exception:
                    continue
                def restore_jitter(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} parent 1:30 handle 30: netem delay 0ms")
                    except Exception: pass
                    self._log_event("jitter_spike_end", s, ds, 0)
                threading.Thread(target=restore_jitter, daemon=True).start()

            else:
                # congestion, micro_burst, link_degrade, route_change
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                loss_add = random.uniform(event["loss"][0], event["loss"][1])
                self._log_event(ename, src, dst, dur, delay_add=delay_add, loss_add=round(loss_add, 2))
                try:
                    node.cmd(f"tc qdisc change dev {intf.name} parent 1:30 handle 30: netem "
                             f"delay {delay_add}ms {delay_add//3}ms 25% loss {loss_add}% 25%")
                except Exception:
                    continue
                def restore_netem(n=node, i=intf.name, d=dur, s=src, ds=dst, en=ename):
                    time.sleep(d)
                    try: n.cmd(f"tc qdisc change dev {i} parent 1:30 handle 30: netem delay 0ms loss 0%")
                    except Exception: pass
                    self._log_event(f"{en}_end", s, ds, 0)
                threading.Thread(target=restore_netem, daemon=True).start()

    def _log_event(self, event_type, src, dst, dur, **extra):
        entry = {"ts": time.time(), "event": event_type, "link": f"{src}-{dst}", "duration": dur, **extra}
        try:
            with open(self._log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

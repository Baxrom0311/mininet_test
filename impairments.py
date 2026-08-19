"""Dinamik tarmoq nosozliklari: congestion, link flap, packet reorder,
buffer bloat, MTU blackhole, duplicate, jitter spike."""

import json
import os
import random
import threading
import time

from config import DATA_DIR
from network_build import QOS_BANDS

# QOS_BANDS (network_build.py) — (classid, handle, share) ro'yxati EF/AF21/BE
# tartibida. Shu yerda tc buyruqlari uchun "1:<hex>" parent handle satrlariga
# aylantiramiz — ikkala modul ham bitta manbadan (network_build.QOS_BANDS)
# ozg'atiladi, shuning uchun ID'lar hech qachon bir-biridan uzoqlashmaydi.
_QOS_BAND_NAMES = ("EF", "AF21", "BE")
BAND_HANDLES = {name: f"1:{classid:x}" for name, (classid, _, _) in zip(_QOS_BAND_NAMES, QOS_BANDS)}

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

# congestion/micro_burst = haqiqiy link-keng saturation, shuning uchun EF/AF21
# navbatlariga ham (kichikroq miqyosda) sizib kiradi. Qolgan turlar (reorder,
# buffer_bloat, jitter_spike, mtu, duplicate, link_flap, link_degrade,
# route_change) fon-trafik hodisalari deb hisoblanadi va faqat BE'ga tegadi.
_LEAK_EVENTS = ("congestion", "micro_burst")
_LEAK_FACTOR_RANGE = (0.20, 0.25)  # EF/AF21 zarari BE'ning shuncha ulushi


def _tc_change(node, ifname, band_handle, netem_args):
    """`band_handle` — masalan "1:30" (BE). Handle raqami parent bilan bir
    xil bo'lishi shart (network_build._setup_qos_qdisc bilan mos)."""
    handle = band_handle.split(":")[1]
    node.cmd(f"tc qdisc change dev {ifname} parent {band_handle} handle {handle}: netem {netem_args}")


class Impairments:
    def __init__(self, net):
        self.net = net
        self._running = False
        self._thread = None
        self._restore_threads = []  # har impairment o'zining "restore_*" fon-threadini shu yerga qo'shadi
        self._log = os.path.join(DATA_DIR, "stats/impairment_log.jsonl")

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        # Restore threadlar hodisa davomiyligicha (dur, eng ko'pi ~60s) uxlab
        # yotadi va shundan keyingina tc'ni asl holatga qaytarib log yozadi.
        # Ularni join qilmasak, dataset_builder impairment_log.jsonl'ni
        # to'liq bo'lmagan holatda o'qib qolishi yoki net.stop() interfeys
        # allaqachon yo'q qilingandan keyin tc buyrug'i bilan poyga qilishi
        # mumkin edi.
        for t in self._restore_threads:
            t.join(timeout=65)

    def _spawn_restore(self, target):
        t = threading.Thread(target=target, daemon=True)
        t.start()
        self._restore_threads.append(t)
        return t

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
                self._spawn_restore(restore)

            elif ename == "packet_reorder":
                # Real paket tartibsizligi — tc netem reorder. Fon-trafik
                # hodisasi: faqat BE bandiga ta'sir qiladi.
                reorder_pct = random.randint(5, 25)
                gap = random.randint(3, 10)
                self._log_event("packet_reorder", src, dst, dur,
                               reorder_pct=reorder_pct, gap=gap, bands=["BE"])
                try:
                    _tc_change(node, intf.name, BAND_HANDLES["BE"],
                               f"delay 10ms reorder {reorder_pct}% gap {gap}")
                except Exception:
                    continue
                def restore_reorder(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: _tc_change(n, i, BAND_HANDLES["BE"], "delay 0ms")
                    except Exception: pass
                    self._log_event("packet_reorder_end", s, ds, 0, bands=["BE"])
                self._spawn_restore(restore_reorder)

            elif ename == "buffer_bloat":
                # Buffer bloat — katta queue delay, past loss. Fon-trafik
                # hodisasi: faqat BE bandiga ta'sir qiladi.
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                self._log_event("buffer_bloat", src, dst, dur, delay_add=delay_add, bands=["BE"])
                try:
                    _tc_change(node, intf.name, BAND_HANDLES["BE"],
                               f"delay {delay_add}ms {delay_add//4}ms distribution normal")
                except Exception:
                    continue
                def restore_bloat(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: _tc_change(n, i, BAND_HANDLES["BE"], "delay 0ms")
                    except Exception: pass
                    self._log_event("buffer_bloat_end", s, ds, 0, bands=["BE"])
                self._spawn_restore(restore_bloat)

            elif ename == "mtu_blackhole":
                # MTU blackhole — katta paketlar yo'qoladi (band-agnostik,
                # interfeys darajasida).
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
                self._spawn_restore(restore_mtu)

            elif ename == "duplicate":
                # Paket duplikatsiyasi. Fon-trafik hodisasi: faqat BE bandiga
                # ta'sir qiladi.
                dup_pct = random.randint(1, 10)
                self._log_event("duplicate", src, dst, dur, duplicate_pct=dup_pct, bands=["BE"])
                try:
                    _tc_change(node, intf.name, BAND_HANDLES["BE"], f"duplicate {dup_pct}%")
                except Exception:
                    continue
                def restore_dup(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: _tc_change(n, i, BAND_HANDLES["BE"], "delay 0ms")
                    except Exception: pass
                    self._log_event("duplicate_end", s, ds, 0, bands=["BE"])
                self._spawn_restore(restore_dup)

            elif ename == "jitter_spike":
                # Kuchli jitter — VoIP/gaming uchun yomon. Fon-trafik
                # hodisasi: faqat BE bandiga ta'sir qiladi.
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                jitter = delay_add * random.uniform(0.5, 1.5)
                corr = random.randint(20, 50)
                self._log_event("jitter_spike", src, dst, dur,
                               delay_add=delay_add, jitter=round(jitter, 1), correlation=corr,
                               bands=["BE"])
                try:
                    _tc_change(node, intf.name, BAND_HANDLES["BE"],
                               f"delay {delay_add}ms {int(jitter)}ms {corr}%")
                except Exception:
                    continue
                def restore_jitter(n=node, i=intf.name, d=dur, s=src, ds=dst):
                    time.sleep(d)
                    try: _tc_change(n, i, BAND_HANDLES["BE"], "delay 0ms")
                    except Exception: pass
                    self._log_event("jitter_spike_end", s, ds, 0, bands=["BE"])
                self._spawn_restore(restore_jitter)

            else:
                # congestion, micro_burst, link_degrade, route_change — barchasi
                # BE bandiga to'liq miqyosda tegadi. congestion/micro_burst
                # qo'shimcha ravishda EF/AF21'ga ham kichikroq (~20-25%)
                # miqyosda "sizib kiradi" — bu haqiqiy link-keng saturationda
                # ham sodir bo'ladi (priority scheduler kamaytiradi, lekin
                # butunlay yo'q qilmaydi). link_degrade/route_change esa shu
                # simulyatorda fon-trafik hodisasi sifatida modellashtirilgan
                # — BE'dan tashqariga chiqmaydi.
                delay_add = random.randint(event["delay"][0], event["delay"][1])
                loss_add = random.uniform(event["loss"][0], event["loss"][1])
                leak = ename in _LEAK_EVENTS
                leak_delay = leak_loss = 0
                if leak:
                    leak_factor = random.uniform(*_LEAK_FACTOR_RANGE)
                    leak_delay = int(round(delay_add * leak_factor))
                    leak_loss = round(loss_add * leak_factor, 2)

                log_extra = {"delay_add": delay_add, "loss_add": round(loss_add, 2)}
                if leak:
                    log_extra.update(leak_delay_ms=leak_delay, leak_loss_pct=leak_loss)
                self._log_event(ename, src, dst, dur,
                               bands=(["BE", "EF", "AF21"] if leak else ["BE"]), **log_extra)

                try:
                    _tc_change(node, intf.name, BAND_HANDLES["BE"],
                               f"delay {delay_add}ms {delay_add//3}ms 25% loss {loss_add}% 25%")
                except Exception:
                    continue

                affected = ["BE"]
                if leak:
                    for bname in ("EF", "AF21"):
                        try:
                            _tc_change(node, intf.name, BAND_HANDLES[bname],
                                       f"delay {leak_delay}ms {leak_delay//3}ms 25% "
                                       f"loss {leak_loss}% 25%")
                            affected.append(bname)
                        except Exception:
                            pass

                def restore_netem(n=node, i=intf.name, d=dur, s=src, ds=dst, en=ename, bnds=affected):
                    time.sleep(d)
                    for bname in bnds:
                        try:
                            _tc_change(n, i, BAND_HANDLES[bname], "delay 0ms loss 0%")
                        except Exception:
                            pass
                    self._log_event(f"{en}_end", s, ds, 0, bands=bnds)
                self._spawn_restore(restore_netem)

    def _log_event(self, event_type, src, dst, dur, **extra):
        entry = {"ts": time.time(), "event": event_type, "link": f"{src}-{dst}", "duration": dur, **extra}
        try:
            with open(self._log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

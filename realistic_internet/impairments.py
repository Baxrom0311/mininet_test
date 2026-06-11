"""
Dinamik tarmoq buzilishlari (tc/netem).
Real internetdagi congestion, jitter, paket yo'qolishi simulyatsiyasi.
"""

import json
import os
import random
import threading
import time

from realistic_internet.config import (
    IMPAIRMENT_SCENARIOS, IMPAIRMENT_CHANGE_MIN, IMPAIRMENT_CHANGE_MAX,
    LINK_PROFILES, AS_DEFINITIONS, INTER_AS_LINKS, STATS_DIR,
)


class ImpairmentManager:
    """Tarmoq buzilishlarini boshqarish."""

    def __init__(self, net):
        self.net = net
        self._running = False
        self._thread = None
        self._log_path = os.path.join(STATS_DIR, "impairment_events.jsonl")
        self._original_params = {}  # link -> asl parametrlari

    def apply_static(self):
        """
        Har xil link turlari uchun netem qo'shimcha jitter qo'shish.
        TCLink allaqachon bw/delay/loss o'rnatgan, biz correlation va
        distribution qo'shamiz - realroq bo'lishi uchun.
        """
        print("[Impairment] Statik netem parametrlari qo'shilmoqda...")

        for link in self.net.links:
            intf1 = link.intf1
            intf2 = link.intf2

            for intf in [intf1, intf2]:
                node = intf.node
                if not hasattr(node, "cmd"):
                    continue

                # Faqat switch interfeyslari (host emas)
                name = intf.name
                if name.startswith("s"):
                    # Correlation qo'shish (oldingi paketga bog'liq)
                    # Bu real tarmoqdagi bursty loss ni simulyatsiya qiladi
                    try:
                        node.cmd(
                            f"tc qdisc change dev {name} root netem "
                            f"delay 0ms 0ms 25% distribution normal "
                            f"loss 0% 25% "
                            f"reorder 1% 50%"
                        )
                    except Exception:
                        pass

        self._log_event("static_applied", {
            "description": "Correlation va reorder qo'shildi"
        })

    def start_dynamic(self):
        """Dinamik impairment generatsiyasini boshlash."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._dynamic_loop, daemon=True)
        self._thread.start()
        print("[Impairment] Dinamik buzilishlar boshlandi")

    def stop_dynamic(self):
        """Dinamik impairment generatsiyasini to'xtatish."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        print("[Impairment] Dinamik buzilishlar to'xtatildi")

    def _dynamic_loop(self):
        """Asosiy dinamik impairment loop."""
        # Barcha switch-to-switch linklarni yig'ish
        switch_links = []
        for link in self.net.links:
            n1 = link.intf1.node.name
            n2 = link.intf2.node.name
            if n1.startswith("s") and n2.startswith("s"):
                switch_links.append(link)

        if not switch_links:
            return

        while self._running:
            interval = random.randint(IMPAIRMENT_CHANGE_MIN, IMPAIRMENT_CHANGE_MAX)
            time.sleep(interval)

            if not self._running:
                break

            # Tasodifiy ssenariy tanlash
            scenario_name, scenario = random.choice(
                list(IMPAIRMENT_SCENARIOS.items())
            )

            # Ehtimollik tekshirish
            if random.random() > scenario["probability"]:
                continue

            # Tasodifiy link tanlash
            link = random.choice(switch_links)
            intf = link.intf1
            node = intf.node

            if scenario_name == "route_flap":
                self._apply_route_flap(link, scenario)
            else:
                self._apply_degradation(link, scenario, scenario_name)

    def _apply_degradation(self, link, scenario, scenario_name):
        """Link degradatsiyasi qo'llash."""
        intf = link.intf1
        node = intf.node
        intf_name = intf.name

        delay_add = random.randint(*scenario["delay_add_ms"])
        loss_add = random.uniform(*scenario["loss_add_pct"])
        jitter_add = random.randint(*scenario["jitter_add_ms"])
        duration = random.randint(*scenario["duration_sec"])

        src = link.intf1.node.name
        dst = link.intf2.node.name

        self._log_event("degradation_start", {
            "scenario": scenario_name,
            "link": f"{src}-{dst}",
            "interface": intf_name,
            "delay_add_ms": delay_add,
            "loss_add_pct": round(loss_add, 2),
            "jitter_add_ms": jitter_add,
            "duration_sec": duration,
        })

        # Netem o'zgartirish
        try:
            node.cmd(
                f"tc qdisc change dev {intf_name} root netem "
                f"delay {delay_add}ms {jitter_add}ms 30% distribution normal "
                f"loss {loss_add}% 25%"
            )
        except Exception:
            return

        # Duration kutib, asl holatga qaytarish
        def restore():
            time.sleep(duration)
            try:
                node.cmd(
                    f"tc qdisc change dev {intf_name} root netem "
                    f"delay 0ms 0ms 25% loss 0% 25%"
                )
            except Exception:
                pass
            self._log_event("degradation_end", {
                "scenario": scenario_name,
                "link": f"{src}-{dst}",
                "interface": intf_name,
            })

        threading.Thread(target=restore, daemon=True).start()

    def _apply_route_flap(self, link, scenario):
        """Link o'chib-yonishi simulyatsiyasi."""
        intf1 = link.intf1
        intf2 = link.intf2
        src = intf1.node.name
        dst = intf2.node.name
        down_time = random.randint(*scenario["link_down_sec"])

        self._log_event("link_flap_start", {
            "link": f"{src}-{dst}",
            "down_seconds": down_time,
        })

        # Link o'chirish
        try:
            intf1.node.cmd(f"ip link set {intf1.name} down")
            intf2.node.cmd(f"ip link set {intf2.name} down")
        except Exception:
            return

        def restore():
            time.sleep(down_time)
            try:
                intf1.node.cmd(f"ip link set {intf1.name} up")
                intf2.node.cmd(f"ip link set {intf2.name} up")
            except Exception:
                pass
            self._log_event("link_flap_end", {
                "link": f"{src}-{dst}",
            })

        threading.Thread(target=restore, daemon=True).start()

    def inject_congestion(self, switch_name: str, duration_sec=30):
        """Qo'lda congestion kiritish (test uchun)."""
        node = self.net.get(switch_name)
        if not node:
            return

        for intf in node.intfList():
            if intf.name == "lo":
                continue
            node.cmd(
                f"tc qdisc change dev {intf.name} root netem "
                f"delay 50ms 20ms 40% loss 5% 33%"
            )

        self._log_event("manual_congestion", {
            "switch": switch_name,
            "duration_sec": duration_sec,
        })

        def restore():
            time.sleep(duration_sec)
            for intf in node.intfList():
                if intf.name == "lo":
                    continue
                node.cmd(
                    f"tc qdisc change dev {intf.name} root netem "
                    f"delay 0ms 0ms 25% loss 0% 25%"
                )

        threading.Thread(target=restore, daemon=True).start()

    def _log_event(self, event_type: str, data: dict):
        """Impairment hodisasini JSONL ga yozish."""
        entry = {
            "ts": time.time(),
            "event": event_type,
            **data,
        }
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

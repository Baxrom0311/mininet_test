"""
ONOS SDN Controller boshqaruvi.
Docker orqali ishga tushirish, API orqali monitoring.
"""

import json
import subprocess
import sys
import time

import requests
from requests.auth import HTTPBasicAuth

from realistic_internet.config import (
    ONOS_IMAGE, ONOS_CONTAINER, ONOS_API,
    ONOS_USER, ONOS_PASS, ONOS_APPS,
)


class ONOSManager:
    """ONOS controller lifecycle + REST API."""

    def __init__(self):
        self._auth = HTTPBasicAuth(ONOS_USER, ONOS_PASS)
        self._session = requests.Session()
        self._session.auth = self._auth
        self._session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    # ── Lifecycle ─────────────────────────────────────────

    def start(self):
        """ONOS Docker containerni ishga tushirish."""
        print("[ONOS] Container tekshirilmoqda...")

        # Agar allaqachon ishlayotgan bo'lsa
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", ONOS_CONTAINER],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and "true" in result.stdout:
            print("[ONOS] Allaqachon ishlayapti")
            return

        # Eski containerni o'chirish
        subprocess.run(
            ["docker", "rm", "-f", ONOS_CONTAINER],
            capture_output=True,
        )

        print(f"[ONOS] Ishga tushirilmoqda ({ONOS_IMAGE})...")
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", ONOS_CONTAINER,
                "--net=host",
                "--restart=unless-stopped",
                ONOS_IMAGE,
            ],
            check=True,
        )
        print("[ONOS] Container ishga tushdi")

    def stop(self):
        """ONOS Docker containerni to'xtatish."""
        print("[ONOS] To'xtatilmoqda...")
        subprocess.run(["docker", "stop", ONOS_CONTAINER], capture_output=True)
        subprocess.run(["docker", "rm", "-f", ONOS_CONTAINER], capture_output=True)
        print("[ONOS] To'xtatildi")

    def wait_ready(self, timeout=120):
        """ONOS API tayyor bo'lguncha kutish."""
        print(f"[ONOS] API tayyor bo'lishi kutilmoqda (max {timeout}s)...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self._session.get(f"{ONOS_API}/applications", timeout=5)
                if r.status_code == 200:
                    print("[ONOS] API tayyor!")
                    return True
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(3)

        print("[ONOS] XATO: API tayyor bo'lmadi!", file=sys.stderr)
        return False

    def activate_apps(self):
        """Kerakli ONOS ilovalarni aktivatsiya qilish."""
        print("[ONOS] Ilovalar aktivatsiya qilinmoqda...")
        for app_name in ONOS_APPS:
            try:
                r = self._session.post(
                    f"{ONOS_API}/applications/{app_name}/active",
                    timeout=10,
                )
                status = "OK" if r.status_code in (200, 409) else f"XATO ({r.status_code})"
                print(f"  {app_name}: {status}")
            except requests.RequestException as e:
                print(f"  {app_name}: XATO ({e})")
        time.sleep(5)  # Ilovalar yuklanishi uchun

    def wait_topology(self, expected_devices=24, expected_links=20, timeout=120):
        """ONOS topologiyani aniqlashini kutish."""
        print(f"[ONOS] Topologiya kutilmoqda ({expected_devices} device, >={expected_links} link)...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                devices = self.get_devices()
                links = self.get_links()
                d_count = len(devices)
                l_count = len(links)
                print(f"  Topildi: {d_count}/{expected_devices} device, {l_count} link", end="\r")
                if d_count >= expected_devices and l_count >= expected_links:
                    print(f"\n[ONOS] Topologiya tayyor: {d_count} device, {l_count} link")
                    return True
            except requests.RequestException:
                pass
            time.sleep(3)

        print(f"\n[ONOS] OGOHLANTIRISH: To'liq topologiya aniqlanmadi")
        return False

    # ── API: Topology ─────────────────────────────────────

    def get_devices(self) -> list:
        """Barcha switchlarni olish."""
        r = self._session.get(f"{ONOS_API}/devices", timeout=10)
        r.raise_for_status()
        return r.json().get("devices", [])

    def get_links(self) -> list:
        """Barcha linklarni olish."""
        r = self._session.get(f"{ONOS_API}/links", timeout=10)
        r.raise_for_status()
        return r.json().get("links", [])

    def get_hosts(self) -> list:
        """Barcha hostlarni olish."""
        r = self._session.get(f"{ONOS_API}/hosts", timeout=10)
        r.raise_for_status()
        return r.json().get("hosts", [])

    def get_paths(self, src_mac: str, dst_mac: str) -> list:
        """Ikki host orasidagi yo'llarni olish."""
        try:
            r = self._session.get(
                f"{ONOS_API}/paths/{src_mac}/{dst_mac}",
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("paths", [])
        except requests.RequestException:
            return []

    # ── API: Statistics ───────────────────────────────────

    def get_flow_stats(self, device_id: str) -> list:
        """Bitta switch uchun flow statistikasi."""
        try:
            r = self._session.get(
                f"{ONOS_API}/flows/{device_id}",
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("flows", [])
        except requests.RequestException:
            return []

    def get_all_flow_stats(self) -> dict:
        """Barcha switchlar uchun flow statistikasi."""
        result = {}
        for device in self.get_devices():
            dev_id = device["id"]
            result[dev_id] = self.get_flow_stats(dev_id)
        return result

    def get_port_stats(self, device_id: str) -> list:
        """Bitta switch uchun port statistikasi."""
        try:
            r = self._session.get(
                f"{ONOS_API}/statistics/ports/{device_id}",
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("statistics", [{}])[0].get("ports", [])
        except (requests.RequestException, IndexError, KeyError):
            return []

    def get_all_port_stats(self) -> dict:
        """Barcha switchlar uchun port statistikasi."""
        result = {}
        for device in self.get_devices():
            dev_id = device["id"]
            result[dev_id] = self.get_port_stats(dev_id)
        return result

    def get_topology_summary(self) -> dict:
        """Topologiya umumiy ma'lumoti."""
        try:
            r = self._session.get(f"{ONOS_API}/topology", timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            return {}

    # ── API: Configuration ────────────────────────────────

    def set_fwd_config(self, packet_out_only=False):
        """Reactive forwarding sozlamalari."""
        config = {"packetOutOnly": packet_out_only}
        try:
            self._session.post(
                f"{ONOS_API}/configuration/org.onosproject.fwd.ReactiveForwarding",
                json=config,
                timeout=10,
            )
        except requests.RequestException:
            pass

    def get_active_apps(self) -> list:
        """Aktivlashtirilgan ilovalar ro'yxati."""
        try:
            r = self._session.get(f"{ONOS_API}/applications", timeout=10)
            r.raise_for_status()
            apps = r.json().get("applications", [])
            return [a["name"] for a in apps if a.get("state") == "ACTIVE"]
        except requests.RequestException:
            return []

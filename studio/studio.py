#!/usr/bin/env python3
"""Network Studio — pywebview desktop ilova (alohida oyna, brauzer emas).

Cisco DNA Center uslubidagi vizual boshqaruv: topologiyani vizual qurish,
simulyatsiyani (WSL/Ubuntu serverda, SSH orqali) ishga tushirish, yig'ilgan
ma'lumotni jonli ko'rish va tahlil qilish — hammasi bitta oynada, glass/neon
dizaynda, kun-tun mavzu bilan.

Ishga tushirish:
    python3 studio/studio.py

Backend (Python) barcha mavjud repo modullarini (routing, topologies,
analytics.data va h.k.) qayta ishlatadi. Frontend web/ ichida (HTML/CSS/JS).
JS <-> Python ko'prigi: window.pywebview.api.<method>().
"""

import os
import sys

# Repo tub papkasini import yo'liga qo'shish — routing/topologies/analytics
# kabi tub darajadagi modullarni studio/ ichidan import qilish uchun.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import webview

# Ham `python3 -m studio.studio` (repo tubidan), ham `python3 studio/studio.py`
# usullarida ishlashi uchun ikki xil import yo'li.
try:
    from studio.api import Api
except ModuleNotFoundError:
    from api import Api

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def main():
    api = Api(repo_root=_REPO_ROOT)
    window = webview.create_window(
        title="Network Studio",
        url=os.path.join(_WEB_DIR, "index.html"),
        js_api=api,
        width=1360,
        height=880,
        min_size=(1024, 680),
        background_color="#0B0F1A",
        text_select=False,
    )
    api.set_window(window)
    # gui=None -> platforma standarti (macOS: WebKit/Cocoa). debug=True ->
    # o'ng-klik "Inspect Element" (dev-tools) ochiladi.
    webview.start(debug=True)


if __name__ == "__main__":
    main()

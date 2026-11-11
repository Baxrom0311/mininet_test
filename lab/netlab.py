#!/usr/bin/env python3
"""NetLab — Mininet asosidagi vizual tarmoq laboratoriyasi (ishga tushirish nuqtasi).

    python3 -m lab.netlab                         # demo driver, http://127.0.0.1:8080
    sudo python3 -m lab.netlab --driver real      # Mininet/OVS/FRR/BMv2
    sudo python3 -m lab.netlab --driver real --host 0.0.0.0   # tarmoqqa ochiq (token bilan)
    python3 -m lab.netlab --window                # pywebview oynasida ochish
"""

import argparse
import os
import secrets
import sys
import threading
import webbrowser

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def main():
    ap = argparse.ArgumentParser(description="NetLab — vizual tarmoq laboratoriyasi")
    ap.add_argument("--driver", choices=["demo", "real"], default=None,
                    help="real = Mininet (root kerak); demo = simulyatsiya. "
                         "Standart: root bo'lsa va Mininet bo'lsa real, aks holda demo")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--token", default=None,
                    help="Kirish tokeni. Standart: avtomatik yaratiladi")
    ap.add_argument("--no-token", action="store_true",
                    help="Token talab qilmaslik (to'g'ridan-to'g'ri kirish)")
    ap.add_argument("--open", action="store_true", help="brauzerda ochish")
    ap.add_argument("--window", action="store_true", help="pywebview oynasida ochish (studio kabi)")
    args = ap.parse_args()

    driver = args.driver
    if driver is None:
        try:
            import mininet  # noqa: F401
            driver = "real" if hasattr(os, "geteuid") and os.geteuid() == 0 else "demo"
        except ImportError:
            driver = "demo"
    if driver == "real" and (not hasattr(os, "geteuid") or os.geteuid() != 0):
        print("XATO: real driver root talab qiladi: sudo python3 -m lab.netlab --driver real")
        sys.exit(1)

    if args.no_token or args.token == "":
        token = None
    else:
        token = args.token or secrets.token_urlsafe(18)

    import uvicorn
    from lab.server import create_app
    app = create_app(driver, token)

    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{shown_host}:{args.port}/" + (f"?token={token}" if token else "")
    print("=" * 60)
    print(f"  NetLab  |  driver: {driver}")
    print(f"  UI:     {url}")
    if token:
        print("  (token bilan himoyalangan — URL'ni boshqalarga bermang)")
    print("=" * 60)

    if args.window:
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        import webview
        webview.create_window("NetLab", url, width=1440, height=900, min_size=(1100, 700),
                              background_color="#070A12")
        webview.start()
        server.should_exit = True
        return
    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

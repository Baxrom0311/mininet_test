#!/usr/bin/env python3
"""Desktop analitika ilovasi -- tugagan simulyatsiya run'laridan qolgan
CSV dataset'larni (results/combined/ yoki results/five_as_<mode>/datasets/)
DuckDB orqali so'rab, Tkinter oynasida vizual tahlil qiladi.

Simulyatorning o'zidan (light_simulation.py va modullaridan) butunlay
mustaqil, qo'shimcha vosita -- ularga hech qanday o'zgarish kiritmaydi.

Ishlatish:
    python3 analytics/app.py
    python3 analytics/app.py --data results/five_as_ospf/datasets
"""

import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# repo tub papkasini sys.path'ga qo'shish -- topologies.py/config.py kabi
# tub darajadagi modullarni import qilish uchun (analytics/ alohida paket).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from analytics.data import DataStore
from analytics.panels.anomaly_panel import AnomalyPanel
from analytics.panels.dns_panel import DNSPanel
from analytics.panels.overview import OverviewPanel
from analytics.panels.qos_panel import QoSPanel
from analytics.panels.query_panel import QueryPanel
from analytics.panels.routing_compare import RoutingComparePanel
from analytics.panels.topology_builder import TopologyBuilderPanel
from analytics.panels.topology_panel import TopologyPanel

DEFAULT_DATA_DIR = os.path.join(_REPO_ROOT, "results", "combined")


class AnalyticsApp(tk.Tk):
    def __init__(self, data_dir):
        super().__init__()
        self.title("Internet Simulation -- Analitika")
        self.geometry("1000x700")

        self.store = DataStore(data_dir)

        self._build_menu()
        self._build_tabs()

    def _build_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Papka ochish...", command=self.open_folder)
        file_menu.add_command(label="Yangilash", command=self.refresh_all)
        file_menu.add_separator()
        file_menu.add_command(label="Chiqish", command=self.destroy)
        menubar.add_cascade(label="Fayl", menu=file_menu)
        self.config(menu=menubar)

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        self.panels = []
        for label, cls in [
            ("Xulosa", OverviewPanel),
            ("Topologiya", TopologyPanel),
            ("Topologiya qurish", TopologyBuilderPanel),
            ("Routing solishtiruvi", RoutingComparePanel),
            ("Anomaliyalar", AnomalyPanel),
            ("DNS", DNSPanel),
            ("QoS/Impairments", QoSPanel),
            ("SQL so'rov", QueryPanel),
        ]:
            panel = cls(self.notebook, self.store)
            self.notebook.add(panel, text=label)
            self.panels.append(panel)

    def open_folder(self):
        path = filedialog.askdirectory(initialdir=os.path.join(_REPO_ROOT, "results"),
                                        title="Dataset papkasini tanlang")
        if not path:
            return
        try:
            self.store.set_data_dir(path)
        except Exception as e:
            messagebox.showerror("Xato", f"Papkani ochib bo'lmadi:\n{e}")
            return
        self.refresh_all()

    def refresh_all(self):
        for panel in self.panels:
            if hasattr(panel, "refresh"):
                panel.refresh()


def main():
    parser = argparse.ArgumentParser(description="Simulyatsiya dataset analitika ilovasi")
    parser.add_argument("--data", default=DEFAULT_DATA_DIR,
                         help="Dataset papkasi (default: results/combined)")
    args = parser.parse_args()

    if not os.path.isdir(args.data):
        print(f"XATO: papka topilmadi: {args.data}")
        sys.exit(1)

    app = AnalyticsApp(args.data)
    app.mainloop()


if __name__ == "__main__":
    main()

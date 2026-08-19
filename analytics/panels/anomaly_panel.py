"""Hujum hodisalari: turlar taqsimoti + kunlik (diurnal) vaqt bo'yicha
tarqalishi (anomaly_events.csv)."""

from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


class AnomalyPanel(ttk.Frame):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store

        ttk.Label(self, text="Anomaliya/hujum hodisalari (anomaly_events.csv)",
                  font=("", 14, "bold")).pack(anchor="w", padx=12, pady=(12, 4))

        self.summary = ttk.Label(self, foreground="#666")
        self.summary.pack(anchor="w", padx=12)

        self.figure = plt.Figure(figsize=(9, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.refresh()

    def refresh(self):
        self.figure.clear()
        if not self.store.has("anomaly_events"):
            self.summary.config(text="anomaly_events.csv topilmadi")
            self.canvas.draw()
            return

        df = self.store.query_df("SELECT type, severity, sim_hour FROM anomaly_events")
        self.summary.config(text=f"Jami {len(df):,} hodisa")
        if df.empty:
            self.canvas.draw()
            return

        ax1 = self.figure.add_subplot(121)
        type_counts = df["type"].value_counts().sort_values()
        ax1.barh(type_counts.index, type_counts.values, color="#E74C3C")
        ax1.set_title("Hujum turi bo'yicha soni")

        ax2 = self.figure.add_subplot(122)
        ax2.hist(df["sim_hour"].dropna(), bins=24, range=(0, 24), color="#9B59B6")
        ax2.set_xlabel("Simulyatsiya soati (0-24)")
        ax2.set_title("Kunlik (diurnal) tarqalish")

        self.figure.tight_layout()
        self.canvas.draw()

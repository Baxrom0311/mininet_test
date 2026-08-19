"""QoS/DiffServ: DSCP/navbat bandi taqsimoti (traffic_log.csv) va tarmoq
nosozliklari turlari (impairments.csv)."""

from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

BAND_NAMES = {0: "EF (real-vaqt)", 1: "AF21 (interaktiv)", 2: "BE/CS1 (fon)"}


class QoSPanel(ttk.Frame):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store

        ttk.Label(self, text="QoS/DiffServ va tarmoq nosozliklari",
                  font=("", 14, "bold")).pack(anchor="w", padx=12, pady=(12, 4))

        self.figure = plt.Figure(figsize=(9, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.refresh()

    def refresh(self):
        self.figure.clear()
        ax1 = self.figure.add_subplot(121)
        if self.store.has("traffic_log"):
            df = self.store.query_df(
                "SELECT queue_band FROM traffic_log WHERE queue_band IS NOT NULL"
            )
            if not df.empty:
                counts = df["queue_band"].value_counts().sort_index()
                labels = [BAND_NAMES.get(int(b), str(b)) for b in counts.index]
                ax1.bar(labels, counts.values, color=["#E74C3C", "#F39C12", "#4A90D9"][:len(labels)])
                ax1.set_title("Trafik: QoS bandi bo'yicha")
                ax1.tick_params(axis="x", labelrotation=15)
            else:
                ax1.text(0.5, 0.5, "queue_band ma'lumoti yo'q", ha="center", va="center")
        else:
            ax1.text(0.5, 0.5, "traffic_log.csv topilmadi", ha="center", va="center")

        ax2 = self.figure.add_subplot(122)
        if self.store.has("impairments"):
            df = self.store.query_df("SELECT event FROM impairments")
            if not df.empty:
                counts = df["event"].value_counts().sort_values()
                ax2.barh(counts.index, counts.values, color="#9B59B6")
                ax2.set_title("Nosozlik turi bo'yicha soni")
            else:
                ax2.text(0.5, 0.5, "impairments bo'sh", ha="center", va="center")
        else:
            ax2.text(0.5, 0.5, "impairments.csv topilmadi", ha="center", va="center")

        self.figure.tight_layout()
        self.canvas.draw()

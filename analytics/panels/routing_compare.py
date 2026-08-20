"""11 routing rejimini RTT/hop/bandwidth bo'yicha solishtiruvchi grafiklar --
bu sessiyada tuzatilgan yo'l xilma-xilligini vizual ko'rsatadi."""

from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


class RoutingComparePanel(ttk.Frame):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store

        ttk.Label(self, text="Routing rejimlari solishtiruvi (path_traces.csv)",
                  font=("", 14, "bold")).pack(anchor="w", padx=12, pady=(12, 4))

        self.figure = plt.Figure(figsize=(9, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.refresh()

    def refresh(self):
        self.figure.clear()
        try:
            self._draw()
        except Exception as e:
            import traceback
            traceback.print_exc()
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"Xato: {e}", ha="center", va="center", wrap=True, color="#c0392b")
            ax.axis("off")
        self.canvas.draw()

    def _draw(self):
        if not self.store.has("path_traces"):
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, "path_traces.csv topilmadi", ha="center", va="center")
            return

        df = self.store.query_df(
            "SELECT routing, real_rtt_ms, num_switch_hops, bottleneck_bw_mbps "
            "FROM path_traces WHERE real_rtt_ms IS NOT NULL"
        )
        if df.empty:
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, "real_rtt_ms bo'lgan qator topilmadi", ha="center", va="center")
            return

        modes = sorted(df["routing"].unique())
        ax1 = self.figure.add_subplot(121)
        avg_rtt = df.groupby("routing")["real_rtt_ms"].mean().reindex(modes)
        ax1.barh(modes, avg_rtt.values, color="#4A90D9")
        ax1.set_xlabel("O'rtacha real RTT (ms)")
        ax1.set_title("Rejim bo'yicha RTT")
        ax1.invert_yaxis()

        ax2 = self.figure.add_subplot(122)
        box_data = [df.loc[df["routing"] == m, "real_rtt_ms"].values for m in modes]
        # matplotlib 3.9+ `labels=`ni deprecate qildi (3.11'da olib tashlangan),
        # o'rniga `tick_labels=`. Eski/yangi ikkalasida ishlashi uchun fallback.
        try:
            ax2.boxplot(box_data, vert=False, tick_labels=modes, showfliers=False)
        except TypeError:
            ax2.boxplot(box_data, vert=False, labels=modes, showfliers=False)
        ax2.set_xlabel("RTT taqsimoti (ms)")
        ax2.set_title("RTT tarqalishi (box-plot)")

        self.figure.tight_layout()

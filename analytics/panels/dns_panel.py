"""DNS ko'p bosqichli rezolyutsiya: root/TLD/authoritative/cache bosqichlari
kechikish taqsimoti (dns_queries.csv)."""

from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

STAGE_ORDER = ["cache", "root", "tld", "authoritative"]


class DNSPanel(ttk.Frame):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store

        ttk.Label(self, text="DNS rezolyutsiya bosqichlari (dns_queries.csv)",
                  font=("", 14, "bold")).pack(anchor="w", padx=12, pady=(12, 4))

        self.summary = ttk.Label(self, foreground="#666")
        self.summary.pack(anchor="w", padx=12)

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
            self.summary.config(text=f"Xato: {e}")
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"Xato: {e}", ha="center", va="center", wrap=True, color="#c0392b")
            ax.axis("off")
        self.canvas.draw()

    def _draw(self):
        if not self.store.has("dns_queries"):
            self.summary.config(text="dns_queries.csv topilmadi")
            return

        df = self.store.query_df("SELECT stage, response_time_ms, cache_hit FROM dns_queries")
        self.summary.config(text=f"Jami {len(df):,} so'rov bosqichi")
        if df.empty:
            return

        stages = [s for s in STAGE_ORDER if s in df["stage"].unique()]
        ax1 = self.figure.add_subplot(121)
        box_data = [df.loc[df["stage"] == s, "response_time_ms"].dropna().values for s in stages]
        ax1.boxplot(box_data, labels=stages, showfliers=False)
        ax1.set_ylabel("Javob vaqti (ms)")
        ax1.set_title("Bosqich bo'yicha kechikish")

        ax2 = self.figure.add_subplot(122)
        cache_counts = df["cache_hit"].value_counts()
        labels = ["Kesh urishi" if v else "Tarmoq so'rovi" for v in cache_counts.index]
        ax2.pie(cache_counts.values, labels=labels, autopct="%1.0f%%",
                colors=["#2ECC71", "#F39C12"])
        ax2.set_title("Kesh nisbati")

        self.figure.tight_layout()

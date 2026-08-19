"""Erkin SQL so'rov oynasi -- DuckDB'ga to'g'ridan-to'g'ri so'rov yuboradi.
"Kuchli" xususiyat: har qanday dataset ustida ixtiyoriy SQL yozish mumkin."""

import tkinter as tk
from tkinter import ttk

SAMPLE_QUERY = "SELECT routing, avg(real_rtt_ms) AS avg_rtt_ms\nFROM path_traces\nGROUP BY routing\nORDER BY avg_rtt_ms;"


class QueryPanel(ttk.Frame):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store

        ttk.Label(self, text="Erkin SQL so'rov (DuckDB)", font=("", 14, "bold")).pack(
            anchor="w", padx=12, pady=(12, 4))
        ttk.Label(self, text="Mavjud jadvallar: " + ", ".join(store.available),
                  foreground="#666", wraplength=760).pack(anchor="w", padx=12)

        self.text = tk.Text(self, height=6, font=("Menlo", 11))
        self.text.insert("1.0", SAMPLE_QUERY)
        self.text.pack(fill="x", padx=12, pady=8)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=12)
        ttk.Button(btn_row, text="Ishga tushirish (Cmd+Enter)", command=self.run_query).pack(side="left")
        self.status = ttk.Label(btn_row, foreground="#c0392b")
        self.status.pack(side="left", padx=12)
        self.text.bind("<Command-Return>", lambda e: self.run_query())
        self.text.bind("<Control-Return>", lambda e: self.run_query())

        self.tree = ttk.Treeview(self, show="headings")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(8, 12))

    def run_query(self):
        sql = self.text.get("1.0", "end").strip()
        if not sql:
            return
        for row in self.tree.get_children():
            self.tree.delete(row)
        try:
            df = self.store.query_df(sql)
        except Exception as e:
            self.status.config(text=f"Xato: {e}")
            self.tree["columns"] = ()
            return

        self.status.config(text=f"{len(df):,} qator qaytdi")
        cols = list(df.columns)
        self.tree["columns"] = cols
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=120, anchor="w")
        for _, row in df.head(500).iterrows():
            self.tree.insert("", "end", values=list(row))

    def refresh(self):
        pass  # so'rov natijasi faqat "Ishga tushirish" bosilganda yangilanadi

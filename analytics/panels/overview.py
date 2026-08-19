"""Umumiy statistika: har dataset necha qator, routing rejimlar taqsimoti."""

import tkinter as tk
from tkinter import ttk


class OverviewPanel(ttk.Frame):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store

        header = ttk.Label(self, text="Dataset xulosasi", font=("", 14, "bold"))
        header.pack(anchor="w", padx=12, pady=(12, 4))

        self.path_label = ttk.Label(self, foreground="#666")
        self.path_label.pack(anchor="w", padx=12, pady=(0, 8))

        columns = ("dataset", "rows")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=14)
        self.tree.heading("dataset", text="Dataset")
        self.tree.heading("rows", text="Qator soni")
        self.tree.column("dataset", width=220, anchor="w")
        self.tree.column("rows", width=120, anchor="e")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        modes_label = ttk.Label(self, text="Routing rejimlar:", font=("", 10, "bold"))
        modes_label.pack(anchor="w", padx=12)
        self.modes_value = ttk.Label(self, wraplength=700, justify="left")
        self.modes_value.pack(anchor="w", padx=12, pady=(0, 12))

        self.refresh()

    def refresh(self):
        self.path_label.config(text=f"Papka: {self.store.data_dir}")
        for row in self.tree.get_children():
            self.tree.delete(row)
        counts = self.store.row_counts()
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            self.tree.insert("", "end", values=(name, f"{n:,}"))
        modes = self.store.routing_modes()
        self.modes_value.config(text=", ".join(modes) if modes else "(topilmadi)")

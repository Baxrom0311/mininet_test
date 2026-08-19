"""Topologiya grafigi -- visualize.py'dagi networkx graf qurish mantig'ini
qayta ishlatadi (o'sha faylga tegilmaydi), lekin PNG saqlash o'rniga
to'g'ridan-to'g'ri Tkinter oynasiga chizadi."""

import tkinter as tk
from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import topologies

COLOR_PALETTE = ["#4A90D9", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6",
                  "#1ABC9C", "#E67E22", "#3498DB"]


def _build_graph(topo):
    """visualize.visualize_topology()dagi graf qurish qismi bilan bir xil
    mantiq (switch/host node'lar + backbone/access edge'lar)."""
    G = nx.Graph()
    for sw, info in topo["switches"].items():
        G.add_node(sw, node_type="switch", as_num=info["as"], role=info["role"])
    for h, info in topo["hosts"].items():
        G.add_node(h, node_type="host", role=info["role"], ip=info["ip"])
    for (a, b), params in topo["links"].items():
        G.add_edge(a, b, bw=params["bw"], link_type="backbone")
    for h, info in topo["hosts"].items():
        sw = info["switch"]
        al = topo["access_links"].get(h, {})
        G.add_edge(h, sw, bw=al.get("bw", 10), link_type="access")
    return G


class TopologyPanel(ttk.Frame):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(controls, text="Topologiya:").pack(side="left")
        self.topo_var = tk.StringVar(value="five_as")
        combo = ttk.Combobox(controls, textvariable=self.topo_var,
                              values=list(topologies.TOPOLOGIES.keys()), state="readonly", width=15)
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda e: self.draw())

        self.figure = plt.Figure(figsize=(8, 6), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.draw()

    def draw(self):
        topo = topologies.TOPOLOGIES[self.topo_var.get()]
        G = _build_graph(topo)
        as_numbers = sorted(set(s["as"] for s in topo["switches"].values()))
        as_colors = {asn: COLOR_PALETTE[i % len(COLOR_PALETTE)] for i, asn in enumerate(as_numbers)}

        self.ax.clear()
        pos = nx.kamada_kawai_layout(G, scale=2.0)
        switch_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "switch"]
        host_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "host"]
        backbone_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("link_type") == "backbone"]
        access_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("link_type") == "access"]

        nx.draw_networkx_edges(G, pos, edgelist=backbone_edges, width=2, edge_color="#999",
                                ax=self.ax)
        nx.draw_networkx_edges(G, pos, edgelist=access_edges, width=0.8, edge_color="#ccc",
                                style="dashed", ax=self.ax)
        sw_colors = [as_colors[G.nodes[n]["as_num"]] for n in switch_nodes]
        nx.draw_networkx_nodes(G, pos, nodelist=switch_nodes, node_size=500,
                                node_color=sw_colors, node_shape="s", ax=self.ax)
        nx.draw_networkx_nodes(G, pos, nodelist=host_nodes, node_size=250,
                                node_color="#bbb", node_shape="o", ax=self.ax)
        nx.draw_networkx_labels(G, pos, font_size=7, ax=self.ax)
        self.ax.set_title(f"{self.topo_var.get()} -- {len(topo['switches'])} switch, "
                           f"{len(topo['hosts'])} host, {len(as_numbers)} AS")
        self.ax.axis("off")
        self.canvas.draw()

    def refresh(self):
        pass  # topologiya papka tanlashga bog'liq emas

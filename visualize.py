"""Topologiyani PNG rasm sifatida vizualizatsiya qilish (matplotlib/networkx,
sudo shart emas)."""

import os
import sys

from config import DATA_DIR


def visualize_topology(topo, topo_name, routing):
    """Topologiyani PNG rasm sifatida generatsiya qiladi."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import networkx as nx
    except ImportError:
        print("Kerakli kutubxonalar yo'q. O'rnating:")
        print("  pip install matplotlib networkx")
        sys.exit(1)

    G = nx.Graph()

    # AS ranglar
    as_numbers = sorted(set(s["as"] for s in topo["switches"].values()))
    color_palette = ["#4A90D9", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6",
                     "#1ABC9C", "#E67E22", "#3498DB"]
    as_colors = {asn: color_palette[i % len(color_palette)] for i, asn in enumerate(as_numbers)}

    # Switch nodelar
    for sw, info in topo["switches"].items():
        G.add_node(sw, node_type="switch", as_num=info["as"], role=info["role"])

    # Host nodelar
    for h, info in topo["hosts"].items():
        G.add_node(h, node_type="host", role=info["role"], ip=info["ip"])

    # Switch-switch linklar
    for (a, b), params in topo["links"].items():
        G.add_edge(a, b, bw=params["bw"], delay=params["delay"],
                   loss=params.get("loss", 0), link_type="backbone")

    # Host-switch linklar
    for h, info in topo["hosts"].items():
        sw = info["switch"]
        al = topo["access_links"].get(h, {})
        G.add_edge(h, sw, bw=al.get("bw", 10), delay=al.get("delay", "1ms"),
                   link_type="access")

    # Layout
    fig, ax = plt.subplots(1, 1, figsize=(16, 12))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    # Kamoshima layout
    pos = nx.kamada_kawai_layout(G, scale=2.5)

    # AS bo'yicha guruhlab, switchlarni grouplab chizish
    switch_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "switch"]
    server_nodes = [n for n, d in G.nodes(data=True) if d.get("role") == "server"]
    client_nodes = [n for n, d in G.nodes(data=True) if d.get("role") == "client"]

    # Backbone linklar (qalin)
    backbone_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("link_type") == "backbone"]
    access_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("link_type") == "access"]

    # Linklar bw bo'yicha qalinligi
    backbone_widths = []
    for u, v in backbone_edges:
        bw = G[u][v].get("bw", 10)
        backbone_widths.append(max(1.5, bw / 15))

    nx.draw_networkx_edges(G, pos, edgelist=backbone_edges, width=backbone_widths,
                           edge_color="#FFD700", alpha=0.7, style="solid", ax=ax)
    nx.draw_networkx_edges(G, pos, edgelist=access_edges, width=0.8,
                           edge_color="#666666", alpha=0.5, style="dashed", ax=ax)

    # Switch nodelar - AS ranglar
    sw_colors = [as_colors[G.nodes[n]["as_num"]] for n in switch_nodes]
    nx.draw_networkx_nodes(G, pos, nodelist=switch_nodes, node_size=900,
                           node_color=sw_colors, node_shape="s",
                           edgecolors="white", linewidths=2, ax=ax)

    # Server nodelar
    nx.draw_networkx_nodes(G, pos, nodelist=server_nodes, node_size=500,
                           node_color="#00E676", node_shape="^",
                           edgecolors="white", linewidths=1.5, ax=ax)

    # Client nodelar
    nx.draw_networkx_nodes(G, pos, nodelist=client_nodes, node_size=400,
                           node_color="#FF5252", node_shape="o",
                           edgecolors="white", linewidths=1.5, ax=ax)

    # Labellar
    nx.draw_networkx_labels(G, pos, font_size=7, font_color="white",
                            font_weight="bold", ax=ax)

    # Link labellari (bw + delay)
    edge_labels = {}
    for u, v, d in G.edges(data=True):
        if d.get("link_type") == "backbone":
            edge_labels[(u, v)] = f"{d['bw']}Mb\n{d['delay']}"
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels,
                                 font_size=5, font_color="#AAAAAA",
                                 bbox=dict(boxstyle="round,pad=0.1",
                                           facecolor="#1a1a2e", edgecolor="none"),
                                 ax=ax)

    # Legend
    legend_items = []
    for asn in as_numbers:
        legend_items.append(mpatches.Patch(color=as_colors[asn], label=f"AS {asn}"))
    legend_items.append(plt.Line2D([0], [0], marker="^", color="w", markerfacecolor="#00E676",
                                   markersize=10, linestyle="None", label="Server"))
    legend_items.append(plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#FF5252",
                                   markersize=10, linestyle="None", label="Client"))
    legend_items.append(plt.Line2D([0], [0], color="#FFD700", linewidth=2, label="Backbone link"))
    legend_items.append(plt.Line2D([0], [0], color="#666666", linewidth=1,
                                   linestyle="--", label="Access link"))
    ax.legend(handles=legend_items, loc="upper left", fontsize=8,
              facecolor="#2a2a4e", edgecolor="#444", labelcolor="white")

    # Title
    sw_count = len(topo["switches"])
    host_count = len(topo["hosts"])
    link_count = len(topo["links"])
    ax.set_title(f"Topology: {topo_name.upper()}  |  Routing: {routing.upper()}\n"
                 f"{sw_count} switches, {host_count} hosts, {link_count} backbone links, "
                 f"{len(as_numbers)} AS",
                 fontsize=14, fontweight="bold", color="white", pad=20)

    ax.axis("off")
    plt.tight_layout()

    # Docker ichida /data ga, aks holda joriy papkaga saqlash
    out_dir = DATA_DIR if os.path.isdir(DATA_DIR) else "."
    out_path = f"{out_dir}/{topo_name}_topology.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()
    print(f"Topologiya rasmi saqlandi: {out_path}")
    print(f"  Switchlar: {sw_count}")
    print(f"  Hostlar:   {host_count}")
    print(f"  Linklar:   {link_count}")
    print(f"  AS lar:    {len(as_numbers)}")

    # Text version ham chiqaramiz
    print(f"\n{'='*60}")
    print(f"  {topo_name.upper()} TOPOLOGY MAP")
    print(f"{'='*60}")
    for asn in as_numbers:
        print(f"\n  AS {asn}:")
        for sw, info in topo["switches"].items():
            if info["as"] == asn:
                hosts_on_sw = [h for h, hi in topo["hosts"].items() if hi["switch"] == sw]
                print(f"    [{sw}] ({info['role']})")
                for h in hosts_on_sw:
                    hi = topo["hosts"][h]
                    print(f"       └── {h} ({hi['ip']}) [{hi['role']}]")
    print(f"\n  Backbone linklar:")
    for (a, b), params in topo["links"].items():
        print(f"    {a} ←──({params['bw']}Mb, {params['delay']})──→ {b}")
    print(f"{'='*60}")

"""Topologiya quruvchi -- sichqoncha bilan switch/host qo'shib, ularni ulab,
yangi topologiya qurish va real simulyatsiyada ishlatsa bo'ladigan holda
saqlash. `topologies.py`ning `_load_custom_topologies()` funksiyasi bu yerda
saqlangan JSON'larni avtomatik `TOPOLOGIES`ga qo'shadi -- shu sababli
saqlangan topologiya darhol `--topology <nom>` orqali ishlatiladi.
"""

import json
import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import routing
import topologies

CUSTOM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "custom_topologies")

COLOR_PALETTE = ["#4A90D9", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6",
                  "#1ABC9C", "#E67E22", "#3498DB"]
SWITCH_ROLE_SUGGESTIONS = ["core", "border", "access", "tier1_core", "tier2_border",
                            "agg", "tor", "distribution", "dmz", "isp_gateway"]
HOST_ROLES = ["server", "client", "gateway"]
MODES = ["switch", "host", "link", "edit"]
MODE_LABELS = {"switch": "Switch qo'shish", "host": "Host qo'shish",
               "link": "Link tortish", "edit": "Tahrirlash/O'chirish"}

SWITCH_R = 16   # kvadrat switch node yarim-o'lchami (piksel)
HOST_R = 10     # doira host node radiusi


class SimpleForm(tk.Toplevel):
    """Labellangan Entry/Combobox maydonlari bilan kichik modal forma.
    `fields`: [(key, label, kind, options_or_default), ...]
      kind == "entry"    -> options_or_default = boshlang'ich matn
      kind == "combo"     -> options_or_default = (values_list, default)
    `.result` -- OK bosilsa {key: value}, bekor qilinsa None."""

    def __init__(self, parent, title, fields):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self._vars = {}

        for i, (key, label, kind, spec) in enumerate(fields):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w", padx=8, pady=4)
            if kind == "combo":
                values, default = spec
                var = tk.StringVar(value=default)
                widget = ttk.Combobox(self, textvariable=var, values=values, width=22)
            else:
                var = tk.StringVar(value=spec)
                widget = ttk.Entry(self, textvariable=var, width=25)
            widget.grid(row=i, column=1, padx=8, pady=4)
            self._vars[key] = var

        btn_row = ttk.Frame(self)
        btn_row.grid(row=len(fields), column=0, columnspan=2, pady=8)
        ttk.Button(btn_row, text="OK", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Bekor qilish", command=self.destroy).pack(side="left", padx=4)

        # `parent` odatda ttk.Frame (panelning o'zi), lekin transient/grab
        # o'zining haqiqiy oynasiga (Toplevel/Tk) qarab ishlashi kerak --
        # aks holda macOS'da dialog asosiy oyna ortida yashiringan yoki
        # fokus olmagan holda qolishi mumkin.
        toplevel = parent.winfo_toplevel()
        self.transient(toplevel)
        self.update_idletasks()
        # Ekran markazi atrofida, chaqiruvchi oynaga nisbatan joylashtirish.
        self.geometry(f"+{toplevel.winfo_rootx() + 60}+{toplevel.winfo_rooty() + 60}")
        self.lift()
        self.attributes("-topmost", True)
        self.after(10, lambda: self.attributes("-topmost", False))
        self.focus_force()
        self.grab_set()
        self.wait_window(self)

    def _on_ok(self):
        self.result = {k: v.get() for k, v in self._vars.items()}
        self.destroy()


class TopologyBuilderPanel(ttk.Frame):
    def __init__(self, parent, store=None):
        super().__init__(parent)
        os.makedirs(CUSTOM_DIR, exist_ok=True)

        self.mode = tk.StringVar(value="switch")
        self.switches = {}       # name -> {"as":, "role":, "x":, "y":}
        self.hosts = {}           # name -> {"switch":, "ip":, "role":, "x":, "y":, "access": {...}}
        self.links = {}            # (s1,s2) -> {"bw":,"delay":,"loss":,"jitter":,"queue":}
        self._item_owner = {}       # canvas item id -> ("switch"|"host"|"link", key)
        self._link_pending = None    # link rejimida birinchi bosilgan switch nomi

        self._build_ui()

    # ── UI qurish ──────────────────────────────────────────
    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        for m in MODES:
            ttk.Radiobutton(top, text=MODE_LABELS[m], value=m, variable=self.mode,
                             command=self._on_mode_change).pack(side="left", padx=4)

        self.canvas = tk.Canvas(self, bg="white", highlightthickness=1,
                                 highlightbackground="#ccc")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=4)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=6)
        ttk.Button(bottom, text="Tekshirish", command=self.validate_topology).pack(side="left", padx=4)
        ttk.Button(bottom, text="Saqlash...", command=self.save_topology).pack(side="left", padx=4)
        ttk.Button(bottom, text="Ochish...", command=self.load_topology).pack(side="left", padx=4)
        ttk.Button(bottom, text="Tozalash", command=self.clear_all).pack(side="left", padx=4)
        self.status = ttk.Label(bottom, foreground="#666")
        self.status.pack(side="left", padx=12)

    def _on_mode_change(self):
        self._link_pending = None
        self._set_status("")

    def _set_status(self, text, error=False):
        self.status.config(text=text, foreground="#c0392b" if error else "#2ECC71")

    def refresh(self):
        pass  # bu panel DuckDB store'ga bog'liq emas

    # ── canvas bosish -> rejimga qarab yo'naltirish ──────────
    def _on_canvas_click(self, event):
        mode = self.mode.get()
        if mode == "switch":
            self._add_switch(event.x, event.y)
        elif mode == "host":
            self._add_host(event.x, event.y)
        elif mode == "link":
            self._click_for_link(event.x, event.y)
        elif mode == "edit":
            self._click_for_edit(event.x, event.y)

    def _hit_test(self, x, y, radius=14):
        for item in self.canvas.find_overlapping(x - radius, y - radius, x + radius, y + radius):
            if item in self._item_owner:
                return self._item_owner[item]
        return None

    # ── Switch qo'shish/chizish ───────────────────────────────
    def _add_switch(self, x, y):
        name = simpledialog.askstring("Switch nomi", "Switch nomi (masalan s1):", parent=self.winfo_toplevel())
        if not name:
            return
        if name in self.switches or name in self.hosts:
            messagebox.showerror("Xato", f"'{name}' nomi allaqachon band.")
            return
        form = SimpleForm(self, f"Switch: {name}", [
            ("as", "AS raqami", "entry", "100"),
            ("role", "Rol", "combo", (SWITCH_ROLE_SUGGESTIONS, "core")),
            ("area", "OSPF area (ixtiyoriy)", "entry", ""),
            ("isis_level", "IS-IS level (ixtiyoriy)", "combo", (["", "L1", "L2", "L1L2"], "")),
        ])
        if not form.result:
            return
        try:
            as_num = int(form.result["as"])
        except ValueError:
            messagebox.showerror("Xato", "AS raqami butun son bo'lishi kerak.")
            return
        self.switches[name] = {"as": as_num, "role": form.result["role"] or "core",
                                "x": x, "y": y}
        if form.result["area"].strip():
            try:
                self.switches[name]["area"] = int(form.result["area"])
            except ValueError:
                pass
        if form.result["isis_level"].strip():
            self.switches[name]["isis_level"] = form.result["isis_level"]
        self._draw_switch(name)
        self._set_status(f"Switch '{name}' qo'shildi.")

    def _draw_switch(self, name):
        info = self.switches[name]
        x, y = info["x"], info["y"]
        color = COLOR_PALETTE[hash(info["as"]) % len(COLOR_PALETTE)]
        rect = self.canvas.create_rectangle(x - SWITCH_R, y - SWITCH_R, x + SWITCH_R, y + SWITCH_R,
                                             fill=color, outline="black", width=2)
        label = self.canvas.create_text(x, y - SWITCH_R - 10, text=f"{name} (AS{info['as']})",
                                          font=("", 9, "bold"))
        self._item_owner[rect] = ("switch", name)
        self._item_owner[label] = ("switch", name)
        info["_items"] = (rect, label)

    # ── Host qo'shish/chizish ─────────────────────────────────
    def _add_host(self, x, y):
        if not self.switches:
            messagebox.showerror("Xato", "Avval kamida bitta switch qo'shing.")
            return
        name = simpledialog.askstring("Host nomi", "Host nomi (masalan web1):", parent=self.winfo_toplevel())
        if not name:
            return
        if name in self.switches or name in self.hosts:
            messagebox.showerror("Xato", f"'{name}' nomi allaqachon band.")
            return
        form = SimpleForm(self, f"Host: {name}", [
            ("switch", "Ulanadigan switch", "combo", (list(self.switches.keys()),
                                                        list(self.switches.keys())[0])),
            ("ip", "IP/mask", "entry", "10.0.0.1/8"),
            ("role", "Rol", "combo", (HOST_ROLES, "client")),
            ("bw", "Access bw (Mbps)", "entry", "10"),
            ("delay", "Access delay", "entry", "1ms"),
            ("loss", "Access loss (%)", "entry", "0"),
        ])
        if not form.result:
            return
        sw = form.result["switch"]
        if sw not in self.switches:
            messagebox.showerror("Xato", f"Switch '{sw}' topilmadi.")
            return
        try:
            bw = float(form.result["bw"]); loss = float(form.result["loss"])
        except ValueError:
            messagebox.showerror("Xato", "bw/loss son bo'lishi kerak.")
            return
        self.hosts[name] = {"switch": sw, "ip": form.result["ip"], "role": form.result["role"],
                             "x": x, "y": y,
                             "access": {"bw": bw, "delay": form.result["delay"], "loss": loss}}
        self._draw_host(name)
        self._set_status(f"Host '{name}' qo'shildi ({sw}ga ulandi).")

    def _draw_host(self, name):
        info = self.hosts[name]
        x, y = info["x"], info["y"]
        sw = self.switches.get(info["switch"])
        oval = self.canvas.create_oval(x - HOST_R, y - HOST_R, x + HOST_R, y + HOST_R,
                                         fill="#ddd", outline="black")
        label = self.canvas.create_text(x, y + HOST_R + 10, text=name, font=("", 8))
        items = [oval, label]
        if sw:
            line = self.canvas.create_line(x, y, sw["x"], sw["y"], fill="#bbb", dash=(2, 2))
            self.canvas.tag_lower(line)
            items.append(line)
        self._item_owner[oval] = ("host", name)
        self._item_owner[label] = ("host", name)
        info["_items"] = tuple(items)

    # ── Link tortish/chizish ───────────────────────────────────
    def _click_for_link(self, x, y):
        hit = self._hit_test(x, y)
        if not hit or hit[0] != "switch":
            self._set_status("Link uchun ikkita SWITCH'ni ketma-ket bosing.", error=True)
            return
        _, name = hit
        if self._link_pending is None:
            self._link_pending = name
            self._set_status(f"'{name}' tanlandi -- ikkinchi switch'ni bosing.")
            return
        if self._link_pending == name:
            self._set_status("Bir xil switch'ni ikki marta bosdingiz, bekor qilindi.", error=True)
            self._link_pending = None
            return
        s1, s2 = self._link_pending, name
        self._link_pending = None
        key = (s1, s2)
        if key in self.links or (s2, s1) in self.links:
            messagebox.showerror("Xato", "Bu ikki switch orasida allaqachon link bor.")
            return
        form = SimpleForm(self, f"Link: {s1} <-> {s2}", [
            ("bw", "Bandwidth (Mbps)", "entry", "50"),
            ("delay", "Delay", "entry", "5ms"),
            ("loss", "Loss (%)", "entry", "0.01"),
            ("jitter", "Jitter", "entry", "1ms"),
            ("queue", "Queue size", "entry", "80"),
        ])
        if not form.result:
            return
        try:
            params = {"bw": float(form.result["bw"]), "delay": form.result["delay"],
                       "loss": float(form.result["loss"]), "jitter": form.result["jitter"],
                       "queue": int(form.result["queue"])}
        except ValueError:
            messagebox.showerror("Xato", "bw/loss/queue son bo'lishi kerak.")
            return
        self.links[key] = params
        self._draw_link(key)
        self._set_status(f"Link {s1}<->{s2} qo'shildi.")

    def _draw_link(self, key):
        s1, s2 = key
        p1, p2 = self.switches[s1], self.switches[s2]
        line = self.canvas.create_line(p1["x"], p1["y"], p2["x"], p2["y"],
                                         fill="#4A90D9", width=3)
        self.canvas.tag_lower(line)
        self._item_owner[line] = ("link", key)
        self.links[key]["_item"] = line

    # ── Tahrirlash/O'chirish ────────────────────────────────────
    def _click_for_edit(self, x, y):
        hit = self._hit_test(x, y)
        if not hit:
            return
        kind, key = hit
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="O'chirish", command=lambda: self._delete(kind, key))
        menu.add_command(label="Bekor qilish")
        menu.tk_popup(self.canvas.winfo_pointerx(), self.canvas.winfo_pointery())

    def _delete(self, kind, key):
        if kind == "switch":
            dependent_hosts = [h for h, i in self.hosts.items() if i["switch"] == key]
            dependent_links = [l for l in self.links if key in l]
            if dependent_hosts or dependent_links:
                if not messagebox.askyesno(
                        "Tasdiqlash",
                        f"'{key}' switch'iga {len(dependent_hosts)} host va "
                        f"{len(dependent_links)} link bog'langan -- ular ham o'chiriladi. Davom etamizmi?"):
                    return
            for h in dependent_hosts:
                self._delete("host", h)
            for l in list(dependent_links):
                self._delete("link", l)
            for item in self.switches[key].get("_items", ()):
                self.canvas.delete(item)
            del self.switches[key]
        elif kind == "host":
            for item in self.hosts[key].get("_items", ()):
                self.canvas.delete(item)
            del self.hosts[key]
        elif kind == "link":
            self.canvas.delete(self.links[key]["_item"])
            del self.links[key]
        self._set_status(f"O'chirildi: {kind} {key}")

    def clear_all(self):
        if not (self.switches or self.hosts or self.links):
            return
        if not messagebox.askyesno("Tasdiqlash", "Butun canvas tozalansinmi?"):
            return
        self.canvas.delete("all")
        self.switches.clear(); self.hosts.clear(); self.links.clear()
        self._item_owner.clear(); self._link_pending = None
        self._set_status("Tozalandi.")

    # ── TOPOLOGIES sxemasiga aylantirish ─────────────────────────
    def _to_topo_dict(self):
        return {
            "switches": {n: {"as": i["as"], "role": i["role"],
                              **({"area": i["area"]} if "area" in i else {}),
                              **({"isis_level": i["isis_level"]} if "isis_level" in i else {})}
                         for n, i in self.switches.items()},
            "hosts": {n: {"switch": i["switch"], "ip": i["ip"], "role": i["role"]}
                      for n, i in self.hosts.items()},
            "links": {k: {kk: vv for kk, vv in v.items() if kk != "_item"}
                      for k, v in self.links.items()},
            "access_links": {n: i["access"] for n, i in self.hosts.items()},
            "_positions": {
                **{n: [i["x"], i["y"]] for n, i in self.switches.items()},
                **{n: [i["x"], i["y"]] for n, i in self.hosts.items()},
            },
        }

    # ── Tekshirish / Saqlash / Ochish ─────────────────────────────
    def validate_topology(self):
        if not self.switches:
            self._set_status("Kamida bitta switch kerak.", error=True)
            return None
        topo = self._to_topo_dict()
        try:
            paths = routing.compute_paths(topo, "l2_learn")
        except Exception as e:
            self._set_status(f"Xato: {e}", error=True)
            messagebox.showerror("Validatsiya xatosi", str(e))
            return None
        expected = [(a, b) for a in self.hosts for b in self.hosts if a != b]
        missing = [p for p in expected if p not in paths]
        if missing:
            msg = (f"{len(missing)} host juftligi orasida yo'l topilmadi -- "
                   f"tarmoq bo'lingan bo'lishi mumkin (masalan: {missing[0]}).")
            self._set_status(msg, error=True)
            messagebox.showerror("Validatsiya xatosi", msg)
            return None
        self._set_status(f"To'g'ri: {len(self.switches)} switch, {len(self.hosts)} host, "
                          f"{len(self.links)} link, {len(paths)} yo'l hisoblandi.")
        return topo

    def save_topology(self):
        topo = self.validate_topology()
        if topo is None:
            return
        name = simpledialog.askstring("Saqlash", "Topologiya nomi (fayl nomi bo'ladi):", parent=self.winfo_toplevel())
        if not name:
            return
        safe_name = "".join(c for c in name if c.isalnum() or c in "_-")
        if not safe_name:
            messagebox.showerror("Xato", "Nom noto'g'ri.")
            return
        save_topo = dict(topo)
        save_topo["links"] = {f"{k[0]}-{k[1]}": v for k, v in topo["links"].items()}
        path = os.path.join(CUSTOM_DIR, f"{safe_name}.json")
        with open(path, "w") as f:
            json.dump(save_topo, f, indent=2)
        self._set_status(f"Saqlandi: custom_topologies/{safe_name}.json")
        messagebox.showinfo(
            "Saqlandi",
            f"'{safe_name}' topologiyasi saqlandi.\n\n"
            f"Real simulyatsiyada ishlatish uchun (WSL/Ubuntu serverda):\n\n"
            f"sudo python3 light_simulation.py --topology {safe_name} --routing hybrid --duration 300"
        )

    def load_topology(self):
        files = sorted(f[:-5] for f in os.listdir(CUSTOM_DIR) if f.endswith(".json"))
        if not files:
            messagebox.showinfo("Ochish", "custom_topologies/ ichida hech narsa yo'q.")
            return
        name = simpledialog.askstring(
            "Ochish", f"Qaysi topologiyani ochamiz?\n({', '.join(files)})", parent=self.winfo_toplevel())
        if not name or name not in files:
            return
        with open(os.path.join(CUSTOM_DIR, f"{name}.json")) as f:
            topo = json.load(f)
        self.clear_all()
        positions = topo.get("_positions", {})
        for sname, info in topo["switches"].items():
            x, y = positions.get(sname, [100 + 80 * len(self.switches), 100])
            self.switches[sname] = {**info, "x": x, "y": y}
            self._draw_switch(sname)
        for hname, info in topo["hosts"].items():
            x, y = positions.get(hname, [100 + 60 * len(self.hosts), 250])
            access = topo.get("access_links", {}).get(hname, {"bw": 10, "delay": "1ms", "loss": 0})
            self.hosts[hname] = {**info, "x": x, "y": y, "access": access}
            self._draw_host(hname)
        for key, params in topo["links"].items():
            s1, s2 = key.split("-", 1) if isinstance(key, str) else key
            self.links[(s1, s2)] = dict(params)
            self._draw_link((s1, s2))
        self._set_status(f"'{name}' yuklandi.")

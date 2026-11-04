"""Ssenariydagi harakat hodisalari (action == "move") bo'yicha qurilma pozitsiyalari.

Hodisa: {"t": 10, "action": "move", "device": "lap3", "x": 120, "y": 80, "duration": 20}
  — t=10 s dan boshlab 20 s davomida (x, y) kanvas nuqtasiga to'g'ri chiziq bo'ylab
  bir tekis harakat (duration=0 — darhol ko'chish). Koordinatalar kanvas px'da
  (metr = px * project.meters_per_px). Uchala dvigatel shu funksiyadan foydalanadi.
"""


def moves(sc):
    return sorted((e for e in (sc.get("events") or []) if e.get("action") == "move"),
                  key=lambda e: float(e.get("t", 0)))


def has_moves(sc):
    return bool(moves(sc))


def positions_at(project, sc, t):
    """t vaqtdagi harakatlanuvchi qurilmalar pozitsiyalari: {dev: (x, y)}."""
    out = {}
    for e in moves(sc):
        dev = e.get("device")
        d = project["devices"].get(dev)
        if not d:
            continue
        x0, y0 = out.get(dev, (float(d.get("x", 0)), float(d.get("y", 0))))
        t0, dur = float(e.get("t", 0)), max(float(e.get("duration", 0) or 0), 0.0)
        x1, y1 = float(e.get("x", x0)), float(e.get("y", y0))
        if t < t0:
            out.setdefault(dev, (x0, y0))
            continue
        f = 1.0 if dur <= 0 else min((t - t0) / dur, 1.0)
        out[dev] = (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)
    return out


def apply_positions(project, positions):
    for dev, (x, y) in positions.items():
        if dev in project["devices"]:
            project["devices"][dev]["x"], project["devices"][dev]["y"] = x, y

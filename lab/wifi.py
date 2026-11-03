"""Wi-Fi radio modeli (analitik dvigatel, Mininet emulyatsiyasi va UI taxminlari uchun).

ns-3 bilan mos qilib kalibrlangan (YansWifiPhy + LogDistance, IdealWifiManager):
  path loss  PL(d) = L0 + 10*n*log10(d),  n = 3,  L0 = 46.68 dB (5 GHz) / 40.05 dB (2.4 GHz)
  RSSI       = Ptx - PL(d)
  shovqin    N = -174 + 10*log10(BW) + 7 (NF) dBm
  aloqa      RSSI >= -82 dBm (ns-3 ThresholdPreambleDetectionModel MinimumRssi)
  MCS        SNR chegaralari (quyidagi jadval) — ns-3 802.11ax 20 MHz o'lchovlariga moslangan
  foydali    PHY * samaradorlik (A-MPDU bilan ~0.8; 802.11g ~0.55)
Bu model lab/web/js/wifi.js da aynan takrorlanadi — o'zgartirsangiz ikkalasini ham.
"""

import math

MIN_RSSI = -82.0
EXPONENT = 3.0
REF_LOSS = {5: 46.6777, 2.4: 40.046}

# (SNR chegarasi dB, PHY Mbit/s @20MHz 1SS, 800ns GI)
MCS = {
    "80211ax": [(2, 8.6), (5, 17.2), (8, 25.8), (11, 34.4), (13.5, 51.6), (16.8, 68.8), (18.5, 77.4),
                (20.5, 86.0), (25, 103.2), (27, 114.7), (31, 129.0), (33, 143.4)],
    "80211ac": [(2, 6.5), (5, 13.0), (8, 19.5), (11, 26.0), (13.5, 39.0), (16.8, 52.0), (18.5, 58.5),
                (20.5, 65.0), (25, 78.0), (27, 86.7)],
    "80211n": [(2, 6.5), (5, 13.0), (8, 19.5), (11, 26.0), (13.5, 39.0), (16.8, 52.0), (18.5, 58.5), (20.5, 65.0)],
    "80211g": [(2, 6), (4, 9), (6, 12), (8, 18), (12, 24), (15, 36), (19, 48), (21, 54)],
}
WIDTH_MULT = {20: 1.0, 40: 2.08, 80: 4.33, 160: 8.67}
EFFICIENCY = {"80211ax": 0.8, "80211ac": 0.8, "80211n": 0.78, "80211g": 0.55}
STANDARDS = list(MCS)

AP_DEFAULTS = {"standard": "80211ax", "band": 5, "width": 20, "tx_power": 20.0, "ssid": ""}


def ap_config(dev):
    cfg = dict(AP_DEFAULTS)
    cfg.update({k: v for k, v in (dev.get("config") or {}).items() if k in AP_DEFAULTS and v not in (None, "")})
    if cfg["standard"] == "80211g":
        cfg["band"], cfg["width"] = 2.4, 20
    if cfg["standard"] == "80211ac":      # 802.11ac faqat 5 GHz
        cfg["band"] = 5
    cfg["band"] = 2.4 if float(cfg["band"]) < 4 else 5
    cfg["width"] = int(cfg["width"]) if int(cfg["width"]) in WIDTH_MULT else 20
    if cfg["band"] == 2.4 and cfg["width"] > 40:
        cfg["width"] = 40
    cfg["tx_power"] = float(cfg["tx_power"])
    return cfg


def estimate(distance_m, cfg):
    """Masofa va AP sozlamasi -> {rssi, snr, mcs, phy_mbps, thr_mbps, ok}."""
    d = max(float(distance_m), 1.0)
    pl = REF_LOSS[cfg["band"]] + 10 * EXPONENT * math.log10(d)
    rssi = cfg["tx_power"] - pl
    noise = -174 + 10 * math.log10(cfg["width"] * 1e6) + 7
    snr = rssi - noise
    table = MCS[cfg["standard"]]
    mcs = -1
    for i, (thr, _r) in enumerate(table):
        if snr >= thr:
            mcs = i
    ok = rssi >= MIN_RSSI and mcs >= 0
    phy = table[mcs][1] * WIDTH_MULT[cfg["width"]] if ok else 0.0
    return {"distance_m": round(d, 1), "rssi_dbm": round(rssi, 1), "snr_db": round(snr, 1), "mcs": mcs if ok else None,
            "phy_mbps": round(phy, 1), "thr_mbps": round(phy * EFFICIENCY[cfg["standard"]], 1), "ok": ok}


def max_range_m(cfg):
    """RSSI = MIN_RSSI bo'ladigan masofa (aloqa chegarasi)."""
    return 10 ** ((cfg["tx_power"] - MIN_RSSI - REF_LOSS[cfg["band"]]) / (10 * EXPONENT))


def meters_per_px(project):
    try:
        return max(float(project.get("meters_per_px", 0.25)), 0.001)
    except (TypeError, ValueError):
        return 0.25


def link_estimate(project, lid):
    """Wi-Fi kabeli (params.medium == 'wifi') uchun taxmin; oddiy kabel bo'lsa None."""
    l = project["links"].get(lid)
    if not l or (l.get("params") or {}).get("medium") != "wifi":
        return None
    a, b = project["devices"][l["a"]["dev"]], project["devices"][l["b"]["dev"]]
    ap, sta = (a, b) if a["type"] == "wifiap" else (b, a)
    dist = math.hypot(a.get("x", 0) - b.get("x", 0), a.get("y", 0) - b.get("y", 0)) * meters_per_px(project)
    est = estimate(dist, ap_config(ap))
    est["ap"] = l["a"]["dev"] if a["type"] == "wifiap" else l["b"]["dev"]
    est["sta"] = l["b"]["dev"] if a["type"] == "wifiap" else l["a"]["dev"]
    return est

/* NetLab — Wi-Fi radio modeli (UI taxminlari). Jadvallar serverdan (/api/info.wifi,
   manba: lab/wifi.py), formula bu yerda aynan takrorlanadi. */
(function (NL) {
  'use strict';
  const S = NL.store;
  const M = () => (S.info && S.info.wifi) || null;

  NL.wifi = {
    apConfig(dev) {
      const m = M(); const c = Object.assign({}, m ? m.ap_defaults : {}, (dev && dev.config) || {});
      if (c.standard === '80211g') { c.band = 2.4; c.width = 20; }
      if (c.standard === '80211ac') c.band = 5;
      c.band = parseFloat(c.band) < 4 ? 2.4 : 5;
      c.width = [20, 40, 80, 160].includes(parseInt(c.width, 10)) ? parseInt(c.width, 10) : 20;
      if (c.band === 2.4 && c.width > 40) c.width = 40;
      c.tx_power = parseFloat(c.tx_power); if (!isFinite(c.tx_power)) c.tx_power = 20;
      return c;
    },
    estimate(distM, c) {
      const m = M(); if (!m) return null;
      const d = Math.max(distM, 1);
      const rssi = c.tx_power - (m.ref_loss[String(c.band)] + 10 * m.exponent * Math.log10(d));
      const snr = rssi - (-174 + 10 * Math.log10(c.width * 1e6) + 7);
      const table = m.mcs[c.standard] || m.mcs['80211ax'];
      let mcs = -1; table.forEach((r, i) => { if (snr >= r[0]) mcs = i; });
      const ok = rssi >= m.min_rssi && mcs >= 0;
      const phy = ok ? table[mcs][1] * m.width_mult[String(c.width)] : 0;
      return { distance_m: d, rssi_dbm: rssi, snr_db: snr, mcs: ok ? mcs : null, phy_mbps: phy,
        thr_mbps: phy * (m.efficiency[c.standard] || 0.8), ok };
    },
    range(c) { const m = M(); return m ? Math.pow(10, (c.tx_power - m.min_rssi - m.ref_loss[String(c.band)]) / (10 * m.exponent)) : 0; },
    /** SNR >= thr bo'ladigan masofa (masalan yuqori MCS zonasi) */
    rangeForSnr(c, snrDb) {
      const m = M(); if (!m) return 0;
      const noise = -174 + 10 * Math.log10(c.width * 1e6) + 7;
      return Math.pow(10, (c.tx_power - (noise + snrDb) - m.ref_loss[String(c.band)]) / (10 * m.exponent));
    },
    mpp: () => { const v = parseFloat(S.project && S.project.meters_per_px); return v > 0 ? v : 0.25; },
    isWifi: (l) => !!(l && l.params && l.params.medium === 'wifi'),
    linkEstimate(lid) {
      const l = S.link(lid); if (!NL.wifi.isWifi(l)) return null;
      const A = S.dev(l.a.dev), B = S.dev(l.b.dev); if (!A || !B) return null;
      const ap = A.type === 'wifiap' ? A : B;
      const est = NL.wifi.estimate(Math.hypot(A.x - B.x, A.y - B.y) * NL.wifi.mpp(), NL.wifi.apConfig(ap));
      if (est) { est.ap = A.type === 'wifiap' ? l.a.dev : l.b.dev; est.sta = A.type === 'wifiap' ? l.b.dev : l.a.dev; }
      return est;
    },
  };
})(window.NL);

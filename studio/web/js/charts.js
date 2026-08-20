/* Charts — canvas asosidagi yengil grafiklar (tashqi kutubxonasiz).
 * Theme ranglarini CSS o'zgaruvchilaridan oladi, HiDPI'ga moslashadi.
 * API:
 *   Charts.bars(canvas, {labels, values, horizontal, colors, unit})
 *   Charts.hourly(canvas, values24, {color})
 *   Charts.donut(canvas, {segments:[{label,value,color}]})
 *   Charts.box(canvas, {labels, series:[[..vals..]]})   // box-plot
 *   Charts.line(canvas, {labels, series:[{name,values,color}]})
 */
(function () {
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  function palette() {
    return [cssVar('--accent', '#35E0FF'), cssVar('--accent-2', '#9B7CFF'),
            cssVar('--accent-3', '#FF5FD2'), cssVar('--success', '#37E39B'),
            cssVar('--warn', '#FFC24B'), cssVar('--danger', '#FF5A76')];
  }
  function setup(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(rect.width, 220), h = Math.max(rect.height, 140);
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h, text: cssVar('--text-dim', '#9AA6C0'),
             faint: cssVar('--text-faint', '#5D6788'), grid: cssVar('--glass-brd', 'rgba(255,255,255,.1)') };
  }
  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }
  function grad(ctx, x0, y0, x1, y1, c) {
    const g = ctx.createLinearGradient(x0, y0, x1, y1);
    g.addColorStop(0, c); g.addColorStop(1, c + '66'); return g;
  }

  const Charts = {
    bars(canvas, { labels, values, horizontal = true, colors, unit = '' }) {
      const s = setup(canvas); const { ctx, w, h } = s;
      const pal = colors || palette();
      const max = Math.max(...values, 1);
      ctx.font = '11px -apple-system, sans-serif'; ctx.textBaseline = 'middle';
      if (horizontal) {
        const padL = Math.min(90, Math.max(...labels.map(l => ctx.measureText(l).width)) + 14);
        const padR = 46, top = 8, bh = (h - top * 2) / values.length, bar = Math.min(bh - 8, 26);
        values.forEach((v, i) => {
          const y = top + i * bh + (bh - bar) / 2;
          const bw = (w - padL - padR) * (v / max);
          ctx.fillStyle = s.faint; ctx.textAlign = 'right'; ctx.fillText(labels[i], padL - 8, y + bar / 2);
          ctx.fillStyle = s.grid; roundRect(ctx, padL, y, w - padL - padR, bar, bar / 2); ctx.fill();
          ctx.fillStyle = grad(ctx, padL, 0, padL + bw, 0, pal[i % pal.length]);
          roundRect(ctx, padL, y, Math.max(bw, 3), bar, bar / 2); ctx.fill();
          ctx.fillStyle = s.text; ctx.textAlign = 'left';
          ctx.fillText((Math.round(v * 100) / 100) + unit, padL + bw + 8, y + bar / 2);
        });
      } else {
        const padB = 26, top = 10, bw = (w - 8) / values.length, bar = Math.min(bw - 10, 40);
        values.forEach((v, i) => {
          const x = 4 + i * bw + (bw - bar) / 2, bh = (h - top - padB) * (v / max);
          const y = h - padB - bh;
          ctx.fillStyle = grad(ctx, 0, y, 0, y + bh, pal[i % pal.length]);
          roundRect(ctx, x, y, bar, Math.max(bh, 3), 6); ctx.fill();
          ctx.fillStyle = s.faint; ctx.font = '10px -apple-system'; ctx.textAlign = 'center';
          ctx.fillText(labels[i], x + bar / 2, h - padB / 2);
        });
      }
    },

    hourly(canvas, values, { color } = {}) {
      const s = setup(canvas); const { ctx, w, h } = s;
      const c = color || palette()[1];
      const max = Math.max(...values, 1), padB = 20, top = 8, bw = (w - 8) / 24;
      values.forEach((v, i) => {
        const x = 4 + i * bw, bh = (h - top - padB) * (v / max), y = h - padB - bh;
        ctx.fillStyle = grad(ctx, 0, y, 0, y + bh, c);
        roundRect(ctx, x + 1.5, y, bw - 3, Math.max(bh, 2), 3); ctx.fill();
      });
      ctx.fillStyle = s.faint; ctx.font = '10px -apple-system'; ctx.textAlign = 'center';
      [0, 6, 12, 18, 23].forEach(hh => ctx.fillText(hh, 4 + hh * bw + bw / 2, h - 6));
    },

    donut(canvas, { segments }) {
      const s = setup(canvas); const { ctx, w, h } = s;
      const total = segments.reduce((a, b) => a + b.value, 0) || 1;
      const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 10, r = R * 0.6;
      let a0 = -Math.PI / 2; const pal = palette();
      segments.forEach((seg, i) => {
        const a1 = a0 + (seg.value / total) * Math.PI * 2;
        ctx.beginPath(); ctx.arc(cx, cy, R, a0, a1); ctx.arc(cx, cy, r, a1, a0, true); ctx.closePath();
        ctx.fillStyle = seg.color || pal[i % pal.length]; ctx.fill();
        a0 = a1;
      });
      ctx.fillStyle = s.text; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = '600 20px -apple-system'; ctx.fillText(total.toLocaleString(), cx, cy - 4);
      ctx.fillStyle = s.faint; ctx.font = '10px -apple-system'; ctx.fillText('jami', cx, cy + 14);
    },

    box(canvas, { labels, series }) {
      const s = setup(canvas); const { ctx, w, h } = s; const pal = palette();
      const stats = series.map(arr => {
        if (!arr.length) return null;
        const a = [...arr].sort((x, y) => x - y); const q = p => a[Math.floor((a.length - 1) * p)];
        return { min: a[0], q1: q(.25), med: q(.5), q3: q(.75), max: a[a.length - 1] };
      });
      const all = series.flat(); const lo = Math.min(...all, 0), hi = Math.max(...all, 1);
      const padL = 40, padB = 26, top = 10, iw = (w - padL - 8) / labels.length;
      const Y = v => h - padB - (h - top - padB) * ((v - lo) / (hi - lo || 1));
      ctx.strokeStyle = s.grid; ctx.lineWidth = 1;
      for (let g = 0; g <= 4; g++) { const yy = top + (h - top - padB) * g / 4;
        ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - 4, yy); ctx.stroke();
        ctx.fillStyle = s.faint; ctx.font = '9px -apple-system'; ctx.textAlign = 'right';
        ctx.fillText(Math.round(hi - (hi - lo) * g / 4), padL - 5, yy); }
      stats.forEach((st, i) => {
        if (!st) return; const cx = padL + i * iw + iw / 2, bw = Math.min(iw * 0.5, 30);
        const c = pal[i % pal.length]; ctx.strokeStyle = c; ctx.fillStyle = c + '33'; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(cx, Y(st.min)); ctx.lineTo(cx, Y(st.max)); ctx.stroke();
        roundRect(ctx, cx - bw / 2, Y(st.q3), bw, Y(st.q1) - Y(st.q3), 4); ctx.fill(); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(cx - bw / 2, Y(st.med)); ctx.lineTo(cx + bw / 2, Y(st.med));
        ctx.lineWidth = 2; ctx.stroke();
        ctx.fillStyle = s.faint; ctx.font = '9px -apple-system'; ctx.textAlign = 'center';
        ctx.fillText(labels[i], cx, h - 8);
      });
    },

    line(canvas, { labels, series }) {
      const s = setup(canvas); const { ctx, w, h } = s; const pal = palette();
      const all = series.flatMap(x => x.values); const lo = Math.min(...all, 0), hi = Math.max(...all, 1);
      const padL = 40, padB = 22, top = 10; const n = labels.length;
      const X = i => padL + (w - padL - 8) * (i / Math.max(n - 1, 1));
      const Y = v => h - padB - (h - top - padB) * ((v - lo) / (hi - lo || 1));
      ctx.strokeStyle = s.grid; ctx.lineWidth = 1;
      for (let g = 0; g <= 3; g++) { const yy = top + (h - top - padB) * g / 3;
        ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - 4, yy); ctx.stroke(); }
      series.forEach((ser, si) => {
        const c = ser.color || pal[si % pal.length];
        ctx.beginPath(); ser.values.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
        ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.stroke();
        ctx.lineTo(X(n - 1), h - padB); ctx.lineTo(X(0), h - padB); ctx.closePath();
        ctx.fillStyle = c + '18'; ctx.fill();
      });
    },
  };
  window.Charts = Charts;
})();

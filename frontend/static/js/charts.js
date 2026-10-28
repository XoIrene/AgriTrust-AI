/* AgriTrust AI - small dependency-free SVG charts.
 * Marks: bars <= 24px with 4px rounded data-end, 2px lines, 8px end dots with a
 * 2px surface ring, hairline grid. Every chart has a hover layer and a table view.
 */
(function () {
  "use strict";
  const { el, fmt } = window.AT;
  const NS = "http://www.w3.org/2000/svg";

  function svg(tag, attrs) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    return node;
  }

  /** Clean tick values: 0, 250K, 500K ... */
  function niceTicks(max, count = 4) {
    if (!(max > 0)) return [0, 1];
    const raw = max / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
    const ticks = [];
    for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
    if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
    return ticks;
  }

  /** Column with a 4px rounded top, square at the baseline. */
  function columnPath(x, y, w, h) {
    const r = Math.min(4, w / 2, h);
    if (h <= 0) return "";
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  function tooltip(container) {
    const tip = el("div", { class: "tooltip hidden", role: "status" });
    container.appendChild(tip);
    return {
      show(title, rows, px, py) {
        tip.replaceChildren(
          el("div", { class: "t-title" }, title),
          ...rows.map((r) =>
            el("div", { class: "t-row" },
              el("i", { class: "t-key", style: `background:var(${r.color})` }),
              el("b", {}, r.value), el("span", {}, r.name)))
        );
        tip.classList.remove("hidden");
        const box = container.getBoundingClientRect();
        const w = tip.offsetWidth;
        tip.style.left = `${Math.min(Math.max(px - w / 2, 0), box.width - w)}px`;
        tip.style.top = `${Math.max(py - tip.offsetHeight - 10, 0)}px`;
      },
      hide() { tip.classList.add("hidden"); },
    };
  }

  function legend(series, kind) {
    if (series.length < 2) return null;
    return el("div", { class: "legend" },
      series.map((s) => el("span", {}, el("i", { class: kind === "line" ? "line" : "", style: `background:var(${s.color})` }), s.name)));
  }

  /** A one-line "how to read this", in the page language. */
  function hint() {
    const { t } = window.AT;
    return el("p", { class: "chart-hint" }, t("chart.hint"));
  }

  /** Redraw at the container's real width, so text stays a readable size on a phone. */
  function responsive(draw) {
    return function (container, opts) {
      let drawnAt = 0;
      const render = () => {
        const w = Math.round(container.clientWidth) || 0;
        const W = Math.max(280, Math.min(w || 640, 900));
        drawnAt = w;
        draw(container, opts, W);
      };
      render();
      if (window.ResizeObserver && !container.__charts) {
        container.__charts = true;
        new ResizeObserver(() => {
          const w = Math.round(container.clientWidth) || 0;
          if (w && Math.abs(w - drawnAt) > 8) render();
        }).observe(container);
      }
    };
  }

  function tableView(headers, rows, caption) {
    return el("details", { class: "table-view" },
      el("summary", {}, caption || "Table"),
      el("div", { class: "table-wrap" },
        el("table", {},
          el("thead", {}, el("tr", {}, headers.map((h, i) => el("th", { class: i ? "num" : "" }, h)))),
          el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c, i) => el("td", { class: i ? "num" : "" }, c))))))));
  }

  /**
   * Grouped columns.
   * opts: {categories: [..], series: [{name, color: "--series-1", values: [..]}], format, tableCaption}
   */
  function columnsAt(container, opts, W) {
    const format = opts.format || fmt.compact;
    const H = W < 420 ? 220 : 250, M = { t: 18, r: 8, b: 28, l: 44 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    const max = Math.max(0, ...opts.series.flatMap((s) => s.values.map(Number)));
    const ticks = niceTicks(max);
    const top = ticks[ticks.length - 1] || 1;
    const band = iw / Math.max(1, opts.categories.length);
    const n = opts.series.length;
    const gap = 2;
    const barW = Math.min(24, (band * 0.7 - gap * (n - 1)) / n);
    const groupW = barW * n + gap * (n - 1);
    const y = (v) => M.t + ih - (Number(v) / top) * ih;

    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.ariaLabel || "" });
    for (const tv of ticks) {
      root.appendChild(svg("line", { class: tv ? "grid-line" : "baseline", x1: M.l, x2: W - M.r, y1: y(tv), y2: y(tv) }));
      const label = svg("text", { class: "tick", x: M.l - 6, y: y(tv) + 4, "text-anchor": "end" });
      label.textContent = format(tv);
      root.appendChild(label);
    }
    const wrap = el("div", { class: "chart" });
    const tip = tooltip(wrap);
    const labelEvery = Math.max(1, Math.ceil(opts.categories.length / Math.max(2, Math.floor(iw / 58))));
    const showValues = opts.categories.length * n <= (W < 420 ? 6 : 12) && band / n >= 30;
    opts.categories.forEach((cat, i) => {
      const x0 = M.l + band * i + (band - groupW) / 2;
      opts.series.forEach((s, j) => {
        const v = Number(s.values[i]) || 0;
        const h = (v / top) * ih;
        root.appendChild(svg("path", {
          d: columnPath(x0 + j * (barW + gap), M.t + ih - h, barW, h),
          style: `fill:var(${s.color})`,
        }));
        if (showValues && v > 0) {
          const vt = svg("text", { class: "value-label", x: x0 + j * (barW + gap) + barW / 2, y: M.t + ih - h - 4, "text-anchor": "middle" });
          vt.textContent = format(v);
          root.appendChild(vt);
        }
      });
      // hit target: the whole band, so the reader never has to land on a thin bar
      const hit = svg("rect", {
        class: "mark", x: M.l + band * i, y: M.t, width: band, height: ih,
        fill: "transparent", tabindex: 0, "aria-label": `${cat}`,
      });
      const show = () => {
        const box = wrap.getBoundingClientRect();
        const scale = box.width / W;
        tip.show(opts.labels ? opts.labels[i] : cat,
          opts.series.map((s) => ({ name: s.name, color: s.color, value: fmt.money(s.values[i]) })),
          (M.l + band * (i + 0.5)) * scale, y(Math.max(...opts.series.map((s) => Number(s.values[i]) || 0))) * scale);
      };
      hit.addEventListener("pointermove", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", tip.hide);
      hit.addEventListener("blur", tip.hide);
      root.appendChild(hit);
      if (i % labelEvery === 0) {
        const lbl = svg("text", { class: "label", x: M.l + band * (i + 0.5), y: H - 8, "text-anchor": "middle" });
        lbl.textContent = opts.labels ? opts.labels[i] : cat;
        root.appendChild(lbl);
      }
    });
    wrap.appendChild(root);
    const totals = opts.series.length && opts.categories.length > 1
      ? el("p", { class: "chart-note" }, opts.series.map((s) =>
        `${s.name}: ${fmt.money(s.values.reduce((a, v) => a + (Number(v) || 0), 0))}`).join("  ·  "))
      : null;
    container.replaceChildren(
      ...[legend(opts.series, "bar"), wrap, totals, hint(),
        tableView(["", ...opts.series.map((s) => s.name)],
          opts.categories.map((c, i) => [opts.labels ? opts.labels[i] : c, ...opts.series.map((s) => fmt.money(s.values[i]))]),
          opts.tableCaption)].filter(Boolean));
  }

  /**
   * Single line with crosshair, optionally inside an uncertainty band.
   * opts: {points: [{label, value, low?, high?}], name, color, format, valueFormat,
   *        bandName, tableCaption}. A band is drawn when points carry low/high.
   */
  /**
   * The longest run of points that all carry low/high bounds, preferring the run
   * that reaches the end so a projection at the end of a series is what gets banded.
   */
  function bandedRun(pts) {
    const banded = pts.map((p, i) => (p.low !== undefined && p.high !== undefined ? i : -1)).filter((i) => i >= 0);
    if (!banded.length) return [];
    const contiguous = [];
    let run = [banded[0]];
    for (let i = 1; i < banded.length; i++) {
      if (banded[i] === run[run.length - 1] + 1) run.push(banded[i]);
      else { if (run.length > contiguous.length) contiguous.splice(0, run.length, ...run); run = [banded[i]]; }
    }
    if (run.length > contiguous.length) contiguous.splice(0, run.length, ...run);
    return contiguous[contiguous.length - 1] === pts.length - 1 ? contiguous : banded;
  }

  function lineAt(container, opts, W) {
    const pts = opts.points;
    const format = opts.format || fmt.compact;
    const color = opts.color || "--series-1";
    const H = W < 420 ? 210 : 230, M = { t: 14, r: W < 420 ? 52 : 64, b: 28, l: 44 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    const hasBand = pts.some((p) => p.low !== undefined && p.high !== undefined);
    // A series may mix banded and unbanded points (recorded prices, then a
    // projection). Missing bounds must not reach the scale, or every coordinate
    // becomes NaN and the chart silently draws nothing.
    const values = pts
      .flatMap((p) => [p.value, p.low, p.high])
      .filter((v) => v !== undefined && v !== null && !Number.isNaN(Number(v)))
      .map(Number);
    if (!values.length) return;
    const minV = Math.min(...values), maxV = Math.max(...values);
    // prices: zoom to the data range (a line encodes position, not length) with padding
    const pad = (maxV - minV) * 0.15 || maxV * 0.1 || 1;
    const lo = minV < 0 ? minV - pad : Math.max(0, minV - pad), hi = maxV + pad;
    const x = (i) => M.l + (pts.length < 2 ? iw / 2 : (i / (pts.length - 1)) * iw);
    const y = (v) => M.t + ih - ((v - lo) / (hi - lo)) * ih;

    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.name || "" });
    const steps = 4;
    for (let k = 0; k <= steps; k++) {
      const tv = lo + ((hi - lo) * k) / steps;
      root.appendChild(svg("line", { class: k ? "grid-line" : "baseline", x1: M.l, x2: M.l + iw, y1: y(tv), y2: y(tv) }));
      const label = svg("text", { class: "tick", x: M.l - 6, y: y(tv) + 4, "text-anchor": "end" });
      label.textContent = format(tv);
      root.appendChild(label);
    }
    const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join("");
    // uncertainty band, drawn only over the run of points that actually carry
    // bounds (a projection after a stretch of recorded history has bounds, the
    // history does not)
    const banded = bandedRun(pts);
    if (banded.length > 1) {
      const upper = banded.map((i, k) => `${k ? "L" : "M"}${x(i)},${y(pts[i].high)}`).join("");
      const lower = banded.map((i) => `L${x(i)},${y(pts[i].low)}`).reverse().join("");
      root.appendChild(svg("path", { d: `${upper}${lower}Z`, style: `fill:var(${color});fill-opacity:0.14` }));
    } else if (!hasBand) {
      root.appendChild(svg("path", {
        d: `${d}L${x(pts.length - 1)},${M.t + ih}L${x(0)},${M.t + ih}Z`,
        style: `fill:var(${color});fill-opacity:0.1`,
      }));
    }
    if (lo < 0) {
      root.appendChild(svg("line", { class: "baseline", x1: M.l, x2: M.l + iw, y1: y(0), y2: y(0), "stroke-width": 1.5 }));
    }
    root.appendChild(svg("path", {
      d, fill: "none", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
      style: `stroke:var(${color})`,
    }));
    const last = pts.length - 1;
    root.appendChild(svg("circle", {
      cx: x(last), cy: y(pts[last].value), r: 4, "stroke-width": 2,
      style: `fill:var(${color});stroke:var(--surface)`,
    }));
    const end = svg("text", { class: "end-label", x: x(last) + 8, y: y(pts[last].value) + 4 });
    end.textContent = format(pts[last].value);
    root.appendChild(end);
    [0, Math.floor(last / 2), last].filter((v, i, a) => a.indexOf(v) === i).forEach((i) => {
      const lbl = svg("text", { class: "label", x: x(i), y: H - 8, "text-anchor": i === 0 ? "start" : i === last ? "end" : "middle" });
      lbl.textContent = pts[i].label;
      root.appendChild(lbl);
    });

    const wrap = el("div", { class: "chart" });
    const tip = tooltip(wrap);
    const cross = svg("line", { class: "crosshair hidden", y1: M.t, y2: M.t + ih });
    const dot = svg("circle", { r: 4, "stroke-width": 2, class: "hidden", style: `fill:var(${color});stroke:var(--surface)` });
    root.appendChild(cross);
    root.appendChild(dot);
    const hit = svg("rect", { x: M.l, y: M.t, width: iw, height: ih, fill: "transparent", tabindex: 0, class: "mark" });
    let focusIndex = last;
    const showAt = (i) => {
      const box = wrap.getBoundingClientRect();
      const scale = box.width / W;
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(pts[i].value));
      cross.classList.remove("hidden"); dot.classList.remove("hidden");
      const vf = opts.valueFormat || format;
      const rows = [{ name: opts.name, color, value: vf(pts[i].value) }];
      if (pts[i].low !== undefined && pts[i].high !== undefined) {
        rows.push({ name: opts.bandName || "", color, value: `${vf(pts[i].low)} – ${vf(pts[i].high)}` });
      }
      tip.show(pts[i].label, rows,
        x(i) * scale, y(pts[i].value) * scale);
    };
    const hide = () => { tip.hide(); cross.classList.add("hidden"); dot.classList.add("hidden"); };
    hit.addEventListener("pointermove", (e) => {
      const box = root.getBoundingClientRect();
      const px = ((e.clientX - box.left) / box.width) * W;
      focusIndex = Math.round(((px - M.l) / iw) * last);
      showAt(Math.max(0, Math.min(last, focusIndex)));
    });
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("focus", () => showAt(focusIndex));
    hit.addEventListener("blur", hide);
    hit.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") focusIndex = Math.max(0, focusIndex - 1);
      else if (e.key === "ArrowRight") focusIndex = Math.min(last, focusIndex + 1);
      else return;
      e.preventDefault();
      showAt(focusIndex);
    });
    root.appendChild(hit);
    wrap.appendChild(root);
    const vf = opts.valueFormat || format;
    const key = hasBand
      ? el("div", { class: "legend" },
        el("span", {}, el("i", { class: "line", style: `background:var(${color})` }), opts.name),
        el("span", {}, el("i", { style: `background:var(${color});opacity:0.3` }), opts.bandName || ""))
      : null;
    const first = pts[0].value, lastV = pts[last].value;
    const note = pts.length > 1
      ? el("p", { class: "chart-note" }, `${pts[0].label}: ${vf(first)}  →  ${pts[last].label}: ${vf(lastV)}`
        + (first ? `  (${lastV >= first ? "+" : "−"}${Math.abs(((lastV - first) / first) * 100).toFixed(0)}%)` : ""))
      : null;
    container.replaceChildren(...[key, wrap, note, hint(),
      tableView(hasBand ? ["", opts.name, opts.bandName || ""] : ["", opts.name],
        pts.map((p) => (p.low === undefined || p.high === undefined
          ? [p.label, vf(p.value)]
          : [p.label, vf(p.value), `${vf(p.low)} – ${vf(p.high)}`])), opts.tableCaption)].filter(Boolean));
  }

  /**
   * Horizontal bars in HTML; labels and values are text, the bar carries colour.
   * rows: [{label, value, color}]  (color is a CSS var name)
   */
  function hbars(container, rows, opts = {}) {
    const max = Math.max(1, ...rows.map((r) => Number(r.value) || 0));
    const format = opts.format || ((v) => fmt.number(v));
    container.replaceChildren(
      ...rows.map((r) =>
        el("div", { class: "hbar-row", title: `${r.label}: ${format(r.value)}` },
          el("span", {}, r.label),
          el("div", { class: "hbar-track" },
            el("div", { class: "hbar-fill", style: `width:${(Number(r.value) / max) * 100}%;min-width:${Number(r.value) > 0 ? 2 : 0}px;background:var(${r.color || "--series-1"})` })),
          el("span", { class: "num", style: "text-align:right" }, format(r.value)))));
  }

  const columns = responsive(columnsAt);
  const line = responsive(lineAt);

  window.ATCharts = { columns, line, hbars };
})();

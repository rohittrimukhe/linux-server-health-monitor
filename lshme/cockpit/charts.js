/* Linux Server Health Monitor Enterprise - SVG chart library (no dependencies)
 * Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
 * Used by the Cockpit page and the standalone live page. Builds DOM nodes only
 * (no innerHTML, no inline style attributes), so it runs under a strict CSP. */
var LshmeCharts = (function () {
    "use strict";
    var NS = "http://www.w3.org/2000/svg";
    var COLORS = { CRITICAL: "#c9190b", WARNING: "#e08e0b", UNKNOWN: "#6a6e73", INFO: "#2b9af3", OK: "#3e8635", HEALTHY: "#3e8635", SKIPPED: "#b8bbbe" };
    var GRID = "rgba(127,140,155,.28)", TRACK = "rgba(127,140,155,.16)";

    function el(name, attrs, text) {
        var e = document.createElementNS(NS, name);
        Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
        if (text !== undefined) e.textContent = text;
        return e;
    }
    function svg(w, h, label) {
        var s = el("svg", { viewBox: "0 0 " + w + " " + h, width: "100%", role: "img", "aria-label": label || "chart" });
        s.style.maxWidth = w + "px";
        return s;
    }
    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
    function swatch(parent, color, label, value, share) {
        var item = document.createElement("span"), i = document.createElement("i");
        i.style.background = color;  /* CSSOM assignment works under a strict CSP */
        item.appendChild(i);
        item.appendChild(document.createTextNode(label));
        if (value !== undefined) {
            var b = document.createElement("b");
            b.textContent = " " + value;
            item.appendChild(b);
        }
        if (share !== undefined) {
            var m = document.createElement("small");
            m.textContent = " " + share;
            item.appendChild(m);
        }
        parent.appendChild(item);
    }
    function fmt(v) { return Math.round(v * 10) / 10; }
    function tick(v, max) {
        var d = max < 1 ? 2 : max < 10 ? 1 : 0;
        return (Math.round(v * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d);
    }
    function fmtTime(t) {
        var d = new Date(t * 1000);
        return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    }

    /* Line chart with shaded areas, thresholds and a hover crosshair.
     * series = [{name, color, points: [[epoch, value|null], ...]}] */
    function line(node, series, opts) {
        opts = opts || {};
        var W = 640, H = opts.height || 240, L = 46, R = 14, T = 12, B = 28;
        var max = opts.max, t0 = Infinity, t1 = -Infinity;
        series.forEach(function (s) {
            s.points.forEach(function (p) {
                if (p[1] === null || p[1] === undefined) return;
                t0 = Math.min(t0, p[0]); t1 = Math.max(t1, p[0]);
                if (opts.max === undefined) max = Math.max(max || 0, p[1]);
            });
        });
        clear(node);
        var s = svg(W, H, opts.label);
        if (!isFinite(t0)) {
            s.appendChild(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", fill: "currentColor", opacity: ".6", "font-size": "14" },
                "Waiting for data from the real-time watcher…"));
            node.appendChild(s);
            return;
        }
        if (opts.max === undefined) max = Math.max(max * 1.15, opts.minMax || 0) || 1;
        if (t1 === t0) t1 = t0 + 60;
        var x = function (t) { return L + (W - L - R) * (t - t0) / (t1 - t0); };
        var y = function (v) { return T + (H - T - B) * (1 - Math.min(Math.max(v, 0), max) / max); };
        [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
            var yy = y(max * f);
            s.appendChild(el("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: GRID }));
            s.appendChild(el("text", { x: L - 6, y: yy + 4, "text-anchor": "end", "font-size": "12", fill: "currentColor", opacity: ".65" },
                tick(max * f, max) + (opts.unit || "")));
        });
        (opts.thresholds || []).forEach(function (th) {
            if (th.value > max) return;
            s.appendChild(el("line", { x1: L, x2: W - R, y1: y(th.value), y2: y(th.value), stroke: th.color, "stroke-dasharray": "5 4", "stroke-width": "1.3" }));
            s.appendChild(el("text", { x: W - R - 2, y: y(th.value) - 4, "text-anchor": "end", "font-size": "11", fill: th.color }, th.label || (th.value + (opts.unit || ""))));
        });
        for (var i = 0; i <= 4; i++) {
            var tt = t0 + (t1 - t0) * i / 4;
            s.appendChild(el("text", { x: x(tt), y: H - 8, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle", "font-size": "12", fill: "currentColor", opacity: ".65" }, fmtTime(tt)));
        }
        var base = y(0);
        series.forEach(function (sr) {
            var segs = [], cur = [];
            sr.points.forEach(function (p) {
                if (p[1] === null || p[1] === undefined) { if (cur.length) segs.push(cur); cur = []; return; }
                cur.push([x(p[0]), y(p[1])]);
            });
            if (cur.length) segs.push(cur);
            segs.forEach(function (seg) {
                var d = seg.map(function (q, n) { return (n ? "L" : "M") + q[0].toFixed(1) + "," + q[1].toFixed(1); }).join(" ");
                if (opts.area !== false && seg.length > 1) {
                    s.appendChild(el("path", { d: d + " L" + seg[seg.length - 1][0].toFixed(1) + "," + base + " L" + seg[0][0].toFixed(1) + "," + base + " Z",
                        fill: sr.color, opacity: ".12" }));
                }
                s.appendChild(el("path", { d: d, fill: "none", stroke: sr.color, "stroke-width": "2.2", "stroke-linejoin": "round", "stroke-linecap": "round" }));
            });
            var last = sr.points.filter(function (p) { return p[1] !== null && p[1] !== undefined; }).pop();
            if (last) s.appendChild(el("circle", { cx: x(last[0]), cy: y(last[1]), r: 4, fill: sr.color, stroke: "var(--card, #fff)", "stroke-width": "2" }));
        });
        /* hover crosshair + readout */
        var cross = el("line", { y1: T, y2: H - B, stroke: "currentColor", opacity: "0", "stroke-dasharray": "3 3" });
        var tip = el("g", { opacity: "0" });
        var tipBox = el("rect", { rx: 6, fill: "var(--card, #fff)", stroke: GRID, width: 150, height: 20 + 16 * series.length });
        var tipText = el("text", { "font-size": "12", fill: "currentColor" });
        tip.appendChild(tipBox);
        tip.appendChild(tipText);
        var hit = el("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" });
        s.appendChild(cross);
        s.appendChild(tip);
        s.appendChild(hit);
        hit.addEventListener("mousemove", function (ev) {
            var box = s.getBoundingClientRect();
            var px = (ev.clientX - box.left) * W / box.width;
            var t = t0 + (px - L) / (W - L - R) * (t1 - t0);
            while (tipText.firstChild) tipText.removeChild(tipText.firstChild);
            tipText.appendChild(el("tspan", { x: 10, dy: 16, "font-weight": "700" }, fmtTime(t)));
            series.forEach(function (sr) {
                var best = null;
                sr.points.forEach(function (p) { if (p[1] !== null && p[1] !== undefined && (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t))) best = p; });
                tipText.appendChild(el("tspan", { x: 10, dy: 16, fill: sr.color }, sr.name + ": " + (best ? fmt(best[1]) + (opts.unit || "") : "–")));
            });
            var tx = px + 160 > W ? px - 160 : px + 10;
            tip.setAttribute("transform", "translate(" + tx + "," + (T + 4) + ")");
            cross.setAttribute("x1", px);
            cross.setAttribute("x2", px);
            cross.setAttribute("opacity", ".35");
            tip.setAttribute("opacity", "1");
        });
        hit.addEventListener("mouseleave", function () { cross.setAttribute("opacity", "0"); tip.setAttribute("opacity", "0"); });
        node.appendChild(s);
        var lg = document.createElement("div");
        lg.className = "legend";
        series.forEach(function (sr) {
            var last = sr.points.filter(function (p) { return p[1] !== null && p[1] !== undefined; }).pop();
            swatch(lg, sr.color, sr.name, last ? fmt(last[1]) + (opts.unit || "") : "–");
        });
        node.appendChild(lg);
    }

    /* Donut with percentage labels, centre total and a legend with value + share. */
    function donut(node, items, opts) {
        opts = opts || {};
        var size = opts.size || 200, r = size / 2 - 6, ri = r * 0.62, c = size / 2;
        items = items.filter(function (i) { return i.value > 0; });
        var total = items.reduce(function (a, i) { return a + i.value; }, 0);
        clear(node);
        var s = svg(size, size, opts.label);
        if (!total) {
            s.appendChild(el("circle", { cx: c, cy: c, r: (r + ri) / 2, fill: "none", stroke: TRACK, "stroke-width": r - ri }));
        } else {
            var a = -Math.PI / 2, gap = items.length > 1 ? 0.012 : 0;
            items.forEach(function (i) {
                var sweep = 2 * Math.PI * i.value / total, a0 = a + gap, a1 = a + sweep - gap, piece;
                if (items.length === 1) {
                    piece = el("circle", { cx: c, cy: c, r: (r + ri) / 2, fill: "none", stroke: i.color, "stroke-width": r - ri });
                } else {
                    var lg = (a1 - a0) > Math.PI ? 1 : 0;
                    piece = el("path", { fill: i.color, d: "M" + (c + r * Math.cos(a0)) + "," + (c + r * Math.sin(a0)) + " A" + r + "," + r + " 0 " + lg + ",1 " +
                        (c + r * Math.cos(a1)) + "," + (c + r * Math.sin(a1)) + " L" + (c + ri * Math.cos(a1)) + "," + (c + ri * Math.sin(a1)) +
                        " A" + ri + "," + ri + " 0 " + lg + ",0 " + (c + ri * Math.cos(a0)) + "," + (c + ri * Math.sin(a0)) + " Z" });
                }
                piece.appendChild(el("title", {}, i.label + ": " + i.value + " (" + Math.round(100 * i.value / total) + "%)"));
                s.appendChild(piece);
                if (i.value / total >= 0.05) {
                    var mid = a + sweep / 2, lr = (r + ri) / 2;
                    s.appendChild(el("text", { x: c + lr * Math.cos(mid), y: c + lr * Math.sin(mid), "text-anchor": "middle", "dominant-baseline": "central",
                        "font-size": Math.max(10, Math.round(size / 19)), "font-weight": "700", fill: "#fff" }, Math.round(100 * i.value / total) + "%"));
                }
                a += sweep;
            });
        }
        s.appendChild(el("text", { x: c, y: c + size * 0.03, "text-anchor": "middle", "font-size": Math.round(size / 6), "font-weight": "800", fill: "currentColor" },
            opts.center !== undefined ? opts.center : total));
        if (opts.label) s.appendChild(el("text", { x: c, y: c + size * 0.13, "text-anchor": "middle", "font-size": Math.max(10, Math.round(size / 17)), fill: "currentColor", opacity: ".65" }, opts.label.toUpperCase()));
        var wrap = document.createElement("div");
        wrap.className = "donut";
        wrap.appendChild(s);
        var legend = document.createElement("div");
        legend.className = "legend col";
        items.forEach(function (i) { swatch(legend, i.color, i.label, i.value, Math.round(100 * i.value / total) + "%"); });
        wrap.appendChild(legend);
        node.appendChild(wrap);
    }

    /* Horizontal bars: worst first, rounded track, value labels, dashed thresholds and an axis. */
    function bars(node, items, opts) {
        opts = opts || {};
        items = items.map(function (i) { return { label: String(i.label), value: Math.max(0, +i.value || 0) }; });
        if (opts.sort !== false) items.sort(function (a, b) { return opts.score ? a.value - b.value : b.value - a.value; });
        var W = 640, row = 32, LBL = 220, VAL = 64, T = 4, max = opts.max || 100, plot = W - LBL - VAL;
        var H = T + row * items.length + 26;
        var X = function (v) { return LBL + plot * Math.min(v, max) / max; };
        var warn = opts.score ? 70 : (opts.warn || 80), crit = opts.score ? 90 : (opts.crit || 90);
        clear(node);
        var s = svg(W, Math.max(H, 50), opts.label);
        items.forEach(function (it, n) {
            var v = it.value, y = T + n * row;
            var color = opts.score ? (v < 70 ? COLORS.CRITICAL : v < 90 ? COLORS.WARNING : COLORS.OK)
                : (v >= crit ? COLORS.CRITICAL : v >= warn ? COLORS.WARNING : COLORS.OK);
            if (n % 2 === 0) s.appendChild(el("rect", { x: 0, y: y, width: W, height: row, rx: 4, fill: TRACK, opacity: ".35" }));
            s.appendChild(el("text", { x: 6, y: y + 21, "font-size": "14", fill: "currentColor" }, it.label.slice(0, 28)));
            s.appendChild(el("rect", { x: LBL, y: y + 9, width: plot, height: 14, rx: 7, fill: TRACK }));
            var bar = el("rect", { x: LBL, y: y + 9, width: Math.max(3, X(v) - LBL), height: 14, rx: 7, fill: color });
            bar.appendChild(el("title", {}, it.label + ": " + fmt(v) + (opts.unit || "")));
            s.appendChild(bar);
            s.appendChild(el("text", { x: W - 4, y: y + 21, "text-anchor": "end", "font-size": "14", "font-weight": "700", fill: "currentColor" }, fmt(v) + (opts.unit || "")));
        });
        var baseY = T + row * items.length;
        [[warn, COLORS.WARNING], [crit, COLORS.CRITICAL]].forEach(function (th) {
            s.appendChild(el("line", { x1: X(th[0]), x2: X(th[0]), y1: T, y2: baseY, stroke: th[1], "stroke-dasharray": "4 3", "stroke-width": "1.2" }));
        });
        [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
            var xx = LBL + plot * f;
            s.appendChild(el("text", { x: xx, y: baseY + 17, "text-anchor": "middle", "font-size": "11", fill: "currentColor", opacity: ".6" }, fmt(max * f) + (opts.unit || "")));
        });
        node.appendChild(s);
    }

    return { line: line, donut: donut, bars: bars, COLORS: COLORS };
}());

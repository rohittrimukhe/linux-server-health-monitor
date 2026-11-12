/* Linux Server Health Monitor Enterprise - tiny SVG chart library (no dependencies)
 * Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
 * Used by the Cockpit page and the standalone live page. */
var LshmeCharts = (function () {
    "use strict";
    var NS = "http://www.w3.org/2000/svg";
    var COLORS = { CRITICAL: "#c9190b", WARNING: "#c58c00", UNKNOWN: "#6a6e73", INFO: "#2b9af3", OK: "#3e8635", HEALTHY: "#3e8635", SKIPPED: "#b8bbbe" };

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
    function swatch(parent, color, text) {
        var item = document.createElement("span"), i = document.createElement("i");
        i.style.background = color;  /* CSSOM assignment works under a strict CSP; inline style attributes would be blocked */
        item.appendChild(i);
        item.appendChild(document.createTextNode(text));
        parent.appendChild(item);
    }
    function tick(v, max) {
        var d = max < 1 ? 2 : max < 10 ? 1 : 0;
        return (Math.round(v * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d);
    }
    function fmtTime(t) {
        var d = new Date(t * 1000);
        return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    }

    /* Line chart: series = [{name, color, points: [[epoch, value|null], ...]}] */
    function line(node, series, opts) {
        opts = opts || {};
        var W = 640, H = opts.height || 230, L = 46, R = 10, T = 10, B = 28;
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
            s.appendChild(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", fill: "currentColor", opacity: ".6", "font-size": "13" },
                "Waiting for data from the real-time watcher…"));
            node.appendChild(s);
            return;
        }
        max = max || 1;
        if (t1 === t0) t1 = t0 + 60;
        var x = function (t) { return L + (W - L - R) * (t - t0) / (t1 - t0); };
        var y = function (v) { return T + (H - T - B) * (1 - Math.min(v, max) / max); };
        [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
            var yy = y(max * f);
            s.appendChild(el("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: "currentColor", opacity: ".12" }));
            s.appendChild(el("text", { x: L - 5, y: yy + 4, "text-anchor": "end", "font-size": "12", fill: "currentColor", opacity: ".65" },
                tick(max * f, max) + (opts.unit || "")));
        });
        (opts.thresholds || []).forEach(function (th) {
            if (th.value <= max) s.appendChild(el("line", { x1: L, x2: W - R, y1: y(th.value), y2: y(th.value), stroke: th.color, "stroke-dasharray": "4 3", opacity: ".8" }));
        });
        for (var i = 0; i <= 4; i++) {
            var tt = t0 + (t1 - t0) * i / 4;
            s.appendChild(el("text", { x: x(tt), y: H - 7, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle", "font-size": "12", fill: "currentColor", opacity: ".65" }, fmtTime(tt)));
        }
        series.forEach(function (sr) {
            var d = "", pen = false;
            sr.points.forEach(function (p) {
                if (p[1] === null || p[1] === undefined) { pen = false; return; }
                d += (pen ? " L" : " M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1);
                pen = true;
            });
            if (d) s.appendChild(el("path", { d: d, fill: "none", stroke: sr.color, "stroke-width": "2", "stroke-linejoin": "round" }));
            var last = sr.points.filter(function (p) { return p[1] !== null && p[1] !== undefined; }).pop();
            if (last) s.appendChild(el("circle", { cx: x(last[0]), cy: y(last[1]), r: 3.5, fill: sr.color }));
        });
        node.appendChild(s);
        var lg = document.createElement("div");
        lg.className = "legend";
        series.forEach(function (sr) {
            var last = sr.points.filter(function (p) { return p[1] !== null && p[1] !== undefined; }).pop();
            swatch(lg, sr.color, sr.name + (last ? " " + last[1] + (opts.unit || "") : " –"));
        });
        node.appendChild(lg);
    }

    /* Donut / pie: items = [{label, value, color}] */
    function donut(node, items, opts) {
        opts = opts || {};
        var size = opts.size || 170, r = size / 2 - 4, c = size / 2;
        items = items.filter(function (i) { return i.value > 0; });
        var total = items.reduce(function (a, i) { return a + i.value; }, 0);
        clear(node);
        var s = svg(size, size, opts.label);
        s.style.maxWidth = size + "px";
        if (!total) {
            s.appendChild(el("circle", { cx: c, cy: c, r: r, fill: "currentColor", opacity: ".1" }));
        } else if (items.length === 1) {
            s.appendChild(el("circle", { cx: c, cy: c, r: r, fill: items[0].color }));
        } else {
            var a = -Math.PI / 2;
            items.forEach(function (i) {
                var sweep = 2 * Math.PI * i.value / total;
                var x1 = c + r * Math.cos(a), y1 = c + r * Math.sin(a);
                a += sweep;
                var x2 = c + r * Math.cos(a), y2 = c + r * Math.sin(a);
                var p = el("path", { d: "M" + c + "," + c + " L" + x1 + "," + y1 + " A" + r + "," + r + " 0 " + (sweep > Math.PI ? 1 : 0) + ",1 " + x2 + "," + y2 + " Z",
                    fill: i.color, stroke: "var(--card, #fff)", "stroke-width": "2" });
                p.appendChild(el("title", {}, i.label + ": " + i.value));
                s.appendChild(p);
            });
        }
        s.appendChild(el("circle", { cx: c, cy: c, r: r * 0.58, fill: "var(--card, #fff)" }));
        s.appendChild(el("text", { x: c, y: c + 6, "text-anchor": "middle", "font-size": size / 7, "font-weight": "700", fill: "currentColor" },
            opts.center !== undefined ? opts.center : total));
        if (opts.label) s.appendChild(el("text", { x: c, y: c + size / 9 + 6, "text-anchor": "middle", "font-size": size / 16, fill: "currentColor", opacity: ".7" }, opts.label));
        var wrap = document.createElement("div");
        wrap.className = "donut";
        wrap.appendChild(s);
        var lg = document.createElement("div");
        lg.className = "legend col";
        items.forEach(function (i) { swatch(lg, i.color, i.label + " " + i.value); });
        wrap.appendChild(lg);
        node.appendChild(wrap);
    }

    /* Horizontal bars: items = [{label, value}]; colour by thresholds or score */
    function bars(node, items, opts) {
        opts = opts || {};
        var W = 640, row = 30, H = Math.max(row * items.length + 8, 40), max = opts.max || 100, LBL = 230, VAL = 70;
        clear(node);
        var s = svg(W, H, opts.label);
        items.forEach(function (it, n) {
            var v = Math.max(0, +it.value || 0), y = 4 + n * row;
            var color = opts.score ? (v < 70 ? COLORS.CRITICAL : v < 90 ? COLORS.WARNING : COLORS.OK)
                : (v >= (opts.crit || 90) ? COLORS.CRITICAL : v >= (opts.warn || 80) ? COLORS.WARNING : COLORS.OK);
            s.appendChild(el("text", { x: 0, y: y + 18, "font-size": "14", fill: "currentColor" }, String(it.label).slice(0, 28)));
            s.appendChild(el("rect", { x: LBL, y: y + 5, width: W - LBL - VAL, height: 17, rx: 3, fill: "currentColor", opacity: ".08" }));
            var bar = el("rect", { x: LBL, y: y + 5, width: Math.max(1, (W - LBL - VAL) * Math.min(v, max) / max), height: 17, rx: 3, fill: color });
            bar.appendChild(el("title", {}, it.label + ": " + v + (opts.unit || "")));
            s.appendChild(bar);
            s.appendChild(el("text", { x: W, y: y + 18, "text-anchor": "end", "font-size": "14", "font-weight": "700", fill: "currentColor" },
                (Math.round(v * 10) / 10) + (opts.unit || "")));
        });
        node.appendChild(s);
    }

    return { line: line, donut: donut, bars: bars, COLORS: COLORS };
}());

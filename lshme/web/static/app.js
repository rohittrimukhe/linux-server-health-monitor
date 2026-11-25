/* Linux Server Health Monitor Enterprise - web interface. Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai.
 * Builds the page with DOM calls only (no innerHTML, no inline styles), so it runs under a strict CSP. */
(function () {
    "use strict";
    var NS = "http://www.w3.org/2000/svg";
    var S = { me: null, cleanup: null, cat: null };
    var root = document.getElementById("app");

    /* ---------------------------------------------------------------- helpers */
    function add(el, c) {
        if (c === null || c === undefined || c === false) return;
        if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
        el.appendChild(c && c.nodeType ? c : document.createTextNode(String(c)));
    }
    function h(tag, attrs) {
        var el = document.createElement(tag), k, v;
        for (k in (attrs || {})) {
            v = attrs[k];
            if (v === null || v === undefined || v === false) continue;
            if (k === "class") el.className = v;
            else if (k === "text") el.textContent = v;
            else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
            else if (k === "value") el.value = v;
            else if (k === "style") el.style.cssText = v;          // through the style object: allowed by the page's security policy (an attribute is not)
            else if (k === "checked" || k === "disabled" || k === "selected") el[k] = !!v;
            else el.setAttribute(k, v === true ? "" : v);
        }
        for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
        return el;
    }
    function svg(tag, attrs) {
        var el = document.createElementNS(NS, tag), k;
        for (k in (attrs || {})) el.setAttribute(k, attrs[k]);
        for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
        return el;
    }
    function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
    function size(b) {
        b = Number(b) || 0;
        var u = ["B", "KB", "MB", "GB", "TB", "PB"], i = 0;
        while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
        return (i === 0 ? b.toFixed(0) : b.toFixed(1)) + " " + u[i];
    }
    function rate(b) { return size(b) + "/s"; }
    function uptime(s) { s = Math.floor(s); var d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + "d " : "") + hh + "h " + m + "m"; }
    function pc(v, d) { return (Number(v) || 0).toFixed(d === undefined ? 1 : d) + "%"; }
    function lvl(v, lim) { return v >= lim[1] ? "crit" : v >= lim[0] ? "warn" : "ok"; }
    function statusLvl(s) { return { HEALTHY: "ok", OK: "ok", WARNING: "warn", CRITICAL: "crit" }[s] || "info"; }
    function when(ts) { return new Date(ts * 1000).toLocaleString(); }

    /* ---------------------------------------------------------------- api */
    function api(method, path, body) {
        var o = { method: method, credentials: "same-origin", headers: {} };
        if (method !== "GET") {
            o.headers["Content-Type"] = "application/json";
            o.headers["X-CSRF-Token"] = S.me ? S.me.csrf : "";
            o.body = JSON.stringify(body || {});
        }
        return fetch(path, o).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (j) {
                if (r.status === 401 && path !== "/api/login" && path !== "/api/me") { showLogin(); throw new Error("Signed out"); }
                if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
                return j;
            });
        });
    }
    function fail(e) { toast(e.message || String(e), "bad"); }

    /* ---------------------------------------------------------------- widgets */
    var toastBox;
    function toast(msg, kind) {
        if (!toastBox) { toastBox = h("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.appendChild(toastBox); }
        var t = h("div", { class: "toast " + (kind || ""), text: msg });
        toastBox.appendChild(t);
        setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, kind === "bad" ? 8000 : 4000);
    }
    function modal(title, body, wide) {
        var bg = h("div", { class: "modalbg" }), box = h("div", { class: "modal" });
        function close() { if (bg.parentNode) bg.parentNode.removeChild(bg); document.removeEventListener("keydown", esc); }
        function esc(e) { if (e.key === "Escape") close(); }
        box.appendChild(h("div", { class: "head" }, h("h2", { text: title }), h("button", { text: "Close", onclick: close })));
        add(box, body);
        bg.appendChild(box);
        bg.addEventListener("mousedown", function (e) { if (e.target === bg) close(); });
        document.addEventListener("keydown", esc);
        document.body.appendChild(bg);
        return close;
    }
    function ask(message, yes, danger) {
        var close = modal("Please confirm", h("div", null, h("p", { text: message }),
            h("div", { class: "row" }, h("button", { class: danger ? "danger" : "primary", text: "Yes", onclick: function () { close(); yes(); } }),
                h("button", { text: "Cancel", onclick: function () { close(); } }))));
    }
    function badge(s) { return h("span", { class: "badge b-" + s, text: s }); }
    function steady(el) {                                                 // redraw without the page jumping: keep the old height for one frame
        var hgt = el.offsetHeight;
        if (hgt) { el.style.minHeight = hgt + "px"; requestAnimationFrame(function () { el.style.minHeight = ""; }); }
        clear(el);
    }
    var UPGRADE_CMD = "curl -fsSL https://raw.githubusercontent.com/rohittrimukhe/linux-server-health-monitor/main/tools/upgrade_from_github.sh -o /tmp/upgrade.sh && bash /tmp/upgrade.sh";
    function runRepair(server, label) {
        toast("Checking and repairing LRS Vitals on " + label + "...", "");
        api("POST", "/api/repair", { server: server }).then(function (r) {
            modal("Repair: " + label, h("div", { class: "stack" },
                h("p", { class: r.ok ? "okline" : "err", text: r.ok ? "Everything is working." : "Some items still need attention:" }),
                table(["Step", "Result", "Detail"], r.steps.map(function (st) {
                    return [h("b", { text: st.step }), badge(st.fixed ? "FIXED" : st.ok ? "OK" : "CRITICAL"), st.detail];
                }))));
        }).catch(fail);
    }
    function copyText(text, done) {
        function fallback() { var t = h("textarea", { "aria-hidden": "true" }); t.value = text; t.style.position = "fixed"; t.style.opacity = "0"; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); toast(done, "good"); } catch (e) { toast(text, ""); } document.body.removeChild(t); }
        if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(function () { toast(done, "good"); }, fallback); else fallback();
    }
    /* A small menu button: a native disclosure, so keyboard and screen readers work; closes after a choice or a click elsewhere. */
    function menu(label, items) {
        var d = h("details", { class: "menu" }), list = h("div", { class: "menu-list", role: "menu" });
        d.appendChild(h("summary", { class: "btn small", text: label + " \u25BE" }));
        items.forEach(function (it) {
            list.appendChild(h("button", { type: "button", role: "menuitem", title: it[1], text: it[0], onclick: function () { d.removeAttribute("open"); it[2](); } }));
        });
        d.appendChild(list);
        document.addEventListener("click", function off(e) { if (!document.body.contains(d)) { document.removeEventListener("click", off); return; } if (!d.contains(e.target)) d.removeAttribute("open"); });
        return d;
    }
    function ring(value, level, text, big) {
        var g = h("div", { class: "gauge lvl-" + level + (big ? " big" : "") }, h("b", { text: text }));
        g.style.setProperty("--p", Math.max(0, Math.min(100, value)));
        return g;
    }
    function bar(value, level) {
        var i = h("i"), b = h("div", { class: "bar lvl-" + level }, i);
        i.style.transform = "scaleX(" + (Math.max(0, Math.min(100, value)) / 100).toFixed(4) + ")";
        return b;
    }
    function barrow(value, level, text) { return h("div", { class: "barrow" }, bar(value, level), h("span", { text: text === undefined ? pc(value, 0) : text })); }
    function card(title, extra) {
        var c = h("section", { class: "card" });
        if (title) c.appendChild(h("h2", null, h("span", { text: title }), extra || null));
        for (var i = 2; i < arguments.length; i++) add(c, arguments[i]);
        return c;
    }
    function spark(values, max) {
        var w = 300, hh = 44, s = svg("svg", { viewBox: "0 0 " + w + " " + hh, preserveAspectRatio: "none", "class": "spark" }), n = values.length;
        if (n < 2) return s;
        var m = max || Math.max.apply(null, values) || 1, pts = values.map(function (v, i) {
            return (i * w / (n - 1)).toFixed(1) + "," + (hh - 2 - Math.min(v, m) / m * (hh - 4)).toFixed(1);
        });
        s.appendChild(svg("polygon", { "class": "ar", points: pts.join(" ") + " " + w + "," + hh + " 0," + hh }));
        s.appendChild(svg("polyline", { "class": "ln", points: pts.join(" "), "vector-effect": "non-scaling-stroke" }));
        return s;
    }
    /* A live trace like a heart monitor: a dark panel with a fine grid, one line per series, the newest point glowing,
       and the current values underneath. series = [{ name, pts: [[t, v], ...], unit }]; opts.max fixes the top of the scale. */
    function ecg(series, opts) {
        opts = opts || {};
        var W = 320, H = 96, P = 4, s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", "class": "ecg-svg", role: "img",
            "aria-label": series.map(function (x) { var l = x.pts.length ? x.pts[x.pts.length - 1][1] : null; return x.name + " " + (l === null ? "no data" : fmtv(l, x.unit)); }).join(", ") });
        function fmtv(v, unit) { return v === null || v === undefined ? "-" : (Math.abs(v) >= 100 ? Math.round(v) : (+v).toFixed(1)) + (unit || ""); }
        for (var gx = 0; gx <= W; gx += 16) s.appendChild(svg("line", { x1: gx, y1: 0, x2: gx, y2: H, "class": gx % 80 === 0 ? "g2" : "g1" }));
        for (var gy = 0; gy <= H; gy += 16) s.appendChild(svg("line", { x1: 0, y1: gy, x2: W, y2: gy, "class": gy % 48 === 0 ? "g2" : "g1" }));
        var all = [], t0 = Infinity, t1 = -Infinity;
        series.forEach(function (x) { x.pts.forEach(function (p) { if (p[1] !== null && p[1] !== undefined) { all.push(p[1]); t0 = Math.min(t0, p[0]); t1 = Math.max(t1, p[0]); } }); });
        var top = opts.max || Math.max(1, Math.max.apply(null, all.length ? all : [1]) * 1.25);
        if (t1 <= t0) { t0 = t1 - 1; }
        var span = Math.max(t1 - t0, opts.window || 0);
        series.forEach(function (x, i) {
            var pts = x.pts.filter(function (p) { return p[1] !== null && p[1] !== undefined; });
            if (!pts.length) return;
            var xy = pts.map(function (p) { return [W - P - (t1 - p[0]) / span * (W - 2 * P), H - P - Math.min(p[1], top) / top * (H - 2 * P)]; });
            if (xy.length === 1) xy.unshift([xy[0][0] - 6, xy[0][1]]);
            s.appendChild(svg("polyline", { "class": "ln s" + i, points: xy.map(function (q) { return q[0].toFixed(1) + "," + q[1].toFixed(1); }).join(" "), "vector-effect": "non-scaling-stroke" }));
            var last = xy[xy.length - 1];
            s.appendChild(svg("circle", { "class": "glow s" + i, cx: last[0].toFixed(1), cy: last[1].toFixed(1), r: 5 }));
            s.appendChild(svg("circle", { "class": "dot s" + i, cx: last[0].toFixed(1), cy: last[1].toFixed(1), r: 2.4 }));
        });
        var legend = h("div", { class: "ecg-legend" }, series.map(function (x, i) {
            var l = null;
            for (var k = x.pts.length - 1; k >= 0; k--) { if (x.pts[k][1] !== null && x.pts[k][1] !== undefined) { l = x.pts[k][1]; break; } }
            return h("span", { class: "k" }, h("i", { class: "s" + i }), x.name + " ", h("b", { text: fmtv(l, x.unit) }));
        }), opts.note ? h("span", { class: "muted", text: opts.note }) : null);
        return h("div", { class: "ecg" + (opts.small ? " small" : "") }, s, legend);
    }
    /* Small live graphs in the style of the Windows Task Manager performance view: a light panel in the page's own colours,
       a faint grid, the newest value at the right edge, a filled area under a thin line, the name and current value above.
       series = [{ name, pts: [[t, v], ...] }] (a second series is drawn as a dashed line); opts: { title, color, max, unit, fmt } */
    var TM = { cpu: "#117dbb", mem: "#8b12ae", net: "#a74f01", disk: "#4da60c", lat: "#c2410c" };
    function tmGraph(title, series, opts) {
        opts = opts || {};
        var W = 300, H = 70, col = opts.color || TM.cpu, fmt = opts.fmt || function (v) { return (Math.abs(v) >= 100 ? Math.round(v) : (+v).toFixed(1)) + (opts.unit || ""); };
        var clean = series.map(function (x) { return { name: x.name, pts: x.pts.filter(function (p) { return p[1] !== null && p[1] !== undefined; }) }; });
        var all = [], t1 = -Infinity, t0 = Infinity;
        clean.forEach(function (x) { x.pts.forEach(function (p) { all.push(p[1]); t1 = Math.max(t1, p[0]); t0 = Math.min(t0, p[0]); }); });
        var top = opts.max || Math.max(1, Math.max.apply(null, all.length ? all : [1]) * 1.15);
        var span = Math.max(opts.window || 0, t1 - t0, 1);
        var s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", "class": "tm-svg", role: "img",
            "aria-label": title + ": " + clean.map(function (x) { var l = x.pts[x.pts.length - 1]; return x.name + " " + (l ? fmt(l[1]) : "no data"); }).join(", ") });
        s.style.setProperty("--tm", col);
        for (var gx = W; gx >= 0; gx -= W / 10) s.appendChild(svg("line", { x1: gx, y1: 0, x2: gx, y2: H, "class": "tm-grid" }));
        for (var gy = 0; gy <= H; gy += H / 4) s.appendChild(svg("line", { x1: 0, y1: gy, x2: W, y2: gy, "class": "tm-grid" }));
        clean.forEach(function (x, i) {
            if (x.pts.length < 2) return;
            var xy = x.pts.map(function (p) { return [W - (t1 - p[0]) / span * W, H - Math.min(p[1], top) / top * (H - 2) - 1]; });
            var line = xy.map(function (q) { return q[0].toFixed(1) + "," + q[1].toFixed(1); }).join(" ");
            if (i === 0) s.appendChild(svg("polygon", { "class": "tm-area", points: xy[0][0].toFixed(1) + "," + H + " " + line + " " + xy[xy.length - 1][0].toFixed(1) + "," + H }));
            s.appendChild(svg("polyline", { "class": "tm-line" + (i ? " second" : ""), points: line, "vector-effect": "non-scaling-stroke" }));
        });
        var cur = clean.map(function (x) { var l = x.pts[x.pts.length - 1]; return (clean.length > 1 ? x.name + " " : "") + (l ? fmt(l[1]) : "-"); }).join("  /  ");
        var box = h("div", { class: "tm" }, h("div", { class: "tm-head" }, h("span", { class: "tm-title", text: title }), h("span", { class: "tm-val", text: cur })), s);
        box.style.setProperty("--tm", col);
        if (opts.max) box.appendChild(h("div", { class: "tm-scale" }, h("span", { text: opts.scale || "" }), h("span", { text: fmt(opts.max) })));
        return box;
    }
    function rate(v) { v = v || 0; return v >= 1048576 ? (v / 1048576).toFixed(1) + " MB/s" : v >= 1024 ? Math.round(v / 1024) + " KB/s" : Math.round(v) + " B/s"; }
    function table(heads, rows, nums, rowClass) {
        var t = h("table", { class: "tbl" }), tr = h("tr");
        heads.forEach(function (x, i) { tr.appendChild(h("th", { text: x, "class": nums && nums.indexOf(i) >= 0 ? "num" : "" })); });
        t.appendChild(h("thead", null, tr));
        var tb = h("tbody");
        rows.forEach(function (r, ri) {
            var row = h("tr", { class: rowClass ? rowClass(ri) : "" });
            r.forEach(function (c, i) { row.appendChild(h("td", { "class": nums && nums.indexOf(i) >= 0 ? "num" : "" }, c)); });
            tb.appendChild(row);
        });
        t.appendChild(tb);
        return h("div", { class: "scroll" }, t);
    }
    function lineChart(series) {
        var w = 900, hh = 220, L = 38, B = 24, T = 10, R = 12, s = svg("svg", { viewBox: "0 0 " + w + " " + hh, "class": "chart", role: "img", "aria-label": "Health score over time" });
        [0, 50, 100].forEach(function (g) {
            var y = T + (hh - T - B) * (1 - g / 100);
            s.appendChild(svg("line", { x1: L, x2: w - R, y1: y, y2: y, "class": "grid" }));
            s.appendChild(svg("text", { x: L - 6, y: y + 4, "text-anchor": "end", "class": "axis" }, String(g)));
        });
        if (series.length) {
            var x0 = series[0][0], x1 = series[series.length - 1][0] || x0 + 1, span = Math.max(1, x1 - x0);
            var pts = series.map(function (p) { return [L + (series.length === 1 ? (w - L - R) / 2 : (p[0] - x0) / span * (w - L - R)), T + (hh - T - B) * (1 - p[1] / 100), p]; });
            s.appendChild(svg("polyline", { "class": "ln", points: pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ") }));
            pts.slice(-120).forEach(function (p) {
                var c = svg("circle", { cx: p[0].toFixed(1), cy: p[1].toFixed(1), r: 3.5, "class": "dot " + lvl(100 - p[2][1], [20, 40]) });
                c.appendChild(svg("title", null, when(p[2][0]) + " - score " + p[2][1]));
                s.appendChild(c);
            });
            s.appendChild(svg("text", { x: L, y: hh - 6, "class": "axis" }, new Date(x0 * 1000).toLocaleDateString()));
            s.appendChild(svg("text", { x: w - R, y: hh - 6, "text-anchor": "end", "class": "axis" }, new Date(x1 * 1000).toLocaleDateString()));
        } else {
            s.appendChild(svg("text", { x: w / 2, y: hh / 2, "text-anchor": "middle", "class": "axis" }, "No history yet - run a health check"));
        }
        return s;
    }

    /* ---------------------------------------------------------------- sign in and shell */
    function stopPage() { if (S.cleanup) { S.cleanup(); S.cleanup = null; } }
    function showLogin() {
        stopPage();
        S.me = null;
        clear(root);
        var user = h("input", { type: "text", autocomplete: "username", placeholder: "User name", required: true, autofocus: true });
        var pass = h("input", { type: "password", autocomplete: "current-password", placeholder: "Password", required: true });
        var msg = h("div", { class: "err", role: "alert" });
        var form = h("form", { onsubmit: function (e) {
            e.preventDefault();
            msg.textContent = "";
            api("POST", "/api/login", { user: user.value, password: pass.value }).then(function (me) { S.me = me; shell(); }).catch(function (x) { msg.textContent = x.message; pass.value = ""; });
        } }, user, pass, h("button", { class: "primary", type: "submit", text: "Sign in" }), msg);
        root.appendChild(h("div", { class: "login" }, h("div", { class: "card" }, h("img", { src: "/lrs_logo.png", alt: "LRS Services" }), h("h1", { text: "LRS Vitals" }), form)));
        user.focus();
    }
    function allowed(p) { return (!p.admin || S.me.role === "admin") && (!S.me.group || ["fleet", "reports", "account", "help"].indexOf(p.id) >= 0); }
    var PAGES = [
        { id: "dashboard", title: "Dashboard", draw: drawDashboard },
        { id: "fleet", title: "Servers", draw: drawFleet },
        { id: "reports", title: "Reports", draw: drawReports },
        { id: "scan", title: "Server scan", draw: drawScan },
        { id: "settings", title: "Settings", draw: drawSettings, admin: true },
        { id: "operations", title: "Operations", draw: drawOperations, admin: true },
        { id: "account", title: "Account", draw: drawAccount },
        { id: "help", title: "Help", draw: drawHelp },
        { id: "server", title: "Server", draw: drawServer, hidden: true, menu: "fleet" }
    ];
    var main;
    /* How often live data is refreshed on screen; kept per browser. */
    var REFRESH = [[0, "Off (manual)"], [2, "Every 2 s"], [5, "Every 5 s"], [10, "Every 10 s"], [30, "Every 30 s"], [60, "Every minute"]];
    function refreshSeconds() { try { var v = +localStorage.getItem("lshme-refresh"); return REFRESH.some(function (x) { return x[0] === v; }) && localStorage.getItem("lshme-refresh") !== null ? v : 5; } catch (e) { return 5; } }
    function setRefresh(n) { try { localStorage.setItem("lshme-refresh", String(n)); } catch (e) { /* storage may be blocked */ } }
    /* Fixed line icons (static strings, no user data): one per page, so the menu can be scanned at a glance. */
    var ICONS = {
        dashboard: "<path d='M3 12l9-8 9 8'/><path d='M5 10v10h5v-6h4v6h5V10'/>",
        fleet: "<rect x='3' y='4' width='18' height='6' rx='1.5'/><rect x='3' y='14' width='18' height='6' rx='1.5'/><path d='M7 7h.01M7 17h.01'/>",
        reports: "<path d='M7 3h8l4 4v14H7z'/><path d='M15 3v4h4M10 12h6M10 16h6'/>",
        scan: "<circle cx='11' cy='11' r='6'/><path d='M21 21l-5-5'/>",
        settings: "<circle cx='12' cy='12' r='3'/><path d='M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14 3h-4l-.6 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2L10 21h4l.6-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z'/>",
        operations: "<path d='M4 6h16M4 12h16M4 18h10'/><circle cx='18' cy='18' r='2'/>",
        account: "<circle cx='12' cy='8' r='4'/><path d='M4 21c1.5-4 5-6 8-6s6.5 2 8 6'/>",
        help: "<circle cx='12' cy='12' r='9'/><path d='M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01'/>"
    };
    var SYMBOLS = {
        server: "<rect x='4' y='3' width='16' height='7' rx='2'/><rect x='4' y='14' width='16' height='7' rx='2'/><path d='M8 6.5h.01M8 17.5h.01M12 6.5h5M12 17.5h5'/>",
        storage: "<ellipse cx='12' cy='5.5' rx='7.5' ry='2.5'/><path d='M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13'/><path d='M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5'/>",
        cpu: "<rect x='6' y='6' width='12' height='12' rx='2'/><rect x='9.5' y='9.5' width='5' height='5' rx='1'/><path d='M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3'/>",
        disk: "<circle cx='12' cy='12' r='8.5'/><circle cx='12' cy='12' r='2.5'/><path d='M12 3.5v3'/>",
        network: "<circle cx='12' cy='5' r='2.5'/><circle cx='5' cy='19' r='2.5'/><circle cx='19' cy='19' r='2.5'/><path d='M12 7.5v4M12 11.5l-5.5 5.5M12 11.5l5.5 5.5'/>",
        pulse: "<path d='M3 12h4l2.5-6 4 12 2.5-6H21'/>"
    };
    function sym(id, cls) {
        var s = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false", "class": "sym" + (cls ? " " + cls : "") });
        s.innerHTML = SYMBOLS[id] || SYMBOLS.pulse;
        return s;
    }
    function icon(id) {
        var s = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" });
        s.innerHTML = ICONS[id] || ICONS.help;
        return s;
    }
    var THEMES = [["balanced", "Balanced (default)"], ["light", "Light"], ["dusk", "Dusk (soft dark)"], ["forest", "Forest"], ["contrast", "High contrast"], ["system", "Match my computer"]];
    function theme(t, keep) {
        if (t === "dark") t = "dusk";
        if (!THEMES.some(function (x) { return x[0] === t; })) t = "balanced";
        if (t === "balanced") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
        if (keep !== false) { try { localStorage.setItem("lshme-theme", t); } catch (e) { /* storage may be blocked */ } }
        return t;
    }
    function savedTheme() { try { return localStorage.getItem("lshme-theme") || "balanced"; } catch (e) { return "balanced"; } }
    function navAuto() { try { return localStorage.getItem("lshme-nav") === "auto"; } catch (e) { return false; } }
    function setNavAuto(on) { try { localStorage.setItem("lshme-nav", on ? "auto" : "pinned"); } catch (e) { /* storage may be blocked */ } }
    function shell() {
        var current = theme(savedTheme(), false);
        clear(root);
        var nav = h("nav", { "aria-label": "Main menu" });
        if (S.me.branding && S.me.branding.color) document.documentElement.style.setProperty("--accent", S.me.branding.color);
        PAGES.forEach(function (p) {
            if (!allowed(p) || p.hidden) return;
            nav.appendChild(h("a", { href: "#/" + p.id, "data-page": p.id, onclick: function () { if (navAuto()) this.blur(); } }, icon(p.id), h("span", { text: p.title })));
        });
        var br = S.me.branding || {};
        var pin = h("button", { class: "pin", type: "button" });
        function paintPin() {
            var auto = navAuto();
            document.documentElement.classList.toggle("nav-auto", auto);
            pin.setAttribute("aria-pressed", auto ? "false" : "true");
            pin.setAttribute("title", auto ? "Keep the menu open (pin)" : "Fold the menu to icons; it opens when you point at it");
            pin.setAttribute("aria-label", pin.getAttribute("title"));
            pin.innerHTML = auto ? "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M9 6l6 6-6 6'/></svg>" : "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M15 6l-6 6 6 6'/></svg>";
        }
        pin.addEventListener("click", function () { setNavAuto(!navAuto()); paintPin(); pin.blur(); });   // a mouse click must not hold the menu open
        paintPin();
        var top = h("header", { class: "top" }, h("div", { class: "in" }, pin,
            h("div", { class: "brand" }, h("span", { class: "mark", "aria-hidden": "true", text: "LRS" }), h("img", { src: "/lrs_logo.png", alt: "LRS Services", class: "lrs" }),
                br.logo ? h("img", { src: "/brand/logo?t=" + Date.now(), alt: br.customer || "customer logo", class: "cust" }) : (br.customer ? h("span", { class: "cust", text: br.customer }) : null)),
            nav,
            h("div", { class: "who" }, h("span", { class: "host", text: S.me.host }), h("span", { text: S.me.user + " (" + S.me.role + ")" }),
                h("label", { class: "pick" }, h("span", { text: "Theme" }), (function () {
                    var sel = h("select", { "aria-label": "Colour theme" }, THEMES.map(function (x) { return h("option", { value: x[0], text: x[1], selected: x[0] === current }); }));
                    sel.addEventListener("change", function () { theme(sel.value); });
                    return sel;
                })()),
                h("label", { class: "pick" }, h("span", { text: "Refresh" }), (function () {
                    var sel = h("select", { "aria-label": "Refresh interval" }, REFRESH.map(function (x) { return h("option", { value: x[0], text: x[1], selected: x[0] === refreshSeconds() }); }));
                    sel.addEventListener("change", function () { setRefresh(+sel.value); route(); });
                    return sel;
                })()),
                h("div", { class: "btns" }, h("button", { class: "small", text: "Sign out", onclick: function () { api("POST", "/api/logout").then(showLogin, showLogin); } })))));
        main = h("main", { id: "content", tabindex: "-1", "aria-label": "Page content" });
        root.appendChild(h("a", { class: "skip", href: "#content", onclick: function (e) { e.preventDefault(); main.focus(); }, text: "Skip to content" }));
        root.appendChild(top);
        root.appendChild(main);
        root.appendChild(h("footer", { text: S.me.app + " " + S.me.version + " - LRS Services Pvt Ltd, Mumbai" + (S.me.tls ? "" : " - not encrypted (local access only)") }));
        route();
    }
    function route() {
        if (!S.me) return;
        var parts = (location.hash || "#/dashboard").replace(/^#\//, "").split("/");
        var page = PAGES.filter(function (p) { return p.id === parts[0] && allowed(p); })[0] || PAGES.filter(allowed)[0];
        stopPage();
        Array.prototype.forEach.call(document.querySelectorAll(".top nav a"), function (a) {
            var on = a.getAttribute("data-page") === (page.menu || page.id);
            a.className = on ? "on" : "";
            if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
            a.setAttribute("title", a.textContent);
        });
        clear(main);
        document.title = page.title + " - " + S.me.host + " - LRS Vitals";
        main.classList.remove("enter");
        void main.offsetWidth;                                    // restart the entrance for the new page
        main.classList.add("enter");
        clearTimeout(S.enterTimer);
        S.enterTimer = setTimeout(function () { main.classList.remove("enter"); }, 700);   // later refreshes appear without motion
        page.draw(main, parts.slice(1));
    }
    window.addEventListener("hashchange", route);

    /* ---------------------------------------------------------------- dashboard */
    function runWidget(onDone) {
        var btn = h("button", { class: "primary", text: "Run health check now" }), prog = h("i"), label = h("span", { class: "muted" });
        var wrap = h("div", { class: "row" }, btn, h("div", { class: "progress" }, prog), label);
        wrap.querySelector(".progress").style.display = "none";
        var timer = null;
        function poll() {
            api("GET", "/api/run").then(function (j) {
                var p = wrap.querySelector(".progress");
                if (j.running) {
                    p.style.display = ""; btn.disabled = true;
                    prog.style.width = (j.total ? 100 * j.progress / j.total : 5) + "%";
                    label.textContent = "Checking: " + (j.title || "...");
                    timer = setTimeout(poll, 1000);
                } else {
                    p.style.display = "none"; btn.disabled = false; label.textContent = "";
                    if (j.error) toast(j.error, "bad");
                    else if (j.result && j.finished && (Date.now() / 1000 - j.finished) < 15) { toast("Health check complete: score " + j.result.score + " - " + j.result.status, "good"); if (onDone) onDone(); }
                }
            }).catch(function () { /* signed out */ });
        }
        btn.addEventListener("click", function () {
            btn.disabled = true;
            api("POST", "/api/run").then(poll).catch(function (e) { fail(e); btn.disabled = false; });
        });
        if (S.me.role !== "admin") btn.style.display = "none";
        poll();
        wrap.stop = function () { clearTimeout(timer); };
        return wrap;
    }
    function drawDashboard(box) {
        var bannerBox = h("div"), live = h("div", { class: "stack" });
        var alerts = alertsCard(), fleetLive = liveFleetCard(), findings = exclusionsCard();
        box.appendChild(h("div", { class: "stack" }, bannerBox, fleetLive.el, alerts.el, findings.el, live));
        var run = runWidget(function () { tick(); });
        var stopped = false, timer = null;
        var lastHl = null;
        S.onFleet = function () { paintBanner(lastHl); };
        function why(s) { return s.status === "OUTDATED" ? "needs an upgrade" : s.status === "UNREACHABLE" ? "cannot be reached" : s.status === "CRITICAL" ? "critical" : s.status === "WARNING" ? "warning" : s.status === "STALE" ? "watcher not reporting" : "no live data"; }
        function paintBanner(hl) {
            lastHl = hl;
            steady(bannerBox);
            var inner = h("div", { class: "row space" }), F = S.fleet;
            if (F && F.enabled && (F.servers.length > 1 || (F.san || []).length)) {     // the estate first; this server's own score is one line below
                var sv = F.servers, sa = F.san || [];
                var badS = sv.filter(function (s) { return s.status !== "HEALTHY" && !(s.status === "UNKNOWN" && s.health_status === "HEALTHY"); });
                var badA = sa.filter(function (a) { return a.status !== "HEALTHY" && a.status !== "WAITING"; });
                var crit = badS.some(function (s) { return s.status === "CRITICAL" || s.status === "UNREACHABLE"; }) || badA.some(function (a) { return a.status === "CRITICAL" || a.status === "UNREACHABLE"; });
                var st = crit ? "CRITICAL" : (badS.length || badA.length) ? "WARNING" : "HEALTHY";
                var parts = badS.map(function (s) { return s.name + " " + why(s); }).concat(badA.map(function (a) { return a.name + " " + (a.status === "UNREACHABLE" ? "cannot be reached" : (a.failed_drives ? a.failed_drives + " failed drive" + (a.failed_drives > 1 ? "s" : "") : a.status.toLowerCase())); }));
                var total = sv.length + " server" + (sv.length === 1 ? "" : "s") + (sa.length ? " and " + sa.length + " storage system" + (sa.length === 1 ? "" : "s") : "");
                var names = badS.map(function (s) { return [s.name, why(s)]; }).concat(badA.map(function (a) { return [a.name, a.status === "UNREACHABLE" ? "cannot be reached" : (a.failed_drives ? a.failed_drives + " failed drive" + (a.failed_drives > 1 ? "s" : "") : a.status.toLowerCase())]; }));
                var sent = h("div", { class: "fb-sentence" });
                if (names.length) {
                    sent.appendChild(document.createTextNode(names.length + " of " + (sv.length + sa.length) + " need" + (names.length === 1 ? "s" : "") + " attention: "));
                    names.forEach(function (n, i) { if (i) sent.appendChild(document.createTextNode(", ")); sent.appendChild(h("b", { class: "fb-name", text: n[0] })); sent.appendChild(document.createTextNode(" " + n[1])); });
                    sent.appendChild(document.createTextNode("."));
                } else sent.appendChild(document.createTextNode("All " + total + " are healthy."));
                var tn = st === "HEALTHY" ? "ok" : st === "WARNING" ? "warn" : "crit";
                inner.appendChild(h("div", { class: "fleetbar alertbar tone-" + tn },
                    h("span", { class: "sym-badge big" }, sym(st === "HEALTHY" ? "pulse" : "server")),
                    h("div", null, h("div", { class: "row fb-title" }, h("b", { text: st === "HEALTHY" ? "All healthy" : st === "WARNING" ? "Needs attention" : "Action needed" }),
                            names.length ? h("span", { class: "pill tone-" + tn, text: names.length + (names.length === 1 ? " ASSET" : " ASSETS") + " DEGRADED" }) : h("span", { class: "pill tone-ok", text: "ALL SYSTEMS NOMINAL" })),
                        sent,
                        hl ? h("div", { class: "muted small", text: "This server (" + S.me.host + "): score " + (hl.score === null ? "-" : hl.score) + ", " + hl.status.toLowerCase() + ", last health check " + hl.when.slice(0, 16) }) : null)));
            } else if (hl) {
                var c = hl.counts, st = hl.status;
                inner.appendChild(h("div", { class: "row" }, ring(hl.score || 0, statusLvl(st), String(hl.score === null ? "-" : hl.score), true),
                    h("div", null, h("div", { class: "big" }, h("span", { text: st + " " }), badge(st)),
                        h("div", { class: "muted", text: "Last health check " + hl.when.slice(0, 16) }),
                        h("div", null, h("span", { class: "chip crit", text: c.CRITICAL + " critical" }), h("span", { class: "chip warn", text: c.WARNING + " warning" }),
                            h("span", { class: "chip", text: c.INFO + " info" })))));
            } else {
                inner.appendChild(h("div", null, h("div", { class: "big", text: "No health check yet" }), h("div", { class: "muted", text: "Run one to see the score and a report." })));
            }
            inner.appendChild(run);
            var F2 = S.fleet, sanN = F2 && F2.san ? F2.san.length : 0;
            var clock = h("span", { class: "strip-item mono" });
            (function tickClock() { if (!document.body.contains(bannerBox)) return; var d = new Date(); clock.textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); setTimeout(tickClock, 1000); })();
            bannerBox.appendChild(h("div", { class: "status-strip", role: "status" },
                h("span", { class: "strip-item live" }, h("i", { class: "live-dot" }), "LIVE MONITORING"), clock,
                sanN ? h("span", { class: "pill tone-ok", text: "SAN checked " + (F2.san_updated ? ago(F2.san_updated) : "soon") }) : null,
                h("span", { class: "strip-spacer" }),
                h("span", { class: "strip-item muted", text: (S.me.branding && S.me.branding.customer ? S.me.branding.customer + "  -  " : "") + S.me.user + " (" + S.me.role + ")" })));
            bannerBox.appendChild(card(null, null, inner));
        }
        function paint(d) {
            var s = d.sample, L = d.limits, cpu = s.cpu, mem = s.mem, H = s.history;
            paintBanner(d.health);
            steady(live);
            var g = h("div", { class: "grid g4" });
            g.appendChild(card("CPU", h("span", { class: "muted", text: cpu.cores + " cores" }),
                h("div", { class: "row" }, ring(cpu.total, lvl(cpu.total, L.cpu), pc(cpu.total, 0)),
                    h("dl", { class: "kv" }, h("dt", { text: "user" }), h("dd", { text: pc(cpu.user) }), h("dt", { text: "system" }), h("dd", { text: pc(cpu.system) }),
                        h("dt", { text: "iowait" }), h("dd", { text: pc(cpu.iowait) }), h("dt", { text: "steal" }), h("dd", { text: pc(cpu.steal) }))), spark(H.cpu, 100)));
            g.appendChild(card("Memory", h("span", { class: "muted", text: size(mem.total) }),
                h("div", { class: "row" }, ring(mem.pct, lvl(mem.pct, L.mem), pc(mem.pct, 0)),
                    h("dl", { class: "kv" }, h("dt", { text: "used" }), h("dd", { text: size(mem.used) }), h("dt", { text: "available" }), h("dd", { text: size(mem.available) }),
                        h("dt", { text: "cache" }), h("dd", { text: size(mem.cache) }))), spark(H.mem, 100)));
            g.appendChild(card("Swap", h("span", { class: "muted", text: size(mem.swap_total) }),
                mem.swap_total ? h("div", { class: "row" }, ring(mem.swap_pct, lvl(mem.swap_pct, L.swap), pc(mem.swap_pct, 0)),
                    h("dl", { class: "kv" }, h("dt", { text: "used" }), h("dd", { text: size(mem.swap_used) }))) : h("p", { class: "muted", text: "No swap configured." })));
            var perCore = s.load[0] / Math.max(1, cpu.cores);
            g.appendChild(card("Load and uptime", h("span", { class: "muted", text: s.hostname }),
                h("div", { class: "big", text: s.load[0].toFixed(2) }), h("div", { class: "muted", text: "5 min " + s.load[1].toFixed(2) + " - 15 min " + s.load[2].toFixed(2) + " - per core " + perCore.toFixed(2) }),
                spark(H.load), h("div", { class: "pad", text: "Up " + uptime(s.uptime) })));
            live.appendChild(g);
            var cores = h("div", { class: "grid g4" });
            cpu.per_core.forEach(function (v, i) { cores.appendChild(h("div", null, h("div", { class: "muted", text: "cpu" + i }), barrow(v, lvl(v, L.cpu)))); });
            live.appendChild(card("CPU cores", null, cores));
            live.appendChild(card("Filesystems and mounts", h("span", { class: "muted", text: s.mounts.length + " mounted" }), table(
                ["Mounted on", "Device", "Type", "Size", "Used", "Free", "Usage", "Inodes", ""], s.mounts.map(function (m) {
                    return [h("b", { text: m.mount }), m.device, m.fstype, size(m.total), size(m.used), size(m.free), barrow(m.pct, lvl(m.pct, L.disk)),
                        pc(m.inode_pct, 0), m.error ? badge("ERROR") : (m.readonly ? badge("READ-ONLY") : "")];
                }), [3, 4, 5, 7])));
            var g2 = h("div", { class: "grid g2" });
            g2.appendChild(card("Network", null, table(["Interface", "State", "In /s", "Out /s", "Total in", "Total out", "Errors"], s.net.map(function (n) {
                return [h("b", { text: n.iface }), n.state, rate(n.rx_bps), rate(n.tx_bps), size(n.rx_total), size(n.tx_total), n.errors];
            }), [2, 3, 4, 5, 6]), h("div", { class: "muted", text: "In" }), spark(H.rx), h("div", { class: "muted", text: "Out" }), spark(H.tx)));
            g2.appendChild(card("Disk I/O", null, s.disks.length ? table(["Device", "Read /s", "Write /s", "Busy"], s.disks.map(function (d) {
                return [h("b", { text: d.name }), rate(d.read_bps), rate(d.write_bps), barrow(d.util, lvl(d.util, L.cpu))];
            }), [1, 2]) : h("p", { class: "muted", text: "No physical disks reported." })));
            live.appendChild(g2);
            var g3 = h("div", { class: "grid g2" }), procs = s.procs;
            function ptable(rows) { return table(["PID", "User", "CPU %", "Memory", "Command"], rows.map(function (p) { return [p.pid, p.user, p.cpu.toFixed(1), size(p.rss), p.name]; }), [0, 2, 3]); }
            g3.appendChild(card("Busiest processes", h("span", { class: "muted", text: procs.total + " processes, " + procs.running + " running" }), ptable(procs.top_cpu)));
            g3.appendChild(card("Largest in memory", null, ptable(procs.top_mem)));
            live.appendChild(g3);
        }
        function tick() {
            api("GET", "/api/live").then(function (d) { if (!stopped) paint(d); }).catch(function (e) { if (!stopped && e.message !== "Signed out") { clear(live); live.appendChild(h("p", { class: "err", text: e.message })); } });
        }
        tick();
        var every = refreshSeconds() * 1000;
        timer = every ? setInterval(tick, every) : null;
        var atimer = every ? setInterval(function () { alerts.load(); fleetLive.load(); }, Math.max(every, 5000)) : null;
        S.cleanup = function () { stopped = true; if (timer) clearInterval(timer); if (atimer) clearInterval(atimer); run.stop(); };
    }

    /* ---------------------------------------------------------------- all servers, live */
    function liveFleetCard() {
        var box = h("div"), data = null;
        function dur(s) { s = Math.max(0, Math.round(s || 0)); return s < 90 ? s + " s" : s < 5400 ? Math.round(s / 60) + " min" : s < 172800 ? Math.round(s / 3600) + " h" : Math.round(s / 86400) + " days"; }
        function tone(s) { return s === "CRITICAL" || s === "UNREACHABLE" ? "crit" : s === "WARNING" || s === "STALE" || s === "OUTDATED" ? "warn" : s === "HEALTHY" ? "ok" : "none"; }
        function word(s) { return s === "HEALTHY" ? "Healthy" : s === "WARNING" ? "Warning" : s === "CRITICAL" ? "Critical" : s === "UNREACHABLE" ? "Unreachable" : s === "OUTDATED" ? "Reachable - LRS Vitals needs an upgrade" : s === "STALE" ? "Watcher not reporting" : s === "WAITING" ? "Waiting for the first answer" : "No live data (watcher not running)"; }
        function open(el, href, label) {                     // a whole card opens a page: by mouse, Enter or Space
            el.setAttribute("role", "link"); el.setAttribute("tabindex", "0"); el.setAttribute("aria-label", label); el.classList.add("clickable");
            el.addEventListener("click", function (e) { if (e.target.closest("a,button,select,input")) return; location.hash = href; });
            el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); location.hash = href; } });
            return el;
        }
        function pill(text, tone) { return h("span", { class: "pill tone-" + (tone || "info"), text: text }); }
        function sanTile(a) {
            var t = tone(a.status);
            var el = h("div", { class: "srv san wide st-" + (t === "ok" ? "HEALTHY" : t === "warn" ? "WARNING" : t === "crit" ? "CRITICAL" : "UNKNOWN") });
            var title = h("div", { class: "who-line" }, h("span", { class: "sym-badge" }, sym("storage")),
                h("div", { class: "who-txt" }, h("div", { class: "row" }, h("b", { class: "nm", text: a.name + (a.system_name ? " (" + a.system_name + ")" : "") }), badge(a.status || "WAITING")),
                    h("div", { class: "muted small", text: [a.model || a.make, a.firmware ? "Firmware " + a.firmware : "", a.volumes !== undefined ? a.volumes + " volumes" : ""].filter(Boolean).join("  -  ") })));
            var right = h("div", { class: "row san-pills" });
            (a.controllers || []).forEach(function (c) { right.appendChild(pill(c.name.replace("Controller ", "Ctrl ") + (c.status === "optimal" ? "  OK" : "  " + c.status.toUpperCase()), c.status === "optimal" ? "ok" : "crit")); });
            if (a.drives) right.appendChild(pill(a.drives + " drives" + (a.failed_drives ? ", " + a.failed_drives + " failed" : ", none failed"), a.failed_drives ? "crit" : "ok"));
            el.appendChild(h("div", { class: "row space san-head" }, title, right));
            if (a.status === "WAITING") { el.appendChild(h("p", { class: "muted small", text: "Checking the storage (every few minutes)..." })); return open(el, "#/fleet", "SAN storage " + a.name); }
            if (a.error) { el.appendChild(h("p", { class: "small", text: a.error + (a.fetched ? " Checked " + dur(Date.now() / 1000 - a.fetched) + " ago." : "") })); return open(el, "#/fleet", "SAN storage " + a.name); }
            var body = h("div", { class: "san-body" });
            var pools = h("div", { class: "pool-box" });
            (a.pools || []).slice(0, 2).forEach(function (p) {
                var lv = p.pct >= 90 ? "crit" : p.pct >= 80 ? "warn" : "ok", used = p.free !== undefined && p.pct ? p.free / (1 - p.pct / 100) * (p.pct / 100) : null;
                pools.appendChild(h("div", { class: "pool" }, h("div", { class: "row space" }, h("b", { text: "Pool " + p.name }), pill(Math.round(p.pct) + "% used", lv)), bar(p.pct, lv),
                    h("div", { class: "row space small muted" }, h("span", { text: used !== null ? "Used " + size(used) : "" }), h("span", { text: p.free !== undefined ? "Free " + size(p.free) : "" }))));
            });
            if (!(a.pools || []).length) pools.appendChild(h("p", { class: "muted small", text: "No pool figures yet." }));
            body.appendChild(pools);
            var pf = a.perf || {}, ss = a.series || [];
            if (ss.length) {
                var lat = pf.latency_ms || 0, slow = lat >= 20;
                body.appendChild(h("div", { class: "tm-row minis" },
                    tmGraph("Throughput IOPS", [{ name: "Read", pts: ss.map(function (p) { return [p[0], p[1]]; }) }, { name: "Write", pts: ss.map(function (p) { return [p[0], p[2]]; }) }], { color: TM.disk, fmt: function (v) { return Math.round(v); } }),
                    tmGraph("Data rate", [{ name: "Read", pts: ss.map(function (p) { return [p[0], p[3]]; }) }, { name: "Write", pts: ss.map(function (p) { return [p[0], p[4]]; }) }], { color: TM.disk, unit: " MB/s" }),
                    (function () { var g = tmGraph("Response time", [{ name: "Response", pts: ss.map(function (p) { return [p[0], p[5]]; }) }], { color: TM.lat, unit: " ms" }); if (slow) g.classList.add("alarm"); return g; })()));
            } else if (pf.read_iops !== undefined) body.appendChild(h("p", { class: "muted small", text: "I/O " + Math.round((pf.read_iops || 0) + (pf.write_iops || 0)) + " IOPS, " + ((pf.read_mbps || 0) + (pf.write_mbps || 0)).toFixed(1) + " MB/s" + (pf.latency_ms ? ", " + pf.latency_ms.toFixed(1) + " ms" : "") }));
            el.appendChild(body);
            if ((a.volumes_top || []).length) {
                el.appendChild(h("h4", { class: "sub2", text: "Busiest volumes now" }));
                el.appendChild(table(["Volume", "IOPS", "Throughput", "Latency", "Status"], a.volumes_top.slice(0, 6).map(function (v) {
                    var lv = v.ms >= 20 ? "warn" : "ok";
                    return [h("b", { text: v.name }), Math.round(v.iops) + " IOPS", v.mbps.toFixed(1) + " MB/s", h("span", { class: lv === "warn" ? "warnink" : "", text: v.ms.toFixed(1) + " ms" }), pill(lv === "warn" ? "Latency high" : "Healthy", lv)];
                }), [1, 2, 3]));
            }
            var foot = [];
            if (a.hardware_bad && a.hardware_bad.length) foot.push("Not OK: " + a.hardware_bad.join(", "));
            if (a.unreachable && a.unreachable.length) foot.push("one controller's management port does not answer");
            foot.push("checked " + dur(Date.now() / 1000 - a.fetched) + " ago");
            el.appendChild(h("div", { class: "card-foot small " + (a.hardware_bad && a.hardware_bad.length ? "err" : "muted"), text: foot.join("  -  ") }));
            return open(el, "#/fleet", "SAN storage " + a.name);
        }
        function tile(s) {
            var t = tone(s.status), pts = (s.series || []).map(function (p) { return p[1] === null || p[1] === undefined ? 0 : p[1]; });
            var shown = s.status === "UNKNOWN" && s.health_status ? s.health_status : s.status;     // no live data: the latest health check speaks
            var scoreTone = shown === "HEALTHY" ? "ok" : shown === "WARNING" ? "warn" : shown === "CRITICAL" ? "crit" : "info";
            var head = h("div", { class: "row space" }, h("div", { class: "who-line" }, h("span", { class: "sym-badge" }, sym("server")),
                    h("div", { class: "who-txt" }, h("div", { class: "row" }, h("b", { class: "nm", text: s.name }), s.key === "_local" ? pill("Central", "info") : null))),
                s.ok && s.score !== null && s.score !== undefined ? h("span", { class: "score-pill tone-" + scoreTone }, h("small", { text: "SCORE" }), h("b", { text: String(s.score) }))
                    : badge(shown === "UNKNOWN" ? "NO DATA" : shown === "OUTDATED" ? "UPGRADE" : (shown || "WAITING")));
            var meta = h("div", { class: "muted small", text: [s.group, s.environment, word(s.status)].filter(Boolean).join(" - ") });
            var el = h("div", { class: "srv st-" + (t === "ok" ? "HEALTHY" : t === "warn" ? "WARNING" : t === "crit" ? "CRITICAL" : "UNKNOWN") }, head, meta);
            open(el, "#/server/" + encodeURIComponent(s.key || "_local"), "Open " + s.name);
            if (!s.ok) {
                el.classList.add("compact");
                if (s.status === "WAITING") { el.appendChild(h("p", { class: "muted small", text: "Waiting for the first answer." })); return el; }
                if (s.status === "OUTDATED") {
                    el.appendChild(h("p", { class: "small", text: "Reachable, but its LRS Vitals is older and has no live data. Upgrade it once:" }));
                    el.appendChild(h("div", { class: "row" }, h("button", { class: "small", type: "button", text: "Copy upgrade command", onclick: function (e) {
                        e.stopPropagation();
                        copyText(UPGRADE_CMD, "Upgrade command copied: run it as root on " + s.name);
                    } })));
                } else {
                    el.appendChild(h("p", { class: "small", text: "Cannot connect over SSH from this server." + (s.fetched ? " Last tried " + dur(Date.now() / 1000 - s.fetched) + " ago." : "") }));
                }
                if (s.error && s.status !== "OUTDATED") el.appendChild(h("details", { class: "small" }, h("summary", { text: "Details" }), h("div", { class: "mono muted", text: s.error })));
                return el;
            }
            var ser = (s.series || []).filter(function (p) { return p[1] !== null && p[1] !== undefined; });
            if (ser.length < 2) {
                el.appendChild(h("div", { class: "tm-row minis" }, [["CPU", s.cpu, TM.cpu], ["RAM", s.mem, TM.mem]].map(function (x) {
                    return h("div", { class: "tm" }, h("div", { class: "tm-head" }, h("span", { class: "tm-title", text: x[0] }), h("span", { class: "tm-val", text: x[1] === null || x[1] === undefined ? "-" : Math.round(x[1]) + "%" })),
                        h("p", { class: "muted small", text: "live graph starts when the watcher reports" }));
                })));
            } else {
                var g = h("div", { class: "tm-row minis" },
                    tmGraph("CPU", [{ name: "CPU", pts: ser.map(function (p) { return [p[0], p[1]]; }) }], { color: TM.cpu, max: 100, unit: "%" }),
                    tmGraph("RAM", [{ name: "Memory", pts: ser.map(function (p) { return [p[0], p[2]]; }) }], { color: TM.mem, max: 100, unit: "%" }));
                if (ser.some(function (p) { return p[3] || p[4]; }))
                    g.appendChild(tmGraph("NET", [{ name: "Receive", pts: ser.map(function (p) { return [p[0], p[3]]; }) }, { name: "Send", pts: ser.map(function (p) { return [p[0], p[4]]; }) }], { color: TM.net, fmt: rate }));
                el.appendChild(g);
            }
            var d0 = (s.disks || [])[0];
            if (d0) el.appendChild(h("div", { class: "disk-line" }, h("div", { class: "row space small" }, h("span", { class: "muted", text: "Fullest mount " + d0.mount }), h("b", { text: Math.round(d0.pct) + "%" })),
                bar(d0.pct, d0.pct >= 90 ? "crit" : d0.pct >= 80 ? "warn" : "ok")));
            var openN = (s.critical || 0) + (s.warning || 0), up = s.uptime_s ? Math.floor(s.uptime_s / 86400) + "d " + Math.floor(s.uptime_s % 86400 / 3600) + "h" : "";
            el.appendChild(h("div", { class: "card-foot small tone-" + (s.critical ? "crit" : openN ? "warn" : "ok") },
                h("span", { text: openN ? (s.critical || 0) + " critical, " + (s.warning || 0) + " warning open" : "0 problems active" }), up ? h("span", { class: "muted", text: "Uptime " + up }) : null));
            if (s.problems && s.problems.length) el.appendChild(h("div", { class: "muted small prob-line", text: s.problems[0].component + ": " + s.problems[0].message }));
            return el;
        }
        function paint() {
            var keep = box.offsetHeight;                                  // hold the page height while redrawing, so the scroll position never jumps
            if (keep) box.style.minHeight = keep + "px";
            clear(box);
            requestAnimationFrame(function () { box.style.minHeight = ""; });
            var sans = (data && data.san) || [];
            if (!data || !data.enabled || !data.servers.length) return;
            var sv = data.servers, bad = sv.filter(function (s) { return s.status !== "HEALTHY"; }).length;
            box.appendChild(glance(sv, sans));
            if (data.servers.length < 2 && !sans.length) return;            // one server alone: the strip is enough
            var c = card("All servers, live", h("span", { class: "muted", text: sv.length + " servers - " + (bad ? bad + " need attention" : "all healthy") + " - " + (data.updated ? "updated " + dur(Date.now() / 1000 - data.updated) + " ago" : "collecting the first answers") + ", servers report every " + data.interval + " s. Click a server for its details." }),
                h("div", { class: "fleet" }, sv.map(tile)));
            if (sans.length) {
                c.appendChild(h("h3", { class: "sub", text: "SAN storage (" + sans.length + ") - checked every " + Math.round((data.san_interval || 300) / 60) + " min" }));
                c.appendChild(h("div", { class: "fleet san-list" }, sans.map(sanTile)));
            }
            box.appendChild(c);
        }
        function gtile(id, label, value, meta, tone) {
            return h("div", { class: "gl v2 tone-" + tone },
                h("div", { class: "row space gl-top" }, h("div", { class: "gl-label", text: label }), h("span", { class: "sym-badge" }, sym(id))),
                h("div", { class: "gl-value", text: value }), h("span", { class: "pill tone-" + tone, text: meta }));
        }
        function glance(sv, sans) {
            var ok = sv.filter(function (s) { return s.status === "HEALTHY"; }).length;
            var live = sv.filter(function (s) { return s.ok && s.cpu !== null && s.cpu !== undefined; });
            var avg = live.length ? live.reduce(function (t, s) { return t + s.cpu; }, 0) / live.length : null;
            var busiest = live.slice().sort(function (a, b) { return b.cpu - a.cpu; })[0];
            var disks = [];
            sv.forEach(function (s) { (s.disks || []).forEach(function (d) { disks.push({ where: s.name + " " + d.mount, pct: d.pct }); }); });
            sans.forEach(function (a) { (a.pools || []).forEach(function (p) { disks.push({ where: a.name + " " + p.name, pct: p.pct }); }); });
            var full = disks.sort(function (a, b) { return b.pct - a.pct; })[0];
            var sOk = sans.filter(function (a) { return a.status === "HEALTHY"; }).length, failed = sans.reduce(function (t, a) { return t + (a.failed_drives || 0); }, 0);
            var tiles = [gtile("server", "Servers healthy", ok + " / " + sv.length, sv.length - ok ? (sv.length - ok) + " need attention" : "all running normally", ok === sv.length ? "ok" : (sv.some(function (s) { return s.status === "CRITICAL" || s.status === "UNREACHABLE"; }) ? "crit" : "warn"))];
            if (sans.length) tiles.push(gtile("storage", "SAN storage optimal", sOk + " / " + sans.length, failed ? failed + " failed drive" + (failed > 1 ? "s" : "") : "no failed drives", sOk === sans.length ? "ok" : "crit"));
            var probs = sv.reduce(function (t, s) { return t + (s.ok ? (s.critical || 0) + (s.warning || 0) : 0); }, 0) + sans.reduce(function (t, a) { return t + (a.failed_drives || 0) + (a.hardware_bad || []).length; }, 0);
            tiles.push(gtile("pulse", "Open problems", String(probs), probs ? "see Active problems below" : "nothing open right now", probs ? (sv.some(function (s) { return s.critical; }) || failed ? "crit" : "warn") : "ok"));
            tiles.push(gtile("cpu", "Average CPU", avg === null ? "-" : Math.round(avg) + "%", busiest ? "busiest: " + busiest.name + " " + Math.round(busiest.cpu) + "%" : "waiting for live data", avg === null ? "info" : avg >= 85 ? "crit" : avg >= 70 ? "warn" : "ok"));
            tiles.push(gtile("disk", "Fullest disk or pool", full ? Math.round(full.pct) + "%" : "-", full ? full.where : "waiting for live data", !full ? "info" : full.pct >= 90 ? "crit" : full.pct >= 80 ? "warn" : "ok"));
            return h("div", { class: "glance", role: "group", "aria-label": "At a glance" }, tiles);
        }
        function load() {
            api("GET", "/api/live/fleet").then(function (d) {
                data = d; S.fleet = d; try { paint(); if (S.onFleet) S.onFleet(); } catch (e) { if (window.console) console.error("live servers card:", e); }
                if (d.servers.some(function (s) { return s.status === "WAITING"; })) setTimeout(load, 4000);   // the first answers come in shortly
            }).catch(function () { /* signed out or not available */ });
        }
        load();
        return { el: box, load: load };
    }

    /* ---------------------------------------------------------------- one server */
    function drawServer(box, args) {
        var key = decodeURIComponent(args[0] || "_local"), wrap = h("div", { class: "stack" }), stopped = false, timer = null;
        box.appendChild(h("p", null, h("a", { href: "#/dashboard", text: "< Dashboard" }), "   ", h("a", { href: "#/fleet", text: "All servers" })));
        box.appendChild(wrap);
        function lvl(v, w, c) { return v >= c ? "crit" : v >= w ? "warn" : "ok"; }
        function size2(b) { return b === null || b === undefined ? "-" : size(b); }
        function paint(d) {
            steady(wrap);
            var L = d.live || {}, C = d.check, st = L.status || (C ? C.status : "UNKNOWN");
            document.title = d.name + " - LRS Vitals";
            var facts = [["Address", d.local ? "this server (central)" : d.host], ["Group", d.group || "-"], ["Environment", d.environment || "-"]];
            if (C) facts = facts.concat([["Host name", C.host], ["IP address", C.ip || "-"], ["Operating system", C.os || "-"], ["Kernel", C.kernel || "-"], ["Uptime", C.uptime || "-"],
                                         ["Hardware", C.hardware || "-"], ["Roles", (C.roles || []).join(", ") || "-"], ["Last health check", when(C.when) + " (" + ago(C.when) + ")"]]);
            wrap.appendChild(card(d.name, badge(st === "OUTDATED" ? "UPGRADE" : st),
                h("div", { class: "row" }, C ? ring(C.score || 0, statusLvl(C.status), String(C.score === null ? "-" : C.score), true) : null,
                    h("div", { class: "facts" }, facts.map(function (f) { return h("div", null, h("span", { class: "muted small", text: f[0] }), h("b", { text: String(f[1]) })); }))),
                L.error ? h("p", { class: st === "OUTDATED" ? "warnline" : "err", text: L.error }) : null,
                S.me.role === "admin" && st !== "UNREACHABLE" ? h("div", { class: "row" }, h("button", { type: "button", text: "Repair LRS Vitals", title: "Checks the program, folders, failed jobs (with the reason), watcher, web service and timers, and fixes what it can",
                    onclick: function () { runRepair(key, d.name); } })) : null));
            if (L.ok) {
                var g = h("div", { class: "grid g4" });
                [["CPU", L.cpu, 75, 90], ["Memory", L.mem, 80, 90], ["Swap", L.swap, 50, 80]].forEach(function (x) {
                    var v = x[1] === null || x[1] === undefined ? null : x[1];
                    g.appendChild(card(x[0], h("span", { class: "muted", text: "live" }), h("div", { class: "big", text: v === null ? "-" : Math.round(v) + "%" }), v === null ? null : bar(v, lvl(v, x[2], x[3]))));
                });
                g.appendChild(card("Load", h("span", { class: "muted", text: (L.cores || "?") + " cores" }), h("div", { class: "big", text: L.load === null || L.load === undefined ? "-" : (+L.load).toFixed(2) }),
                    h("div", { class: "muted small", text: L.uptime_s ? "up " + Math.floor(L.uptime_s / 86400) + " d " + Math.floor(L.uptime_s % 86400 / 3600) + " h" : "" })));
                wrap.appendChild(g);
                var pts = (d.series || []).map(function (p) { return p[1] || 0; }), mpts = (d.series || []).map(function (p) { return p[2] || 0; });
                var se = (d.series || []).filter(function (p) { return p[1] !== null && p[1] !== undefined; });
                if (se.length > 1) {
                    var big = h("div", { class: "tm-row big" },
                        tmGraph("CPU", [{ name: "CPU", pts: se.map(function (p) { return [p[0], p[1]]; }) }], { color: TM.cpu, max: 100, unit: "%", scale: "last " + Math.max(1, Math.round((se[se.length - 1][0] - se[0][0]) / 60)) + " min" }),
                        tmGraph("Memory", [{ name: "Memory", pts: se.map(function (p) { return [p[0], p[2]]; }) }], { color: TM.mem, max: 100, unit: "%" }));
                    if (se.some(function (p) { return p[3] || p[4]; })) big.appendChild(tmGraph("Network", [{ name: "Receive", pts: se.map(function (p) { return [p[0], p[3]]; }) }, { name: "Send", pts: se.map(function (p) { return [p[0], p[4]]; }) }], { color: TM.net, fmt: rate }));
                    wrap.appendChild(card("Performance", h("span", { class: "muted", text: "live, newest on the right" }), big));
                }
                if ((L.disks || []).length) wrap.appendChild(card("Disks (live)", null, h("div", { class: "stack" }, L.disks.map(function (x) {
                    return h("div", { class: "mini" }, h("span", { text: x.mount }), barrow(x.pct, lvl(x.pct, 80, 90), Math.round(x.pct) + "%"));
                }))));
                wrap.appendChild(card("Open problems now", null, (L.problems || []).length ? table(["Level", "Where", "Problem", "Since"], L.problems.map(function (p) {
                    return [h("span", { class: "pri " + (p.severity === "CRITICAL" ? "p1" : "p2"), text: p.severity === "CRITICAL" ? "P1" : "P2" }), h("b", { text: p.component }), p.message, ago(p.since)];
                })) : h("p", { class: "okline" }, h("span", { class: "dot ok" }), "No open problems.")));
            }
            if (!C) { wrap.appendChild(card("Health check", null, h("p", { class: "muted", text: d.local ? "No health check yet. Run one from the dashboard." : "No health check of this server yet. Use 'Check all servers now' on the Servers page." }))); return; }
            wrap.appendChild(card("Findings in the latest health check", h("span", { class: "muted", text: (C.counts.CRITICAL || 0) + " critical, " + (C.counts.WARNING || 0) + " warning" }),
                C.findings.length ? table(["Level", "Area", "Where", "Finding", "What to do"], C.findings.map(function (f) {
                    return [h("span", { class: "pri " + (f.severity === "CRITICAL" ? "p1" : "p2"), text: f.severity === "CRITICAL" ? "CRIT" : "WARN" }), f.module, h("b", { text: f.component }), f.message, h("span", { class: "muted small", text: f.recommendation || "" })];
                })) : h("p", { class: "okline" }, h("span", { class: "dot ok" }), "Nothing to fix."),
                (C.excluded || []).length ? h("p", { class: "muted small", text: C.excluded.length + " accepted exception(s) are kept out of the score." }) : null));
            wrap.appendChild(card("Checks by area", null, table(["Area", "Score", "Critical", "Warning", "Summary"], C.modules.filter(function (m) { return m.status !== "SKIPPED"; }).map(function (m) {
                return [h("b", { text: m.title }), m.score === null ? "-" : m.score, m.critical, m.warning, h("span", { class: "muted small", text: m.summary })];
            }), [1, 2, 3])));
            if (C.filesystems.length) wrap.appendChild(card("Filesystems", null, table(["Mount", "Used", "Free", "Size", "Type"], C.filesystems.map(function (f) {
                return [h("b", { text: f.mount }), barrow(f.pct || 0, lvl(f.pct || 0, 80, 90), Math.round(f.pct || 0) + "%"), size2(f.free), size2(f.size), f.fstype || ""];
            }), [2, 3])));
            if (C.vms.length) wrap.appendChild(card("Virtual machines", h("span", { class: "muted", text: C.vms.filter(function (v) { return v.state === "running"; }).length + " of " + C.vms.length + " running" }),
                table(["VM", "State", "vCPU", "Memory", "CPU", "Disk used"], C.vms.map(function (v) {
                    return [h("b", { text: v.name }), badge(v.state === "running" ? "OK" : v.state === "shut off" ? "OFF" : "WARNING"), v.vcpus, size2(v.memory), v.cpu === null || v.cpu === undefined ? "-" : Math.round(v.cpu) + "%", size2(v.disk)];
                }), [2, 3, 4, 5]),
                C.pools.length ? table(["Storage pool", "Used", "Free", "State"], C.pools.map(function (p) {
                    return [h("b", { text: p.name }), p.pct === null || p.pct === undefined ? "-" : barrow(p.pct, lvl(p.pct, 80, 90), Math.round(p.pct) + "%"), size2(p.free), p.state || ""];
                }), [2]) : null));
            wrap.appendChild(card("Reports", null, h("p", null, "Daily, weekly and monthly reports of this server are on the ", h("a", { href: "#/reports", text: "Reports page" }), ".")));
        }
        function load() {
            api("GET", "/api/server?name=" + encodeURIComponent(key)).then(function (d) { if (!stopped) paint(d); }).catch(function (e) {
                if (!stopped && e.message !== "Signed out") { clear(wrap); wrap.appendChild(card("Server", null, h("p", { class: "err", text: e.message === "not found" ? "This server is not in servers.conf." : e.message }))); }
            });
        }
        load();
        var every = refreshSeconds() * 1000;
        timer = every ? setInterval(load, Math.max(every, 5000)) : null;
        S.cleanup = function () { stopped = true; if (timer) clearInterval(timer); };
    }

    /* ---------------------------------------------------------------- alerts */
    function ago(ts) {
        if (!ts) return "-";
        var s = Math.max(0, Date.now() / 1000 - ts);
        return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 172800 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago";
    }
    function alertsCard() {
        var box = h("div", { class: "stack" }), st = { tab: "", days: 30 }, MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
        try { st.tab = localStorage.getItem("lshme-inc-tab") || ""; } catch (e) { /* storage may be blocked */ }
        function p2(n) { return (n < 10 ? "0" : "") + n; }
        function when(ts) { if (!ts) return "-"; var d = new Date(ts * 1000); return d.getDate() + " " + MON[d.getMonth()] + " " + p2(d.getHours()) + ":" + p2(d.getMinutes()); }
        function dur(s) {
            s = Math.round(s || 0);
            if (s < 90) return s + " s";
            var m = Math.round(s / 60);
            if (m < 120) return m + " min";
            var hr = Math.floor(m / 60);
            if (hr < 48) return hr + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
            return Math.floor(hr / 24) + " d " + (hr % 24) + " h";
        }
        function pri(i) { return h("span", { class: "pri " + (i.priority === "P1" ? "p1" : "p2"), title: i.severity === "CRITICAL" ? "Critical" : "Warning", text: i.priority }); }
        function status(s) { return h("span", { class: "st st-" + s.toLowerCase(), text: s }); }
        function tile(label, value, tone, sub) { return h("div", { class: "card tile " + tone }, h("div", { class: "muted", text: label }), h("div", { class: "big", text: String(value) }), h("div", { class: "muted", text: sub || "" })); }
        function post(body, msg) { api("POST", "/api/alerts", body).then(function () { toast(msg, "good"); load(); }).catch(fail); }
        function alertActions(a) {
            var admin = S.me.role === "admin";
            if (a.muted) {
                var until = a.mute_until ? "until " + when(a.mute_until) : "until it ends";
                return h("div", { class: "row" }, h("span", { class: "st st-muted", text: "Muted " + until, title: "by " + a.ack.by + " " + ago(a.ack.at) }),
                    admin ? h("button", { class: "small", type: "button", text: "Unmute", onclick: function () { post({ action: "unmute", key: a.key }, "Unmuted: alerts for this problem start again"); } }) : "");
            }
            if (!admin) return a.ack ? h("span", { class: "muted", text: "acknowledged by " + a.ack.by + " " + ago(a.ack.at) }) : "";
            var items = [];
            if (!a.ack) items.push(["Acknowledge", "Reminders stop until it gets worse or ends", function () { post({ action: "ack", key: a.key }, "Acknowledged: reminders stop until it gets worse or ends"); }]);
            [[6, "Mute 6 hours"], [24, "Mute 24 hours"], [168, "Mute 7 days"], [0, "Mute until it ends"]].forEach(function (o) {
                items.push([o[1], "No emails for this problem", function () { post({ action: "mute", key: a.key, hours: o[0] }, o[1] + ": no emails for this problem"); }]);
            });
            return h("div", { class: "row" }, a.ack ? h("span", { class: "muted small", text: "acknowledged by " + a.ack.by + " " + ago(a.ack.at) }) : null, menu("Actions", items));
        }
        function paint(d) {
            steady(box);
            var sm = d.incident_summary, inc = d.incidents, ch = d.channels, how = [];
            if (ch.email) how.push("email" + (ch.to ? " to " + ch.to : ""));
            if (ch.syslog) how.push("syslog");
            if (ch.webhook) how.push("webhook");
            if (ch.slack) how.push("Slack");
            if (ch.teams) how.push("Teams");
            S.openIncidents = sm.open;
            var info = h("span", { class: "muted", text: how.length ? "Alerts by " + how.join(", ") + " - from " + ch.min_severity + " up, after " + ch.confirm + " bad checks in a row" + (ch.quiet_hours ? " - quiet hours " + ch.quiet_hours : "") + (ch.digest ? " - daily digest " + ch.digest : "") : "No alert channel is switched on (Settings > main > watch)" });
            var active = d.active.length ? table(["Priority", "Problem", "Since", "Checks", "Alert", ""], d.active.map(function (a) {
                return [h("span", { class: "pri " + (a.severity === "CRITICAL" ? "p1" : "p2"), text: a.severity === "CRITICAL" ? "P1" : "P2", title: a.severity }),
                    h("div", null, h("b", { text: a.component }), h("div", { class: "muted", text: a.message })), ago(a.since), a.count, a.alerted ? "sent" : "waiting",
                    alertActions(a)];
            }), [3]) : h("p", { class: "okline" }, h("span", { class: "dot ok" }), "No active problems. CPU, memory, disks, services and ports are within their limits.");
            box.appendChild(card("Active problems", info, active));
            var tab = st.tab || (sm.open ? "open" : "resolved");
            var shown = inc.filter(function (i) { return tab === "all" || (tab === "open" ? i.status !== "Resolved" : i.status === "Resolved"); });
            var nOpen = inc.filter(function (i) { return i.status !== "Resolved"; }).length;
            function tabBtn(id, label, n) {
                return h("button", { class: "seg" + (tab === id ? " on" : ""), type: "button", "aria-pressed": tab === id ? "true" : "false", text: label + " (" + n + ")", onclick: function () {
                    st.tab = id; try { localStorage.setItem("lshme-inc-tab", id); } catch (e) { /* ignore */ } paint(d);
                } });
            }
            var days = h("select", { "aria-label": "Period" }, [[1, "24 hours"], [7, "7 days"], [30, "30 days"], [90, "90 days"]].map(function (o) { return h("option", { value: o[0], text: o[1], selected: o[0] === st.days }); }));
            days.addEventListener("change", function () { st.days = +days.value; load(); });
            var controls = h("div", { class: "row" }, h("div", { class: "segs", role: "group", "aria-label": "Incident status" }, tabBtn("open", "Open", nOpen), tabBtn("resolved", "Resolved", inc.length - nOpen), tabBtn("all", "All", inc.length)), days);
            var body = shown.length ? h("div", { class: "scroll" }, table(["Incident", "Priority", "Where", "Description", "Opened", "Resolved", "Time to resolve", "Status"], shown.map(function (i) {
                return [h("span", { class: "mono", text: i.id }), pri(i), h("b", { text: i.component }), i.message, when(i.opened), i.resolved ? when(i.resolved) : "-", dur(i.duration), status(i.status)];
            }), [6], function (idx) { return shown[idx].status === "Resolved" ? "resolved" : ""; }))
                : h("p", { class: "muted", text: tab === "open" ? "No open incidents." : "No " + (tab === "all" ? "" : "resolved ") + "incidents in the last " + (st.days === 1 ? "24 hours" : st.days + " days") + "." });
            box.appendChild(card("Incident log", h("span", { class: "muted", text: sm.resolved_24h + " resolved in 24 h - " + sm.resolved_total + " in " + sm.days + " days" + (sm.mttr === null ? "" : " - mean time to resolve " + dur(sm.mttr)) }), controls, body, h("p", { class: "muted small", text: "P1 = critical, P2 = warning. A resolved incident is closed history: it is not a problem now and does not count in the health score." })));
        }
        function load() { api("GET", "/api/alerts?days=" + st.days).then(paint).catch(function () { /* signed out */ }); }
        load();
        return { el: box, load: load };
    }

    /* ---------------------------------------------------------------- all servers */
    /* Open findings: one click to accept one (exclude) or to take a rule back (un-exclude) */
    function exclusionsCard() {
        var box = card("Open findings and exceptions", null, h("p", { class: "muted", text: "Loading..." }));
        function load() { api("GET", "/api/exclusions").then(paint).catch(function () { /* signed out */ }); }
        function paint(d) {
            clear(box);
            box.appendChild(h("h2", null, h("span", { text: "Open findings and exceptions" }), h("span", { class: "muted", text: d.checked ? "from the health check " + ago(d.checked) : "" })));
            var admin = S.me.role === "admin";
            box.appendChild(d.findings.length ? table(["Level", "Where", "Finding", ""], d.findings.map(function (f) {
                return [h("span", { class: "pri " + (f.severity === "CRITICAL" ? "p1" : "p2"), text: f.severity === "CRITICAL" ? "CRIT" : "WARN" }),
                    h("div", null, h("b", { text: f.component || f.module }), h("div", { class: "muted small", text: f.module + " - " + f.code })),
                    h("div", null, f.message, f.recommendation ? h("div", { class: "muted small", text: f.recommendation }) : null),
                    admin ? h("button", { class: "small", type: "button", text: "Exclude", title: "Accept this finding: it leaves the score, the alerts and the open lists, and is listed under Accepted exceptions", onclick: function () { form(f); } }) : ""];
            })) : h("p", { class: "okline" }, h("span", { class: "dot ok" }), "No open warnings or critical findings in the latest health check."));
            var rules = d.rules;
            box.appendChild(h("h3", { text: "Accepted exceptions (" + rules.length + ")" }));
            box.appendChild(rules.length ? table(["Rule", "Finding", "Where", "Reason", "Approved by", "Until", "Status", ""], rules.map(function (r) {
                return [h("span", { class: "mono", text: r.name }), r.code, r.component + (r.hosts && r.hosts !== "*" ? " on " + r.hosts : ""), r.reason, r.owner || "-", r.until || "no end",
                    h("span", { class: "st st-" + (r.status === "active" ? "resolved" : r.status === "expired" ? "muted" : "open"), text: r.status }),
                    admin ? h("button", { class: "small", type: "button", text: "Un-exclude", onclick: function () {
                        ask("Take back the exception " + r.name + "? '" + r.code + "' counts again in the score and alerts from the next check.", function () {
                            api("POST", "/api/exclusions", { action: "remove", name: r.name }).then(function () { toast("Un-excluded: " + r.code + " counts again from the next check", "good"); load(); }).catch(fail);
                        });
                    } }) : ""];
            })) : h("p", { class: "muted", text: "No exceptions. Exclude a finding above when it is accepted (for example root SSH login by policy)." }));
            box.appendChild(h("p", { class: "muted small", text: "An exclusion needs a reason and is shown in every report under 'Accepted exceptions', so nothing is hidden. Give an end date to have it come back automatically." }));
        }
        function form(f) {
            var reason = h("input", { type: "text", placeholder: "why it is accepted (required)", maxlength: 300 });
            var owner = h("input", { type: "text", placeholder: "approved by", value: S.me.user, maxlength: 80 });
            var until = h("input", { type: "date" });
            var scope = h("select", null, h("option", { value: "this", text: "Only " + (f.component || "this item") }), h("option", { value: "all", text: "Every " + f.code + " finding" }));
            var close = modal("Exclude: " + (f.component || f.code), h("div", { class: "stack" },
                h("p", { text: f.message }),
                h("label", null, "Applies to ", scope),
                h("label", null, "Reason ", reason),
                h("div", { class: "row" }, h("label", null, "Approved by ", owner), h("label", null, "Until (optional) ", until)),
                h("div", { class: "row" }, h("button", { class: "primary", type: "button", text: "Exclude", onclick: function () {
                    if (!reason.value.trim()) return toast("Give a reason; it is shown in the reports", "bad");
                    api("POST", "/api/exclusions", { action: "add", code: f.code, component: scope.value === "all" ? "*" : f.component, module: f.module,
                        reason: reason.value.trim(), owner: owner.value.trim(), until: until.value }).then(function (r) {
                        toast("Excluded (" + r.name + "). It leaves the score and alerts from the next check.", "good"); close(); load();
                    }).catch(fail);
                } }), h("button", { type: "button", text: "Cancel", onclick: function () { close(); } }))));
        }
        load();
        return { el: box, load: load };
    }

    /* SAN storage: add any make, test the connection, remove (administrators) */
    function sanCard() {
        var box = card("SAN storage", h("span", { class: "muted", text: "any make, read-only" }), h("p", { class: "muted", text: "Loading..." }));
        if (S.me.role !== "admin") { clear(box); box.appendChild(h("h2", null, h("span", { text: "SAN storage" }))); box.appendChild(h("p", { class: "muted", text: "Administrators add and test SAN storage here." })); return box; }
        function load() { api("GET", "/api/san").then(paint).catch(fail); }
        function paint(d) {
            clear(box);
            box.appendChild(h("h2", null, h("span", { text: "SAN storage" }), h("span", { class: "muted", text: d.arrays.length + " added" })));
            var label = {};
            d.types.forEach(function (t) { label[t.type] = t; });
            box.appendChild(d.arrays.length ? table(["Name", "Make", "Controllers", "User", "Password", ""], d.arrays.map(function (a) {
                return [h("b", { text: a.name }), a.type_label, a.controllers.join(", ") + " : " + a.port, a.username || "-", a.has_password ? "saved" : (a.type === "reach" ? "-" : h("span", { class: "err", text: "missing" })),
                    h("div", { class: "row" },
                        h("button", { class: "small", type: "button", text: "Test", onclick: function () {
                            toast("Testing " + a.name + "...", "");
                            api("POST", "/api/san", { action: "test", saved: true, name: a.name }).then(function (r) { toast(r.message, r.ok ? "good" : "bad"); }).catch(fail);
                        } }),
                        h("button", { class: "small", type: "button", text: "Edit", onclick: function () { form(d, a); } }),
                        h("button", { class: "small danger", type: "button", text: "Remove", onclick: function () {
                            ask("Remove SAN storage " + a.name + " and its saved password? Its history stays.", function () { api("POST", "/api/san", { action: "remove", name: a.name }).then(function () { toast("Removed " + a.name, "good"); load(); }).catch(fail); }, true);
                        } }))];
            })) : h("p", { class: "muted", text: "No SAN storage added. Add one to check its controllers, drives, pools, volumes, hardware and I/O with every health check and alert on failures." }));
            box.appendChild(h("div", { class: "row" }, h("button", { class: "primary", type: "button", text: "Add SAN storage", onclick: function () { form(d, null); } })));
        }
        function form(d, a) {
            var type = h("select", { "aria-label": "Make" }, d.types.map(function (t) { return h("option", { value: t.type, text: t.label, selected: a ? a.type === t.type : t.type === "santricity" }); }));
            var howto = h("p", { class: "muted small" });
            var name = h("input", { type: "text", placeholder: "short name, e.g. hhk-de", value: a ? a.name : "", disabled: !!a });
            var ca = h("input", { type: "text", placeholder: "Controller A management IP", value: a ? (a.controllers[0] || "") : "" });
            var cb = h("input", { type: "text", placeholder: "Controller B management IP (optional)", value: a ? (a.controllers[1] || "") : "" });
            var port = h("input", { type: "number", min: 1, max: 65535, value: a ? a.port : 8443 });
            var user = h("input", { type: "text", placeholder: "read-only user", autocomplete: "off", value: a ? a.username : "monitor" });
            var pass = h("input", { type: "password", autocomplete: "new-password", placeholder: a && a.has_password ? "saved - leave empty to keep" : "password (API token for Pure)" });
            var tls = h("input", { type: "checkbox", checked: a ? a.verify_tls : false });
            var result = h("p", { class: "muted", text: "" });
            function sync() { var t = type.value, x = d.types.filter(function (y) { return y.type === t; })[0]; howto.textContent = "How to connect: " + x.howto; if (!a) port.value = x.port; }
            type.addEventListener("change", sync); sync();
            function body(action) { return { action: action, name: a ? a.name : name.value.trim(), type: type.value, controllers: [ca.value.trim(), cb.value.trim()], port: +port.value, username: user.value.trim(), password: pass.value, verify_tls: tls.checked }; }
            var close = modal(a ? "Edit SAN storage " + a.name : "Add SAN storage", h("div", { class: "stack" },
                h("label", null, "Make ", type), howto,
                h("div", { class: "row" }, h("label", null, "Name ", name), h("label", null, "Port ", port)),
                h("div", { class: "row" }, h("label", null, "Controller A ", ca), h("label", null, "Controller B ", cb)),
                h("div", { class: "row" }, h("label", null, "User ", user), h("label", null, "Password ", pass)),
                h("label", { class: "check" }, tls, " Check the storage's HTTPS certificate (leave off for the usual self-signed one)"),
                result,
                h("div", { class: "row" },
                    h("button", { type: "button", text: "Test connection", onclick: function () {
                        result.className = "muted"; result.textContent = "Testing...";
                        api("POST", "/api/san", body("test")).then(function (r) { result.className = r.ok ? "okline" : "err"; result.textContent = r.message; }).catch(function (e) { result.className = "err"; result.textContent = e.message; });
                    } }),
                    h("button", { class: "primary", type: "button", text: "Save", onclick: function () {
                        api("POST", "/api/san", body("save")).then(function (r) { toast("Saved SAN storage " + r.name + ". It is checked with every health check.", "good"); close(); load(); }).catch(fail);
                    } }))), true);
        }
        load();
        return box;
    }

    function drawFleet(box) {
        var outer = h("div", { class: "stack" });
        box.appendChild(outer);
        var wrap = h("div", { class: "stack" });
        outer.appendChild(wrap);
        outer.appendChild(sanCard());
        var timer = null, stopped = false, busy = false;
        function tile(label, value, cls) { return h("div", { class: "card tile " + (cls || "") }, h("div", { class: "big", text: String(value) }), h("div", { class: "muted", text: label })); }
        function paint(d) {
            clear(wrap);
            var c = { HEALTHY: 0, WARNING: 0, CRITICAL: 0, UNREACHABLE: 0, STALE: 0, UNKNOWN: 0 };
            d.servers.forEach(function (s) { c[s.status] = (c[s.status] || 0) + 1; });
            wrap.appendChild(h("div", { class: "grid g4" }, tile("Servers", d.servers.length), tile("Healthy", c.HEALTHY, "ok"), tile("Warning", c.WARNING, c.WARNING ? "warn" : ""),
                tile("Critical or unreachable", c.CRITICAL + c.UNREACHABLE, c.CRITICAL + c.UNREACHABLE ? "crit" : "")));
            var j = d.job, btn = h("button", { class: "primary", text: j.running ? "Checking " + (j.title || "...") : "Check all servers now", disabled: j.running || busy,
                onclick: function () {
                    busy = true;
                    api("POST", "/api/fleet", { action: "run" }).then(function () { toast("Checking all servers...", "good"); load(); }).catch(function (e) { busy = false; fail(e); });
                } });
            if (S.me.role !== "admin") btn.style.display = "none";
            var grid = h("div", { class: "fleet" });
            d.servers.forEach(function (s) {
                var cardEl = h("div", { class: "srv st-" + s.status }, h("div", { class: "row space" }, h("b", { class: "nm", text: s.name }), badge(s.status)));
                cardEl.appendChild(h("div", { class: "muted", text: [s.ip, s.environment, s.group].filter(Boolean).join(" - ") || s.host }));
                if (s.roles.length) cardEl.appendChild(h("div", null, s.roles.map(function (r) { return h("span", { class: "chip", text: r }); })));
                if (s.status === "UNREACHABLE") {
                    cardEl.appendChild(h("p", { class: "err", text: s.top || "Could not connect." }));
                } else {
                    cardEl.appendChild(h("div", { class: "row space" }, h("span", { class: "score", text: s.score === null ? "-" : s.score + "/100" }),
                        h("span", null, h("span", { class: "chip crit", text: s.critical + " critical" }), h("span", { class: "chip warn", text: s.warning + " warning" }))));
                    [["CPU", s.cpu], ["Memory", s.mem], ["Fullest disk", s.disk]].forEach(function (m) {
                        if (m[1] !== null && m[1] !== undefined) cardEl.appendChild(h("div", { class: "mini" }, h("span", { class: "muted", text: m[0] }), barrow(m[1], m[1] >= 90 ? "crit" : m[1] >= 80 ? "warn" : "ok")));
                    });
                    if (s.top) cardEl.appendChild(h("div", { class: "topline", text: s.top }));
                    cardEl.appendChild(h("div", { class: "muted", text: (s.local ? "This server - " : "") + "checked " + ago(s.collected) }));
                }
                grid.appendChild(cardEl);
            });
            wrap.appendChild(card("All servers", btn, d.servers.length ? grid : h("p", { class: "muted", text: "No results yet." })));
            if (!d.configured.length) {
                wrap.appendChild(card("Add more servers", null, h("div", { class: "stack" },
                    h("p", { text: "This server is listed automatically. To watch other servers from here:" }),
                    h("ol", null, h("li", { text: "Install LRS Vitals on each server (the same RPM)." }),
                        h("li", { text: "Make sure this server can log in to them over SSH with a key (root or a user that can run vitals)." }),
                        h("li", { text: "Add each one under Settings > servers: a name, the host and the user." }),
                        h("li", { text: "Press Check all servers now. The central server asks every server for its health and shows it here." })),
                    S.me.role === "admin" ? h("a", { class: "btn primary", href: "#/settings", text: "Open Settings" }) : null)));
            }
            busy = busy && j.running;
        }
        function load() {
            api("GET", "/api/fleet").then(function (d) {
                if (stopped) return;
                paint(d);
                if (d.job.running || busy) { clearTimeout(timer); timer = setTimeout(load, 2000); } else { clearTimeout(timer); timer = setTimeout(load, 15000); }
            }).catch(function (e) { if (!stopped && e.message !== "Signed out") wrap.appendChild(h("p", { class: "err", text: e.message })); });
        }
        load();
        S.cleanup = function () { stopped = true; clearTimeout(timer); };
    }

    /* ---------------------------------------------------------------- reports */
    function showReport(id) {
        var body = h("div", null, h("p", { class: "muted", text: "Loading..." }));
        modal("Report", body);
        api("GET", "/api/report?id=" + encodeURIComponent(id)).then(function (d) {
            clear(body);
            if (d.health) {
                body.appendChild(h("div", { class: "row" }, ring(d.health.score || 0, statusLvl(d.health.status), String(d.health.score), true),
                    h("div", null, h("div", { class: "big" }, d.health.status, " ", badge(d.health.status)), h("div", { class: "muted", text: d.host + " - " + d.when }),
                        h("div", null, h("span", { class: "chip crit", text: d.counts.CRITICAL + " critical" }), h("span", { class: "chip warn", text: d.counts.WARNING + " warning" }),
                            h("span", { class: "chip", text: d.counts.INFO + " info" })))));
                body.appendChild(h("h3", { text: "Areas" }));
                body.appendChild(table(["Area", "Status", "Score", "Findings", "Summary"], d.modules.map(function (m) {
                    return [h("b", { text: m.title }), badge(m.status), m.score === null ? "" : m.score, m.findings, m.summary];
                }), [2, 3]));
                var attention = d.findings.filter(function (f) { return f.severity === "CRITICAL" || f.severity === "WARNING"; });
                body.appendChild(h("h3", { text: attention.length ? "Findings that need attention" : "No warnings or critical findings" }));
                attention.forEach(function (f) {
                    body.appendChild(h("div", { class: "finding " + f.severity }, h("b", { text: f.severity + " - " }), f.message, f.recommendation ? h("div", { class: "rec", text: "Recommended: " + f.recommendation }) : null));
                });
            } else if (d.error) body.appendChild(h("p", { class: "err", text: d.error }));
            var admin = S.me.role === "admin", picks = {};
            var KIND = { pdf: "PDF", html: "Web page", xlsx: "Excel", pptx: "PowerPoint", csv: "CSV", txt: "Text", json: "Data (JSON)" };
            body.appendChild(h("h3", { text: "Files" }));
            body.appendChild(h("p", { class: "muted", text: "Files kept on disk open as they are. Files removed by retention are rebuilt from the stored report data, byte for byte as they were sent." }));
            body.appendChild(table(["Format", "File", "Size", "Kept as", "", ""].concat(admin ? ["Email"] : []), d.files.map(function (f) {
                var view = /^(html|pdf|txt|csv|json)$/.test(f.format);
                var box = h("input", { type: "checkbox", onchange: function () { picks[f.name] = box.checked; } });
                var kept = f.on_disk ? (f.compressed ? "compressed (" + size(f.stored) + ")" : "file") : "rebuilt on request";
                return [h("b", { text: KIND[f.format] || f.format.toUpperCase() }), f.name, size(f.size), kept,
                    view ? h("a", { class: "btn small primary", href: f.url, target: "_blank", rel: "noopener", text: "View" }) : h("span", { class: "muted", text: "opens in " + (KIND[f.format] || "its app") }),
                    h("a", { class: "btn small", href: f.url + "?download=1", text: "Download" })].concat(admin ? [box] : []);
            }), [2]));
            if (admin) {
                var to = h("input", { type: "text", placeholder: "recipients, comma separated (empty = the configured ones)", style: "min-width:320px;flex:1" });
                body.appendChild(h("h3", { text: "Send by email" }));
                body.appendChild(h("div", { class: "row" }, to, h("button", { class: "primary", text: "Email selected files", onclick: function () {
                    var files = Object.keys(picks).filter(function (k) { return picks[k]; });
                    if (!files.length) { toast("Tick the files to send in the Email column.", "bad"); return; }
                    api("POST", "/api/reports/email", { id: id, to: to.value, files: files }).then(function (r) {
                        if (r.ok) toast("Sent " + r.attached.join(", ") + " to " + r.recipients.join(", ") + (r.rebuilt && r.rebuilt.length ? " (rebuilt: " + r.rebuilt.join(", ") + ")" : ""), "good");
                        else toast("Not sent: " + r.error, "bad");
                    }).catch(fail);
                } })));
            }
        }).catch(function (e) { clear(body); body.appendChild(h("p", { class: "err", text: e.message })); });
    }
    function drawTemplates(box) {
        api("GET", "/api/templates").then(function (d) {
            clear(box);
            if (!d.templates.length) { return; }
            var grid = h("div", { class: "tplgrid", style: "display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px" });
            d.templates.forEach(function (t) {
                var exec = t.variant === "executive";
                var c = h("div", { style: "border:1px solid var(--line,#d6dde6);border-radius:12px;padding:14px;" + (exec ? "border-left:5px solid #0b5ed7;background:rgba(11,94,215,.05)" : "") },
                    h("div", { class: "row" }, h("b", { text: t.name }), exec ? h("span", { class: "badge b-checked", text: "for management" }) : null),
                    h("p", { class: "muted", style: "margin:4px 0", text: "For: " + t.audience + "  -  " + t.pages + " pages" }),
                    h("p", { style: "margin:6px 0", text: t.about }),
                    h("details", null, h("summary", { class: "muted", text: "What is in it (" + t.sections.length + " sections)" }),
                        h("ol", { style: "margin:6px 0 0 18px" }, t.sections.map(function (s) { return h("li", { text: s }); }))),
                    h("p", { class: "muted", style: "margin:8px 0 4px", text: "Formats: " + t.formats + ". Sent to: " + t.sent_to + "." }),
                    h("div", { class: "row" },
                        h("a", { class: "btn small primary", href: "/api/templates/preview?id=" + encodeURIComponent(t.id) + "&fmt=html", target: "_blank", rel: "noopener", text: "Preview" }),
                        h("a", { class: "btn small", href: "/api/templates/preview?id=" + encodeURIComponent(t.id) + "&fmt=pdf", target: "_blank", rel: "noopener", text: "Preview as PDF" })));
                grid.appendChild(c);
            });
            box.appendChild(grid);
            box.appendChild(h("p", { class: "muted", text: "To send the executive summary to management, list their addresses as to_summary in the schedule (Setup > Schedules). They then receive only that report." }));
        }).catch(function () { clear(box); });
    }
    /* Any image the browser can open (PNG, JPG, GIF, WebP, BMP, SVG) becomes a PNG that fits 900 x 300; the server stores it. */
    function prepareLogo(file, maxW, maxH) {
        return new Promise(function (resolve, reject) {
            var url = URL.createObjectURL(file), img = new Image(), bad = "This file could not be read as an image. Use PNG, JPG, GIF, WebP, BMP or SVG.";
            img.onload = function () {
                try {
                    var w = img.naturalWidth || 600, hh = img.naturalHeight || 200, sc = Math.min(maxW / w, maxH / hh, 1);
                    var cw = Math.max(1, Math.round(w * sc)), ch = Math.max(1, Math.round(hh * sc)), c = document.createElement("canvas");
                    c.width = cw; c.height = ch;
                    c.getContext("2d").drawImage(img, 0, 0, cw, ch);
                    URL.revokeObjectURL(url);
                    c.toBlob(function (blob) {
                        if (!blob) { reject(new Error(bad)); return; }
                        var rd = new FileReader();
                        rd.onload = function () { resolve({ b64: String(rd.result).split(",")[1] || "", width: cw, height: ch, bytes: blob.size, from: w + " x " + hh }); };
                        rd.readAsDataURL(blob);
                    }, "image/png");
                } catch (e) { URL.revokeObjectURL(url); reject(new Error(bad)); }
            };
            img.onerror = function () { URL.revokeObjectURL(url); reject(new Error(bad)); };
            img.src = url;
        });
    }
    function sendLogo(file, scope, done) {
        var sizes = [[900, 300], [600, 200], [400, 140]], i = 0;
        (function attempt() {
            prepareLogo(file, sizes[i][0], sizes[i][1]).then(function (p) {
                if (p.bytes > 650 * 1024 && i < sizes.length - 1) { i++; attempt(); return; }
                api("POST", "/api/branding/upload", { scope: scope, filename: file.name, data: p.b64 }).then(function (r) {
                    toast("Logo stored (" + p.from + " px fitted to " + p.width + " x " + p.height + "). New reports use it.", "good");
                    done(r);
                }).catch(fail);
            }).catch(fail);
        })();
    }
    function drawLogos(box) {
        function paint(d) {
            clear(box);
            var scope = h("select", null, h("option", { value: "all", text: "All servers (default logo)" }));
            d.servers.forEach(function (s) { scope.appendChild(h("option", { value: s.name, text: "Only " + s.name })); });
            var file = h("input", { type: "file", accept: "image/*", "aria-label": "Logo image" });
            var note = h("p", { class: "muted", text: "Any image works: PNG, JPG, GIF, WebP, BMP or SVG, of any size. It is converted to PNG and fitted automatically (at most 900 x 300 pixels, never enlarged). A wide logo on a plain or transparent background looks best." });
            function upload() {
                var f = file.files[0];
                if (!f) { toast("Choose an image file first.", "bad"); return; }
                sendLogo(f, scope.value, paint);
            }
            var cur = d.default ? h("img", { src: "/api/branding/logo?scope=all&t=" + Date.now(), alt: "logo", style: "max-height:56px;max-width:220px;background:#fff;border:1px solid var(--line,#d6dde6);border-radius:6px;padding:6px" })
                : h("span", { class: "muted", text: "No logo yet: reports show the customer name instead." });
            box.appendChild(h("div", { class: "row" }, cur, d.default ? h("span", { class: "muted", text: d.default.width + " x " + d.default.height + " px, " + size(d.default.bytes) }) : null,
                d.default ? h("button", { class: "small", text: "Remove", onclick: function () { api("POST", "/api/branding/remove", { scope: "all" }).then(paint).catch(fail); } }) : null));
            box.appendChild(h("div", { class: "row" }, file, scope, h("button", { class: "primary", text: "Upload logo", onclick: upload })));
            box.appendChild(note);
            if (d.servers.length) {
                box.appendChild(table(["Server", "Logo it uses", "", ""], d.servers.map(function (s) {
                    return [h("b", { text: s.name }), s.logo ? (s.own ? "its own logo" : "the default logo") + " (" + s.logo.width + " x " + s.logo.height + ")" : "none",
                        s.own ? h("button", { class: "small", text: "Use the default", onclick: function () { api("POST", "/api/branding/remove", { scope: s.name }).then(paint).catch(fail); } }) : "",
                        s.logo ? h("a", { class: "btn small", href: "/api/branding/logo?scope=" + encodeURIComponent(s.name), target: "_blank", rel: "noopener", text: "View" }) : ""];
                })));
                box.appendChild(h("div", { class: "row" }, h("button", { text: "Send logos to the servers", onclick: function () {
                    api("POST", "/api/branding/push", {}).then(function (r) {
                        var bad = r.results.filter(function (x) { return !x.ok; });
                        toast(bad.length ? bad.length + " of " + r.results.length + " failed: " + bad.map(function (x) { return x.server + " (" + x.detail + ")"; }).join("; ") : "Sent to " + r.results.length + " server(s).", bad.length ? "bad" : "good");
                    }).catch(fail);
                } }), h("span", { class: "muted", text: "Each server writes its own reports, so it needs its own copy of the logo." })));
            }
        }
        api("GET", "/api/branding").then(paint).catch(function () { clear(box); });
    }
    function drawReports(box) {
        var found = h("div");
        var q = h("input", { type: "search", placeholder: "words to find in every stored report, e.g. tablespace USERS", style: "min-width:320px;flex:1" });
        function search() {
            if (!q.value.trim()) { clear(found); return; }
            api("GET", "/api/reports/search?q=" + encodeURIComponent(q.value)).then(function (d) {
                clear(found);
                if (!d.results.length) { found.appendChild(h("p", { class: "muted", text: "Nothing found." })); return; }
                found.appendChild(table(["When", "Report", "File", "Where it appears", ""], d.results.map(function (r) {
                    return [r.when.slice(0, 16), r.rid, r.name, r.snippet, h("div", { class: "row" },
                        h("button", { class: "small", text: "Summary", onclick: function () { showReport(r.rid); } }),
                        h("a", { class: "btn small", href: "/reports/" + r.rid + "/" + r.name, target: "_blank", rel: "noopener", text: "Open" }))];
                })));
            }).catch(fail);
        }
        q.addEventListener("keydown", function (e) { if (e.key === "Enter") search(); });
        box.appendChild(card("Search stored reports", h("span", { class: "muted", text: "older reports are kept compressed and are searched and opened as they are" }),
            h("div", { class: "row" }, q, h("button", { class: "primary", text: "Search", onclick: search })), found));
        var wrap = h("div", { class: "stack" });
        box.appendChild(wrap);
        if (S.me.role === "admin") {
            var tplBox = h("div", null, h("p", { class: "muted", text: "Loading..." }));
            box.appendChild(card("Report templates", h("span", { class: "muted", text: "explore what each report looks like, built from the latest health check" }), tplBox));
            drawTemplates(tplBox);
            var logoBox = h("div", null, h("p", { class: "muted", text: "Loading..." }));
            box.appendChild(card("Report logos", h("span", { class: "muted", text: "shown at the top of every report: PDF, HTML, Excel" }), logoBox));
            drawLogos(logoBox);
            var rawBox = h("div", null, h("p", { class: "muted", text: "Loading..." }));
            box.appendChild(card("Raw data by day", h("span", { class: "muted", text: "every measurement as taken, 00:00:00 to 23:59:59, sealed at 02:00" }), rawBox));
            api("GET", "/api/raw").then(function (d) {
                clear(rawBox);
                if (!d.days.length) { rawBox.appendChild(h("p", { class: "muted", text: "No raw data yet. It is written by the live watcher and every health check." })); return; }
                rawBox.appendChild(table(["Server", "Day", "Period", "State", "Records", "Coverage", "Complete", "Size", ""], d.days.slice(0, 120).map(function (x) {
                    return [x.host, x.day, x.period, x.sealed ? (x.open ? "sealed + late records" : "sealed") : "open (today)",
                        x.records === null || x.records === undefined ? "-" : x.records, x.coverage === null || x.coverage === undefined ? "-" : x.coverage.toFixed(1) + " %",
                        x.complete === null || x.complete === undefined ? "-" : (x.complete ? badge("OK") : h("span", { text: (x.gaps || 0) + " gap(s)" })), size(x.bytes),
                        h("a", { class: "btn small", href: "/api/raw/file?host=" + encodeURIComponent(x.host) + "&day=" + x.day, text: "Download" })];
                }), [4, 5, 7]));
            }).catch(function () { clear(rawBox); });
        }
        function load() {
            api("GET", "/api/reports").then(function (d) {
                clear(wrap);
                wrap.appendChild(card("Health score over time", h("span", { class: "muted", text: d.series.length + " runs" }), lineChart(d.series)));
                wrap.appendChild(card("Reports (" + d.reports.length + ")", null, d.reports.length ? table(["When", "Schedule", "Status", "Score", "Critical", "Warning", "Files", ""], d.reports.map(function (r) {
                    var htm = r.files.filter(function (f) { return /\.html$/i.test(f); })[0];
                    return [r.when.slice(0, 16), r.category, r.status ? badge(r.status) : "", r.score === null ? "-" : r.score, r.critical, r.warning, r.files.length,
                        h("div", { class: "row" }, h("button", { class: "small primary", text: "Summary", onclick: function () { showReport(r.id); } }),
                            htm ? h("a", { class: "btn small", href: "/reports/" + r.id + "/" + htm, target: "_blank", rel: "noopener", text: "Open report" }) : null)];
                }), [3, 4, 5, 6]) : h("p", { class: "muted", text: "No reports yet. Run a health check from the dashboard." })));
            }).catch(fail);
        }
        load();
    }

    /* ---------------------------------------------------------------- server scan */
    function drawScan(box) {
        var wrap = h("div", { class: "stack" });
        box.appendChild(wrap);
        var admin = S.me.role === "admin";
        function paint(d) {
            clear(wrap);
            var p = d.profile, sc = d.schedule;
            var actions = admin ? h("div", { class: "row" }, h("button", { text: "Scan now", onclick: function () { api("POST", "/api/scan", { action: "scan" }).then(paint).catch(fail); } }),
                h("button", { class: "primary", text: "Apply result", disabled: !p, onclick: function () {
                    api("POST", "/api/scan", { action: "apply" }).then(function (r) { toast("Applied. " + (r.changes && r.changes.length ? r.changes.join("; ") : "No changes since the last scan."), "good"); paint(r); }).catch(fail);
                } })) : null;
            wrap.appendChild(card("About this scan", actions, h("p", { text: "The scan finds out what is really installed on this server. Only that is checked and reported: if there is no KVM, no database or no backup product, it is left out of every scan and report." }),
                h("p", { class: "muted", text: sc.enabled ? "An automatic rescan runs every " + sc.weekday.toUpperCase() + " at " + sc.time + " and picks up anything new." : "The automatic weekly rescan is switched off." }),
                d.pending ? h("p", null, badge("WARNING"), " This result has not been applied yet.") : null));
            if (!p) { wrap.appendChild(card(null, null, h("p", { text: "No scan has been stored yet. Press Scan now." }))); return; }
            var inv = p.inventory;
            wrap.appendChild(card("This server", h("span", { class: "muted", text: "scanned " + p.scanned_at_text }), h("dl", { class: "kv" },
                h("dt", { text: "Host" }), h("dd", { text: inv.hostname }), h("dt", { text: "System" }), h("dd", { text: inv.os + " (kernel " + inv.kernel + ")" }),
                h("dt", { text: "Hardware" }), h("dd", { text: inv.cpus + " CPU, " + inv.memory_mb + " MB RAM, " + inv.virtualization }),
                h("dt", { text: "Filesystems" }), h("dd", { text: (inv.filesystems || []).join(", ") }),
                h("dt", { text: "Databases" }), h("dd", { text: (inv.databases || []).join(", ") || "none" }),
                h("dt", { text: "Containers" }), h("dd", { text: (inv.container_engines || []).join(", ") || "none" }))));
            var names = (d.order || Object.keys(p.modules)).filter(function (n) { return p.modules[n]; });
            wrap.appendChild(card("Areas", null, table(["Area", "Status", "Why", ""], names.map(function (n) {
                var m = p.modules[n], forced = d.forced.indexOf(n) >= 0;
                var state = m.present ? "checked" : (forced ? "forced" : "absent");
                return [h("b", { text: m.title }), h("span", { class: "badge b-" + state, text: m.present ? "checked" : (forced ? "checked (forced on)" : "left out") }), m.reason,
                    admin && !m.present ? h("button", { class: "small", text: forced ? "Stop checking" : "Check anyway", onclick: function () {
                        api("POST", "/api/scan", { action: "force", module: n, on: !forced }).then(paint).catch(fail);
                    } }) : ""];
            }))));
        }
        api("GET", "/api/scan").then(paint).catch(fail);
    }

    /* ---------------------------------------------------------------- settings */
    function makeInput(e) {
        var k = e.kind, cur = e.value, ctl, get;
        if (k === "bool") { ctl = h("input", { type: "checkbox", checked: cur === "yes" }); get = function () { return ctl.checked ? "yes" : "no"; }; }
        else if (k === "choice") {
            ctl = h("select", null, (e.choices || []).map(function (c) { return h("option", { value: c, text: c === "" ? "(default)" : c, selected: c === cur }); }));
            get = function () { return ctl.value; };
        } else if (k === "multi") {
            var have = (cur || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean), boxes = {};
            ctl = h("div", { class: "checks" }, (e.choices || []).map(function (c) {
                boxes[c] = h("input", { type: "checkbox", checked: have.indexOf(c) >= 0 });
                return h("label", null, boxes[c], c);
            }));
            get = function () { return (e.choices || []).filter(function (c) { return boxes[c].checked; }).join(","); };
        } else if (k === "secret") {
            ctl = h("input", { type: "password", autocomplete: "new-password", placeholder: e.set ? "(saved - type to replace)" : "(not set)" });
            get = function () { return ctl.value; };
        } else {
            ctl = h("input", { type: k === "int" || k === "float" ? "number" : "text", step: k === "float" ? "any" : (k === "int" ? "1" : null), value: cur, placeholder: e.default || "" });
            get = function () { return ctl.value; };
        }
        return { el: ctl, get: get, changed: function () { return k === "secret" ? get() !== "" : get() !== cur; } };
    }
    function sectionCard(file, sec, kinds, reload) {
        var inputs = {}, rows = h("div"), removeKeys = [], saveBtn = h("button", { class: "primary", text: "Save changes", disabled: true });
        function dirtyCheck() {
            var any = removeKeys.length > 0;
            Object.keys(inputs).forEach(function (key) { var d = inputs[key].changed(); inputs[key].row.className = "field" + (d ? " dirty" : ""); any = any || d; });
            saveBtn.disabled = !any;
        }
        function addRow(e, removable) {
            var inp = makeInput(e), ctl = h("div", { class: "ctl" }, inp.el);
            var row = h("div", { class: "field" }, h("div", { class: "name" }, e.key, e.help ? h("div", { class: "hint", text: e.help }) : null,
                e.default !== "" && e.kind !== "secret" ? h("div", { class: "hint", text: "default: " + e.default }) : null), ctl);
            if (file === "main" && sec.name === "general" && e.key === "logo") {      // upload the customer logo here instead of typing a path
                var pick = h("input", { type: "file", accept: "image/*", "aria-label": "Customer logo image" });
                if (e.value) ctl.insertBefore(h("img", { src: "/api/branding/logo?scope=all&t=" + Date.now(), alt: "current logo", class: "logoprev" }), ctl.firstChild);
                ctl.appendChild(h("div", { class: "row" }, pick, h("button", { class: "small primary", type: "button", text: "Upload and use", onclick: function () {
                    if (!pick.files[0]) { toast("Choose an image file first.", "bad"); return; }
                    sendLogo(pick.files[0], "all", function () { if (reload) reload(); });
                } })));
                ctl.appendChild(h("div", { class: "hint", text: "Upload any image: it is converted to PNG and fitted for the reports, and this path is filled in for you." }));
            }
            if (removable) ctl.appendChild(h("button", { class: "small danger", type: "button", text: "Remove", onclick: function () { removeKeys.push(e.key); delete inputs[e.key]; rows.removeChild(row); dirtyCheck(); } }));
            inp.row = row;
            inputs[e.key] = inp;
            inp.el.addEventListener("input", dirtyCheck);
            inp.el.addEventListener("change", dirtyCheck);
            rows.appendChild(row);
        }
        sec.keys.forEach(function (e) { addRow(e, sec.free); });
        var extra = [];
        if (sec.free) {
            var kn = h("input", { type: "text", placeholder: "new setting name", list: "keys-" + file + sec.name }), kv = h("input", { type: "text", placeholder: "value" });
            var suggestions = [];
            kinds.forEach(function (k) { if (sec.name.indexOf(k.prefix) === 0 && k.prefix) suggestions = k.keys; });
            var dl = h("datalist", { id: "keys-" + file + sec.name }, suggestions.filter(function (s) { return !inputs[s]; }).map(function (s) { return h("option", { value: s }); }));
            extra = [h("div", { class: "sectionbar" }, kn, kv, dl, h("button", { type: "button", text: "Add setting", onclick: function () {
                if (!kn.value.trim()) return;
                addRow({ key: kn.value.trim(), value: "", kind: "text", default: "", help: "", choices: null }, true);
                inputs[kn.value.trim()].el.value = kv.value;
                kn.value = ""; kv.value = ""; dirtyCheck();
            } }))];
        }
        saveBtn.addEventListener("click", function () {
            var values = {};
            Object.keys(inputs).forEach(function (key) { if (inputs[key].changed()) values[key] = inputs[key].get(); });
            api("POST", "/api/config", { file: file, section: sec.name, values: values, remove: removeKeys }).then(function (r) { toast("Saved " + file + ".conf [" + sec.name + "]", "good"); reload(); }).catch(fail);
        });
        var bar = [saveBtn];
        if (!sec.builtin) bar.push(h("button", { class: "danger", text: "Delete this section", onclick: function () {
            ask("Delete [" + sec.name + "] from " + file + ".conf?", function () { api("POST", "/api/config/section", { action: "delete", file: file, name: sec.name }).then(function () { toast("Deleted", "good"); reload(); }).catch(fail); }, true);
        } }));
        return card(sec.label, h("span", { class: "secname mono", text: sec.name === sec.label ? "" : "[" + sec.name + "]" }), rows, extra, h("div", { class: "sectionbar" }, bar));
    }
    function drawSettings(box, args) {
        var wrap = h("div", { class: "split" }), side = h("aside", { class: "card side" }), content = h("div", { class: "stack" });
        wrap.appendChild(side); wrap.appendChild(content); box.appendChild(wrap);
        var current = args[0] || "main", filter = "";
        function reload() { api("GET", "/api/config").then(function (cat) { S.cat = cat; draw(); }).catch(fail); }
        function draw() {
            clear(side); clear(content);
            S.cat.files.forEach(function (f) {
                side.appendChild(h("a", { href: "#/settings/" + f.file, class: f.file === current ? "on" : "" }, f.title, h("small", { text: f.file + ".conf" })));
            });
            var f = S.cat.files.filter(function (x) { return x.file === current; })[0] || S.cat.files[0];
            var search = h("input", { type: "text", placeholder: "Filter settings...", value: filter, oninput: function (e) { filter = e.target.value.toLowerCase(); drawSections(); } });
            content.appendChild(card(f.title, h("span", { class: "secname mono", text: f.file + ".conf" }), h("p", { class: "muted", text: f.about }), search));
            var sectionsBox = h("div", { class: "stack" });
            content.appendChild(sectionsBox);
            function drawSections() {
                clear(sectionsBox);
                f.sections.forEach(function (sec) {
                    var copy = JSON.parse(JSON.stringify(sec));
                    if (filter) {
                        copy.keys = copy.keys.filter(function (k) { return (k.key + " " + k.help + " " + sec.name + " " + sec.label).toLowerCase().indexOf(filter) >= 0; });
                        if (!copy.keys.length && (sec.name + sec.label).toLowerCase().indexOf(filter) < 0) return;
                    }
                    if (!copy.keys.length && !sec.free && !filter) return;
                    sectionsBox.appendChild(sectionCard(f.file, copy, f.kinds, reload));
                });
                if (f.kinds && f.kinds.length && !filter) sectionsBox.appendChild(newSectionCard(f));
            }
            drawSections();
        }
        function newSectionCard(f) {
            var kind = h("select", null, f.kinds.map(function (k, i) { return h("option", { value: i, text: k.label }); }));
            var name = h("input", { type: "text", placeholder: "name, e.g. ORCL or web01" });
            return card("Add new", null, h("div", { class: "row" }, kind, name, h("button", { class: "primary", text: "Create", onclick: function () {
                var k = f.kinds[kind.value], n = name.value.trim();
                if (!n) return toast("Enter a name first", "bad");
                api("POST", "/api/config/section", { action: "add", file: f.file, name: k.prefix + n }).then(function () { toast("Created " + k.prefix + n, "good"); reload(); }).catch(fail);
            } })));
        }
        window.addEventListener("hashchange", function onh() {
            if (!document.body.contains(wrap)) { window.removeEventListener("hashchange", onh); return; }
            var p = (location.hash || "").replace(/^#\//, "").split("/");
            if (p[0] === "settings" && S.cat) { current = p[1] || "main"; draw(); }
        });
        reload();
    }

    /* ---------------------------------------------------------------- operations */
    function updateCard() {
        var box = card("Automatic updates", null, h("p", { class: "muted", text: "Loading..." }));
        function load() { api("GET", "/api/update").then(paint).catch(function () { /* signed out */ }); }
        function paint(d) {
            clear(box);
            var s = d.settings, st = d.state || {}, res = st.result || "";
            var tone = { updated: "OK", current: "OK", postponed: "WARNING", "rolled back": "WARNING", failed: "CRITICAL", off: "UNKNOWN" }[res] || "UNKNOWN";
            box.appendChild(h("h2", null, h("span", { text: "Automatic updates" }), badge(s.auto ? (st.available ? "WARNING" : tone) : "UNKNOWN")));
            box.appendChild(h("p", { class: "muted", text: s.auto ? "Every day at " + s.time + ": if a newer LRS Vitals is out, it waits until no health check or report is running, backs up the program, updates, tests itself and restarts; on any problem it puts the old version back. Settings and data are never touched." : "Switched off. Set auto = yes in Settings > main > update to update safely every evening." }));
            box.appendChild(table(["", ""], [
                ["Installed", d.installed ? h("span", { class: "mono", text: d.installed.slice(0, 12) }) : "unknown (installed before automatic updates)"],
                ["Newest on GitHub", st.latest ? h("span", null, h("span", { class: "mono", text: st.latest.slice(0, 12) + " " }), st.latest_message || "") : "not checked yet"],
                ["Last check", st.checked_at ? when(st.checked_at) : "-"],
                ["Last result", res ? h("span", null, h("b", { text: res + ": " }), st.message || "") : "-"],
                ["Keeps", s.keep_backups + " previous version(s) as backups; waits up to " + s.wait_minutes + " min for running jobs"]]));
            if (st.log && st.log.length) box.appendChild(h("details", null, h("summary", { text: "Steps of the last update" }), h("pre", { class: "logs", text: st.log.join("\n") })));
            if (S.me.role === "admin") box.appendChild(h("div", { class: "row" },
                h("button", { type: "button", text: "Check now", onclick: function () {
                    api("POST", "/api/update", { action: "check" }).then(function (r) { toast(r.available ? "A newer version is available: " + r.latest.slice(0, 7) + " " + r.message : "Up to date", r.available ? "" : "good"); load(); }).catch(fail);
                } }),
                h("button", { class: "primary", type: "button", text: "Update now", onclick: function () {
                    ask("Update LRS Vitals now? It waits for running reports, backs up, updates, tests and restarts the web page (it comes back in about a minute). On any problem the old version is restored.", function () {
                        api("POST", "/api/update", { action: "now" }).then(function () { toast("Update started. This page reconnects when the web service is back.", "good"); setTimeout(load, 60000); }).catch(fail);
                    });
                } })));
        }
        load();
        return box;
    }
    function drawOperations(box) {
        var wrap = h("div", { class: "stack" });
        box.appendChild(updateCard());
        if (S.me.role === "admin") box.appendChild(card("Self-repair", null, h("p", { class: "muted", text: "Checks LRS Vitals itself on this server - program, folders, failed jobs with their reason, watcher, web service and timers - and fixes what it can. Each server's own page has the same button; from this central server it repairs members over SSH." }),
            h("div", { class: "row" }, h("button", { class: "primary", type: "button", text: "Repair this server", onclick: function () { runRepair("_local", "this server"); } }))));
        box.appendChild(wrap);
        function act(name, ok) { return function () { api("POST", "/api/action", { name: name }).then(ok || function (r) { toast("Done: " + JSON.stringify(r), "good"); load(); }).catch(fail); }; }
        function load() {
            Promise.all([api("GET", "/api/watch"), api("GET", "/api/schedules"), api("GET", "/api/backups"), api("GET", "/api/logs"), api("GET", "/api/toolhealth"), api("GET", "/api/retention"), api("GET", "/api/cert")]).then(function (r) {
                var w = r[0], sch = r[1], bk = r[2], logs = r[3], th = r[4], rt = r[5], cr = r[6];
                clear(wrap);
                var bad = th.checks.filter(function (c) { return c.status !== "ok"; }).length;
                wrap.appendChild(card("Health of LRS Vitals", badge(bad ? (th.checks.some(function (c) { return c.status === "critical"; }) ? "CRITICAL" : "WARNING") : "OK"),
                    h("p", { class: "muted", text: bad ? bad + " problem(s) with the monitoring itself." : "The watcher, schedules, reports and e-mail are all working." }),
                    table(["Check", "Status", "Detail"], th.checks.map(function (c) { return [h("b", { text: c.name }), badge(c.status === "ok" ? "OK" : c.status.toUpperCase()), c.detail]; }))));
                var cs = cr.status, cj = cr.job;
                wrap.appendChild(card("Web certificate (Let's Encrypt)", badge(cs.certificate ? (cs.certificate.days_left < 14 ? "WARNING" : "OK") : (cs.enabled ? "WARNING" : "UNKNOWN")),
                    h("p", { class: "muted", text: cs.enabled ? "Names: " + (cs.domains.join(", ") || "none") + " - " + cs.directory + " - check by " + cs.challenge : "Switched off. Set acme = yes in Settings > web to use a free Let's Encrypt certificate for this web interface only." }),
                    cs.problem ? h("p", { class: "err", text: cs.problem }) : null,
                    cs.certificate ? table(["Subject", "Issuer", "Expires", "Days left", "Used by this page"], [[cs.certificate.subject, cs.certificate.issuer, when(cs.certificate.expires), cs.certificate.days_left.toFixed(0), cs.certificate.in_use ? "yes" : "no"]])
                        : h("p", { class: "muted", text: "No Let's Encrypt certificate yet." }),
                    cs.last_error ? h("p", { class: "err", text: "Last problem: " + cs.last_error }) : null,
                    cj.result ? h("p", { class: cj.result.ok ? "muted" : "err", text: cj.result.message }) : null,
                    h("p", { class: "muted", text: "It needs a public DNS name that points at this server and port 80 reachable from the internet; a LAN-only name or an IP address cannot get one. Renewal is automatic " + cs.renew_days + " days before expiry while the web service runs." }),
                    S.me.role === "admin" ? h("div", { class: "row" }, h("button", { class: "primary", text: cj.running ? "Working..." : "Get / renew now", disabled: !cs.enabled || cj.running, onclick: function () {
                        api("POST", "/api/cert", { action: "issue", force: true }).then(function () { toast("Asking the certificate authority...", "good"); setTimeout(load, 5000); }).catch(fail);
                    } })) : null));
                var space = rt.space;
                wrap.appendChild(card("Retention - what is kept and for how long", h("span", { class: "muted", text: space.total ? size(space.free) + " free of " + size(space.total) : "" }),
                    table(["Report type", "Kept for", "Reports", "Disk used", "Oldest", "Newest", "Next removal"], rt.types.map(function (t) {
                        return [h("b", { text: t.type }), t.keep_days + " days", t.count, size(t.bytes), t.oldest ? when(t.oldest) : "-", t.newest ? when(t.newest) : "-", t.next_removal ? when(t.next_removal) : "-"];
                    }), [2, 3]),
                    table(["Other data", "Kept"], rt.other.map(function (o) { return [o.name, o.keep]; })),
                    h("p", { class: "muted", text: "Old reports are removed automatically" + (space.auto_apply ? "" : " (automatic cleanup is OFF)") + ". If free space drops below " + space.min_free_percent + "% the oldest reports go first" +
                        (space.cleanup ? "" : " (space cleanup is OFF)") + (space.max_report_dir_gb ? ", and reports never use more than " + space.max_report_dir_gb + " GB" : "") + "." })));
                var st = w.state;
                wrap.appendChild(card("Real-time monitoring", badge(w.service === "active" ? "OK" : "UNKNOWN"),
                    h("p", { class: "muted", text: "A light watcher checks the server every minute and sends alerts. Service: " + w.service + (w.installed ? "" : " (not installed)") }),
                    st ? h("p", { text: "Last check: " + (st.last_check_text || when(st.last_check || 0)) }) : null,
                    h("div", { class: "row" }, h("button", { class: "primary", text: "Install / restart service", onclick: act("watch-install") }),
                        h("button", { class: "danger", text: "Remove service", onclick: function () { ask("Stop and remove the real-time monitor?", act("watch-remove"), true); } }))));
                wrap.appendChild(card("Schedules (" + sch.backend + ")", h("button", { class: "primary small", text: "Install / refresh timers", onclick: act("schedule-install", function (x) { toast((x.errors && x.errors.length ? x.errors.join("; ") : "Installed " + x.installed.length + " schedule(s)"), x.errors && x.errors.length ? "bad" : "good"); load(); }) }),
                    table(["Name", "On", "When", "Days", "Formats", "Email", "Installed", "Next run"], sch.schedules.map(function (s) {
                        return [h("b", { text: s.name }), s.enabled ? "yes" : "no", s.when, s.days, s.formats, s.email ? "yes" : "no", s.installed ? badge("OK") : badge("WARNING"), s.next || ""];
                    }), [3])));
                wrap.appendChild(card("Email", null, h("p", { class: "muted", text: "Send a test message with the current email settings." }),
                    h("button", { text: "Send test email", onclick: act("email-test", function (x) { toast(x.ok ? "Test email sent to " + (x.recipients || []).join(", ") : "Failed: " + x.error, x.ok ? "good" : "bad"); }) })));
                wrap.appendChild(card("Configuration backups", h("button", { class: "small primary", text: "Back up now", onclick: act("config-backup", function (x) { toast("Saved " + x.archive, "good"); load(); }) }),
                    bk.backups.length ? table(["Backup", "Taken", "Size", ""], (bk.details || bk.backups.map(function (b) { return { name: b, size: 0, time: 0 }; })).slice(0, 15).map(function (d) {
                        var b = d.name;
                        return [h("span", { class: "mono", text: b }), d.time ? when(d.time) : "-", d.size ? size(d.size) : "-", h("div", { class: "row" },
                            h("a", { class: "btn small", href: "/api/backups?download=" + encodeURIComponent(b), text: "Download" }),
                            h("button", { class: "small", text: "Restore", onclick: function () {
                                ask("Replace the current settings with " + b + "? The current settings are backed up first.", function () { api("POST", "/api/backups", { archive: b }).then(function () { toast("Restored", "good"); load(); }).catch(fail); }, true);
                            } }))];
                    }), [2]) : h("p", { class: "muted", text: "No backups yet. One is taken before every change." })));
                var logBox = h("pre", { class: "logs", text: "Choose a log." }), sel = h("select", { onchange: function (e) {
                    if (!e.target.value) return;
                    api("GET", "/api/logs?name=" + encodeURIComponent(e.target.value) + "&lines=300").then(function (j) { logBox.textContent = j.lines.join("\n") || "(empty)"; logBox.scrollTop = logBox.scrollHeight; }).catch(fail);
                } }, h("option", { value: "", text: "Select a log file..." }), logs.logs.map(function (l) { return h("option", { value: l, text: l }); }));
                wrap.appendChild(card("Logs", sel, logBox));
                var auditBox = h("div", null, h("p", { class: "muted", text: "Loading..." }));
                var filter = h("input", { type: "search", placeholder: "Filter by user, action or text", "class": "filter" });
                api("GET", "/api/audit").then(function (j) {
                    function paintAudit() {
                        var q = filter.value.toLowerCase();
                        var rows = j.records.filter(function (x) { return !q || (x.user + " " + x.action + " " + x.detail).toLowerCase().indexOf(q) >= 0; }).slice(0, 150);
                        clear(auditBox);
                        auditBox.appendChild(rows.length ? table(["When", "Who", "What", "Details"], rows.map(function (x) { return [x.time, h("b", { text: x.user }), x.action, h("span", { class: "muted", text: x.detail })]; }))
                            : h("p", { class: "muted", text: "Nothing matches." }));
                    }
                    filter.addEventListener("input", paintAudit);
                    paintAudit();
                });
                wrap.appendChild(card("Audit log - who changed what", filter, auditBox));
            }).catch(fail);
        }
        load();
    }

    /* ---------------------------------------------------------------- account */
    function drawAccount(box) {
        var wrap = h("div", { class: "stack" });
        box.appendChild(wrap);
        var th = h("select", { "aria-label": "Colour theme" }, THEMES.map(function (x) { return h("option", { value: x[0], text: x[1], selected: x[0] === savedTheme() }); }));
        th.addEventListener("change", function () { theme(th.value); });
        var rf = h("select", { "aria-label": "Refresh interval" }, REFRESH.map(function (x) { return h("option", { value: x[0], text: x[1], selected: x[0] === refreshSeconds() }); }));
        rf.addEventListener("change", function () { setRefresh(+rf.value); toast("Refresh interval saved for this browser", "good"); });
        wrap.appendChild(card("Appearance", h("span", { class: "muted", text: "saved in this browser" }), h("div", { class: "row" },
            h("label", null, "Colour theme ", th), h("label", null, "Refresh live data ", rf),
            h("span", { class: "muted", text: "The page fits any screen: phones, laptops, large and 4K monitors." }))));
        var oldp = h("input", { type: "password", placeholder: "Current password", autocomplete: "current-password" });
        var newp = h("input", { type: "password", placeholder: "New password (10+ characters, upper, lower, digit)", autocomplete: "new-password" });
        wrap.appendChild(card("Change my password", null, h("div", { class: "row" }, oldp, newp, h("button", { class: "primary", text: "Change password", onclick: function () {
            api("POST", "/api/password", { old: oldp.value, "new": newp.value }).then(function () { toast("Password changed", "good"); oldp.value = ""; newp.value = ""; }).catch(fail);
        } }))));
        if (S.me.role !== "admin") return;
        var users = h("div");
        wrap.appendChild(card("Users", null, users));
        function paint(list) {
            clear(users);
            users.appendChild(table(["User", "Role", ""], list.map(function (u) {
                var self = u.user === S.me.user;
                return [h("b", { text: u.user }), u.role, h("div", { class: "row" },
                    h("button", { class: "small", text: "Reset password", onclick: function () {
                        var p = window.prompt("New password for " + u.user + " (10+ characters, upper, lower, digit):");
                        if (p) api("POST", "/api/users", { action: "passwd", user: u.user, password: p }).then(function () { toast("Password set", "good"); }).catch(fail);
                    } }),
                    h("button", { class: "small", text: u.role === "admin" ? "Make read-only" : "Make administrator", disabled: self, onclick: function () {
                        api("POST", "/api/users", { action: "role", user: u.user, role: u.role === "admin" ? "viewer" : "admin" }).then(function (j) { paint(j.users); }).catch(fail);
                    } }),
                    h("button", { class: "small danger", text: "Remove", disabled: self, onclick: function () {
                        ask("Remove user " + u.user + "?", function () { api("POST", "/api/users", { action: "remove", user: u.user }).then(function (j) { paint(j.users); }).catch(fail); }, true);
                    } }))];
            })));
            var nu = h("input", { type: "text", placeholder: "New user name" }), np = h("input", { type: "password", placeholder: "Password", autocomplete: "new-password" });
            var role = h("select", null, h("option", { value: "viewer", text: "read-only" }), h("option", { value: "admin", text: "administrator" }));
            users.appendChild(h("div", { class: "sectionbar" }, nu, np, role, h("button", { class: "primary", text: "Add user", onclick: function () {
                api("POST", "/api/users", { action: "add", user: nu.value.trim(), password: np.value, role: role.value }).then(function (j) { toast("User added", "good"); paint(j.users); }).catch(fail);
            } })));
        }
        api("GET", "/api/users").then(function (j) { paint(j.users); }).catch(fail);
    }

    /* ---------------------------------------------------------------- help */
    function drawHelp(box) {
        api("GET", "/api/help").then(function (j) { box.appendChild(card("Help", null, h("pre", { class: "logs", text: j.lines.join("\n") }))); }).catch(fail);
    }

    /* ---------------------------------------------------------------- start */
    api("GET", "/api/me").then(function (me) { S.me = me; shell(); }).catch(showLogin);
}());

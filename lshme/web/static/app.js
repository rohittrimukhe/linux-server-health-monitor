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
        if (!toastBox) { toastBox = h("div", { class: "toasts" }); document.body.appendChild(toastBox); }
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
    function ring(value, level, text, big) {
        var g = h("div", { class: "gauge lvl-" + level + (big ? " big" : "") }, h("b", { text: text }));
        g.style.setProperty("--p", Math.max(0, Math.min(100, value)));
        return g;
    }
    function bar(value, level) {
        var i = h("i"), b = h("div", { class: "bar lvl-" + level }, i);
        i.style.width = Math.max(0, Math.min(100, value)) + "%";
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
    function table(heads, rows, nums) {
        var t = h("table", { class: "tbl" }), tr = h("tr");
        heads.forEach(function (x, i) { tr.appendChild(h("th", { text: x, "class": nums && nums.indexOf(i) >= 0 ? "num" : "" })); });
        t.appendChild(h("thead", null, tr));
        var tb = h("tbody");
        rows.forEach(function (r) {
            var row = h("tr");
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
    var PAGES = [
        { id: "dashboard", title: "Dashboard", draw: drawDashboard },
        { id: "fleet", title: "Servers", draw: drawFleet },
        { id: "reports", title: "Reports", draw: drawReports },
        { id: "scan", title: "Server scan", draw: drawScan },
        { id: "settings", title: "Settings", draw: drawSettings, admin: true },
        { id: "operations", title: "Operations", draw: drawOperations, admin: true },
        { id: "account", title: "Account", draw: drawAccount },
        { id: "help", title: "Help", draw: drawHelp }
    ];
    var main;
    function theme(t) { if (t) { document.documentElement.setAttribute("data-theme", t); try { localStorage.setItem("lshme-theme", t); } catch (e) { /* storage may be blocked */ } } }
    function shell() {
        try { theme(localStorage.getItem("lshme-theme")); } catch (e) { /* ignore */ }
        clear(root);
        var nav = h("nav");
        PAGES.forEach(function (p) {
            if (p.admin && S.me.role !== "admin") return;
            nav.appendChild(h("a", { href: "#/" + p.id, "data-page": p.id, text: p.title }));
        });
        var dark = function () { return document.documentElement.getAttribute("data-theme") === "dark" || (!document.documentElement.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches); };
        var top = h("header", { class: "top" }, h("div", { class: "in" }, h("img", { src: "/lrs_logo.png", alt: "LRS Services" }), nav,
            h("div", { class: "who" }, h("span", { class: "host", text: S.me.host }), h("span", { text: S.me.user + " (" + S.me.role + ")" }),
                h("button", { class: "small", text: "Light / dark", onclick: function () { theme(dark() ? "light" : "dark"); } }),
                h("button", { class: "small", text: "Sign out", onclick: function () { api("POST", "/api/logout").then(showLogin, showLogin); } }))));
        main = h("main");
        root.appendChild(top);
        root.appendChild(main);
        root.appendChild(h("footer", { text: S.me.app + " " + S.me.version + " - LRS Services Pvt Ltd, Mumbai" + (S.me.tls ? "" : " - not encrypted (local access only)") }));
        route();
    }
    function route() {
        if (!S.me) return;
        var parts = (location.hash || "#/dashboard").replace(/^#\//, "").split("/");
        var page = PAGES.filter(function (p) { return p.id === parts[0] && (!p.admin || S.me.role === "admin"); })[0] || PAGES[0];
        stopPage();
        Array.prototype.forEach.call(document.querySelectorAll(".top nav a"), function (a) { a.className = a.getAttribute("data-page") === page.id ? "on" : ""; });
        clear(main);
        document.title = page.title + " - " + S.me.host + " - LRS Vitals";
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
        var alerts = alertsCard();
        box.appendChild(h("div", { class: "stack" }, bannerBox, alerts.el, live));
        var run = runWidget(function () { tick(); });
        var stopped = false, timer = null;
        function paintBanner(hl) {
            clear(bannerBox);
            var inner = h("div", { class: "row space" });
            if (hl) {
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
            bannerBox.appendChild(card(null, null, inner));
        }
        function paint(d) {
            var s = d.sample, L = d.limits, cpu = s.cpu, mem = s.mem, H = s.history;
            paintBanner(d.health);
            clear(live);
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
        timer = setInterval(tick, 2000);
        var atimer = setInterval(alerts.load, 10000);
        S.cleanup = function () { stopped = true; clearInterval(timer); clearInterval(atimer); run.stop(); };
    }

    /* ---------------------------------------------------------------- alerts */
    function ago(ts) {
        if (!ts) return "-";
        var s = Math.max(0, Date.now() / 1000 - ts);
        return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 172800 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago";
    }
    function alertsCard() {
        var box = h("div");
        function paint(d) {
            clear(box);
            var ch = d.channels, how = [];
            if (ch.email) how.push("email" + (ch.to ? " to " + ch.to : ""));
            if (ch.syslog) how.push("syslog");
            if (ch.webhook) how.push("webhook");
            var info = h("span", { class: "muted", text: how.length ? "Sent by " + how.join(", ") + " - from " + ch.min_severity + " up, after " + ch.confirm + " bad checks in a row" : "No alert channel is switched on (Settings > main > watch)" });
            var body = h("div", { class: "stack" });
            body.appendChild(d.active.length ? table(["Level", "Problem", "Since", "Checks", "Alert"], d.active.map(function (a) {
                return [badge(a.severity), h("div", null, h("b", { text: a.component }), h("div", { class: "muted", text: a.message })), ago(a.since), a.count, a.alerted ? "sent" : "waiting"];
            }), [3]) : h("p", { class: "muted", text: "No active problems. CPU, memory, disks, services and ports are within their limits." }));
            if (d.events.length) {
                body.appendChild(h("h3", { text: "Recent alerts and recoveries" }));
                body.appendChild(table(["When", "Event", "Level", "Message"], d.events.slice(0, 10).map(function (e) {
                    return [ago(e.ts), e.event, e.severity ? badge(e.severity) : "", e.message || e.component];
                })));
            }
            box.appendChild(card("Alerts", info, body));
        }
        function load() { api("GET", "/api/alerts").then(paint).catch(function () { /* signed out */ }); }
        load();
        return { el: box, load: load };
    }

    /* ---------------------------------------------------------------- all servers */
    function drawFleet(box) {
        var wrap = h("div", { class: "stack" });
        box.appendChild(wrap);
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
                    if (s.top) cardEl.appendChild(h("div", { class: "top", text: s.top }));
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
            body.appendChild(h("h3", { text: "Files" }));
            body.appendChild(h("div", { class: "row" }, d.files.map(function (f) {
                var viewable = /\.(html|pdf)$/i.test(f.name);
                return h("a", { class: "btn", href: f.url, target: "_blank", rel: "noopener", text: (viewable ? "Open " : "Download ") + f.name + " (" + size(f.size) + ")" });
            })));
        }).catch(function (e) { clear(body); body.appendChild(h("p", { class: "err", text: e.message })); });
    }
    function drawReports(box) {
        var wrap = h("div", { class: "stack" });
        box.appendChild(wrap);
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
    function drawOperations(box) {
        var wrap = h("div", { class: "stack" });
        box.appendChild(wrap);
        function act(name, ok) { return function () { api("POST", "/api/action", { name: name }).then(ok || function (r) { toast("Done: " + JSON.stringify(r), "good"); load(); }).catch(fail); }; }
        function load() {
            Promise.all([api("GET", "/api/watch"), api("GET", "/api/schedules"), api("GET", "/api/backups"), api("GET", "/api/logs")]).then(function (r) {
                var w = r[0], sch = r[1], bk = r[2], logs = r[3];
                clear(wrap);
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
                    bk.backups.length ? table(["Backup", ""], bk.backups.slice(0, 15).map(function (b) {
                        return [h("span", { class: "mono", text: b }), h("button", { class: "small", text: "Restore", onclick: function () {
                            ask("Replace the current settings with " + b + "? The current settings are backed up first.", function () { api("POST", "/api/backups", { archive: b }).then(function () { toast("Restored", "good"); }).catch(fail); }, true);
                        } })];
                    })) : h("p", { class: "muted", text: "No backups yet. One is taken before every change." })));
                var logBox = h("pre", { class: "logs", text: "Choose a log." }), sel = h("select", { onchange: function (e) {
                    if (!e.target.value) return;
                    api("GET", "/api/logs?name=" + encodeURIComponent(e.target.value) + "&lines=300").then(function (j) { logBox.textContent = j.lines.join("\n") || "(empty)"; logBox.scrollTop = logBox.scrollHeight; }).catch(fail);
                } }, h("option", { value: "", text: "Select a log file..." }), logs.logs.map(function (l) { return h("option", { value: l, text: l }); }));
                wrap.appendChild(card("Logs", sel, logBox));
                var audit = h("pre", { class: "logs", text: "Loading..." });
                api("GET", "/api/audit").then(function (j) { audit.textContent = j.lines.join("\n") || "(empty)"; audit.scrollTop = audit.scrollHeight; });
                wrap.appendChild(card("Audit log - who changed what", null, audit));
            }).catch(fail);
        }
        load();
    }

    /* ---------------------------------------------------------------- account */
    function drawAccount(box) {
        var wrap = h("div", { class: "stack" });
        box.appendChild(wrap);
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

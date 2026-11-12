/* Linux Server Health Monitor Enterprise - live health page
 * Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
 *
 * One page, two hosts:
 *  - inside Cockpit: data files are read with cockpit.file() and watched, so
 *    the page updates the moment the watcher or a run writes new data;
 *  - standalone (`healthcheck live`): the same data is polled from a small
 *    read-only JSON API every few seconds.
 */
(function () {
    "use strict";
    var cfg = { data_dir: "/var/lib/lshme", command: ["/usr/local/bin/healthcheck"], mode: "cockpit", poll_seconds: 15 };
    var state = { latest: null, watch: null, metrics: [], hours: 6 };
    var C = window.LshmeCharts;

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s === undefined || s === null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "–"; }
    function sev(s) { return '<span class="sev ' + esc(s) + '">' + esc(s) + "</span>"; }
    var standalone = function () { return cfg.mode === "http" || typeof window.cockpit === "undefined"; };

    /* ---------- latest full health check ---------- */
    function renderLatest(run) {
        state.latest = run;
        if (!run) {
            $("sub").textContent = "No health check has run yet." + (standalone() ? " Run 'healthcheck run' on the server." : " Click 'Run health check now'.");
            return;
        }
        var h = run.health || {}, c = h.counts || {};
        $("sub").textContent = (run.fqdn || run.host) + " · last check " + when(run.collected_at) + " · " + (run.days || 1) + "-day window";
        $("score").textContent = h.score === undefined ? "–" : h.score;
        $("score").className = "score t-" + (h.status || "");
        $("status").innerHTML = sev(h.status || "UNKNOWN");
        $("counts").textContent = "critical " + (c.CRITICAL || 0) + " · warning " + (c.WARNING || 0) + " · unknown " + (c.UNKNOWN || 0) +
            ((run.excluded && run.excluded.length) ? " · excluded " + run.excluded.length : "");
        C.donut($("g-sev"), ["CRITICAL", "WARNING", "UNKNOWN", "INFO"].map(function (k) {
            return { label: k.charAt(0) + k.slice(1).toLowerCase(), value: c[k] || 0, color: C.COLORS[k] };
        }), { label: "findings" });
        C.bars($("g-mod"), (h.modules || []).filter(function (m) { return m.score !== null; })
            .map(function (m) { return { label: m.title, value: m.score }; }), { score: true, label: "Module scores" });

        var issues = [];
        (run.modules || []).forEach(function (m) {
            (m.findings || []).forEach(function (f) {
                if (f.severity === "CRITICAL" || f.severity === "WARNING" || f.severity === "UNKNOWN") issues.push(f);
            });
        });
        var rank = { CRITICAL: 0, WARNING: 1, UNKNOWN: 2 };
        issues.sort(function (a, b) { return rank[a.severity] - rank[b.severity]; });
        $("issues").innerHTML = issues.length ? "<table><thead><tr><th>Severity</th><th>Component</th><th>Finding</th></tr></thead><tbody>" +
            issues.map(function (f) {
                return "<tr><td>" + sev(f.severity) + "</td><td>" + esc(f.component) + "</td><td>" + esc(f.message) +
                    (f.recommendation ? '<div class="rec">' + esc(f.recommendation) + "</div>" : "") + "</td></tr>";
            }).join("") + "</tbody></table>" : "No critical, warning or unknown findings.";
        $("issues").className = issues.length ? "" : "muted";
        $("modules").innerHTML = "<table><thead><tr><th>Module</th><th>Score</th><th>Status</th><th>Summary</th></tr></thead><tbody>" +
            (h.modules || []).map(function (m) {
                var st = m.score === null ? "SKIPPED" : (m.worst === "CRITICAL" || m.worst === "WARNING" ? m.worst : "OK");
                var mod = (run.modules || []).filter(function (x) { return x.name === m.module; })[0] || {};
                return "<tr><td>" + esc(m.title) + "</td><td>" + (m.score === null ? "–" : esc(m.score)) + "</td><td>" + sev(st) +
                    "</td><td>" + esc(mod.summary || "") + "</td></tr>";
            }).join("") + "</tbody></table>";
        var files = (run.meta && run.meta.report_files) || [];
        if (files.length && !standalone()) {
            $("reports").className = "";
            $("reports").innerHTML = files.map(function (p, i) {
                return '<button class="btn" type="button" data-i="' + i + '">' + esc(p.split("/").pop()) + "</button>";
            }).join(" ");
            Array.prototype.forEach.call($("reports").querySelectorAll("button"), function (b) {
                b.addEventListener("click", function () { openReport(files[+b.dataset.i]); });
            });
        } else {
            $("reports").textContent = files.length ? "Latest reports: " + files.map(function (p) { return p.split("/").pop(); }).join(", ") +
                " (open them on the server or through Cockpit)." : "Reports from the latest run appear here.";
        }
    }

    function openReport(path) {
        var type = { html: "text/html", pdf: "application/pdf", json: "application/json", txt: "text/plain", csv: "text/csv",
            xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation" }[path.split(".").pop()] || "application/octet-stream";
        cockpit.file(path, { binary: true, superuser: "try", max_read_size: 64 * 1024 * 1024 }).read().then(function (data) {
            var url = URL.createObjectURL(new Blob([data], { type: type }));
            var a = document.createElement("a");
            a.href = url;
            a.target = "_blank";
            a.rel = "noopener";
            if (!/html|pdf|plain|json/.test(type)) a.download = path.split("/").pop();
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
        }).catch(function (e) { $("runmsg").textContent = "Cannot open report: " + e; });
    }

    /* ---------- real-time watcher ---------- */
    function renderWatch(st) {
        state.watch = st;
        if (!st || !st.last_tick) return;
        var probs = st.problems || {}, keys = Object.keys(probs);
        var html = '<div class="muted">Last check ' + esc(when(st.last_tick)) + " · " + esc(st.ticks || 0) + " checks · " +
            esc(st.last_tick_cpu_ms) + " ms CPU · " + esc(st.rss_mb) + " MB</div>";
        html += !keys.length ? "<p>No active problems.</p>" :
            "<table><thead><tr><th>Severity</th><th>Component</th><th>Problem</th><th>Since</th></tr></thead><tbody>" +
            keys.map(function (k) {
                var p = probs[k];
                return "<tr><td>" + sev(p.severity) + "</td><td>" + esc(p.component) + "</td><td>" + esc(p.message) + "</td><td>" + esc(when(p.since)) + "</td></tr>";
            }).join("") + "</tbody></table>";
        $("watch").className = "";
        $("watch").innerHTML = html;
    }

    function renderMetrics(series) {
        state.metrics = Array.isArray(series) ? series : [];
        var cutoff = Date.now() / 1000 - state.hours * 3600;
        var pts = state.metrics.filter(function (p) { return p.t >= cutoff; });
        var col = function (k) { return pts.map(function (p) { return [p.t, p[k] === undefined ? null : p[k]]; }); };
        C.line($("g-usage"), [{ name: "CPU", color: "#0066cc", points: col("cpu") }, { name: "Memory", color: "#8476d1", points: col("mem") },
            { name: "Swap", color: "#ec7a08", points: col("swap") }],
        { max: 100, unit: "%", label: "CPU, memory and swap", thresholds: [{ value: 85, color: C.COLORS.WARNING }, { value: 95, color: C.COLORS.CRITICAL }] });
        C.line($("g-load"), [{ name: "Load/CPU", color: "#009596", points: col("load") }],
            { label: "Load per CPU", thresholds: [{ value: 1.5, color: C.COLORS.WARNING }, { value: 3, color: C.COLORS.CRITICAL }] });
        C.line($("g-problems"), [{ name: "Critical", color: C.COLORS.CRITICAL, points: col("crit") }, { name: "Warning", color: C.COLORS.WARNING, points: col("warn") }],
            { label: "Active problems" });
        var last = state.metrics[state.metrics.length - 1];
        C.bars($("g-disk"), last ? Object.keys(last.disk || {}).map(function (k) { return { label: k, value: last.disk[k] }; }) : [], { unit: "%", label: "Filesystem usage" });
    }

    /* ---------- data sources ---------- */
    function startCockpit() {
        var watch = function (name, render) {
            cockpit.file(cfg.data_dir + "/" + name, { syntax: JSON, superuser: "try", max_read_size: 32 * 1024 * 1024 })
                .watch(function (content) { render(content); });
        };
        watch("latest.json", renderLatest);
        watch("watch_state.json", renderWatch);
        watch("watch_metrics.json", renderMetrics);
        $("run").addEventListener("click", runNow);
    }

    function startHttp() {
        document.documentElement.classList.add("standalone");
        var get = function (url, render) {
            var x = new XMLHttpRequest();
            x.open("GET", url);
            x.onload = function () { if (x.status === 200) { try { render(JSON.parse(x.responseText)); } catch (e) { } } };
            x.send();
        };
        var poll = function () {
            get("api/latest", renderLatest);
            get("api/watch", renderWatch);
            get("api/metrics", renderMetrics);
        };
        poll();
        setInterval(poll, Math.max(5, cfg.poll_seconds) * 1000);
    }

    function runNow() {
        var btn = $("run");
        btn.disabled = true;
        $("runmsg").textContent = "Running… (low priority, usually under a minute)";
        cockpit.spawn(cfg.command.concat(["run", "--formats", "html,pdf,json", "-q"]), { superuser: "require", err: "message" })
            .then(function () { $("runmsg").textContent = "Finished " + new Date().toLocaleTimeString(); })
            .catch(function (e) { $("runmsg").textContent = "Run failed: " + (e.message || e); })
            .then(function () { btn.disabled = false; });
    }

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll(".range button"), function (b) {
            b.addEventListener("click", function () {
                state.hours = +b.dataset.h;
                Array.prototype.forEach.call(document.querySelectorAll(".range button"), function (x) { x.classList.toggle("on", x === b); });
                renderMetrics(state.metrics);
            });
        });
        if (standalone()) startHttp(); else startCockpit();
    }

    document.addEventListener("DOMContentLoaded", function () {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", "config.json");
        xhr.onload = function () {
            try {
                if (xhr.status === 200) {
                    var c = JSON.parse(xhr.responseText);
                    Object.keys(c).forEach(function (k) { cfg[k] = c[k]; });
                }
            } catch (e) { }
            start();
        };
        xhr.onerror = start;
        xhr.send();
    });
}());

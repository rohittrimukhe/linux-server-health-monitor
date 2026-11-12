/* Linux Server Health Monitor Enterprise - Cockpit page
 * Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
 * Read-only view of LSHME results. Files are watched, so the page updates
 * by itself when a scheduled run or the real-time watcher writes new data. */
(function () {
    "use strict";
    var cfg = { data_dir: "/var/lib/lshme", command: ["/usr/local/bin/healthcheck"] };

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s === undefined || s === null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "–"; }
    function sev(s) { return '<span class="sev ' + esc(s) + '">' + esc(s) + "</span>"; }

    function renderLatest(run) {
        if (!run) {
            $("sub").textContent = "No health check has run yet. Click 'Run health check now'.";
            return;
        }
        var h = run.health || {}, c = h.counts || {};
        $("sub").textContent = (run.fqdn || run.host) + " · last check " + when(run.collected_at) + " · " + (run.days || 1) + "-day window";
        $("score").textContent = h.score === undefined ? "–" : h.score;
        $("score").className = "score t-" + (h.status || "");
        $("status").innerHTML = sev(h.status || "UNKNOWN");
        $("counts").textContent = "critical " + (c.CRITICAL || 0) + " · warning " + (c.WARNING || 0) + " · unknown " + (c.UNKNOWN || 0) +
            ((run.excluded && run.excluded.length) ? " · excluded " + run.excluded.length : "");
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
        var mods = (h.modules || []).map(function (m) {
            var st = m.score === null ? "SKIPPED" : (m.worst === "CRITICAL" || m.worst === "WARNING" ? m.worst : "OK");
            var mod = (run.modules || []).filter(function (x) { return x.name === m.module; })[0] || {};
            return "<tr><td>" + esc(m.title) + "</td><td>" + (m.score === null ? "–" : esc(m.score)) + "</td><td>" + sev(st) +
                "</td><td>" + esc(mod.summary || "") + "</td></tr>";
        });
        $("modules").innerHTML = "<table><thead><tr><th>Module</th><th>Score</th><th>Status</th><th>Summary</th></tr></thead><tbody>" +
            mods.join("") + "</tbody></table>";
        var files = (run.meta && run.meta.report_files) || [];
        if (files.length) {
            $("reports").className = "";
            $("reports").innerHTML = files.map(function (p, i) {
                return '<button class="btn" type="button" data-i="' + i + '">' + esc(p.split("/").pop()) + "</button>";
            }).join(" ");
            Array.prototype.forEach.call($("reports").querySelectorAll("button"), function (b) {
                b.addEventListener("click", function () { openReport(files[+b.dataset.i]); });
            });
        } else {
            $("reports").textContent = "Reports from the latest run appear here.";
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

    function renderWatch(st) {
        if (!st || !st.last_tick) return;
        var probs = st.problems || {}, keys = Object.keys(probs);
        var html = '<div class="muted">Last check ' + esc(when(st.last_tick)) + " · " + esc(st.ticks || 0) + " checks · " +
            esc(st.last_tick_cpu_ms) + " ms CPU · " + esc(st.rss_mb) + " MB</div>";
        if (!keys.length) {
            html += "<p>No active problems.</p>";
        } else {
            html += "<table><thead><tr><th>Severity</th><th>Component</th><th>Problem</th><th>Since</th></tr></thead><tbody>" +
                keys.map(function (k) {
                    var p = probs[k];
                    return "<tr><td>" + sev(p.severity) + "</td><td>" + esc(p.component) + "</td><td>" + esc(p.message) +
                        "</td><td>" + esc(when(p.since)) + "</td></tr>";
                }).join("") + "</tbody></table>";
        }
        $("watch").className = "";
        $("watch").innerHTML = html;
    }

    function watchJson(name, render) {
        var f = cockpit.file(cfg.data_dir + "/" + name, { syntax: JSON, superuser: "try" });
        f.watch(function (content) { render(content); });
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
        $("run").addEventListener("click", runNow);
        watchJson("latest.json", renderLatest);
        watchJson("watch_state.json", renderWatch);
    }

    document.addEventListener("DOMContentLoaded", function () {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", "config.json");
        xhr.onload = function () {
            try { if (xhr.status === 200) { var c = JSON.parse(xhr.responseText); cfg.data_dir = c.data_dir || cfg.data_dir; cfg.command = c.command || cfg.command; } } catch (e) { }
            start();
        };
        xhr.onerror = start;
        xhr.send();
    });
}());

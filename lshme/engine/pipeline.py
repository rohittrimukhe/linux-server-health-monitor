"""Runs modules and post-processing (drift, trends, scoring, history)."""
import socket
import time
import traceback

from lshme import APP_NAME, VERSION
from lshme.core import logs
from lshme.core.model import ModuleResult, RunResult
from lshme.core.util import primary_ip
from lshme.engine import baseline, maintenance, scoring
from lshme.engine.history import History, analyse


def run_modules(ctx, modules, progress=None):
    run = RunResult(socket.gethostname(), ctx.days, ctx.schedule)
    run.fqdn = socket.getfqdn()
    run.ip = primary_ip()
    run.version = VERSION
    g = ctx.cfg.section("main", "general")
    run.meta = {"application": APP_NAME, "company": g.get("company", ""), "customer": g.get("customer", ""),
                "environment": g.get("environment", ""), "prepared_by": g.get("prepared_by", ""),
                "location": g.get("location", ""), "logo": g.get("logo", "")}
    log = logs.get()
    deadline = time.time() + ctx.cfg.getint("main", "general", "run_timeout", 1800)
    for i, mod in enumerate(modules):
        res = ModuleResult(mod.NAME, mod.TITLE, mod.WEIGHT)
        if time.time() > deadline:
            res.status = "SKIPPED"
            res.add("UNKNOWN", "RUN_TIMEOUT", mod.NAME, "Skipped: overall run time limit reached")
            run.modules.append(res)
            continue
        if progress:
            progress(i, len(modules), mod.TITLE)
        start = time.time()
        try:
            mod.run(ctx, res)
        except Exception as e:  # a broken module must never abort the whole health check
            res.status = "ERROR"
            res.error(traceback.format_exc(limit=3))
            res.add("UNKNOWN", "MODULE_ERROR", mod.NAME, "Module failed: %s" % e,
                    recommendation="Report this error; other modules were not affected.")
            log.error("module %s failed: %s", mod.NAME, traceback.format_exc())
        res.duration = time.time() - start
        if res.errors and res.status == "OK":
            res.status = "PARTIAL"
        log.info("module %s %s in %.1fs (%d findings)", mod.NAME, res.status, res.duration, len(res.findings))
        run.modules.append(res)
    if progress:
        progress(len(modules), len(modules), "done")
    return run


def execute(ctx, modules, paths=None, save_history=True, progress=None, drift=True, trends=True):
    run = run_modules(ctx, modules, progress)
    if paths and drift:
        try:
            d = baseline.drift_module(ctx, paths)
            if d.status != "SKIPPED" or d.findings:
                run.modules.append(d)
        except Exception as e:
            logs.get().error("drift check failed: %s", e)
    hist = None
    history_on = paths is not None and ctx.cfg.getbool("main", "history", "enabled", True)
    if history_on:
        try:
            hist = History(paths.history_db)
            if trends:
                t = analyse(hist, run, ctx.cfg)
                if t.status != "SKIPPED" or t.findings:
                    run.modules.append(t)
        except Exception as e:
            logs.get().error("history analysis failed: %s", e)
    if paths:
        run.maintenance = maintenance.active(paths)
    run.finished_at = time.time()
    scoring.score_run(run)
    sh = run.trends.get("score_history") if run.trends else None
    if sh:
        sh[-1] = (run.collected_at, run.health["score"])
    run.run_id = None
    if hist is not None:
        if save_history:
            run.run_id = hist.save(run)
        hist.close()
    return run

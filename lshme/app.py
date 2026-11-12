# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Application services shared by the CLI and the interactive menu."""
import json
import os

from lshme import VERSION, modules, reports
from lshme.core import logs
from lshme.core.config import Config, Paths, backup_config
from lshme.core.context import Context
from lshme.core.lock import RunLock
from lshme.core.runner import Runner
from lshme.delivery import mailer
from lshme.engine import pipeline
from lshme.engine.history import History
from lshme.ops import exporters, fleet, retention, scheduler
from lshme.reports import doc as docmod
from lshme.reports import html as htmlrep


class App(object):
    def __init__(self, app_dir, config_dir=None, home=None, verbose=False):
        self.paths = Paths(app_dir, config_dir, home)
        self.home = home
        self.cfg = Config(self.paths.config_dir)
        self.paths.apply_overrides(self.cfg)
        self.paths.ensure()
        self.log = logs.setup(self.paths.log_dir, verbose)
        self.runner = Runner(self.cfg.getint("main", "general", "command_timeout", 30))

    def reload(self):
        self.cfg = Config(self.paths.config_dir)

    def launcher(self):
        return os.path.join(self.paths.app_dir, "healthcheck")

    def global_args(self):
        return ["--home", self.home] if self.home else ["--config-dir", self.paths.config_dir]

    def audit(self, action, **kw):
        logs.audit(self.paths.log_dir, action, **kw)

    def save_config(self, name):
        backup_config(self.paths.config_dir, self.paths.config_backup_dir, "before-%s" % name,
                      keep=self.cfg.getint("retention", "retention", "config_backups_keep", 30))
        self.cfg.save(name)
        self.audit("config-change", file=name + ".conf")

    # -- health checks --------------------------------------------------------
    def check(self, module_names=None, days=1, schedule="adhoc", save_history=True, progress=None):
        ctx = Context(self.cfg, self.paths, self.runner, days, schedule)
        mods = modules.resolve(self.cfg, module_names)
        full = module_names in (None, "", "all", ["all"])
        # one-off partial checks would distort score history; scheduled subsets are kept
        if not full and schedule in ("adhoc", "nagios", "export"):
            save_history = False
        return pipeline.execute(ctx, mods, self.paths, save_history=save_history, progress=progress,
                                drift=full, trends=full or schedule not in ("adhoc", "nagios", "export"))

    def full_run(self, module_names=None, days=1, formats=None, report_type=None, email=None, schedule_name=None,
                 force_email=False, write_reports=True, progress=None, to=None, cc=None, policy=None):
        """Health check + reports + email + exports + retention, under the run lock."""
        sched = scheduler.get(self.cfg, schedule_name) if schedule_name else None
        if schedule_name and sched is None:
            raise ValueError("unknown schedule '%s'" % schedule_name)
        category = schedule_name or "adhoc"
        if sched:
            module_names = module_names or sched.modules
            days = sched.days
            formats = formats or sched.formats
            report_type = report_type or sched.report_type
            email = sched.email if email is None else email
            to = to or sched.to
            cc = cc or sched.cc
            policy = policy or sched.send_policy
        formats = reports.parse_formats(formats or self.cfg.get("reports", "reports", "formats", "html,pdf,json"))
        report_type = report_type or self.cfg.get("reports", "reports", "report_type", "technical")
        result = {"files": [], "notes": [], "email": None, "report_dir": ""}
        self.lower_priority()
        with RunLock(self.paths.lock_file, wait_seconds=300 if schedule_name else 0):
            self.log.info("run start schedule=%s modules=%s days=%s", category, module_names or "all", days)
            if write_reports and formats:
                for a in retention.space_check(self.cfg, self.paths):  # make room before writing reports
                    if a["action"] == "space-warning":
                        result["notes"].append(a["reason"])
                    elif a.get("result") == "ok":
                        result["notes"].append("space cleanup removed %s (%s)" % (a["path"], a["reason"]))
            run = self.check(module_names, days, category, progress=progress)
            free = retention.free_space_warning(self.cfg, self.paths)
            if free:
                result["notes"].append(free)
            if write_reports and formats:
                document, outdir, files, notes = reports.generate(run, self.cfg, self.paths, formats, report_type, category)
                result.update(files=files, report_dir=outdir)
                result["notes"] += notes
                for a in retention.space_check(self.cfg, self.paths):  # enforce the cap including the new reports
                    if a.get("result") == "ok":
                        result["notes"].append("space cleanup removed %s (%s)" % (a["path"], a["reason"]))
                    elif a["action"] == "space-warning":
                        result["notes"].append(a["reason"])
                if getattr(run, "run_id", None):
                    h = History(self.paths.history_db)
                    h.set_report_dir(run.run_id, outdir)
                    h.close()
            else:
                document = docmod.build(run, self.cfg, report_type)
            if email or force_email:
                result["email"] = self.email_report(run, document, result["files"], force_email, to, cc, policy)
            prom = self.cfg.get("main", "integrations", "prometheus_textfile", "")
            if prom:
                try:
                    exporters.write_prometheus(run, prom)
                except OSError as e:
                    result["notes"].append("Prometheus export failed: %s" % e)
            drop = self.cfg.get("main", "integrations", "agent_dropdir", "")
            if drop:
                try:
                    os.makedirs(drop, exist_ok=True)
                    with open(os.path.join(drop, "%s.json" % run.host), "w", encoding="utf-8") as f:
                        json.dump(run.to_dict(), f)
                except OSError as e:
                    result["notes"].append("Agent drop-dir export failed: %s" % e)
            if schedule_name and self.cfg.getbool("retention", "retention", "auto_apply", True):
                acts = [a for a in retention.plan(self.cfg, self.paths)]
                retention.apply(self.cfg, self.paths, acts)
            self.log.info("run end status=%s score=%s files=%d", run.health["status"], run.health["score"], len(result["files"]))
        return run, result

    def email_report(self, run, document, files, force=False, to=None, cc=None, policy=None):
        ms = mailer.MailSettings(self.cfg)
        send, why = mailer.should_send(policy or ms.policy, run.health["status"], bool(run.maintenance), force)
        if not send:
            self.log.info("email not sent: %s", why)
            return {"ok": None, "skipped": why}
        problems = ms.validate()
        if problems and not to:
            return {"ok": False, "error": "; ".join(problems)}
        status = run.health["status"]
        subject = "%s %s %s — %s (score %s)" % (ms.prefix, document.title, run.fqdn or run.host, status, run.health["score"])
        text = "%s\n%s\n\nStatus: %s   Score: %s/100\nCritical: %d  Warning: %d  Unknown: %d\n\n" % (
            document.title, document.subtitle, status, run.health["score"], document.counts.get("CRITICAL", 0),
            document.counts.get("WARNING", 0), document.counts.get("UNKNOWN", 0))
        text += "\n".join("[%s] %s: %s" % (f["severity"], f["component"], f["message"]) for f in document.findings
                          if f["severity"] in ("CRITICAL", "WARNING"))[:20000]
        msg, recipients, attached, skipped = mailer.build_message(ms, subject, text, htmlrep.email_body(document, files), files, status, to, cc)
        if skipped:
            self.log.warning("attachments skipped: %s", skipped)
        res = mailer.send(ms, msg, recipients, self.paths.log_dir)
        res["attached"], res["skipped_attachments"] = attached, skipped
        try:
            h = History(self.paths.history_db)
            h.record_delivery(run.host, getattr(run, "run_id", None), res["ok"], recipients, res["attempts"], res.get("error") or ",".join(attached))
            h.close()
        except Exception:
            pass
        if res["ok"]:
            self.log.info("email sent to %s", ", ".join(recipients))
        else:
            self.log.error("email failed after %s attempt(s): %s", res["attempts"], res["error"])
        return res

    # -- fleet ----------------------------------------------------------------
    def fleet_run(self, servers=None, formats=None, report_type="technical", email=False, days=1, dropdir=None, progress=None):
        if dropdir:
            runs, errors = fleet.read_dropdir(dropdir), {}
        else:
            local = lambda d: self.check(None, d, "fleet", save_history=False)
            runs, errors = fleet.collect(self.cfg, self.runner, days, servers, local, progress)
        h = History(self.paths.history_db)
        for r in runs:
            if r.host != os.uname()[1]:
                h.save(r)
        h.close()
        formats = reports.parse_formats(formats or self.cfg.get("reports", "reports", "formats", "html,pdf,json"))
        document, outdir, files, notes = reports.generate_fleet(runs, self.cfg, self.paths, formats, report_type, errors)
        mail = None
        if email:
            class _R(object):
                pass
            pseudo = _R()
            pseudo.health, pseudo.maintenance, pseudo.host, pseudo.fqdn, pseudo.run_id = document.health, None, "fleet", "fleet", None
            mail = self.email_report(pseudo, document, files)
        return runs, errors, files, notes, mail

    def lower_priority(self):
        """Run below normal priority so health checks never compete with production work."""
        from lshme.core.priority import set_priority
        set_priority(self.cfg.getint("main", "performance", "nice", 10), self.cfg.getbool("main", "performance", "idle_io", True))

    def version(self):
        return VERSION

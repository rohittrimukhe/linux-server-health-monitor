# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Report generation entry point."""
import os
import re
import time

from lshme.core import logs
from lshme.reports import doc as docmod
from lshme.reports import html, pdf, pptx, simple, xlsx

FORMATS = ("html", "pdf", "xlsx", "pptx", "txt", "csv", "json")


def parse_formats(value):
    fm = [x.strip().lower() for x in (value or "").split(",") if x.strip()]
    if "all" in fm:
        return list(FORMATS)
    bad = [x for x in fm if x not in FORMATS]
    if bad:
        raise ValueError("unknown report format(s): %s (choose from %s)" % (", ".join(bad), ", ".join(FORMATS)))
    return fm


def safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "host")


def output_dir(report_root, host, category, stamp=None):
    stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
    d = os.path.join(report_root, safe(host), safe(category), stamp)
    n = 1
    while os.path.exists(d):
        d = os.path.join(report_root, safe(host), safe(category), "%s_%d" % (stamp, n))
        n += 1
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o750)
    except OSError:
        pass
    return d


def write_all(document, formats, outdir, basename, run=None, runs=None, logo=""):
    """Render a ReportDoc into each format. Returns (files, notes)."""
    files, notes = [], []
    log = logs.get()
    for fmt in formats:
        path = os.path.join(outdir, "%s.%s" % (basename, fmt))
        try:
            if fmt == "html":
                files.append(html.render(document, path, logo))
            elif fmt == "txt":
                files.append(simple.render_txt(document, path))
            elif fmt == "json":
                files.append(simple.render_json(runs if runs is not None else run, path))
            elif fmt == "csv":
                files.extend(simple.render_csv(document, path, run))
            elif fmt == "pdf":
                p, engine = pdf.render(document, path, logo)
                files.append(p)
                if engine == "builtin":
                    notes.append("PDF written with the built-in renderer (install python3-reportlab for charts).")
            elif fmt == "xlsx":
                p, engine = xlsx.render(document, path, run)
                files.append(p)
                if engine == "builtin":
                    notes.append("XLSX written with the built-in writer (install python3-openpyxl for richer formatting).")
            elif fmt == "pptx":
                p, engine = pptx.render(document, path)
                files.append(p)
                if engine == "builtin":
                    notes.append("PPTX written as text slides (install python-pptx for charts and tables).")
        except Exception as e:  # one failing format must not lose the others
            log.exception("report format %s failed", fmt)
            notes.append("%s report failed: %s" % (fmt.upper(), e))
    for f in files:
        try:
            os.chmod(f, 0o640)
        except OSError:
            pass
    return files, notes


PERIODIC = ("daily", "weekly", "monthly")


def write_periodic(docs, formats, outdir, base, logo=""):
    """Render daily/weekly/monthly documents (monthly has a summary and a comprehensive one)."""
    from lshme.reports import periodic_html, periodic_out, periodic_pdf
    files, notes = [], []
    log = logs.get()
    for doc in docs:
        stem = os.path.join(outdir, base + doc.basename_suffix())
        for fmt in formats:
            try:
                if fmt == "html":
                    files.append(periodic_html.render(doc, stem + ".html", logo))
                elif fmt == "pdf":
                    files.append(periodic_pdf.render(doc, stem + ".pdf", logo))
                elif fmt == "xlsx":
                    files.append(periodic_out.render_xlsx(doc, stem + ".xlsx")[0])
                elif fmt == "csv":
                    files.extend(periodic_out.render_csv(doc, stem + ".csv"))
                elif fmt == "txt":
                    files.append(periodic_out.render_txt(doc, stem + ".txt"))
                elif fmt == "json":
                    files.append(periodic_out.render_json(doc, stem + ".json"))
                elif fmt == "pptx":
                    notes.append("PowerPoint is not produced for %s reports." % doc.ptype)
            except Exception as e:
                log.exception("periodic report format %s failed", fmt)
                notes.append("%s report failed: %s" % (fmt.upper(), e))
    for f in files:
        try:
            os.chmod(f, 0o640)
        except OSError:
            pass
    return files, notes


def build_document(run, cfg, paths, report_type):
    """The main document for a report type (the summary for monthly)."""
    if report_type in PERIODIC:
        from lshme.reports import periodic
        return periodic.build_all(run, cfg, paths, report_type)[0]
    return docmod.build(run, cfg, report_type)


def generate(run, cfg, paths, formats, report_type=None, category=None):
    report_type = report_type or cfg.get("reports", "reports", "report_type", "technical")
    category = category or run.schedule or "adhoc"
    if report_type in PERIODIC:
        from lshme.reports import periodic
        docs = periodic.build_all(run, cfg, paths, report_type)
        from lshme.ops import scheduler
        sched = scheduler.get(cfg, category)
        if sched is not None and sched.to_database:                      # the DBA team gets a database-only report
            docs.append(periodic.build(run, cfg, paths, report_type, "database"))
        outdir = output_dir(paths.report_dir, run.host, category)
        base = "%s_%s_%s" % (safe(run.host), safe(category), time.strftime("%Y%m%d_%H%M", time.localtime(run.collected_at)))
        files, notes = write_periodic(docs, formats, outdir, base, cfg.get("main", "general", "logo", ""))
        return docs[0], outdir, files, notes
    document = docmod.build(run, cfg, report_type)
    outdir = output_dir(paths.report_dir, run.host, category)
    base = "%s_%s_%s" % (safe(run.host), safe(category), time.strftime("%Y%m%d_%H%M", time.localtime(run.collected_at)))
    logo = cfg.get("main", "general", "logo", "")
    files, notes = write_all(document, formats, outdir, base, run=run, logo=logo)
    return document, outdir, files, notes


def generate_fleet(runs, cfg, paths, formats, report_type="technical", errors=None):
    document = docmod.build_fleet(runs, cfg, report_type, errors)
    outdir = output_dir(paths.report_dir, "_fleet", "fleet")
    base = "fleet_%s" % time.strftime("%Y%m%d_%H%M")
    files, notes = write_all(document, formats, outdir, base, runs=runs, logo=cfg.get("main", "general", "logo", ""))
    return document, outdir, files, notes

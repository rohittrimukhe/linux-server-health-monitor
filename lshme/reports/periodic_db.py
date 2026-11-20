# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Inside the databases: every database of an instance, the largest tables, Oracle tablespaces - size and growth."""
from lshme.core.util import fmt_bytes
from lshme.reports import periodic as P
from lshme.reports.periodic import Cell, table

KINDS = (("oracle.", "Oracle", None), ("postgresql.", "PostgreSQL", None), ("mysql.", "MySQL/MariaDB", None), ("mongodb.", "MongoDB", None), ("sqlserver.", "SQL Server", None))


def split_key(key, marker):
    """'postgresql.main.db_bytes:app' -> ('main', 'app') for the marker 'db_bytes'."""
    head, _, tail = key.partition(":")
    parts = head.split(".")
    if len(parts) < 3 or parts[-1] != marker:
        return None
    return ".".join(parts[1:-1]), tail


def collect(data, marker):
    rows = []
    for prefix, label, _ in KINDS:
        for k in data.keys(prefix):
            sp = split_key(k, marker)
            if not sp:
                continue
            it = P.growth_item(data, k)
            if it and it["now"] > 0:
                rows.append({"engine": label, "instance": sp[0], "name": sp[1], "item": it})
    return sorted(rows, key=lambda r: -r["item"]["now"])


def growth_row(r, monthly):
    it = r["item"]
    return [r["engine"], r["instance"], Cell(r["name"], "b"), P.fmt_size(it["now"]), P.fmt_delta(it["d1"], it["now"]), P.fmt_delta(it["d7"], it["now"])] + \
        ([P.fmt_delta(it["d30"], it["now"])] if monthly else []) + \
        [Cell("%.1f %%" % it["pct_week"], "wn" if it["pct_week"] >= 5 else "ok") if it["pct_week"] is not None else Cell("-", "fl")]


def blocks(data, limit=15):
    out = []
    monthly = data.days > 7
    heads = ["Engine", "Instance", "Name", "Size", "Growth 24 h", "Growth 7 d"] + (["Growth 30 d"] if monthly else []) + ["% per week"]
    align = ["", "", "", "r", "r", "r"] + (["r"] if monthly else []) + ["r"]
    for marker, title, legend in (("db_bytes", "Every database: size and growth", "One row per database or schema inside each instance, largest first."),
                                  ("table_bytes", "Largest tables: size and growth", "The biggest tables (with their indexes) and how fast each grows."),
                                  ("ts_used_bytes", "Oracle tablespaces: used space and growth", "Space in use per tablespace, from the Oracle data dictionary.")):
        rows = collect(data, marker)
        if marker == "ts_used_bytes":
            rows = [r for r in rows if r["engine"] == "Oracle"]
        if marker == "table_bytes":
            rows = [r for r in rows if r["engine"] != "Oracle"]
        if rows:
            out.append(table(title, heads, [growth_row(r, monthly) for r in rows[:limit]], align=align, legend=legend))
    return out

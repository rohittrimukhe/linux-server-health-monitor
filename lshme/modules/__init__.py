"""Module registry.

Each module defines NAME, TITLE, WEIGHT (share of the health score) and
run(ctx, res) which fills a ModuleResult. Modules are strictly read-only.
"""
import importlib

MODULE_ORDER = (
    "system", "cpu_memory", "storage", "services", "network", "hardware", "kvm", "cockpit",
    "database", "backup", "logs", "security", "certificates", "containers", "cluster",
    "applications", "custom",
)


def load(name):
    return importlib.import_module("lshme.modules." + name)


def all_modules():
    return [load(n) for n in MODULE_ORDER]


def resolve(cfg, requested=None):
    """Return module objects to run, honouring config enable/disable lists."""
    if requested and requested not in ("all", ["all"]):
        names = requested if isinstance(requested, list) else [x.strip() for x in requested.split(",")]
    else:
        enabled = cfg.getlist("main", "modules", "enabled", "all")
        names = list(MODULE_ORDER) if (not enabled or enabled == ["all"]) else enabled
        disabled = set(cfg.getlist("main", "modules", "disabled", ""))
        names = [n for n in names if n not in disabled]
    unknown = [n for n in names if n not in MODULE_ORDER]
    if unknown:
        raise ValueError("unknown module(s): %s (available: %s)" % (", ".join(unknown), ", ".join(MODULE_ORDER)))
    return [load(n) for n in MODULE_ORDER if n in names]

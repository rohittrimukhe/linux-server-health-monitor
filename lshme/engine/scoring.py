"""Health scoring.

Each module starts at 100 and loses 30 per CRITICAL, 10 per WARNING and 3 per
UNKNOWN finding (floor 0). The overall score is the weight-averaged module
score. A critical finding caps the overall score at 69 and a warning at 89, so
a single failed critical check can never hide behind a high average.
"""
from lshme.core.model import SEV_RANK

PENALTY = {"CRITICAL": 30, "WARNING": 10, "UNKNOWN": 3, "INFO": 0}


def module_score(m):
    if m.status == "SKIPPED" and not m.findings:
        return None
    s = 100
    for f in m.findings:
        s -= PENALTY[f.severity]
    return max(0, s)


def status_of(findings):
    worst = max([SEV_RANK[f.severity] for f in findings] or [0])
    return {3: "CRITICAL", 2: "WARNING"}.get(worst, "HEALTHY")


def score_run(run):
    total_w, acc = 0.0, 0.0
    module_scores = []
    for m in run.modules:
        m.score = module_score(m)
        module_scores.append({"module": m.name, "title": m.title, "score": m.score, "status": m.status,
                              "worst": m.worst(), "critical": sum(1 for f in m.findings if f.severity == "CRITICAL"),
                              "warning": sum(1 for f in m.findings if f.severity == "WARNING")})
        if m.score is not None:
            total_w += m.weight
            acc += m.weight * m.score
    score = round(acc / total_w) if total_w else 100
    findings = run.findings()
    status = status_of(findings)
    if status == "CRITICAL":
        score = min(score, 69)
    elif status == "WARNING":
        score = min(score, 89)
    unknown = sum(1 for f in findings if f.severity == "UNKNOWN")
    run.health = {"score": int(score), "status": status, "modules": module_scores, "counts": run.counts(),
                  "unknown_checks": unknown,
                  "method": "Weighted module scores (100 - 30/critical - 10/warning - 3/unknown); "
                            "overall capped at 69 with any critical and 89 with any warning finding."}
    return run.health

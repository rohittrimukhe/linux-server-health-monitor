# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Security posture: SELinux, firewall, SSH policy, privileged accounts, audit,
pending (security) updates and last patch date."""
import re

NAME = "security"
TITLE = "Security & Patching"
WEIGHT = 8


def selinux(ctx):
    if ctx.has("getenforce"):
        r = ctx.run(["getenforce"])
        if r.ok:
            return r.out.strip().lower()
    v = ctx.read("/sys/fs/selinux/enforce", "")
    if v:
        return "enforcing" if v.strip() == "1" else "permissive"
    if ctx.has("aa-status"):
        return "apparmor"
    return "disabled"


def sshd_effective(ctx):
    if ctx.has("sshd") and ctx.runner.is_root():
        r = ctx.run(["sshd", "-T"], timeout=20)
        if r.ok:
            d = {}
            for line in r.lines():
                p = line.split(None, 1)
                if len(p) == 2:
                    d[p[0].lower()] = p[1].strip()
            return d, "sshd -T"
    d = {}
    for line in ctx.read("/etc/ssh/sshd_config").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            p = line.split(None, 1)
            if len(p) == 2 and p[0].lower() not in d:
                d[p[0].lower()] = p[1].strip()
    return d, "sshd_config"


def pending_updates(ctx):
    timeout = ctx.cfg.getint("main", "security", "updates_timeout", 120)
    cached = ctx.cache_get("updates", ctx.cfg.getint("main", "security", "updates_cache_hours", 12) * 3600)
    if cached:
        cached["cached"] = True
        return cached
    out = {"tool": "", "total": None, "security": None, "packages": [], "error": "", "cached": False}
    if ctx.has("dnf") or ctx.has("yum"):
        tool = "dnf" if ctx.has("dnf") else "yum"
        out["tool"] = tool
        r = ctx.run([tool, "-q", "-C", "check-update"], timeout=timeout)
        if r.rc == 1 or r.timed_out or r.missing:
            out["error"] = r.brief_error()
        elif r.rc in (0, 100):
            pk = [l.split()[0] for l in r.lines() if len(l.split()) == 3 and "." in l.split()[0] and not l.startswith(("Obsoleting", "Security"))]
            out["total"] = len(pk)
            out["packages"] = pk[:200]
            s = ctx.run([tool, "-q", "-C", "updateinfo", "list", "security"] if tool == "dnf" else
                        [tool, "-q", "-C", "updateinfo", "list", "security"], timeout=timeout)
            if s.ok:
                out["security"] = len(set(l.split()[-1] for l in s.lines() if len(l.split()) >= 3))
        if out["total"] is None and not out["error"]:
            out["error"] = "package metadata cache unavailable (run '%s makecache')" % tool
    elif ctx.has("apt-get"):
        out["tool"] = "apt"
        r = ctx.run(["apt-get", "-s", "-o", "Debug::NoLocking=true", "upgrade"], timeout=timeout)
        if r.ok:
            insts = [l for l in r.lines() if l.startswith("Inst ")]
            out["total"] = len(insts)
            out["security"] = sum(1 for l in insts if "security" in l.lower())
            out["packages"] = [l.split()[1] for l in insts][:200]
        else:
            out["error"] = r.brief_error()
    elif ctx.has("zypper"):
        out["tool"] = "zypper"
        r = ctx.run(["zypper", "-q", "--no-refresh", "lu"], timeout=timeout)
        out["total"] = sum(1 for l in r.lines() if l.startswith("v "))
    if out["total"] is not None:
        ctx.cache_put("updates", out)
    return out


def last_patch(ctx):
    if ctx.has("rpm"):
        r = ctx.run(["rpm", "-qa", "--last"], timeout=60)
        first = (r.lines() or [""])[0]
        m = re.search(r"\s{2,}(.+)$", first)
        return m.group(1).strip() if m else ""
    log = ctx.read("/var/log/dpkg.log")
    lines = [l for l in log.splitlines() if " upgrade " in l]
    return lines[-1][:19] if lines else ""


def run(ctx, res):
    pol = lambda k, d="": ctx.cfg.get("main", "security", k, d)
    se = selinux(ctx)
    expected_se = pol("expected_selinux", "enforcing").lower()
    fw = ctx.shared.get("firewall")
    if fw is None:
        from lshme.modules.network import firewall
        fw = firewall(ctx)
    sshd, ssh_src = sshd_effective(ctx)
    uid0 = [l.split(":")[0] for l in ctx.read("/etc/passwd").splitlines() if l.count(":") >= 6 and l.split(":")[2] == "0"]
    shadow = ctx.read("/etc/shadow") if ctx.runner.is_root() else ""
    empty_pw = [l.split(":")[0] for l in shadow.splitlines() if l.count(":") >= 8 and l.split(":")[1] == ""]
    auditd = None
    if ctx.has("systemctl"):
        auditd = ctx.run(["systemctl", "is-active", "auditd"]).out.strip() or None
    upd = pending_updates(ctx) if ctx.cfg.getbool("main", "security", "check_updates", True) else None
    patched = last_patch(ctx)
    nopasswd = []
    for f in ["/etc/sudoers"] + ctx.runner.glob("/etc/sudoers.d/*"):
        for line in ctx.read(f).splitlines():
            if "NOPASSWD" in line and not line.strip().startswith("#") and "ALL" in line:
                nopasswd.append("%s: %s" % (f, line.strip()))

    res.data = {"selinux": se, "firewall": fw, "sshd": sshd, "sshd_source": ssh_src, "uid0_accounts": uid0,
                "empty_passwords": empty_pw, "auditd": auditd, "updates": upd, "last_patch": patched,
                "sudo_nopasswd": nopasswd}
    rows = [["SELinux / MAC", se], ["Firewall", "%s (%s)" % (fw["tool"], "active" if fw["active"] else "inactive")],
            ["SSH PermitRootLogin", sshd.get("permitrootlogin", "default")],
            ["SSH PasswordAuthentication", sshd.get("passwordauthentication", "default")],
            ["UID 0 accounts", ", ".join(uid0)], ["auditd", auditd or "unknown"],
            ["Last package change", patched or "unknown"]]
    if upd:
        rows.append(["Pending updates (security)", "%s (%s)" % (upd["total"] if upd["total"] is not None else "unknown",
                                                             upd["security"] if upd["security"] is not None else "?")])
    res.table("Security posture", ["Control", "State"], rows, kind="summary")

    if expected_se in ("enforcing", "permissive") and se not in (expected_se, "apparmor"):
        sev = "WARNING" if se == "permissive" else "CRITICAL" if expected_se == "enforcing" and se == "disabled" else "WARNING"
        res.add(sev, "SELINUX_MODE", "selinux", "SELinux is %s (policy expects %s)" % (se, expected_se),
                recommendation="Set SELINUX=%s in /etc/selinux/config; relabel if it was disabled." % expected_se)
    if ctx.cfg.getbool("main", "security", "require_firewall", True) and not fw["active"]:
        res.add("WARNING", "FIREWALL_INACTIVE", "firewall", "No active host firewall (%s)" % fw["tool"],
                recommendation="Enable firewalld/nftables with a minimal rule set.")
    prl = sshd.get("permitrootlogin", "").lower()
    if prl == "yes" and not ctx.cfg.getbool("main", "security", "permit_root_login_allowed", False):
        res.add("WARNING", "SSH_ROOT_LOGIN", "sshd", "SSH allows direct root login with password (PermitRootLogin yes)",
                recommendation="Set PermitRootLogin prohibit-password or no and use named accounts with sudo.")
    if sshd.get("passwordauthentication", "").lower() == "yes" and not ctx.cfg.getbool("main", "security", "password_authentication_allowed", True):
        res.add("WARNING", "SSH_PASSWORD_AUTH", "sshd", "SSH password authentication is enabled",
                recommendation="Prefer key-based authentication.")
    if sshd.get("permitemptypasswords", "").lower() == "yes":
        res.add("CRITICAL", "SSH_EMPTY_PASSWORDS", "sshd", "SSH permits empty passwords")
    if sshd.get("protocol", "2").strip() == "1":
        res.add("CRITICAL", "SSH_PROTOCOL1", "sshd", "SSH protocol 1 is enabled")
    for u in uid0:
        if u != "root":
            res.add("CRITICAL", "EXTRA_UID0", u, "Account %s has UID 0 (root privileges)" % u,
                    recommendation="Remove or re-number the account; only root should have UID 0.")
    for u in empty_pw:
        res.add("CRITICAL", "EMPTY_PASSWORD", u, "Account %s has an empty password" % u,
                recommendation="Lock the account (passwd -l) or set a password.")
    if ctx.cfg.getbool("main", "security", "require_auditd", False) and auditd != "active":
        res.add("WARNING", "AUDITD_INACTIVE", "auditd", "auditd is %s" % (auditd or "not installed"))
    for line in nopasswd[:5]:
        res.add("INFO", "SUDO_NOPASSWD", "sudoers", "Passwordless sudo rule: %s" % line)
    if upd:
        if upd["total"] is None:
            res.add("UNKNOWN", "UPDATES_UNKNOWN", upd["tool"] or "packages", "Pending updates could not be determined",
                    evidence=upd["error"])
        else:
            res.metric("security.pending_updates", upd["total"])
            if upd["security"]:
                res.metric("security.pending_security_updates", upd["security"])
                res.add("WARNING", "SECURITY_UPDATES", upd["tool"], "%d security update(s) pending (%d total)" % (upd["security"], upd["total"]),
                        evidence=", ".join(upd["packages"][:40]),
                        recommendation="Apply security errata in the next patch window.")
            elif upd["total"] > 50:
                res.add("INFO", "UPDATES_PENDING", upd["tool"], "%d package update(s) pending" % upd["total"])
        if upd["packages"]:
            res.table("Pending package updates", ["Package"], [[p] for p in upd["packages"][:200]])
    res.summary = "SELinux %s, firewall %s, %s pending updates" % (
        se, "active" if fw["active"] else "inactive", upd["total"] if upd and upd["total"] is not None else "?")

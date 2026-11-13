# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Email delivery over SMTP (plain, STARTTLS or implicit TLS) or local sendmail.

Features: To/Cc/Bcc, extra recipients for critical reports, send policy
(always / issues / critical / never), attachment format filter and total size
limit, retries with back-off, and a JSONL delivery log.
"""
import email.utils
import json
import mimetypes
import os
import smtplib
import socket
import ssl
import subprocess
import time
from email.message import EmailMessage

from lshme.core.config import as_list

MIME = {"pdf": "application/pdf", "html": "text/html", "json": "application/json", "csv": "text/csv", "txt": "text/plain",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


class MailSettings(object):
    def __init__(self, cfg):
        s = cfg.section("smtp", "smtp")
        self.method = s.get("method", "smtp").lower()
        self.host = s.get("host", "")
        self.port = int(s.get("port") or 587)
        self.security = s.get("security", "starttls").lower()
        self.username = s.get("username", "")
        self.password = cfg.secret("smtp", "password")
        self.sender = s.get("from", "")
        self.to = as_list(s.get("to"))
        self.cc = as_list(s.get("cc"))
        self.bcc = as_list(s.get("bcc"))
        self.critical_to = as_list(s.get("critical_to"))
        self.prefix = s.get("subject_prefix", "[LSHME]")
        self.policy = s.get("send_policy", "always").lower()
        self.max_mb = float(s.get("max_attachment_mb") or 15)
        self.attach_formats = as_list(s.get("attach_formats", "pdf,xlsx,html"))
        self.retries = int(s.get("retries") or 3)
        self.retry_delay = float(s.get("retry_delay") or 20)
        self.timeout = float(s.get("timeout") or 30)
        self.sendmail_path = s.get("sendmail_path", "/usr/sbin/sendmail")

    def validate(self):
        problems = []
        if self.method == "smtp" and not self.host:
            problems.append("SMTP host is not configured")
        if not self.sender:
            problems.append("sender (from) is not configured")
        if not (self.to or self.critical_to):
            problems.append("no recipients (to) configured")
        if self.security not in ("none", "starttls", "ssl"):
            problems.append("security must be none, starttls or ssl")
        if self.username and not self.password:
            problems.append("username set but no password in credentials.conf [smtp] or LSHME_SMTP_PASSWORD")
        return problems


def should_send(policy, status, maintenance=False, force=False):
    if force:
        return True, "forced"
    if maintenance:
        return False, "maintenance window active"
    if policy == "never":
        return False, "send_policy=never"
    if policy == "critical" and status != "CRITICAL":
        return False, "status %s below send_policy=critical" % status
    if policy == "issues" and status not in ("CRITICAL", "WARNING"):
        return False, "no issues (send_policy=issues)"
    return True, "policy %s" % policy


def build_message(ms, subject, text, html_body, attachments, status="HEALTHY", to_override=None, cc_override=None):
    to = list(to_override) if to_override else list(ms.to)
    if status == "CRITICAL":
        for r in ms.critical_to:
            if r not in to:
                to.append(r)
    cc = list(cc_override) if cc_override is not None else list(ms.cc)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = ms.sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=(ms.sender.split("@")[-1] if "@" in ms.sender else socket.getfqdn()))
    msg["X-Mailer"] = "LSHME"
    if status == "CRITICAL":
        msg["X-Priority"] = "1"
        msg["Importance"] = "High"
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    attached, skipped = [], []
    budget = ms.max_mb * 1024 * 1024
    used = 0
    for path in attachments:
        ext = path.rsplit(".", 1)[-1].lower()
        if ms.attach_formats and ext not in ms.attach_formats and "all" not in ms.attach_formats:
            continue
        size = os.path.getsize(path)
        if used + size > budget:
            skipped.append("%s (%.1f MB exceeds the %.0f MB limit)" % (os.path.basename(path), size / 1048576.0, ms.max_mb))
            continue
        ctype = MIME.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(path))
        used += size
        attached.append(os.path.basename(path))
    recipients = to + cc + list(ms.bcc)
    return msg, recipients, attached, skipped


def _send_once(ms, msg, recipients):
    if ms.method == "sendmail":
        p = subprocess.Popen([ms.sendmail_path, "-t", "-oi"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate(msg.as_bytes(), timeout=ms.timeout * 2)
        if p.returncode != 0:
            raise RuntimeError("sendmail exit %s: %s" % (p.returncode, err.decode("utf-8", "replace")[:300]))
        return
    context = ssl.create_default_context()
    if ms.security == "ssl":
        server = smtplib.SMTP_SSL(ms.host, ms.port, timeout=ms.timeout, context=context)
    else:
        server = smtplib.SMTP(ms.host, ms.port, timeout=ms.timeout)
    try:
        server.ehlo()
        if ms.security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        if ms.username:
            server.login(ms.username, ms.password)
        refused = server.send_message(msg, to_addrs=recipients)
        if refused:
            raise RuntimeError("recipients refused: %s" % ", ".join(refused))
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send(ms, msg, recipients, log_dir=None, sleep=time.sleep):
    """Send with retries. Returns dict(ok, attempts, error)."""
    attempts, last = 0, ""
    for attempt in range(1, max(1, ms.retries) + 1):
        attempts = attempt
        try:
            _send_once(ms, msg, recipients)
            last = ""
            break
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
            last = "%s: %s" % (type(e).__name__, e)
            break  # permanent errors: retrying will not help
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            if attempt < ms.retries:
                sleep(ms.retry_delay * attempt)
    result = {"ok": not last, "attempts": attempts, "error": last, "recipients": recipients,
              "subject": str(msg["Subject"]), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "email.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(result) + "\n")
        except OSError:
            pass
    return result


def send_test(ms, log_dir=None):
    problems = ms.validate()
    if problems:
        return {"ok": False, "attempts": 0, "error": "; ".join(problems), "recipients": []}
    text = ("This is a test message from Linux Server Health Monitor Enterprise on %s.\n"
            "If you received it, SMTP delivery is configured correctly." % socket.getfqdn())
    msg, recipients, _, _ = build_message(ms, "%s SMTP test from %s" % (ms.prefix, socket.gethostname()), text, None, [])
    return send(ms, msg, recipients, log_dir)

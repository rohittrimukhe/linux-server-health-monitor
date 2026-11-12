"""Small parsing and formatting helpers used across modules."""
import calendar
import datetime
import re
import socket
import time

PSEUDO_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2",
             "devpts", "mqueue", "debugfs", "tracefs", "securityfs", "pstore", "bpf", "autofs",
             "configfs", "fusectl", "hugetlbfs", "rpc_pipefs", "binfmt_misc", "nsfs", "efivarfs",
             "selinuxfs", "fuse.gvfsd-fuse", "fuse.portal", "ramfs", "iso9660", "udf", "nfsd",
             "fuse.lxcfs", "shm"}
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "glusterfs", "fuse.glusterfs", "ceph",
              "fuse.sshfs", "lustre", "gpfs", "ocfs2", "gfs2"}

SECRET_KEY_RE = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|credential)", re.I)
SECRET_VALUE_RES = [
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(identified\s+by\s+)\S+"),
    re.compile(r"\b([A-Za-z0-9_$#]+)/([^\s/@]+)@([A-Za-z0-9_.:/-]+)"),  # user/pass@tns
    re.compile(r"(?i)(mongodb(\+srv)?://[^:/\s]+:)[^@\s]+(@)"),
]


def fmt_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "N/A"
    sign = "-" if n < 0 else ""
    n = abs(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024:
            return "%s%.1f %s" % (sign, n, unit)
        n /= 1024.0
    return "%s%.1f EB" % (sign, n)


def fmt_duration(seconds):
    try:
        seconds = int(float(seconds))
    except (TypeError, ValueError):
        return "N/A"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return "%dd %dh %dm" % (d, h, m)
    if h:
        return "%dh %dm" % (h, m)
    return "%dm" % m


def pct(part, whole):
    try:
        whole = float(whole)
        return round(100.0 * float(part) / whole, 2) if whole else None
    except (TypeError, ValueError):
        return None


def to_float(value, default=None):
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError, AttributeError):
        return default


def to_int(value, default=None):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return default


def parse_kv_lines(text, sep="="):
    out = {}
    for line in text.splitlines():
        if sep in line:
            k, v = line.split(sep, 1)
            out[k.strip()] = v.strip()
    return out


def parse_pairs(line):
    """Parse lsblk -P style KEY="value" pairs."""
    return dict(re.findall(r'(\w[\w:-]*)="([^"]*)"', line))


def severity_for(value, warning, critical, higher_is_worse=True):
    if value is None:
        return None
    if higher_is_worse:
        if value >= critical:
            return "CRITICAL"
        if value >= warning:
            return "WARNING"
    else:
        if value <= critical:
            return "CRITICAL"
        if value <= warning:
            return "WARNING"
    return None


def redact(text):
    if not text:
        return text
    s = str(text)
    s = SECRET_VALUE_RES[0].sub(lambda m: m.group(1) + "=******", s)
    s = SECRET_VALUE_RES[1].sub(lambda m: m.group(1) + "******", s)
    s = SECRET_VALUE_RES[2].sub(lambda m: m.group(1) + "/******@" + m.group(3), s)
    s = SECRET_VALUE_RES[3].sub(lambda m: m.group(1) + "******" + m.group(3), s)
    return s


def redact_obj(obj):
    """Recursively mask secrets in data destined for reports."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_KEY_RE.search(str(k)) and isinstance(v, (str, int, float)) and str(v):
                out[k] = "******"
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


def primary_ip():
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET; no packet is sent for UDP connect
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""
    finally:
        if s:
            s.close()


# --- listening sockets without ss/netstat -----------------------------------
def _hex_port(addr):
    try:
        return int(addr.rsplit(":", 1)[1], 16)
    except Exception:
        return None


def listening_ports_from_proc(runner):
    """Return {(proto, port)} for listening TCP sockets and bound UDP sockets."""
    ports = set()
    for proto, path, state in (("tcp", "/proc/net/tcp", "0A"), ("tcp", "/proc/net/tcp6", "0A"),
                               ("udp", "/proc/net/udp", "07"), ("udp", "/proc/net/udp6", "07")):
        for line in runner.read(path).splitlines()[1:]:
            parts = line.split()
            if len(parts) > 3 and parts[3] == state:
                p = _hex_port(parts[1])
                if p:
                    ports.add((proto, p))
    return ports


def listening_ports(runner):
    """Listening ports with owning process where ss is available."""
    out = []
    r = runner.run(["ss", "-H", "-lntup"]) if runner.has("ss") else None
    if r is not None and r.ok:
        for line in r.lines():
            parts = line.split()
            if len(parts) < 5:
                continue
            proto = parts[0]
            local = parts[4]
            port = to_int(local.rsplit(":", 1)[-1])
            proc = ""
            m = re.search(r'users:\(\("([^"]+)"', line)
            if m:
                proc = m.group(1)
            if port:
                out.append({"proto": proto, "address": local, "port": port, "process": proc})
        return out
    if r is not None and not r.ok and "-H" in r.err:
        # very old ss without -H
        r = runner.run(["ss", "-lntup"])
        if r.ok:
            for line in r.lines()[1:]:
                parts = line.split()
                if len(parts) >= 5:
                    port = to_int(parts[4].rsplit(":", 1)[-1])
                    m = re.search(r'users:\(\("([^"]+)"', line)
                    if port:
                        out.append({"proto": parts[0], "address": parts[4], "port": port,
                                    "process": m.group(1) if m else ""})
            return out
    for proto, port in sorted(listening_ports_from_proc(runner)):
        out.append({"proto": proto, "address": "*:%d" % port, "port": port, "process": ""})
    return out


# --- log timestamps --------------------------------------------------------
_SYSLOG_RE = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")
_ORA_RE = re.compile(r"^[A-Z][a-z]{2}\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\s+(\d{4})")
_MONTHS = dict((m, i) for i, m in enumerate(calendar.month_abbr) if m)


def parse_log_time(line, now=None):
    """Best-effort timestamp of a log line (syslog, ISO-8601 or Oracle 11g alert format)."""
    now = now or time.time()
    m = _ISO_RE.match(line)
    if m:
        try:
            return time.mktime(datetime.datetime(*[int(x) for x in m.groups()]).timetuple())
        except ValueError:
            return None
    m = _ORA_RE.match(line)
    if m:
        mon, day, hh, mm, ss, year = m.groups()
        try:
            return time.mktime(datetime.datetime(int(year), _MONTHS[mon], int(day), int(hh), int(mm), int(ss)).timetuple())
        except (ValueError, KeyError):
            return None
    m = _SYSLOG_RE.match(line)
    if m:
        mon, day, hh, mm, ss = m.groups()
        year = datetime.datetime.fromtimestamp(now).year
        try:
            ts = time.mktime(datetime.datetime(year, _MONTHS[mon], int(day), int(hh), int(mm), int(ss)).timetuple())
        except (ValueError, KeyError):
            return None
        if ts > now + 86400:  # December lines read in January
            ts = time.mktime(datetime.datetime(year - 1, _MONTHS[mon], int(day), int(hh), int(mm), int(ss)).timetuple())
        return ts
    return None


def parse_openssl_date(text):
    """Parse 'notAfter=Jan  1 00:00:00 2030 GMT' style dates to epoch (UTC)."""
    text = text.split("=", 1)[-1].strip()
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b  %d %H:%M:%S %Y %Z"):
        try:
            return calendar.timegm(time.strptime(text, fmt))
        except ValueError:
            continue
    m = re.match(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\s+(\d{4})", text)
    if m:
        mon, day, hh, mm, ss, year = m.groups()
        return calendar.timegm((int(year), _MONTHS[mon], int(day), int(hh), int(mm), int(ss), 0, 0, 0))
    return None


def linear_forecast(points, limit):
    """Least-squares fit of (t, value) points; seconds until value reaches limit.

    Returns None when the trend is flat/decreasing or there are too few points.
    """
    if len(points) < 2:
        return None
    n = float(len(points))
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope = (n * sxy - sx * sy) / denom
    if slope <= 0:
        return None
    intercept = (sy - slope * sx) / n
    t_limit = (limit - intercept) / slope
    last_t = max(p[0] for p in points)
    return max(0.0, t_limit - last_t)

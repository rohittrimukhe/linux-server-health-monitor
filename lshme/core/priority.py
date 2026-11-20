# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Process priority: keep LSHME out of the way of production workloads."""
import os
import platform

# ioprio_set syscall numbers per architecture
_IOPRIO_SET = {"x86_64": 251, "aarch64": 30, "ppc64le": 273, "s390x": 282}
_IOPRIO_CLASS_IDLE = 3


def set_priority(nice=10, idle_io=True):
    """Raise our nice value to `nice` (never lowers it) and, optionally, use the idle I/O class."""
    try:
        cur = os.nice(0)
        if nice > cur:
            os.nice(nice - cur)
    except OSError:
        pass
    if idle_io:
        nr = _IOPRIO_SET.get(platform.machine())
        if nr:
            try:
                import ctypes
                ctypes.CDLL(None, use_errno=True).syscall(nr, 1, 0, _IOPRIO_CLASS_IDLE << 13)
            except Exception:
                pass

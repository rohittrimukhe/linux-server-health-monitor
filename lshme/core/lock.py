"""Exclusive run lock so scheduled and manual runs never overlap."""
import fcntl
import os
import time


class LockBusy(Exception):
    pass


class RunLock(object):
    def __init__(self, path, wait_seconds=0):
        self.path = path
        self.wait_seconds = wait_seconds
        self.fh = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "a+", encoding="utf-8")
        deadline = time.time() + self.wait_seconds
        while True:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (IOError, OSError):
                if time.time() >= deadline:
                    self.fh.seek(0)
                    holder = self.fh.read().strip()
                    self.fh.close()
                    self.fh = None
                    raise LockBusy("another LSHME run is in progress (%s)" % (holder or "unknown pid"))
                time.sleep(2)
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write("pid=%d started=%s\n" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
        self.fh.flush()
        return self

    def __exit__(self, *exc):
        if self.fh:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            finally:
                self.fh.close()
                self.fh = None
        return False

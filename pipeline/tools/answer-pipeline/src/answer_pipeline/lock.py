"""跨进程文件锁。

定时巡检与手动执行可能同时触发 scan/render，没有锁会重复入队、
同时写同一个 output 文件。这里用系统级文件锁（POSIX flock / Windows
msvcrt.locking），进程崩溃时锁由内核释放，不会留下死锁文件。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


class LockTimeout(RuntimeError):
    pass


class FileLock:
    def __init__(self, path: Path, timeout: float = 30.0, poll: float = 0.1):
        self.path = Path(path)
        self.timeout = timeout
        self.poll = poll
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._lock(fh)
                self._fh = fh
                return
            except OSError:
                if time.monotonic() >= deadline:
                    fh.close()
                    raise LockTimeout(
                        f"等待锁超时（{self.timeout}s）：{self.path}，"
                        "可能有另一个进程正在处理队列"
                    )
                time.sleep(self.poll)

    @staticmethod
    def _lock(fh) -> None:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(fh) -> None:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def release(self) -> None:
        if self._fh is not None:
            try:
                self._unlock(self._fh)
            finally:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

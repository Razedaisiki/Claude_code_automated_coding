import json
import os
import sys
import time
from pathlib import Path
from typing import Optional


class WorkspaceLockError(RuntimeError):
    pass


class WorkspaceLock:
    def __init__(self, root: Path = None, operation: str = ""):
        self.root = Path(root or Path.cwd()).resolve()
        self.operation = operation or ""
        self.lock_path = self.root / ".agent" / "workflow.lock"
        self._fd = None
        self._locked = False

    def acquire(self, timeout: float = 0):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.lock_path.exists():
            try:
                self.lock_path.write_text(json.dumps({"pid": os.getpid(), "operation": self.operation}) + "\n", encoding="utf-8")
            except Exception:
                pass
        try:
            self._fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        except Exception as e:
            raise WorkspaceLockError(f"Cannot open lock file: {e}") from e
        if sys.platform.startswith("win"):
            return self._acquire_windows()
        else:
            return self._acquire_posix(timeout)

    def _acquire_posix(self, timeout: float):
        import fcntl
        start = time.time()
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._locked = True
                try:
                    os.ftruncate(self._fd, 0)
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    meta = {"pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "operation": self.operation}
                    os.write(self._fd, (json.dumps(meta) + "\n").encode("utf-8"))
                    try:
                        os.fsync(self._fd)
                    except Exception:
                        pass
                except Exception:
                    pass
                return
            except BlockingIOError:
                pass
            except OSError as e:
                if e.errno in (11, 35):
                    pass
                else:
                    raise WorkspaceLockError(f"Cannot acquire lock: {e}") from e
            if timeout and (time.time() - start) >= timeout:
                raise self._conflict_error()
            if timeout == 0:
                raise self._conflict_error()
            time.sleep(0.05)

    def _acquire_windows(self):
        import msvcrt
        try:
            msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            self._locked = True
            try:
                os.ftruncate(self._fd, 0)
                os.lseek(self._fd, 0, os.SEEK_SET)
                meta = {"pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "operation": self.operation}
                os.write(self._fd, (json.dumps(meta) + "\n").encode("utf-8"))
                try:
                    os.fsync(self._fd)
                except Exception:
                    pass
            except Exception:
                pass
            return
        except OSError:
            raise self._conflict_error()

    def _conflict_error(self):
        meta = ""
        try:
            raw = self.lock_path.read_text(encoding="utf-8").strip()
            if raw:
                try:
                    j = json.loads(raw.splitlines()[0])
                    meta = f" (holder pid={j.get('pid')} started={j.get('started')} operation={j.get('operation')})"
                except Exception:
                    meta = f" ({raw[:120]})"
        except Exception:
            pass
        return WorkspaceLockError(f"Another workflow is already running in {self.root}{meta}. Use `workflow resume` or wait for it to finish.")

    def release(self):
        if self._fd is not None:
            try:
                if sys.platform.startswith("win"):
                    import msvcrt
                    try:
                        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                    except Exception:
                        pass
            finally:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None
                self._locked = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

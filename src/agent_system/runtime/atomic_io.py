import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Union


def fsync_parent_dir(path: Path):
    parent = Path(path).parent
    try:
        fd = os.open(str(parent), os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


def atomic_write_bytes(target: Path, data: bytes):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(str(tmp), str(target))
        fsync_parent_dir(target)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def atomic_write_text(target: Path, text: str, encoding: str = "utf-8"):
    atomic_write_bytes(Path(target), text.encode(encoding))


def atomic_write_json(target: Path, obj, indent: int = 2):
    data = json.dumps(obj, indent=indent, ensure_ascii=False) + "\n"
    atomic_write_bytes(Path(target), data.encode("utf-8"))

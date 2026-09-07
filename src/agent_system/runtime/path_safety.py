from pathlib import Path
from typing import Tuple


def _root_resolved(root: Path) -> Path:
    return Path(root).resolve()


def is_safe_relative_path(path: str) -> bool:
    if not path or not path.strip():
        return False
    p = Path(path)
    if p.is_absolute():
        return False
    for part in p.parts:
        if part == "..":
            return False
    return True


def resolve_workspace_path(root: Path, rel: str) -> Path:
    root_r = _root_resolved(root)
    candidate = (root_r / rel)
    return candidate


def check_containment(root: Path, candidate: Path) -> Tuple[bool, str]:
    root_r = _root_resolved(root)
    try:
        resolved = candidate.resolve()
    except Exception:
        return False, "cannot resolve path"
    if resolved == root_r:
        return True, ""
    try:
        resolved.relative_to(root_r)
        return True, ""
    except ValueError:
        return False, f"path escapes workspace: {candidate}"

    # unreachable


def is_symlink_escape(root: Path, rel: str) -> Tuple[bool, str]:
    root_r = _root_resolved(root)
    parts = Path(rel).parts
    cur = root_r
    for part in parts:
        if part in (".", ""):
            continue
        cur = cur / part
        try:
            if cur.is_symlink():
                target = cur.resolve()
                try:
                    target.relative_to(root_r)
                except ValueError:
                    return True, f"symlink escapes workspace: {rel} -> {cur.readlink() if hasattr(cur, 'readlink') else target}"
                return False, ""
        except Exception:
            pass
        # check parent symlink: cur may not exist but parent symlink chain
        parent = cur.parent
        if parent != root_r and parent.exists() and parent.is_symlink():
            try:
                resolved_parent = parent.resolve()
                resolved_parent.relative_to(root_r)
            except ValueError:
                return True, f"parent symlink escapes workspace: {rel}"
    # also check final resolved containment
    final = (root_r / rel).resolve()
    try:
        final.relative_to(root_r)
    except ValueError:
        return True, f"path escapes workspace: {rel}"
    if (root_r / rel).is_symlink():
        # symlink file itself - caller should not dereference
        return False, "symlink"
    return False, ""


def safe_read_text(root: Path, rel: str, max_bytes: int = 0) -> Tuple[str, str]:
    if not is_safe_relative_path(rel):
        return "", f"unsafe path: {rel}"
    root_r = _root_resolved(root)
    p = root_r / rel
    # Check every prefix for symlink
    parts = Path(rel).parts
    cur = root_r
    for i, part in enumerate(parts):
        cur = cur / part
        # lstat check without following
        try:
            if cur.is_symlink():
                try:
                    target_str = str(cur.readlink())
                except Exception:
                    target_str = "(symlink)"
                return "", f"symlink not dereferenced: {rel} -> {target_str}"
        except Exception:
            pass
        # also check if parent is symlink when cur doesn't exist yet
        if i < len(parts) - 1:
            try:
                if cur.is_symlink():
                    return "", f"symlink parent: {rel}"
            except Exception:
                pass
    # Resolve without following final symlink? We already rejected symlinks
    try:
        resolved = p.resolve()
        try:
            resolved.relative_to(root_r)
        except ValueError:
            return "", f"path escapes workspace: {rel}"
    except Exception as e:
        return "", str(e)
    if not p.exists():
        return "", "not found"
    if p.is_symlink():
        try:
            return "", f"symlink: {rel} -> {p.readlink()}"
        except Exception:
            return "", f"symlink: {rel}"
    if not p.is_file():
        return "", "not a file"
    try:
        text = p.read_text(encoding="utf-8")
        return text, ""
    except Exception as e:
        return "", str(e)

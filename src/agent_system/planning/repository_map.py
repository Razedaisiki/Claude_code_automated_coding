from __future__ import annotations

from pathlib import Path
from typing import List


def _limited_read(path: Path, max_chars: int = 800) -> str:
    try:
        text = path.read_text(encoding="utf-8")
        if len(text) > max_chars:
            return text[:max_chars] + "\n...(truncated)"
        return text
    except Exception:
        return ""


def build_repository_map(root: Path, *, max_files: int = 500, max_chars: int = 12000) -> str:
    root = Path(root or Path.cwd()).resolve()
    lines: List[str] = []
    # Git head/branch
    try:
        from agent_system.runtime.shell import Shell
        shell = Shell(root)
        branch = shell.run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        head = shell.run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
        if branch or head:
            lines.append(f"branch: {branch or '(unknown)'}")
            lines.append(f"head: {head or '(unknown)'}")
            lines.append("")
    except Exception:
        pass

    # Tree (tracked files + limited fs walk)
    tracked: List[str] = []
    try:
        from agent_system.runtime.shell import Shell
        shell = Shell(root)
        out = shell.run(["git", "ls-files"]).stdout
        tracked = [l.strip() for l in out.splitlines() if l.strip()]
        # filter out .agent / runtime
        tracked = [p for p in tracked if not p.startswith(".agent/") and "__pycache__" not in p]
    except Exception:
        tracked = []

    if not tracked:
        # fallback: walk filesystem limited
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            if rel.startswith(".agent/") or rel.startswith(".git/") or "__pycache__" in rel or rel.endswith(".pyc"):
                continue
            tracked.append(rel)
            if len(tracked) >= max_files:
                break

    tracked = tracked[:max_files]
    if tracked:
        lines.append("Tree:")
        # Build tree view limited depth
        for p in sorted(tracked)[:max_files]:
            lines.append(f"  {p}")
        lines.append("")

    # Build/test signals
    signals: List[str] = []
    candidates = {
        "pyproject.toml": "Python project via pyproject.toml",
        "package.json": "Node project via package.json",
        "Cargo.toml": "Rust project via Cargo.toml",
        "go.mod": "Go project via go.mod",
        "CMakeLists.txt": "CMake project via CMakeLists.txt",
        "Makefile": "Makefile present",
        "build.sh": "build.sh present",
    }
    for name, desc in candidates.items():
        if (root / name).exists():
            signals.append(f"- {desc}")
    # Requirements files
    for pat in ["requirements*.txt"]:
        for f in root.glob(pat):
            signals.append(f"- {f.name} present")
    # README
    readme = None
    for name in ["README.md", "README", "CLAUDE.md"]:
        if (root / name).exists():
            readme = name
            break
    if readme:
        signals.append(f"- {readme} present")
    # Tests detection
    has_tests = any("tests/" in p or p.startswith("tests/") or "/tests/" in p or p.endswith("_test.py") or p.startswith("test_") for p in tracked)
    if has_tests:
        # List test files compactly
        test_files = [p for p in tracked if "test" in p.lower()][:20]
        if test_files:
            signals.append(f"- tests detected: {', '.join(test_files[:10])}")
        else:
            signals.append("- tests detected")
    if signals:
        lines.append("Build/test signals:")
        lines.extend(signals)
        lines.append("")

    # Limited manifest summaries
    manifests = ["pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile"]
    for name in manifests:
        p = root / name
        if p.exists():
            excerpt = _limited_read(p, 600)
            if excerpt.strip():
                lines.append(f"{name} excerpt:")
                lines.append(excerpt.strip())
                lines.append("")

    text = "\n".join(lines).strip()
    if not text:
        text = "(repository map unavailable)"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    return text

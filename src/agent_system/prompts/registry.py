from pathlib import Path
from typing import Optional

PROMPT_ROOT = Path(__file__).parent

_PLANNING = PROMPT_ROOT / "planning"
_AGENTS_PARENT = PROMPT_ROOT / "agents" / "parent"
_AGENTS_CODING = PROMPT_ROOT / "agents" / "coding"
_AGENTS_REVIEW = PROMPT_ROOT / "agents" / "review"
_RUNTIME = PROMPT_ROOT / "runtime"
_COMMON = PROMPT_ROOT / "common"
_VALIDATION = PROMPT_ROOT / "agents" / "validation"


def _read_first(*paths: Path) -> Optional[str]:
    for p in paths:
        if p and p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                continue
    return None


def _path_first(*paths: Path) -> Optional[Path]:
    for p in paths:
        if p and p.exists():
            return p
    return None


class PromptRegistry:
    @staticmethod
    def planning_prompt(name: str) -> str:
        rel = name if name.endswith(".md") else f"{name}.md"
        return _read_first(_PLANNING / rel) or ""

    @staticmethod
    def planning_prompt_path(name: str) -> Optional[Path]:
        rel = name if name.endswith(".md") else f"{name}.md"
        return _path_first(_PLANNING / rel)

    @staticmethod
    def parent_system() -> str:
        return _read_first(_AGENTS_PARENT / "system.md") or ""

    @staticmethod
    def coding_prompt(name: str) -> str:
        rel = name if name.endswith(".md") else f"{name}.md"
        return _read_first(_AGENTS_CODING / rel) or ""

    @staticmethod
    def coding_prompts_combined() -> str:
        parts = []
        for n in ["code.md", "system.md", "execution.md"]:
            text = PromptRegistry.coding_prompt(n)
            if text:
                parts.append(text)
        common = _read_first(_COMMON / "engineering_rules.md")
        if common and parts:
            parts.append(common)
        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def review_system() -> str:
        return _read_first(_AGENTS_REVIEW / "system.md") or ""

    @staticmethod
    def runtime_prompt(name: str) -> str:
        rel = name if name.endswith(".md") else f"{name}.md"
        return _read_first(_RUNTIME / rel) or ""

    @staticmethod
    def validation_system() -> str:
        return _read_first(_VALIDATION / "system.md") or ""

    @staticmethod
    def common_rules() -> str:
        return _read_first(_COMMON / "engineering_rules.md") or ""

import json
import os
from pathlib import Path
from typing import Optional


def _load_claude_settings() -> dict:
    p = Path.home() / ".claude" / "settings.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("env") or {}
    except Exception:
        return {}


def resolve_claude_code_model() -> Optional[str]:
    for k in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL"):
        v = os.environ.get(k)
        if v:
            return v
    env = _load_claude_settings()
    for k in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL"):
        v = env.get(k)
        if v:
            return v
    return None


def resolve_claude_code_api_key() -> Optional[str]:
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v
    env = _load_claude_settings()
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        v = env.get(k)
        if v:
            return v
    return None


def resolve_claude_code_base_url() -> Optional[str]:
    v = os.environ.get("ANTHROPIC_BASE_URL")
    if v:
        return v
    env = _load_claude_settings()
    v = env.get("ANTHROPIC_BASE_URL")
    if v:
        return v
    return None

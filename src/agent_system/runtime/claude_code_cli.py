import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from agent_system.agents.models import ExecutionEvidence, ToolEvent


MIN_CLAUDE_CODE_VERSION = (2, 1, 248)


@dataclass
class ClaudeCodeRunResult:
    returncode: int
    result_text: str
    evidence: ExecutionEvidence
    execution_status: str
    stop_reason: Optional[str] = None
    session_id: Optional[str] = None


def _parse_version(s: str):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", s)
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def _version_gte(v, req):
    if v is None:
        return False
    return v >= req


class ClaudeCodeCLI:
    def __init__(self, root: Path, model: Optional[str] = None):
        self.root = Path(root).resolve() if root else Path.cwd().resolve()
        self.model = model

    def _find_claude(self) -> str:
        p = shutil.which("claude")
        if not p:
            raise RuntimeError(f"Claude Code not found: `claude` not in PATH. Install Claude Code >= {'.'.join(str(x) for x in MIN_CLAUDE_CODE_VERSION)}.")
        return p

    def _check_version(self, claude_bin: str):
        try:
            r = subprocess.run([claude_bin, "--version"], capture_output=True, text=True, timeout=10)
        except Exception as e:
            raise RuntimeError(f"Claude Code version check failed: {e}. Required >= {'.'.join(str(x) for x in MIN_CLAUDE_CODE_VERSION)}.") from e
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            raise RuntimeError(f"Claude Code --version failed (exit {r.returncode}): {out or '(no output)'}. Required >= {'.'.join(str(x) for x in MIN_CLAUDE_CODE_VERSION)}.")
        v = _parse_version(out)
        if v is None:
            raise RuntimeError(f"Claude Code version unparseable: {out!r}. Required >= {'.'.join(str(x) for x in MIN_CLAUDE_CODE_VERSION)}.")
        if not _version_gte(v, MIN_CLAUDE_CODE_VERSION):
            raise RuntimeError(f"Claude Code >= {'.'.join(str(x) for x in MIN_CLAUDE_CODE_VERSION)} required, found {out}.")

    def _check_sandbox_deps(self):
        if not sys.platform.startswith("linux"):
            return
        missing = []
        if not shutil.which("bwrap"):
            missing.append("bubblewrap (bwrap)")
        if not shutil.which("socat"):
            missing.append("socat")
        if missing:
            raise RuntimeError(f"Claude Code sandbox dependencies missing: {', '.join(missing)}. Install bubblewrap and socat.")

    def _prepare_settings(self) -> Path:
        agent_dir = self.root / ".agent" / "claude-code"
        agent_dir.mkdir(parents=True, exist_ok=True)
        settings_path = agent_dir / "settings.json"
        code_system_path = agent_dir / "code-system.md"
        if not code_system_path.exists():
            code_system_path.write_text(
                "# Runtime Authority\n\n"
                "You may inspect Git state using read-only commands when necessary.\n"
                "You do not own repository delivery.\n"
                "Do not stage, commit, push, switch branches, reset Git state, modify refs, or modify Git metadata.\n"
                "The Runtime owns Git index, commits, branches, pushes, and CI orchestration.\n"
                "Finish by leaving project changes in the working tree for Runtime review.\n",
                encoding="utf-8",
            )
        hook_script = (Path(__file__).with_name("git_policy_hook.py")).resolve()
        settings = {
            "permissions": {
                "defaultMode": "dontAsk",
                "disableBypassPermissionsMode": "disable",
                "disableAutoMode": "disable",
                "allow": ["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                "deny": [
                    "Read(.agent/**)",
                    "Edit(.agent/**)",
                    "Read(.git/**)",
                    "Edit(.git/**)",
                    "Bash(git add *)",
                    "Bash(git commit *)",
                    "Bash(git push *)",
                    "Bash(git pull *)",
                    "Bash(git fetch *)",
                    "Bash(git reset *)",
                    "Bash(git checkout *)",
                    "Bash(git switch *)",
                    "Bash(git merge *)",
                    "Bash(git rebase *)",
                    "Bash(git cherry-pick *)",
                    "Bash(git stash *)",
                    "Bash(git tag *)",
                    "Bash(git clean *)",
                    "Bash(git restore *)",
                    "Bash(git rm *)",
                    "Bash(git update-index *)",
                    "Bash(git update-ref *)",
                    "Bash(gh *)",
                ],
            },
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": sys.executable,
                                "args": [str(hook_script)],
                                "timeout": 5,
                            }
                        ],
                    }
                ],
            },
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {
                    "denyRead": [str(self.root / ".agent")],
                    "denyWrite": [str(self.root / ".agent"), str(self.root / ".git")],
                },
                "credentials": {
                    "files": [
                        {"path": "~/.ssh", "mode": "deny"},
                        {"path": "~/.config/gh/hosts.yml", "mode": "deny"},
                        {"path": "~/.git-credentials", "mode": "deny"},
                    ],
                    "envVars": [
                        {"name": "GITHUB_TOKEN", "mode": "deny"},
                        {"name": "GH_TOKEN", "mode": "deny"},
                        {"name": "SSH_AUTH_SOCK", "mode": "deny"},
                        {"name": "ANTHROPIC_API_KEY", "mode": "deny"},
                        {"name": "ANTHROPIC_AUTH_TOKEN", "mode": "deny"},
                    ],
                },
            },
        }
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return settings_path

    def _build_command(self, claude_bin: str, prompt: str, settings_path: Path) -> List[str]:
        agent_dir = self.root / ".agent" / "claude-code"
        code_system = agent_dir / "code-system.md"
        cmd = [
            claude_bin,
            "--restricted",
            "--bare",
            "-p", prompt,
            "--permission-mode", "dontAsk",
            "--tools", "Read,Edit,Write,Glob,Grep,Bash",
            "--disallowedTools", "mcp__*",
            "--settings", str(settings_path),
            "--append-system-prompt-file", str(code_system),
            "--output-format", "stream-json",
            "--verbose",
            "--no-session-persistence",
        ]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def _build_env(self) -> dict:
        from agent_system.config import resolve_api_key, resolve_base_url
        env = os.environ.copy()
        api_key = resolve_api_key()
        if api_key and not env.get("ANTHROPIC_API_KEY") and not env.get("ANTHROPIC_AUTH_TOKEN"):
            env["ANTHROPIC_API_KEY"] = api_key
        base_url = resolve_base_url()
        if base_url and not env.get("ANTHROPIC_BASE_URL"):
            env["ANTHROPIC_BASE_URL"] = base_url
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "Never"
        env["GIT_OPTIONAL_LOCKS"] = "0"
        for k in ("GITHUB_TOKEN", "GH_TOKEN", "SSH_AUTH_SOCK"):
            env.pop(k, None)
        return env

    def run(self, prompt: str, timeout: int = 1800) -> ClaudeCodeRunResult:
        claude_bin = self._find_claude()
        self._check_version(claude_bin)
        self._check_sandbox_deps()
        settings_path = self._prepare_settings()
        cmd = self._build_command(claude_bin, prompt, settings_path)
        env = self._build_env()

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start Claude Code: {e}") from e

        EXIT_CODE_PATTERNS = [re.compile(r"(?im)^Exit code\s+(-?\d+)\s*$"), re.compile(r"(?im)\bexit_code\s*:\s*(-?\d+)\b")]

        def _parse_bash_exit_code(text: str):
            for pat in EXIT_CODE_PATTERNS:
                m = pat.search(text or "")
                if m:
                    try:
                        return int(m.group(1))
                    except Exception:
                        continue
            return None

        def _extract_result_exit_code(payload) -> Optional[int]:
            if not isinstance(payload, dict):
                return None
            for k in ("exit_code", "exitCode", "returncode", "return_code"):
                if k in payload:
                    try:
                        return int(payload[k])
                    except Exception:
                        continue
            return None

        def _normalize_output(content) -> str:
            if isinstance(content, list):
                parts = []
                for x in content:
                    if isinstance(x, dict):
                        parts.append(str(x.get("text", "") or x.get("output", "") or ""))
                    else:
                        parts.append(str(x))
                return "\n".join(parts)
            return str(content or "")

        pending: dict = {}
        order: List[str] = []
        tid_to_index: dict = {}
        completed_by_tid: dict = {}
        debug = os.getenv("XXX_DEBUG_AGENT_TURNS") == "1"
        events: List[ToolEvent] = []
        result_text = ""
        returncode = 0
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            returncode = proc.returncode if proc.returncode is not None else 0
            for line in (stdout or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type", "")
                if etype == "assistant":
                    msg = ev.get("message") or {}
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tid = str(block.get("id", "") or "")
                                name = block.get("name", "") or ""
                                inp = block.get("input", {}) if isinstance(block.get("input"), dict) else {}
                                if not tid:
                                    tid = f"__orphan_{len(order)}"
                                pending[tid] = {"tool": name, "input": inp}
                                tid_to_index[tid] = len(order)
                                order.append(tid)
                elif etype == "user":
                    msg = ev.get("message") or {}
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tid = str(block.get("tool_use_id", "") or block.get("toolUseId", "") or "")
                                raw_content = block.get("content", "")
                                out = _normalize_output(raw_content)[:8000]
                                structured_ec = _extract_result_exit_code(block)
                                if structured_ec is None and isinstance(raw_content, dict):
                                    structured_ec = _extract_result_exit_code(raw_content)
                                if tid and tid in pending:
                                    pend = pending.pop(tid)
                                    tool = pend["tool"]
                                    inp = pend["input"]
                                    if tool == "Bash":
                                        ec = structured_ec if structured_ec is not None else _parse_bash_exit_code(out)
                                    else:
                                        ec = None
                                    completed_by_tid[tid] = ToolEvent(tool=tool, input=inp, output=out[:4000], exit_code=ec)
                                else:
                                    if debug:
                                        print(f"[evidence warning] unmatched tool_result {tid!r}")
                                    continue
                elif etype == "result":
                    result_text = str(ev.get("result", "") or ev.get("output", "") or "")[:4000]
                    if not result_text:
                        result_text = str(ev)[:2000]
                elif etype == "system" and ev.get("subtype") == "api_retry":
                    continue
            for tid in order:
                if tid in pending:
                    pend = pending[tid]
                    completed_by_tid[tid] = ToolEvent(tool=pend["tool"], input=pend["input"], output="[tool result unavailable]", exit_code=None)
            events = [completed_by_tid[tid] for tid in order if tid in completed_by_tid]
            if debug:
                for ev in events:
                    cmd = ev.input.get("command", "") if isinstance(ev.input, dict) else ""
                    print(f"[CodeAgent tool] {ev.tool}: {cmd[:120]} exit_code={ev.exit_code} output={ev.output[:300].replace(chr(10), ' | ')}")
            if not result_text:
                result_text = (stdout or "")[-4000:] or (stderr or "")[-2000:] or "done"
                if len(result_text) > 4000:
                    result_text = result_text[:4000]
            if returncode != 0 and not result_text.strip():
                result_text = (stderr or stdout or "")[:2000] or f"Claude Code exited {returncode}"
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
            return ClaudeCodeRunResult(
                returncode=124,
                result_text="CodeAgent timed out",
                evidence=ExecutionEvidence(events=events),
                execution_status="ERROR",
                stop_reason="process_timeout",
            )
        except Exception as e:
            return ClaudeCodeRunResult(
                returncode=1,
                result_text=f"Claude Code runtime error: {e}",
                evidence=ExecutionEvidence(events=events),
                execution_status="ERROR",
                stop_reason="runtime_exception",
            )

        if returncode != 0:
            return ClaudeCodeRunResult(
                returncode=returncode,
                result_text=result_text[:2000],
                evidence=ExecutionEvidence(events=events),
                execution_status="ERROR",
                stop_reason="process_error",
            )
        return ClaudeCodeRunResult(
            returncode=0,
            result_text=result_text[:4000] if result_text else "done",
            evidence=ExecutionEvidence(events=events),
            execution_status="COMPLETED",
            stop_reason="completed",
        )

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from agent_system.agents.models import ExecutionEvidence, ToolEvent


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
            raise RuntimeError("Claude Code not found: `claude` not in PATH. Install Claude Code >= 2.1.179.")
        return p

    def _check_version(self, claude_bin: str):
        try:
            r = subprocess.run([claude_bin, "--version"], capture_output=True, text=True, timeout=10)
            out = (r.stdout + r.stderr).strip()
            v = _parse_version(out)
            if v and not _version_gte(v, (2, 1, 179)):
                raise RuntimeError(f"Claude Code >= 2.1.179 required, found {out}")
        except RuntimeError:
            raise
        except Exception:
            pass

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
        settings = {
            "permissions": {
                "defaultMode": "dontAsk",
                "allow": ["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                "deny": [
                    f"Read({self.root / '.agent'} >> {self.root / '.agent'}/**)",
                    f"Edit({self.root / '.agent'} >> {self.root / '.agent'}/**)",
                    f"Read({self.root / '.git'} >> {self.root / '.git'}/**)",
                    f"Edit({self.root / '.git'} >> {self.root / '.git'}/**)",
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
        }
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return settings_path

    def _build_command(self, claude_bin: str, prompt: str, settings_path: Path) -> List[str]:
        agent_dir = self.root / ".agent" / "claude-code"
        code_system = agent_dir / "code-system.md"
        try:
            r = subprocess.run([claude_bin, "--version"], capture_output=True, text=True, timeout=5)
            v = _parse_version((r.stdout + r.stderr).strip())
        except Exception:
            v = None
        has_restricted = v is not None and _version_gte(v, (2, 1, 248))
        cmd = [claude_bin]
        if has_restricted:
            cmd.append("--restricted")
        cmd += [
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
                                name = block.get("name", "")
                                inp = block.get("input", {})
                                events.append(ToolEvent(tool=name, input=inp if isinstance(inp, dict) else {}, output="", exit_code=0))
                elif etype == "user":
                    msg = ev.get("message") or {}
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                out = block.get("content", "")
                                if isinstance(out, list):
                                    out = "\n".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in out)
                                out = str(out)[:2000]
                                if events:
                                    last = events[-1]
                                    events[-1] = ToolEvent(tool=last.tool, input=last.input, output=out, exit_code=0)
                elif etype == "result":
                    result_text = str(ev.get("result", "") or ev.get("output", "") or "")[:4000]
                    if not result_text:
                        result_text = str(ev)[:2000]
                elif etype == "system" and ev.get("subtype") == "api_retry":
                    continue
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

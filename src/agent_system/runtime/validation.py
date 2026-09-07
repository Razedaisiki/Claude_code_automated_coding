import os
import re
import shlex
import shutil
from pathlib import Path

from agent_system.contracts.validation import ValidationCommandResult, ValidationResult


def _normalize_argv(cmd: str):
    try:
        return shlex.split(cmd)
    except Exception:
        return [cmd]


def _canonical_argv(cmd: str):
    try:
        argv = shlex.split(cmd)
    except Exception:
        return [cmd]
    if not argv:
        return argv
    if argv[0] == "python" and shutil.which("python") is None and shutil.which("python3") is not None:
        argv = ["python3"] + argv[1:]
    return argv


def _argv_match(expected: str, executed: str) -> bool:
    return _canonical_argv(expected) == _canonical_argv(executed)


def _effective_cmd(cmd: str) -> str:
    argv = _canonical_argv(cmd)
    try:
        return shlex.join(argv)
    except Exception:
        return " ".join(argv)


def _infer_exit_code_from_output(output: str, ec):
    if ec is not None:
        return ec
    low = (output or "").lower()
    if re.search(r"\d+\s+passed", low) and "failed" not in low:
        return 0
    if "passed" in low and "failed" not in low and "error" not in low:
        return 0
    return ec


class MockValidationRunner:
    def __init__(self, root=None):
        self.root = Path(root or Path.cwd()).resolve()

    def validate(self, task, reviewed_tree_sha: str) -> ValidationResult:
        cmds = list(getattr(task, "validation", None) or [])
        results = []
        for c in cmds:
            results.append(ValidationCommandResult(command=c, exit_code=0, status="PASSED", output="mock validation passed"))
        status = "PASSED" if all(r.status == "PASSED" for r in results) else "FAILED"
        if not cmds:
            status = "PASSED"
        return ValidationResult(tree_sha=reviewed_tree_sha or "", status=status, commands=results)


class ClaudeCodeValidationRunner:
    def __init__(self, root=None, model=None):
        self.root = Path(root or Path.cwd()).resolve()
        self.model = model

    def validate(self, task, reviewed_tree_sha: str) -> ValidationResult:
        cmds = list(getattr(task, "validation", None) or [])
        if not cmds:
            return ValidationResult(tree_sha=reviewed_tree_sha or "", status="PASSED", commands=[])
        effective_cmds = [_effective_cmd(c) for c in cmds]
        from agent_system.backends.claude_code.cli import ClaudeCodeCLI
        cmd_list = "\n".join(f"- {c}" for c in effective_cmds)
        prompt = (
            "You are in validation mode. Execute ONLY the following validation commands. "
            "Do not edit files, do not run other commands.\n"
            f"Commands:\n{cmd_list}\n"
            "Run each command exactly as listed using Bash and report results."
        )
        try:
            cli = ClaudeCodeCLI(self.root, model=self.model)
            result = cli.run(prompt, timeout=300)
            events = getattr(getattr(result, "evidence", None), "events", None) or []
            out_results = []
            all_pass = True
            for expected, effective in zip(cmds, effective_cmds):
                found = None
                for ev in events:
                    if getattr(ev, "tool", "") != "Bash":
                        continue
                    inp = getattr(ev, "input", {}) or {}
                    ce = str(inp.get("command") or inp.get("cmd") or "")
                    if _argv_match(expected, ce) or _argv_match(effective, ce):
                        found = ev
                        break
                if found is None:
                    all_pass = False
                    out_results.append(ValidationCommandResult(command=expected, exit_code=1, status="FAILED", output=f"command not executed: {expected}"))
                else:
                    ec = getattr(found, "exit_code", None)
                    ec = _infer_exit_code_from_output(getattr(found, "output", "") or "", ec)
                    if ec is not None and ec != 0:
                        all_pass = False
                        out_results.append(ValidationCommandResult(command=expected, exit_code=ec if ec is not None else 1, status="FAILED", output=getattr(found, "output", "") or ""))
                    else:
                        raw = str(getattr(found, "input", {}).get("command") or "")
                        if "||" in raw or ";" in raw or "| " in raw:
                            if not _argv_match(expected, raw) and not _argv_match(effective, raw):
                                all_pass = False
                                out_results.append(ValidationCommandResult(command=expected, exit_code=1, status="FAILED", output=f"command wrapper detected: {raw}"))
                                continue
                        out_results.append(ValidationCommandResult(command=expected, exit_code=0, status="PASSED", output=getattr(found, "output", "") or ""))
            status = "PASSED" if all_pass else "FAILED"
            return ValidationResult(tree_sha=reviewed_tree_sha or "", status=status, commands=out_results)
        except Exception as e:
            results = [ValidationCommandResult(command=c, exit_code=1, status="FAILED", output=str(e)) for c in cmds]
            return ValidationResult(tree_sha=reviewed_tree_sha or "", status="FAILED", commands=results)


def get_validation_runner(root=None, model=None):
    if os.getenv("WORKFLOW_MOCK") == "1":
        return MockValidationRunner(root)
    return ClaudeCodeValidationRunner(root, model=model)

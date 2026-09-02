from abc import ABC, abstractmethod
import os
from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.context import ProjectContext
from agent_system.runtime.git import Git


def _is_mock_mode() -> bool:
    return os.getenv("XXX_MOCK") == "1"


class CodingRuntime(ABC):
    @abstractmethod
    def execute(self, task: AgentTask, context: ProjectContext) -> AgentResult:
        pass


class ClaudeCodeRuntime(CodingRuntime):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model
        self.git = Git(self.root)

    def _capture_baseline(self, task: AgentTask):
        from agent_system.agents.models import FileSnapshot, TaskBaseline

        files = {}
        for rel in (task.files or []):
            p = self.root / rel
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8")
                    files[rel] = FileSnapshot(exists=True, content=content[:3000])
                except Exception:
                    files[rel] = FileSnapshot(exists=True)
            else:
                files[rel] = FileSnapshot(exists=False)
        r = self.git.shell.run("git rev-parse HEAD")
        sha = r.stdout.strip() if r.returncode == 0 else ""
        return TaskBaseline(commit_sha=sha, files=files)

    def _build_prompt(self, task: AgentTask, baseline, context: ProjectContext) -> str:
        base = Path(__file__).parent.parent / "prompts"
        parts = []
        for p in [base / "code.md", base / "code" / "system.md", base / "code" / "execution.md"]:
            if p.exists():
                parts.append(p.read_text(encoding="utf-8"))
        if parts and (base / "common" / "engineering_rules.md").exists():
            parts.append((base / "common" / "engineering_rules.md").read_text(encoding="utf-8"))
        system = "\n\n".join(parts)
        baseline_lines = []
        for rel, snap in (baseline.files or {}).items():
            baseline_lines.append(f"- {rel}: {'exists' if snap.exists else 'does not exist'}")
        baseline_text = "\n".join(baseline_lines) if baseline_lines else "(no task files tracked)"
        acc = "\n".join(f"- {a}" for a in (task.acceptance or [])) or "(none)"
        val = "\n".join(f"- {v}" for v in (task.validation or [])) or "(none)"
        task_block = f"Task: {task.description}\nAcceptance:\n{acc}\nValidation:\n{val}\nTask baseline:\n{baseline_text}\nWorkspace: {self.root}\nFiles hint: {', '.join(task.files) if task.files else 'auto-detect'}"
        if system:
            return system + "\n\n" + task_block + "\n\nUse tools to inspect and modify files as needed. Leave changes in the working tree for Runtime review."
        return task_block + "\n\nUse tools to inspect and modify files as needed. Leave changes in the working tree for Runtime review."

    def execute(self, task: AgentTask, context: ProjectContext) -> AgentResult:
        if _is_mock_mode():
            baseline = self._capture_baseline(task)
            return AgentResult(status="SUCCESS", message=f"mock code for {task.description}", artifacts=task.files, baseline=baseline)

        from agent_system.config import resolve_api_key
        api_key = resolve_api_key()
        if not api_key:
            raise RuntimeError("API key not configured. Set ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN or use XXX_MOCK=1 for mock mode.")

        baseline = self._capture_baseline(task)
        from agent_system.runtime.git_control import capture_git_control_state, validate_unchanged
        git_before = capture_git_control_state(self.root)

        prompt = self._build_prompt(task, baseline, context)

        from agent_system.runtime.claude_code_cli import ClaudeCodeCLI
        cli = ClaudeCodeCLI(root=self.root, model=self.model)
        run_result = cli.run(prompt=prompt)

        git_after = capture_git_control_state(self.root)
        violation = validate_unchanged(git_before, git_after)
        if violation:
            return AgentResult(
                status="FAILED",
                message=violation,
                artifacts=[],
                baseline=baseline,
                evidence=run_result.evidence,
                execution_status="ERROR",
                stop_reason="runtime_authority_violation",
            )

        if run_result.execution_status == "ERROR":
            return AgentResult(
                status="FAILED",
                message=run_result.result_text[:2000],
                artifacts=[],
                baseline=baseline,
                evidence=run_result.evidence,
                execution_status="ERROR",
                stop_reason=run_result.stop_reason or "process_error",
            )

        changes_after = self.git.project_changes_model()
        return AgentResult(
            status="SUCCESS",
            message=run_result.result_text[:4000] if run_result.result_text else "done",
            artifacts=changes_after.changed_files,
            baseline=baseline,
            evidence=run_result.evidence,
            execution_status=run_result.execution_status or "COMPLETED",
            stop_reason=run_result.stop_reason or "completed",
        )

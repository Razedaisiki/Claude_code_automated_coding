from abc import ABC, abstractmethod
import os
from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.context import ProjectContext
from agent_system.runtime.git import Git
from agent_system.runtime.tools import TOOLS, ToolRuntime


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
        self.tools = ToolRuntime(self.root)
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

    def execute(self, task: AgentTask, context: ProjectContext) -> AgentResult:
        from agent_system.config import resolve_api_key, resolve_base_url, resolve_model

        if _is_mock_mode():
            baseline = self._capture_baseline(task)
            return AgentResult(status="SUCCESS", message=f"mock code for {task.description}", artifacts=task.files, baseline=baseline)
        api_key = resolve_api_key()
        if not api_key:
            raise RuntimeError("API key not configured. Set ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN or use XXX_MOCK=1 for mock mode.")

        try:
            import anthropic

            import hashlib

            base_url = resolve_base_url()
            client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=40) if base_url else anthropic.Anthropic(api_key=api_key, timeout=40)
            model = self.model or resolve_model() or "claude-sonnet-4-20250514"
            base = Path(__file__).parent.parent / "prompts"
            parts = []
            for p in [base / "code.md", base / "code" / "system.md", base / "code" / "execution.md"]:
                if p.exists():
                    parts.append(p.read_text(encoding="utf-8"))
            if parts and (base / "common" / "engineering_rules.md").exists():
                parts.append((base / "common" / "engineering_rules.md").read_text(encoding="utf-8"))
            prompt = "\n\n".join(parts)
            baseline = self._capture_baseline(task)
            baseline_lines = []
            for rel, snap in (baseline.files or {}).items():
                baseline_lines.append(f"- {rel}: {'exists' if snap.exists else 'does not exist'}")
            baseline_text = "\n".join(baseline_lines) if baseline_lines else "(no task files tracked)"
            acc = "\n".join(f"- {a}" for a in (task.acceptance or [])) or "(none)"
            val = "\n".join(f"- {v}" for v in (task.validation or [])) or "(none)"
            user = f"Task: {task.description}\nAcceptance:\n{acc}\nValidation:\n{val}\nTask baseline:\n{baseline_text}\nWorkspace: {self.root}\nFiles hint: {', '.join(task.files) if task.files else 'auto-detect'}\nUse tools to inspect and modify files as needed."
            self.tools.events = []
            from agent_system.delivery import DeliveryConfig as _DC
            _is_local = _DC.load(self.root).mode == "local"
            if _is_local:
                before = None
            else:
                before = set(self.git.changed_files())

            DEFAULT_MAX_TURNS = 100
            DEFAULT_STAGNANT_TURNS = 10
            max_turns = min(max(1, int(os.getenv("XXX_MAX_TURNS", str(DEFAULT_MAX_TURNS)))), 100)
            max_stagnant = min(max(1, int(os.getenv("XXX_STAGNANT_TURNS", str(DEFAULT_STAGNANT_TURNS)))), max_turns)

            def _diff_fp() -> str:
                return self.git.project_changes_model().fingerprint

            last_fp = _diff_fp()
            stagnant = 0
            stop_reason = None
            debug_turns = os.getenv("XXX_DEBUG_AGENT_TURNS") == "1"
            messages = [{"role": "user", "content": user}]
            result_text = ""

            for turn in range(1, max_turns + 1):
                resp = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=prompt[:4000] if prompt else None,
                    tools=TOOLS,
                    messages=messages,
                )

                tool_calls = [
                    b for b in resp.content
                    if getattr(b, "type", None) == "tool_use"
                ]

                texts = [
                    b.text for b in resp.content
                    if getattr(b, "type", None) == "text"
                ]

                if debug_turns:
                    print(f"    [CodeAgent turn {turn}/{max_turns} | stagnant {stagnant}/{max_stagnant}]")

                    for text in texts:
                        text = text.strip()
                        if text:
                            print(f"      text: {text[:500]}")

                if not tool_calls:
                    result_text = "\n".join(texts) if texts else "done"
                    stop_reason = "completed"

                    if debug_turns:
                        print("      final response — no more tool calls")

                    break

                tool_results = []

                for tc in tool_calls:
                    if debug_turns:
                        debug_input = dict(tc.input)

                        if tc.name == "write_file" and "content" in debug_input:
                            content = debug_input["content"]
                            debug_input["content"] = (
                                f"<{len(content)} chars>"
                            )

                        print(
                            f"      -> {tc.name}: "
                            f"{debug_input}"
                        )

                    out = self.tools.handle(tc.name, tc.input)

                    if debug_turns:
                        preview = out.replace("\n", "\\n")[:800]
                        print(f"      <- {preview}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": out[:8000],
                    })

                messages.append({
                    "role": "assistant",
                    "content": resp.content,
                })

                messages.append({
                    "role": "user",
                    "content": tool_results,
                })

                cur_fp = _diff_fp()
                if cur_fp != last_fp:
                    if debug_turns:
                        print(f"    [CodeAgent progress] project diff changed — stagnant reset 0/{max_stagnant}")
                    stagnant = 0
                    last_fp = cur_fp
                else:
                    stagnant += 1

                if stagnant >= max_stagnant:
                    stop_reason = "stagnation_budget"
                    result_text = "\n".join(texts) if texts else "done"

                    if debug_turns:
                        changed_now = sorted(set(self.git.changed_files()) - (before or set()))
                        print(f"    [CodeAgent stopped] reason: stagnation_budget turn: {turn}/{max_turns} stagnant: {stagnant}/{max_stagnant} changed: {changed_now}")

                    break

            else:
                result_text = "done (max turns)"
                stop_reason = "hard_turn_limit"

                if debug_turns:
                    print(
                        f"    [CodeAgent stopped] reason: hard_turn_limit "
                        f"turn: {max_turns}/{max_turns} stagnant: {stagnant}/{max_stagnant}"
                    )

            from agent_system.agents.models import ExecutionEvidence, ToolEvent as _TE

            events = [_TE(tool=e["tool"], input=e["input"], output=e["output"][:2000], exit_code=e["exit_code"]) for e in self.tools.events]
            evidence = ExecutionEvidence(events=events)

            if before is None:
                status = "SUCCESS" if stop_reason in ("completed", "stagnation_budget") else "INCOMPLETE"
                return AgentResult(status=status, message=result_text[:800] if result_text else "done", artifacts=task.files or [], baseline=baseline, evidence=evidence, execution_status="BUDGET_STOPPED" if stop_reason in ("stagnation_budget", "hard_turn_limit") else "COMPLETED", stop_reason=stop_reason)
            after = set(self.git.changed_files())
            changed = sorted(after - before)
            artifacts = [c for c in changed if ".agent/" not in c and "__pycache__" not in c and not c.endswith(".pyc")]
            if stop_reason in ("stagnation_budget", "hard_turn_limit"):
                return AgentResult(status="SUCCESS", message=result_text[:800] if result_text else "done", artifacts=artifacts, baseline=baseline, evidence=evidence, execution_status="BUDGET_STOPPED", stop_reason=stop_reason)
            return AgentResult(status="SUCCESS", message=result_text[:800] if result_text else "done", artifacts=artifacts, baseline=baseline, evidence=evidence, execution_status="COMPLETED", stop_reason=stop_reason)
        except Exception as e:
            from agent_system.agents.models import ExecutionEvidence as _EE2

            baseline_fb = self._capture_baseline(task) if hasattr(self, '_capture_baseline') else None
            ev_fb = _EE2(events=[]) if '_EE2' else None
            return AgentResult(status="FAILED", message=f"CodeAgent runtime error: {e}", artifacts=[], baseline=baseline_fb, evidence=ev_fb, execution_status="ERROR", stop_reason="runtime_exception")

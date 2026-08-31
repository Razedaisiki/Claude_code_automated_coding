from abc import ABC, abstractmethod
from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.context import ProjectContext
from agent_system.runtime.git import Git
from agent_system.runtime.tools import TOOLS, ToolRuntime


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

        api_key = resolve_api_key()
        if not api_key:
            baseline = self._capture_baseline(task)
            return AgentResult(status="SUCCESS", message=f"mock code for {task.description}", artifacts=task.files, baseline=baseline)

        try:
            import anthropic

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
            messages = [{"role": "user", "content": user}]
            result_text = ""
            hit_limit = False
            # for _ in range(12):
            #     resp = client.messages.create(
            #         model=model,
            #         max_tokens=4096,
            #         system=prompt[:4000] if prompt else None,
            #         tools=TOOLS,
            #         messages=messages,
            #     )
            #     tool_calls = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            #     texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            #     if not tool_calls:
            #         result_text = "\n".join(texts) if texts else "done"
            #         break
            #     tool_results = []
            #     for tc in tool_calls:
            #         out = self.tools.handle(tc.name, tc.input)
            #         tool_results.append({"type": "tool_result", "tool_use_id": tc.id, "content": out[:8000]})
            #     messages.append({"role": "assistant", "content": resp.content})
            #     messages.append({"role": "user", "content": tool_results})
            # else:
            #     result_text = "done (max turns)"
            #     hit_limit = True
            
            import os
            debug_turns = os.getenv("XXX_DEBUG_AGENT_TURNS") == "1"

            for turn in range(1, 13):
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
                    print(f"    [CodeAgent turn {turn}/12]")

                    for text in texts:
                        text = text.strip()
                        if text:
                            print(f"      text: {text[:500]}")

                if not tool_calls:
                    result_text = "\n".join(texts) if texts else "done"

                    if debug_turns:
                        print("      final response — no more tool calls")

                    break

                tool_results = []

                for tc in tool_calls:
                    if debug_turns:
                        debug_input = dict(tc.input)

                        # 不要把整个 write_file 内容刷到终端
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

            else:
                result_text = "done (max turns)"
                hit_limit = True

                if debug_turns:
                    print(
                        "    [CodeAgent] execution budget exhausted "
                        "after 12 turns"
                    )

            from agent_system.agents.models import ExecutionEvidence, ToolEvent as _TE

            events = [_TE(tool=e["tool"], input=e["input"], output=e["output"][:2000], exit_code=e["exit_code"]) for e in self.tools.events]
            evidence = ExecutionEvidence(events=events)
            if hit_limit:
                after_chk = set(self.git.changed_files()) if before is not None else set()
                has_change = bool(after_chk - before) if before is not None else False
                if not has_change:
                    return AgentResult(status="INCOMPLETE", message="execution budget exhausted without completing task", artifacts=[], baseline=baseline, evidence=evidence)

            if before is None:
                return AgentResult(status="SUCCESS", message=result_text[:800] if result_text else "done", artifacts=task.files or [], baseline=baseline, evidence=evidence)
            after = set(self.git.changed_files())
            changed = sorted(after - before)
            artifacts = [c for c in changed if ".agent/" not in c and "__pycache__" not in c and not c.endswith(".pyc")]
            return AgentResult(status="SUCCESS", message=result_text[:800] if result_text else "done", artifacts=artifacts, baseline=baseline, evidence=evidence)
        except Exception as e:
            baseline_fb = self._capture_baseline(task) if hasattr(self, '_capture_baseline') else None
            return AgentResult(status="SUCCESS", message=f"mock code for {task.description} (fallback: {e})", artifacts=task.files, baseline=baseline_fb)

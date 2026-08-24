from pathlib import Path

from agent_system.agents.models import AgentResult
from agent_system.agents.parent import ParentAgent
from agent_system.config import resolve_api_key, resolve_base_url, resolve_model
from agent_system.context import ProjectContext, load_context
from agent_system.plan_parser import parse_plan
from agent_system.runtime.git import Git


def _load_prompt(name: str) -> str:
    bundled = Path(__file__).parent.parent / "prompts" / f"{name}.md"
    if bundled.exists():
        return bundled.read_text(encoding="utf-8")
    return ""


class ClaudeParentAgent(ParentAgent):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model or resolve_model()
        self.git = Git(self.root)

    def run(self, task: str) -> AgentResult:
        print("Starting Claude Parent")
        ctx = load_context(self.root)
        print("  Loading context")
        if ctx.task:
            print("  TASK analyzed")
        if ctx.instructions:
            print("  CLAUDE.md loaded")
        if ctx.milestones:
            print("  milestones loaded")

        plan_text = self._plan(ctx.task or task, ctx)
        plan_file = self.root / ".agent" / "plan.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(plan_text, encoding="utf-8")
        print("  plan.md created")

        tasks = parse_plan(plan_text)
        if tasks:
            print(f"  parsed {len(tasks)} tasks from plan")
            for t in tasks:
                print(f"    {t.id} [{t.role}] {t.description}")

            from agent_system.agents.code_agent import CodeAgent
            from agent_system.agents.models import AgentTask
            from agent_system.agents.subagent import MockTestAgent

            for t in tasks:
                print(f"  Dispatch: {t.id} -> {t.role}")
                for attempt in range(1, 4):
                    diff_before = self.git.diff()
                    if t.role == "code":
                        if attempt > 1:
                            t = AgentTask(id=t.id, role=t.role, description=t.description + f"\n[Retry {attempt}: previous review failed: {last_reason}]", files=t.files)
                        result = CodeAgent(root=self.root, model=self.model).execute(t)
                    else:
                        result = MockTestAgent().execute(t)

                    diff_after = self.git.diff()
                    review = self._review(t, result, diff_before, diff_after)
                    if review.status == "SUCCESS":
                        print(f"  Review PASSED for {t.id} (attempt {attempt})")
                        break
                    last_reason = review.message
                    print(f"  Review FAILED for {t.id} (attempt {attempt}): {review.message}")
                    if attempt == 3:
                        return review
                    print(f"  Retrying {t.id}...")

        else:
            self._execute(plan_text, ctx)

        print("Parent finished")
        return AgentResult(
            status="SUCCESS",
            message="plan executed",
            artifacts=[str(plan_file)],
            next_action="DONE",
        )

    def get_context(self) -> ProjectContext:
        return load_context(self.root)

    def _review(self, task, result: AgentResult, diff_before: str = "", diff_after: str = "") -> AgentResult:
        if result.status == "FAILED":
            return AgentResult(status="FAILED", message=f"task {task.id} failed: {result.message}", artifacts=result.artifacts)
        if not result.message:
            return AgentResult(status="FAILED", message=f"task {task.id} produced empty result", artifacts=result.artifacts)
        diff = diff_after[len(diff_before):] if diff_after.startswith(diff_before) else diff_after
        if task.role == "code" and not diff.strip() and not result.artifacts:
            return AgentResult(status="FAILED", message=f"task {task.id} produced no file changes", artifacts=result.artifacts)
        ctx = load_context(self.root)
        score = self._llm_review(task, result, diff, ctx)
        if score is not None and not score.get("pass", True):
            return AgentResult(status="FAILED", message=f"task {task.id} review failed: {score.get('reason','')}", artifacts=result.artifacts)
        if diff.strip():
            print(f"    diff: {diff[:200]}")
        return AgentResult(status="SUCCESS", message=f"task {task.id} accepted", artifacts=result.artifacts)

    def _llm_review(self, task, result: AgentResult, diff: str, ctx: ProjectContext):
        try:
            import anthropic, json

            api_key = resolve_api_key()
            if not api_key:
                return None
            base_url = resolve_base_url()
            client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=15) if base_url else anthropic.Anthropic(api_key=api_key, timeout=15)
            system = "You are a code reviewer. Judge if the diff satisfies the task and plan. Reply JSON only: {\"pass\": bool, \"reason\": string}"
            user = f"Task: {task.description}\nPlan excerpt: {ctx.plan[:1500]}\nDiff:\n{diff[:3000]}\nResult: {result.message[:500]}"
            resp = client.messages.create(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception:
            return None
        return None

    def _plan(self, task: str, ctx: ProjectContext) -> str:
        system = _load_prompt("parent")
        milestones_text = "\n\n".join(f"## {m.name}\n{m.content}" for m in ctx.milestones)
        user = f"Task:\n{task}\n\nRepo: {ctx.repository}\n\nCLAUDE.md:\n{ctx.instructions[:2000]}\n\nMilestones:\n{milestones_text[:2000]}"
        invoked = self._invoke(system, user)
        if invoked and not invoked.startswith("mock result"):
            return invoked if invoked.lstrip().startswith("#") else f"# Plan\n\n{invoked}\n"
        first_line = task.strip().splitlines()[0] if task.strip() else "No task"
        return (
            f"# Objective\n{first_line}\n\n"
            f"# Analysis\nTask: {task.strip()[:500]}\nRepo: {ctx.repository or 'unknown'}\n\n"
            f"# Tasks\n1. Implement feature\n2. Add tests\n\n"
            f"# Execution Order\n1. Implement\n2. Test\n\n"
            f"# Validation\nRun demo workflow and verify output.\n"
        )

    def _execute(self, plan: str, ctx: ProjectContext):
        print("  execute phase")
        print("  reading plan")
        if ctx.task:
            print(f"  applying plan for: {ctx.task.strip().splitlines()[0][:60]}")
        print("  tools: read / write / bash (mock)")

    def _invoke(self, system: str, user: str) -> str:
        try:
            import anthropic

            api_key = resolve_api_key()
            if not api_key:
                return self._fallback(user)

            base_url = resolve_base_url()
            if base_url:
                client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=20)
            else:
                client = anthropic.Anthropic(api_key=api_key, timeout=20)
            kwargs = dict(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{"role": "user", "content": user[:6000]}],
            )
            if system:
                kwargs["system"] = system[:4000]
            resp = client.messages.create(**kwargs)
            parts = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    parts.append(block.text)
            return "\n".join(parts)
        except Exception as e:
            return self._fallback(user, error=str(e))

    def _fallback(self, task: str, error: str = "") -> str:
        if error:
            print(f"  [fallback: {error[:80]}]")
        else:
            print("  [fallback: no ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN, using mock]")
        return f"mock result for task: {task[:80]}"

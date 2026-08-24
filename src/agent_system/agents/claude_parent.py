from pathlib import Path

from agent_system.agents.models import AgentResult
from agent_system.agents.parent import ParentAgent
from agent_system.config import resolve_api_key, resolve_base_url, resolve_model
from agent_system.context import ProjectContext, load_context


def _load_prompt(name: str) -> str:
    bundled = Path(__file__).parent.parent / "prompts" / f"{name}.md"
    if bundled.exists():
        return bundled.read_text(encoding="utf-8")
    return ""


class ClaudeParentAgent(ParentAgent):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model or resolve_model()

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

        self._execute(plan_text, ctx)

        print("Parent finished")
        return AgentResult(
            status="SUCCESS",
            message="plan created",
            artifacts=[str(plan_file)],
            next_action="EXECUTE",
        )

    def get_context(self) -> ProjectContext:
        return load_context(self.root)

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
                client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
            else:
                client = anthropic.Anthropic(api_key=api_key)
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

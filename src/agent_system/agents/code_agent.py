from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.agents.subagent import SubAgent
from agent_system.config import resolve_api_key, resolve_base_url, resolve_model


def _load_prompt(name: str) -> str:
    p = Path(__file__).parent.parent / "prompts" / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


class CodeAgent(SubAgent):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model or resolve_model()

    def execute(self, task: AgentTask) -> AgentResult:
        print(f"  [CodeAgent] {task.id}: {task.description}")
        if task.files:
            print(f"  files: {', '.join(task.files)}")

        result = self._invoke(task)
        if result and not result.startswith("mock"):
            print(f"  result: {result[:120]}")

        print("  CodeAgent done")
        return AgentResult(status="SUCCESS", message=result[:500] if result else "done", artifacts=task.files)

    def _invoke(self, task: AgentTask) -> str:
        system = _load_prompt("code")
        user = f"Task: {task.description}\nFiles: {', '.join(task.files) if task.files else 'auto-detect'}"
        try:
            import anthropic

            api_key = resolve_api_key()
            if not api_key:
                return f"mock code for {task.description}"
            base_url = resolve_base_url()
            client = anthropic.Anthropic(api_key=api_key, base_url=base_url) if base_url else anthropic.Anthropic(api_key=api_key)
            kwargs = dict(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{"role": "user", "content": user[:6000]}],
            )
            if system:
                kwargs["system"] = system[:4000]
            resp = client.messages.create(**kwargs)
            parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            return "\n".join(parts) if parts else f"mock code for {task.description}"
        except Exception as e:
            print(f"  [fallback: {str(e)[:60]}]")
            return f"mock code for {task.description}"

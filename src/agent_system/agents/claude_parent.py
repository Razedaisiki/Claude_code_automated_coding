from pathlib import Path
from typing import Optional

from agent_system.agents.parent import ParentAgent
from agent_system.context import AgentContext, load_context


class ClaudeParentAgent(ParentAgent):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model

    def run(self, task: str) -> None:
        print("Starting Claude Parent")
        ctx = load_context(self.root)
        print("  Loading context")
        if ctx.task:
            print("  TASK analyzed")
        if ctx.claude_md:
            print("  CLAUDE.md loaded")
        if ctx.milestone:
            print("  milestones loaded")

        result = self._invoke(ctx.task or task)

        if result:
            print(f"  result: {result[:120]}")

        print("Parent finished")

    def get_context(self) -> AgentContext:
        return load_context(self.root)

    def _invoke(self, task: str) -> str:
        try:
            import anthropic

            api_key = self._resolve_api_key()
            if not api_key:
                return self._fallback(task)

            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": task[:4000]}],
            )
            parts = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    parts.append(block.text)
            return "\n".join(parts)
        except Exception as e:
            return self._fallback(task, error=str(e))

    def _resolve_api_key(self) -> Optional[str]:
        import os

        return os.environ.get("ANTHROPIC_API_KEY")

    def _fallback(self, task: str, error: str = "") -> str:
        if error:
            print(f"  [fallback: {error[:80]}]")
        else:
            print("  [fallback: no ANTHROPIC_API_KEY, using mock]")
        return f"mock result for task: {task[:80]}"

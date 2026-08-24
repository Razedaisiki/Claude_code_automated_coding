import json
from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.agents.subagent import SubAgent
from agent_system.config import resolve_api_key, resolve_base_url, resolve_model
from agent_system.runtime.filesystem import Filesystem
from agent_system.runtime.git import Git
from agent_system.runtime.shell import Shell


def _load_prompt(name: str) -> str:
    p = Path(__file__).parent.parent / "prompts" / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from workspace",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "write_file",
        "description": "Write a file to workspace",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    },
    {
        "name": "list_files",
        "description": "List files matching glob",
        "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": []},
    },
    {
        "name": "run_command",
        "description": "Run shell command in workspace",
        "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
    },
]


class CodeAgent(SubAgent):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model or resolve_model()
        self.fs = Filesystem(self.root)
        self.shell = Shell(self.root)
        self.git = Git(self.root)

    def execute(self, task: AgentTask) -> AgentResult:
        print(f"  [CodeAgent] {task.id}: {task.description}")
        if task.files:
            print(f"  files: {', '.join(task.files)}")

        before = set(self.git.changed_files())

        result_text = self._run_with_tools(task)

        after = set(self.git.changed_files())
        changed = sorted(after - before) if after != before else sorted(after)
        if not changed:
            stat = self.git.diff_stat()
            if stat.strip():
                changed = [l.split("|")[0].strip() for l in stat.splitlines() if "|" in l]

        artifacts = changed if changed else task.files
        if result_text and not result_text.startswith("mock"):
            print(f"  result: {result_text[:120]}")
        print(f"  changed: {artifacts if artifacts else 'none'}")
        print("  CodeAgent done")
        return AgentResult(status="SUCCESS", message=result_text[:800] if result_text else "done", artifacts=artifacts)

    def _run_with_tools(self, task: AgentTask) -> str:
        api_key = resolve_api_key()
        if not api_key:
            return self._mock_execute(task)

        try:
            import anthropic

            base_url = resolve_base_url()
            client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=40) if base_url else anthropic.Anthropic(api_key=api_key, timeout=40)
            system = _load_prompt("code")
            user = f"Task: {task.description}\nWorkspace: {self.root}\nFiles hint: {', '.join(task.files) if task.files else 'auto-detect'}\nUse tools to inspect and modify files as needed."

            messages = [{"role": "user", "content": user}]
            for _ in range(6):
                resp = client.messages.create(
                    model=self.model or "claude-sonnet-4-20250514",
                    max_tokens=4096,
                    system=system[:4000] if system else None,
                    tools=TOOLS,
                    messages=messages,
                )
                tool_calls = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
                texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
                if not tool_calls:
                    return "\n".join(texts) if texts else "done"

                tool_results = []
                for tc in tool_calls:
                    out = self._handle_tool(tc.name, tc.input)
                    print(f"    tool {tc.name} -> {out[:80]}")
                    tool_results.append({"type": "tool_result", "tool_use_id": tc.id, "content": out[:8000]})

                messages.append({"role": "assistant", "content": resp.content})
                messages.append({"role": "user", "content": tool_results})

            return "done (max turns)"
        except Exception as e:
            print(f"  [fallback: {str(e)[:80]}]")
            return self._mock_execute(task)

    def _handle_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "read_file":
                return self.fs.read_file(inp["path"])
            if name == "write_file":
                self.fs.write_file(inp["path"], inp["content"])
                return f"wrote {inp['path']}"
            if name == "list_files":
                pattern = inp.get("pattern", "**/*")
                files = self.fs.list_files(pattern)
                return "\n".join(files[:100]) if files else "(no files)"
            if name == "run_command":
                r = self.shell.run(inp["cmd"])
                return (r.stdout + r.stderr)[:4000] or f"exit {r.returncode}"
        except Exception as e:
            return f"error: {e}"
        return f"unknown tool {name}"

    def _mock_execute(self, task: AgentTask) -> str:
        return f"mock code for {task.description}"

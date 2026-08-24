from pathlib import Path

from agent_system.runtime.filesystem import Filesystem
from agent_system.runtime.git import Git
from agent_system.runtime.shell import Shell

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


class ToolRuntime:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.fs = Filesystem(self.root)
        self.shell = Shell(self.root)
        self.git = Git(self.root)

    def handle(self, name: str, inp: dict) -> str:
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

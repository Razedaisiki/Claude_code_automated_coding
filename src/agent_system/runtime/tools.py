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
        self.events: list = []

    def handle(self, name: str, inp: dict) -> str:
        try:
            if name == "read_file":
                out = self.fs.read_file(inp["path"])
                self.events.append({"tool": name, "input": dict(inp), "output": out[:4000], "exit_code": 0})
                return out
            if name == "write_file":
                self.fs.write_file(inp["path"], inp["content"])
                out = f"wrote {inp['path']}"
                self.events.append({"tool": name, "input": {"path": inp["path"]}, "output": out, "exit_code": 0})
                return out
            if name == "list_files":
                pattern = inp.get("pattern", "**/*")
                files = self.fs.list_files(pattern)
                out = "\n".join(files[:100]) if files else "(no files)"
                self.events.append({"tool": name, "input": dict(inp), "output": out[:4000], "exit_code": 0})
                return out
            if name == "run_command":
                r = self.shell.run(inp["cmd"])
                bounded = (r.stdout + r.stderr)[:4000] or f"exit {r.returncode}"
                out = f"exit_code: {r.returncode}\nstdout:\n{r.stdout[:3000]}\n" + (f"stderr:\n{r.stderr[:1000]}" if r.stderr else "")
                out = out[:4000]
                self.events.append({"tool": name, "input": dict(inp), "output": bounded, "exit_code": r.returncode})
                return out
        except Exception as e:
            err = f"error: {e}"
            self.events.append({"tool": name, "input": dict(inp), "output": err, "exit_code": 1})
            return err
        self.events.append({"tool": name, "input": dict(inp), "output": f"unknown tool {name}", "exit_code": 1})
        return f"unknown tool {name}"

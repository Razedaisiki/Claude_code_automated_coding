import subprocess
import tempfile
from pathlib import Path

from agent_system.agents.claude_parent import ClaudeParentAgent
import agent_system.agents.claude_parent as cp

ROOT = Path(__file__).parent.parent
TMPDIR = Path(tempfile.mkdtemp())
print(f"Workspace: {TMPDIR}")

(TMPDIR / "src").mkdir(parents=True)
(TMPDIR / "src/app.py").write_text("# empty\n")

(TMPDIR / "TASK.md").write_text('# Task\nAdd hello() function returning "hello" to src/app.py\n')

subprocess.run("git init -q && git config user.email 'demo@test.com' && git config user.name 'demo' && git add . && git commit -qm init", shell=True, cwd=TMPDIR)

print("=== TASK ===")
print((TMPDIR / "TASK.md").read_text())
print("\n=== RUN ===")

orig_plan = cp.ClaudeParentAgent._plan

def fixed_plan(self, task, ctx):
    return """# Objective
Add hello function
# Analysis
Simple task
# Tasks
1. Add hello() function returning "hello" to src/app.py
# Execution Order
1
# Validation
Check git diff contains hello
"""

cp.ClaudeParentAgent._plan = fixed_plan

parent = ClaudeParentAgent(root=TMPDIR)
r = parent.run("Add hello function")
print(f"Result: {r}")

diff = subprocess.run("git diff", shell=True, cwd=TMPDIR, capture_output=True, text=True).stdout
print("--- git diff ---")
print(diff[:800] if diff else "(empty)")
print("--- file ---")
print((TMPDIR / "src/app.py").read_text()[:500])
print(f"\nWorkspace kept at: {TMPDIR} (inspect manually, rm when done)")

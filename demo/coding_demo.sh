#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TMPDIR=$(mktemp -d)
echo "Workspace: $TMPDIR"

mkdir -p "$TMPDIR/src"
cat > "$TMPDIR/src/app.py" <<'PY'
# empty
PY
cp pyproject.toml "$TMPDIR/" 2>/dev/null || true
cp -r src "$TMPDIR/src_agent" 2>/dev/null || true

git -C "$TMPDIR" init -q
git -C "$TMPDIR" config user.email "demo@test.com"
git -C "$TMPDIR" config user.name "demo"
git -C "$TMPDIR" add . 2>/dev/null; git -C "$TMPDIR" commit -qm init 2>/dev/null || true

cat > "$TMPDIR/TASK.md" <<'EOF'
# Task
Add hello() function returning "hello" to src/app.py
EOF

echo "=== TASK ==="
cat "$TMPDIR/TASK.md"

echo ""
echo "=== RUN ==="
PYTHONPATH="$ROOT/src" python3 -c "
from pathlib import Path
from agent_system.agents.claude_parent import ClaudeParentAgent
from agent_system.supervisor.supervisor import Supervisor

# Use tmpdir as workspace root
import agent_system.agents.claude_parent as cp
# Force a deterministic plan so demo doesn't depend on API flakiness
orig_plan = cp.ClaudeParentAgent._plan
def fixed_plan(self, task, ctx):
    return '''# Objective
Add hello function
# Analysis
Simple task
# Tasks
1. Add hello() function returning \"hello\" to src/app.py
# Execution Order
1
# Validation
Check git diff contains hello
'''
cp.ClaudeParentAgent._plan = fixed_plan

root = Path('$TMPDIR')
# Supervisor would create session; run parent directly for isolated demo
parent = ClaudeParentAgent(root=root)
r = parent.run('Add hello function')
print(f'Result: {r}')

import subprocess
diff = subprocess.run('git diff', shell=True, cwd='$TMPDIR', capture_output=True, text=True).stdout
print('--- git diff ---')
print(diff[:800] if diff else '(empty)')
print('--- file ---')
print(open('$TMPDIR/src/app.py').read()[:500])
"

echo ""
echo "Workspace kept at: $TMPDIR (inspect manually, rm when done)"

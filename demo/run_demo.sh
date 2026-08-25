#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_ROOT="$ROOT/demo"

echo "=== INIT ==="
rm -rf "$DEMO_ROOT/.agent" "$DEMO_ROOT/TASK.md" "$DEMO_ROOT/src" "$DEMO_ROOT/__init__.py"
mkdir -p "$DEMO_ROOT"
echo "demo workspace: $DEMO_ROOT"

echo ""
echo "=== TASK ==="
cat > "$DEMO_ROOT/TASK.md" <<'EOF'
# Task
Add hello() function returning "hello" to demo/src/demo_app.py
EOF
cat "$DEMO_ROOT/TASK.md"

echo ""
echo "=== RUN ==="
echo "Supervisor(root=demo) -> Parent -> CodeAgent(workspace=demo) -> git diff -> review"

PYTHONPATH="$ROOT/src" python3 -c "
from pathlib import Path
from agent_system.supervisor.supervisor import Supervisor
from agent_system.init import init_workspace

demo_root = Path('$DEMO_ROOT')
init_workspace(demo_root)
print(f'Initialized workspace at {demo_root / \".agent\"}')

sup = Supervisor(root=demo_root)
sup.start()
"

echo ""
echo "=== plan.md ==="
cat "$DEMO_ROOT/.agent/plan.md" 2>&1 | head -40

echo ""
echo "=== git diff (demo/src only) ==="
git -C "$ROOT" diff --no-index /dev/null "$DEMO_ROOT/src/demo_app.py" 2>&1 | head -40 || git -C "$ROOT" diff -- demo/src 2>&1 | head -40 || true
if [ -f "$DEMO_ROOT/src/demo_app.py" ] && [ -z "$(git -C "$ROOT" diff -- demo/src 2>&1)" ]; then
  echo "(untracked new file, showing content diff)"
  git -C "$ROOT" diff --no-index /dev/null "$DEMO_ROOT/src/demo_app.py" 2>&1 | head -20 || true
fi

echo ""
echo "=== file ==="
cat "$DEMO_ROOT/src/demo_app.py" 2>&1 | head -20 || echo "(no file)"

echo ""
echo "=== state ==="
cat "$DEMO_ROOT/.agent/state.json" 2>&1 | head -10

echo ""
echo "=== DONE ==="
echo "Demo completed. Workspace: $DEMO_ROOT/.agent, file: $DEMO_ROOT/src/demo_app.py"
echo "To clean: rm -rf $DEMO_ROOT/.agent $DEMO_ROOT/TASK.md $DEMO_ROOT/src"

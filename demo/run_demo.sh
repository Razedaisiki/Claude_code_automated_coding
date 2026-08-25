#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== INIT ==="
rm -rf .agent TASK.md src 2>/dev/null || true
xxx init

echo ""
echo "=== TASK ==="
cat > TASK.md <<'EOF'
# Task
Add hello() function returning "hello" to src/demo_app.py
EOF
cat TASK.md

echo ""
echo "=== RUN ==="
xxx run

echo ""
echo "=== plan.md ==="
cat .agent/plan.md 2>&1 | head -40

echo ""
echo "=== git diff ==="
git diff -- src 2>&1 | head -40
if [ -f src/demo_app.py ] && git status --porcelain -- src 2>&1 | grep -q "??"; then
  echo "(untracked new file)"
  git diff --no-index /dev/null src/demo_app.py 2>&1 | head -20 || true
fi

echo ""
echo "=== file ==="
cat src/demo_app.py 2>&1 | head -20 || echo "(no file)"

echo ""
echo "=== state ==="
cat .agent/state.json 2>&1 | head -10

echo ""
echo "=== DONE ==="
echo "Demo workspace: $SCRIPT_DIR"
echo "To clean: rm -rf $SCRIPT_DIR/.agent $SCRIPT_DIR/TASK.md $SCRIPT_DIR/src"

#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== INIT ==="
rm -rf .agent
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
echo "Supervisor -> Parent -> CodeAgent -> git diff -> review"
xxx run

echo ""
echo "=== plan.md ==="
cat .agent/plan.md 2>&1 | head -40

echo ""
echo "=== git diff ==="
git diff 2>&1 | head -40 || true

echo ""
echo "=== file ==="
cat src/demo_app.py 2>&1 | head -20 || true

echo ""
echo "=== state ==="
cat .agent/state.json

echo ""
echo "=== DONE ==="

rm -rf .agent TASK.md src/demo_app.py 2>/dev/null
git checkout -- . 2>/dev/null || true
echo "Demo completed (cleaned workspace)"

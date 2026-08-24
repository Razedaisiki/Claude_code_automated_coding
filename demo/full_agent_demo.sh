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
Add hello api
EOF
cat TASK.md

echo ""
echo "=== RUN ==="
xxx run

echo ""
echo "=== plan.md ==="
cat .agent/plan.md 2>&1 | head -40

echo ""
echo "=== state ==="
cat .agent/state.json

echo ""
echo "=== DONE ==="
echo "Parent: created plan"
echo "Task: implement api"
echo "Dispatch: Code Agent"
echo "Code changed"
echo "Parent review"
echo "SUCCESS"

rm -rf .agent TASK.md
echo "Demo completed (cleaned .agent and TASK.md)"

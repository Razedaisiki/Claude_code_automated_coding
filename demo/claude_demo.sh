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
Create hello world API
EOF
cat TASK.md

echo ""
echo "=== RUN (Claude Parent) ==="
xxx run

echo ""
echo "=== plan.md ==="
cat .agent/plan.md 2>&1 | head -30

echo ""
echo "=== state ==="
cat .agent/state.json

echo ""
echo "=== DONE ==="

# cleanup demo artifacts
rm -rf .agent TASK.md
echo "Demo completed (cleaned .agent and TASK.md)"

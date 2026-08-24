#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== INIT ==="
rm -rf .agent
xxx init

echo ""
echo "=== TASK loaded ==="
cp demo/simple_task/TASK.md ./TASK.md
cat TASK.md

echo ""
echo "=== Supervisor ==="
xxx run

echo ""
echo "=== State saved ==="
cat .agent/state.json

echo ""
echo "=== DONE ==="
ls -R .agent 2>&1 | head -30

# cleanup demo artifacts (keep demo/ but remove runtime)
rm -rf .agent TASK.md
echo ""
echo "Demo completed (cleaned .agent and TASK.md)"

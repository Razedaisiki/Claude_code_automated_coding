#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Demo B — Resume / Recovery (gh) ==="
echo "Task: format_user_name in src/user_utils.py"
echo "External operation: xxx run → WAITING_CI → Ctrl+C → xxx resume"

echo ""
echo "=== INIT ==="
rm -rf .agent src 2>/dev/null || true
xxx init
xxx remote gh 2>&1 | head -5 || true
if [ ! -d .git ]; then
  git init -q
  git config user.email "demo@test.com" 2>/dev/null || true
  git config user.name "demo" 2>/dev/null || true
  git add . 2>/dev/null || true
  git commit -qm "initial demo" 2>/dev/null || true
fi
if ! git remote 2>&1 | grep -q origin; then
  git remote add origin git@github.com:Razedaisiki/test_demo.git 2>&1 || true
fi

echo ""
echo "=== TASK ==="
cat TASK.md

echo ""
echo "=== RUN ==="
echo "Run: xxx run, then Ctrl+C during WAITING_CI, then xxx resume"
xxx run

echo ""
echo "=== state ==="
cat .agent/state.json 2>&1 | head -20

echo ""
echo "=== DONE (demo_b) ==="
echo "To test resume: kill during WAITING_CI, then run: xxx resume"

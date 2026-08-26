#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Demo A — Happy Path (gh) ==="
echo "Task: greet(name) in src/greeting.py"

echo ""
echo "=== INIT ==="
rm -rf .agent src 2>/dev/null || true
xxx init
xxx remote gh 2>&1 | head -5 || true
xxx remote status 2>&1 | head -15 || true
if [ ! -d .git ]; then
  git init -q
  git config user.email "demo@test.com" 2>/dev/null || true
  git config user.name "demo" 2>/dev/null || true
  git add . 2>/dev/null || true
  git commit -qm "initial demo" 2>/dev/null || true
  echo "git init + initial commit"
fi
if ! git remote 2>&1 | grep -q origin; then
  git remote add origin git@github.com:Razedaisiki/test_demo.git 2>&1 || true
  echo "remote added: test_demo.git"
fi

echo ""
echo "=== TASK ==="
cat TASK.md

echo ""
echo "=== RUN ==="
xxx run

echo ""
echo "=== plan.md ==="
cat .agent/plan.md 2>&1 | head -40

echo ""
echo "=== state ==="
cat .agent/state.json 2>&1 | head -20

echo ""
echo "=== git log ==="
git log --oneline -3 2>&1 | head -5

echo ""
echo "=== DONE (demo_a) ==="
echo "To clean: rm -rf $SCRIPT_DIR/.agent $SCRIPT_DIR/src $SCRIPT_DIR/.git"

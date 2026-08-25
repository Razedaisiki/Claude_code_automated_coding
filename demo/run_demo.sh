#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== INIT ==="
rm -rf .agent src 2>/dev/null || true
xxx init
if [ ! -d .git ]; then
  git init -q
  git config user.email "demo@test.com" 2>/dev/null || true
  git config user.name "demo" 2>/dev/null || true
  git add . 2>/dev/null || true
  git commit -qm "initial demo" 2>/dev/null || true
  echo "git init + initial commit"
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
echo "To clean: rm -rf $SCRIPT_DIR/.agent $SCRIPT_DIR/src $SCRIPT_DIR/.git"

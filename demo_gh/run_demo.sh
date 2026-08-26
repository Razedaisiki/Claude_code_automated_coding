#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== GH DELIVERY DEMO ==="
echo "mode: gh — commit + push + CI monitor + ci_review"

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

echo ""
echo "=== TASK ==="
cat TASK.md

echo ""
echo "=== RUN ==="
echo "Expect: WAITING_CI → CI monitor → Parent ci_review"
echo "Connect remote first: git remote add origin <url> (gh CLI auth required for CI)"
xxx run

echo ""
echo "=== state ==="
cat .agent/state.json 2>&1 | head -20

echo ""
echo "=== DONE (gh) ==="
echo "If push failed, effective mode falls back to local (config stays gh)"
echo "To clean: xxx remote local; rm -rf $SCRIPT_DIR/.agent $SCRIPT_DIR/src $SCRIPT_DIR/.git"
echo "To connect GitHub: git remote add origin <url> && gh auth status"

#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== GH DELIVERY DEMO ==="
echo "mode: gh — commit + push + CI monitor + ci_review"

echo ""
echo "=== INIT ==="
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
fi
rm -rf .agent src 2>/dev/null || true
xxx init
xxx remote gh 2>&1 | head -5 || true
xxx remote status 2>&1 | head -15 || true

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
echo "Shared remote: clean before next demo with: rm <file> && git add -A && git commit -m 'chore: clean' && git push"

#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Demo D — Multi-file Commit Boundary (gh) ==="
echo "Task: User.to_dict/to_json"

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
BRANCH="demo/json-serialization-user-$(date +%Y%m%d-%H%M%S)"
git fetch origin 2>&1 | head -5 || true
git switch -c "$BRANCH" origin/main 2>&1 | head -5 || true
echo "Creating demo branch: $BRANCH"
if ! PUSH_OUTPUT=$(git push -u origin "$BRANCH" 2>&1); then
  echo "$PUSH_OUTPUT"
  echo "ERROR: failed to establish demo branch upstream"
  exit 1
fi
echo "$PUSH_OUTPUT"
echo "Branch ready: $BRANCH"
rm -rf .agent 2>/dev/null || true
xxx init
xxx remote gh 2>&1 | head -5 || true
xxx remote status 2>&1 | head -15 || true

echo ""
echo "=== TASK ==="
cat TASK.md

echo ""
echo "=== RUN ==="
echo "Expect: 1 implementation task (not 4) — single commit boundary"
xxx run

echo ""
echo "=== state ==="
cat .agent/state.json 2>&1 | head -20

echo ""
echo "=== DONE (demo_d) ==="
echo "Shared remote: test_demo.git — clean up before next demo:"
echo "  rm -rf src/  # remove User-related files created by this demo"
echo "  git add -A && git commit -m 'chore: clean demo_d artifacts' && git push"

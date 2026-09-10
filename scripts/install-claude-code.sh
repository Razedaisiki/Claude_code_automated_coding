#!/usr/bin/env bash
set -euo pipefail

curl -fsSL https://bun.com/install | bash
cat >> ~/.bashrc <<'EOF'
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"
EOF
source ~/.bashrc
bun --version
bun add -g --trust @anthropic-ai/claude-code
claude --version && claude doctor
claude -p "reply OK"

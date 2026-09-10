#!/usr/bin/env bash
set -euo pipefail

info() { echo "[info] $*"; }
warn() { echo "[warn] $*"; }
fail() { echo "[error] $*"; exit 1; }

if ! uname -a 2>/dev/null | grep -qi linux && [[ "${OSTYPE:-}" != linux* ]]; then
  warn "Non-Linux detected; continuing anyway (Bun/Claude Code also work on macOS)."
fi

for req in curl unzip; do
  command -v "$req" >/dev/null 2>&1 || fail "Required tool '$req' not found. Please install it first."
done

BUN_INSTALL_DEFAULT="$HOME/.bun"
BUN_BIN="$BUN_INSTALL_DEFAULT/bin/bun"
LINE_BUN_INSTALL='export BUN_INSTALL="$HOME/.bun"'
LINE_BUN_PATH='export PATH="$BUN_INSTALL/bin:$PATH"'

persist_path() {
  local line="$1"
  local updated=0
  for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [[ -f "$rc" ]]; then
      if grep -Fq "$line" "$rc" 2>/dev/null; then
        info "Already present in $rc: $line"
      else
        printf '\n%s\n' "$line" >> "$rc"
        info "Added to $rc: $line"
        updated=1
      fi
    fi
  done
  if [[ $updated -eq 0 ]] && [[ ! -f "$HOME/.bashrc" && ! -f "$HOME/.zshrc" ]]; then
    warn "Neither ~/.bashrc nor ~/.zshrc found; creating ~/.bashrc"
    printf '\n%s\n' "$line" >> "$HOME/.bashrc"
    info "Added to ~/.bashrc: $line"
  fi
}

if ! command -v bun >/dev/null 2>&1 && [[ ! -x "$BUN_BIN" ]]; then
  info "Installing Bun (user-local, no sudo)..."
  curl -fsSL https://bun.com/install | bash
else
  info "Bun already installed: $(command -v bun 2>/dev/null || echo "$BUN_BIN")"
fi

export BUN_INSTALL="$BUN_INSTALL_DEFAULT"
export PATH="$BUN_INSTALL/bin:$PATH"

persist_path "$LINE_BUN_INSTALL"
persist_path "$LINE_BUN_PATH"

if ! command -v bun >/dev/null 2>&1; then
  fail "bun not found on PATH after install (expected $BUN_BIN). Try: source ~/.bashrc"
fi
info "bun $(bun --version) at $(command -v bun)"

info "Installing Claude Code via Bun (global, --trust allows postinstall)..."
bun add -g --trust @anthropic-ai/claude-code

if ! command -v claude >/dev/null 2>&1; then
  fail "claude not found on PATH after install (expected $HOME/.bun/bin/claude). Check PATH includes \$HOME/.bun/bin"
fi
info "claude $(claude --version 2>&1 | head -1) at $(command -v claude)"
claude doctor 2>&1 | head -20 || warn "claude doctor failed (may need credentials in ~/.claude/settings.json)"

mkdir -p "$HOME/.claude"
chmod 700 "$HOME/.claude" 2>/dev/null || true
if [[ ! -f "$HOME/.claude/settings.json" ]]; then
  info "No ~/.claude/settings.json yet. Create it to configure credentials (see README):"
  echo "  vi ~/.claude/settings.json  &&  chmod 600 ~/.claude/settings.json"
else
  info "Found ~/.claude/settings.json (user-level Claude Code config)"
fi

echo ""
echo "Done. Expected paths:"
echo "  $HOME/.bun/bin/bun"
echo "  $HOME/.bun/bin/claude"
echo "  ~/.claude/settings.json  (user-level config, all projects)"
echo ""
echo "Verify:"
echo "  bun --version && claude --version && claude doctor"
echo "  claude -p \"reply OK\"   # tests API once settings.json is configured"
echo ""
echo "Reload shell: source ~/.bashrc  (or source ~/.zshrc)"

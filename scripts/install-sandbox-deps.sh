#!/usr/bin/env bash
set -euo pipefail

info() { echo "[info] $*"; }
warn() { echo "[warn] $*"; }
fail() { echo "[error] $*"; exit 1; }

if [[ "${OSTYPE:-}" != linux* ]] && ! uname -a | grep -qi linux; then
  info "Non-Linux system detected; sandbox deps are Linux-only. Nothing to do."
  exit 0
fi

for req in apt-get dpkg-deb; do
  command -v "$req" >/dev/null 2>&1 || fail "Required tool '$req' not found. This script is Debian/Ubuntu only (needs apt-get + dpkg-deb)."
done

BIN_DIR="${HOME}/.local/bin"
DEPS_PREFIX="${HOME}/.local/opt/workflow-deps"
CACHE_DIR="${HOME}/.cache/workflow-deps"

mkdir -p "$BIN_DIR" "$DEPS_PREFIX" "$CACHE_DIR"
cd "$CACHE_DIR"

rm -f "$CACHE_DIR"/bubblewrap_*.deb "$CACHE_DIR"/socat_*.deb

info "Downloading bubblewrap and socat without sudo (apt-get download)..."
apt-get download bubblewrap socat

info "Extracting into ${DEPS_PREFIX}..."
dpkg-deb -x ./bubblewrap_*.deb "$DEPS_PREFIX"
dpkg-deb -x ./socat_*.deb "$DEPS_PREFIX"

info "Linking bwrap and socat into ${BIN_DIR}..."
ln -sf "$DEPS_PREFIX/usr/bin/bwrap" "$BIN_DIR/bwrap"
ln -sf "$DEPS_PREFIX/usr/bin/socat" "$BIN_DIR/socat"

LINE='export PATH="$HOME/.local/bin:$PATH"'
updated=0
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  if [[ -f "$rc" ]]; then
    if grep -Fq "$LINE" "$rc" 2>/dev/null; then
      info "Already present in $rc"
    else
      printf '\n%s\n' "$LINE" >> "$rc"
      info "Added PATH export to $rc"
      updated=1
    fi
  fi
done

if [[ $updated -eq 0 ]] && [[ ! -f "$HOME/.bashrc" && ! -f "$HOME/.zshrc" ]]; then
  warn "Neither ~/.bashrc nor ~/.zshrc found; adding to ~/.bashrc"
  printf '\n%s\n' "$LINE" >> "$HOME/.bashrc"
  info "Added PATH export to ~/.bashrc"
fi

export PATH="$BIN_DIR:$PATH"
info "PATH updated for current shell."

for bin in bwrap socat; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    fail "$bin not found on PATH after install (expected ${BIN_DIR}/${bin})"
  fi
done

if command -v bwrap >/dev/null 2>&1; then bwrap --version || warn "bwrap --version failed"; fi
if command -v socat >/dev/null 2>&1; then socat -V 2>&1 | head -1 || warn "socat -V failed"; fi

if ! ldd "$(command -v bwrap)" 2>&1 | grep -q "not found"; then
  info "bwrap shared libs: OK"
else
  warn "bwrap has missing shared libs:"
  ldd "$(command -v bwrap)" 2>&1 | grep "not found" || true
fi
if ! ldd "$(command -v socat)" 2>&1 | grep -q "not found"; then
  info "socat shared libs: OK"
else
  warn "socat has missing shared libs:"
  ldd "$(command -v socat)" 2>&1 | grep "not found" || true
fi

echo ""
echo "Done. Expected paths:"
echo "  $BIN_DIR/bwrap"
echo "  $BIN_DIR/socat"
echo "Open a new shell or run: source ~/.bashrc  (or source ~/.zshrc)"

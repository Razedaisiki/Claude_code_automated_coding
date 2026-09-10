#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
DEPS_PREFIX="$HOME/.local/opt/workflow-deps"
CACHE_DIR="$HOME/.cache/workflow-deps"

mkdir -p "$BIN_DIR" "$DEPS_PREFIX" "$CACHE_DIR"
cd "$CACHE_DIR"
rm -f bubblewrap_*.deb socat_*.deb
apt-get download bubblewrap socat
dpkg-deb -x ./bubblewrap_*.deb "$DEPS_PREFIX"
dpkg-deb -x ./socat_*.deb "$DEPS_PREFIX"
ln -sf "$DEPS_PREFIX/usr/bin/bwrap" "$BIN_DIR/bwrap"
ln -sf "$DEPS_PREFIX/usr/bin/socat" "$BIN_DIR/socat"
LINE='export PATH="$HOME/.local/bin:$PATH"'
for rc in ~/.bashrc ~/.zshrc; do [ -f "$rc" ] && grep -Fq "$LINE" "$rc" 2>/dev/null || { [ -f "$rc" ] && printf '\n%s\n' "$LINE" >> "$rc"; }; done
export PATH="$BIN_DIR:$PATH"
command -v bwrap && bwrap --version
command -v socat && socat -V | head -1

# Environment

Environment-specific setup: Bun, Claude Code, and the Linux sandbox (`bwrap`/`socat`). For the remaining project requirements (Python, Git, credentials, Anthropic options, GitHub delivery), see `README.md#Requirements`.

## Claude Code via Bun (user-local, no sudo)

Requires `curl` + `unzip` on Linux. User-local install, no `sudo`.

```bash
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
```

## Linux sandbox dependencies

`bubblewrap` package provides the `bwrap` executable (package `bubblewrap` ≠ executable `bwrap`).

### With sudo — recommended

```bash
sudo apt update && sudo apt install bubblewrap socat
bwrap --version && socat -V
```

### Without sudo — Debian/Ubuntu

Requires `apt-get` / `dpkg-deb`; not universal, administrators may restrict package access.

**One-click install:**

```bash
bash scripts/install-sandbox-deps.sh
```

The script downloads `bubblewrap`/`socat` without `sudo`, extracts into `~/.local/opt/workflow-deps`, symlinks `bwrap`/`socat` into `~/.local/bin`, auto-detects `~/.bashrc`/`~/.zshrc` (writes to whichever exists, `$HOME/.bashrc` if neither), and exports `PATH` for the current shell. Re-running it is idempotent.

Manual steps (equivalent to the script):

```bash
mkdir -p "$HOME/.local/bin" "$HOME/.local/opt/workflow-deps" "$HOME/.cache/workflow-deps"
cd "$HOME/.cache/workflow-deps"
rm -f bubblewrap_*.deb socat_*.deb
apt-get download bubblewrap socat
dpkg-deb -x ./bubblewrap_*.deb "$HOME/.local/opt/workflow-deps"
dpkg-deb -x ./socat_*.deb "$HOME/.local/opt/workflow-deps"
ln -sf "$HOME/.local/opt/workflow-deps/usr/bin/bwrap" "$HOME/.local/bin/bwrap"
ln -sf "$HOME/.local/opt/workflow-deps/usr/bin/socat" "$HOME/.local/bin/socat"
LINE='export PATH="$HOME/.local/bin:$PATH"'
for rc in ~/.bashrc ~/.zshrc; do [ -f "$rc" ] && grep -Fq "$LINE" "$rc" 2>/dev/null || { [ -f "$rc" ] && printf '\n%s\n' "$LINE" >> "$rc"; }; done
export PATH="$HOME/.local/bin:$PATH"
command -v bwrap && command -v socat
bwrap --version && socat -V
```

Expected paths: `~/.local/bin/bwrap`, `~/.local/bin/socat`.

Shared-library check:

```bash
ldd "$(command -v bwrap)" | grep "not found"
ldd "$(command -v socat)" | grep "not found"
```

If nothing is printed, no missing libraries. If any are missing, ask the administrator for runtime libraries — do not manually chase transitive dependencies.

> Installing `bwrap` is not always sufficient. Bubblewrap relies on Linux user namespaces. If the host disables unprivileged user namespaces, a user-local `bwrap` cannot override that policy.

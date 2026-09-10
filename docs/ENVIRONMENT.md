# Environment

Environment-specific setup: Bun, Claude Code, and the Linux sandbox (`bwrap`/`socat`). For the remaining project requirements (Python, Git, credentials, Anthropic options, GitHub delivery), see `README.md#Requirements`.

## Claude Code via Bun (user-local, no sudo)

Requires `curl` + `unzip` on Linux. User-local install, no `sudo`.

**One-click install:**

```bash
bash scripts/install-claude-code.sh
```

Re-running is idempotent — strings together the manual steps below.

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
sudo apt update
```

```bash
sudo apt install bubblewrap socat
```

Verify:

```bash
bwrap --version
```

```bash
socat -V
```

### Without sudo — Debian/Ubuntu

Requires `apt-get` / `dpkg-deb`; not universal, administrators may restrict package access.

**One-click install:**

```bash
bash scripts/install-sandbox-deps.sh
```

The script downloads `bubblewrap`/`socat` without `sudo`, extracts into `~/.local/opt/workflow-deps`, symlinks `bwrap`/`socat` into `~/.local/bin`, auto-detects `~/.bashrc`/`~/.zshrc` (writes to whichever exists, `$HOME/.bashrc` if neither), and exports `PATH` for the current shell. Re-running it is idempotent.

Manual steps (equivalent to the script):

Create local directories:

```bash
mkdir -p "$HOME/.local/bin"
```

```bash
mkdir -p "$HOME/.local/opt/workflow-deps"
```

Create a local download directory:

```bash
mkdir -p "$HOME/.cache/workflow-deps"
```

```bash
cd "$HOME/.cache/workflow-deps"
```

```bash
rm -f "$HOME/.cache/workflow-deps"/bubblewrap_*.deb "$HOME/.cache/workflow-deps"/socat_*.deb
```

Download packages without installing system-wide:

```bash
apt-get download bubblewrap
```

```bash
apt-get download socat
```

Extract into the user-local prefix:

```bash
dpkg-deb -x ./bubblewrap_*.deb "$HOME/.local/opt/workflow-deps"
```

```bash
dpkg-deb -x ./socat_*.deb "$HOME/.local/opt/workflow-deps"
```

Expose executables on `PATH` and make it permanent (auto-detect shell config):

```bash
ln -sf "$HOME/.local/opt/workflow-deps/usr/bin/bwrap" "$HOME/.local/bin/bwrap"
```

```bash
ln -sf "$HOME/.local/opt/workflow-deps/usr/bin/socat" "$HOME/.local/bin/socat"
```

```bash
LINE='export PATH="$HOME/.local/bin:$PATH"'
```

```bash
for rc in ~/.bashrc ~/.zshrc; do [ -f "$rc" ] && grep -Fq "$LINE" "$rc" 2>/dev/null || { [ -f "$rc" ] && printf '\n%s\n' "$LINE" >> "$rc"; }; done
```

Apply immediately without restarting the shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Verify:

```bash
command -v bwrap
```

```bash
command -v socat
```

```bash
bwrap --version
```

```bash
socat -V
```

Expected paths:

```text
/home/<user>/.local/bin/bwrap
/home/<user>/.local/bin/socat
```

Check shared-library dependencies:

```bash
ldd "$(command -v bwrap)"
```

```bash
ldd "$(command -v socat)"
```

```bash
ldd "$(command -v bwrap)" | grep "not found"
```

```bash
ldd "$(command -v socat)" | grep "not found"
```

If nothing is printed by `grep "not found"`, no missing libraries were detected. If libraries are missing, the extracted package alone is insufficient — ask the administrator to provide the runtime libraries or use another compatible environment. Do not attempt manual recursive dependency extraction here.

> Installing `bwrap` is not always sufficient. Bubblewrap relies on Linux user namespaces. If the host or administrator disables unprivileged user namespaces, a user-local `bwrap` cannot override that kernel/security policy.

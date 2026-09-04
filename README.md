# Workflow

Workflow is a deterministic delivery runtime for coding agents.

Coding agents may be stochastic. Workflow owns the deterministic boundaries around their work: workspace lifecycle, Git authority, review, commit, push, CI, correction, checkpointing, and resume.

Current 1.0 ships with:

- **Reasoning provider:** Anthropic
- **Coding backend:** Claude Code
- **Delivery:** local Git and GitHub + GitHub Actions

The architecture is provider/backend-neutral — future providers and backends can be added behind stable contracts — but 1.0 does not include Codex or OpenAI reasoning.

---

## What Workflow does

Workflow turns a written task into one or more independently reviewable delivery units:

```
TASK.md → plan (tasks) → per-task execution → review → commit → push → CI → correction if needed → checkpoint
```

Per-task invariant:

```
Task = Review = Commit = Push = CI boundary
```

Each task either results in **`CHANGED`** (commit) or **`SATISFIED`** (already satisfied, no commit).

---

## Current 1.0 support

| Capability | Implementation |
| --- | --- |
| Reasoning | Anthropic |
| Coding backend | Claude Code |
| Local delivery | Supported |
| GitHub push | Supported |
| GitHub Actions CI | Supported |
| CI correction | Supported |
| Resume / checkpoint | Supported |
| Already-satisfied tasks | Supported |
| Codex backend | Not included in 1.0 |
| OpenAI reasoning provider | Not included in 1.0 |

---

## Requirements

- Python >= 3.8
- Git
- Claude Code >= 2.1.248
- Anthropic credentials
- On Linux: `bubblewrap` (provides `bwrap`) and `socat` for the Claude Code sandbox

For GitHub delivery (`workflow remote gh`) additionally:

- Git remote configured
- GitHub CLI (`gh`) installed
- `gh` authenticated (`gh auth status`)

Credentials may come from `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`, or from `~/.claude/settings.json`.

Optional Anthropic configuration:

- `ANTHROPIC_MODEL` / `ANTHROPIC_DEFAULT_SONNET_MODEL`
- `ANTHROPIC_BASE_URL`

### Linux sandbox dependencies

On Linux, Workflow's Claude Code sandbox requires `bwrap` and `socat`. The Debian/Ubuntu package `bubblewrap` installs the `bwrap` executable (package name `bubblewrap` ≠ executable `bwrap`).

#### With sudo — recommended

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

#### Without sudo — Debian/Ubuntu

This option uses user-local extraction via `apt-get`/`dpkg-deb`. It requires those tools and is not universal; administrators may restrict package access.

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

Expose executables on `PATH`:

```bash
ln -sf "$HOME/.local/opt/workflow-deps/usr/bin/bwrap" "$HOME/.local/bin/bwrap"
```

```bash
ln -sf "$HOME/.local/opt/workflow-deps/usr/bin/socat" "$HOME/.local/bin/socat"
```

```bash
export PATH="$HOME/.local/bin:$PATH"
```

To make this permanent, add the same line to `~/.bashrc` or `~/.zshrc` (do not edit automatically).

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

---

## Installation

From the repository:

```bash
python3 -m pip install .
```

For development:

```bash
python3 -m pip install -e .
```

Verify:

```bash
workflow --version
workflow --help
```

---

## Quick start

```bash
mkdir my-project
```

```bash
cd my-project
```

```bash
git init
```

```bash
workflow init
```

Edit `TASK.md`:

```markdown
# Task

Add validation to the user registration endpoint and preserve existing behavior.
```

Then:

```bash
workflow run
```

Local delivery is the default (`workflow remote local`). If the project has existing changes when `workflow run` starts, they are preserved in a pre-workflow snapshot commit. No `.agent` editing is required.

---

## Task input

- **`TASK.md`** is the primary input. `workflow init` creates it only if it does not already exist.
- **`CLAUDE.md`** is optional repository/project instructions loaded into planning context. Workflow does not create it.

---

## Project files and `.agent`

`.agent/` is Runtime-owned workspace state. It may contain `state.json`, `config.yaml`, `plan.json`, `plan.md`, session/checkpoint state, and `milestones/` — exact contents may evolve. Do not build application logic around undocumented internals.

`workflow init` does **not** copy framework prompts into `.agent/`. Framework prompts remain installed with the `agent_system` package at `agent_system/prompts/**`.

`.agent` is excluded from Git by the Runtime.

Do not edit `.agent/state.json` manually.

---

## `workflow init` semantics

`workflow init` is non-destructive and safe to run again:

- Existing runtime state is not reset.
- Existing `TASK.md` content is preserved.
- Existing user configuration is preserved.
- Workflow may add or migrate required metadata in `.agent/config.yaml` (e.g. `delivery`, `prompt_version`).
- Missing workspace files/directories may be created.
- New `.agent/prompts/` directories are not created.
- Existing legacy `.agent/prompts/` directories are not deleted automatically.

There is no implicit workspace reset command.

---

## Delivery modes

### `workflow remote local` (default)

Workflow commits approved task changes locally. No push, no remote CI wait.

### `workflow remote gh`

Workflow commits approved changes, pushes the Runtime-owned commit, and monitors GitHub Actions for that `commit SHA`. If CI fails with a change-related failure, Workflow enters CI review/correction and may produce a correction task.

Check status:

```bash
workflow remote status
```

---

## Existing working-tree changes

If the project has existing changes when `workflow run` starts, Workflow preserves them in a pre-workflow snapshot commit (`chore: ...`) before beginning agent execution. Dirty changes are not discarded or stashed.

---

## Runtime ownership and Git safety

The coding backend may inspect the repository, edit project files, and run validation. It does **not** own:

- `git add` / `commit` / `push`
- branch changes
- CI orchestration

Workflow validates Git control state around backend execution and fails closed if the backend mutates Runtime-owned Git state.

---

## Architecture

Architecture diagrams are available in [`docs/diagrams/`](docs/diagrams/):

- `system-architecture.png` — overall Workflow architecture
- `task-runtime.png` — TaskRuntime state machine
- `lifecycle.png` — delivery lifecycle
- `commit-ownership.png` — commit ownership
- `run.png` — run flow

---

## Review and task outcomes

- **`CHANGED`** — approved project changes were committed.
- **`SATISFIED`** — repository already satisfied the task; no commit is created for that task.

A Git diff is evidence, not the definition of completion. Empty diff with `SATISFIED` is valid; `SATISFIED` with pending project changes is rejected.

---

## Resume and checkpoints

```bash
workflow resume
```

Resumes from the persisted checkpoint rather than starting a new task. If execution was interrupted while waiting for CI, `workflow resume` continues from that checkpoint without re-running completed work. `resume` does not create a new session.

---

## Command reference

```
workflow init
workflow run
workflow resume
workflow remote local
workflow remote gh
workflow remote status
workflow milestone
workflow milestone --feedback "..."
workflow milestone --path ...
workflow --version
workflow --help
```

All commands are also available as `workflow <cmd> --help` (e.g. `workflow run --help`).

---

## Exit codes

```
0 = successful command / workflow completion
1 = Runtime / workflow operation failure
2 = CLI usage / argparse error
```

```bash
workflow run
echo $?
```

---

## Milestones

```bash
workflow milestone
```

Requires `status == COMPLETED`. Generates a milestone via the current reasoning provider.

```bash
workflow milestone --feedback "human notes"
```

---

## Configuration and environment variables

Workflow-specific:

- `WORKFLOW_MOCK=1` — use deterministic mock provider/backend while preserving the normal Workflow/TaskRuntime path.
- `WORKFLOW_DEBUG_AGENT_TURNS=1` — enable additional Claude Code turn/debug output.

Anthropic (read by current 1.0 implementation):

- `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_MODEL` / `ANTHROPIC_DEFAULT_SONNET_MODEL`
- `ANTHROPIC_BASE_URL`

---

## Release validation

Workflow is validated through deterministic probes and end-to-end delivery demos covering local delivery, Git authority isolation, satisfaction, execution evidence, remote CI, resume, and correction:

- Authority Isolation
- Already Satisfied
- Execution Evidence
- Multi-file Service
- CI Resume
- CI Correction
- CI Bootstrap

---

## Troubleshooting

**`claude` not found** — install Claude Code `>= 2.1.248` and ensure `claude` is on `PATH`.

**Linux sandbox dependencies missing** — install `bubblewrap` (`bwrap`) and `socat` (see Linux sandbox dependencies).

**Anthropic API key not configured** — set `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` (or provide via `~/.claude/settings.json`).

**Workspace is not a Git root** — run `workflow` from the repository root with a valid `.git`.

**GitHub mode cannot monitor CI** — check `gh auth status` and `workflow remote status`; verify the repo has a configured remote.

---

## Current limitations

- 1.0 is prompt-driven and benefits from a clear, testable `TASK.md`.
- GitHub delivery requires GitHub Actions CI to be configured for correction to be observable.
- Milestone generation requires a completed workflow.

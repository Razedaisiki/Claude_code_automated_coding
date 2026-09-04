# Workflow 1.0.0

Workflow is a deterministic delivery runtime for coding agents.

Coding agents may be stochastic. Workflow owns the deterministic delivery boundaries around their work: workspace lifecycle, Git authority, review, commit, push, CI, correction, checkpointing, and resume.

## Included in 1.0

- Anthropic reasoning provider
- Claude Code coding backend
- deterministic Runtime-owned Git authority
- task-level review / commit / push / CI boundaries
- local Git delivery
- GitHub push and GitHub Actions CI delivery
- CI failure review and correction lifecycle
- checkpoint and resume
- already-satisfied task handling
- execution evidence
- non-destructive workspace initialization
- milestone generation
- provider/backend extensibility contracts

## 1.0 support

| Capability | Implementation |
| --- | --- |
| Reasoning | Anthropic |
| Coding backend | Claude Code |
| Local delivery | Supported |
| GitHub delivery | Supported |
| GitHub Actions CI | Supported |
| CI correction | Supported |
| Resume / checkpoint | Supported |
| Already-satisfied tasks | Supported |
| Codex backend | Not included in 1.0 |
| OpenAI reasoning provider | Not included in 1.0 |

## Release validation

Workflow 1.0 was validated with:

- Authority Isolation
- Already Satisfied
- Execution Evidence
- Multi-file Service
- CI Resume
- CI Correction
- CI Bootstrap

Additional release gates validated:

- non-destructive `workflow init`
- CLI exit-code contract
- clean wheel installation
- packaged framework prompts
- installed-product mock workflow
- README quick-start flow

## CLI contract

- `0` — successful command / workflow completion
- `1` — Runtime / workflow failure
- `2` — CLI usage error

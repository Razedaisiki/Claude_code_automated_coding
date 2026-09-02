import json
import re
import shlex
import sys

SAFE_GIT = {
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "ls-files",
    "grep",
    "blame",
    "cat-file",
    "show-ref",
    "for-each-ref",
    "name-rev",
    "describe",
    "shortlog",
}


def deny(reason: str):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _strip_env_prefix(tokens):
    out = list(tokens)
    while out and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", out[0]):
        out = out[1:]
    return out


def _extract_git_subcommand(tokens):
    try:
        i = tokens.index("git")
    except ValueError:
        return None, None
    args = tokens[i + 1:]
    while args and args[0].startswith("-"):
        if args[0] == "-C" and len(args) >= 2:
            args = args[2:]
        else:
            args = args[1:]
    if not args:
        return None, args
    return args[0], args[1:]


def check_tokens(tokens):
    tokens = _strip_env_prefix(tokens)
    if not tokens:
        return
    if tokens[0] in ("sudo", "env", "command"):
        tokens = _strip_env_prefix(tokens[1:])
    if tokens[:2] == ["bash", "-c"] or tokens[:2] == ["sh", "-c"]:
        raw = tokens[2] if len(tokens) > 2 else ""
        for part in re.split(r"&&|\|\||;|\n", raw):
            part = part.strip()
            if not part:
                continue
            try:
                inner = shlex.split(part)
            except ValueError:
                deny("Unable to safely parse shell command.")
            if "git" in inner:
                sub, _ = _extract_git_subcommand(inner)
                if sub is None:
                    deny("Git invocation without subcommand is not allowed.")
                if sub not in SAFE_GIT:
                    deny(f"Git mutation is Runtime-owned. CodeAgent may not run `git {sub}`.")
        return
    if "git" not in tokens:
        return
    for tok in tokens:
        if "git" in tok and tok != "git":
            deny("Git mutation is Runtime-owned.")
    sub, _ = _extract_git_subcommand(tokens)
    if sub is None:
        deny("Git invocation without subcommand is not allowed.")
    if sub not in SAFE_GIT:
        deny(f"Git mutation is Runtime-owned. CodeAgent may not run `git {sub}`.")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if payload.get("tool_name") != "Bash":
        return
    command = payload.get("tool_input", {}).get("command", "") or ""
    normalized = command.replace("&&", ";").replace("||", ";")
    for part in re.split(r";|\n", normalized):
        part = part.strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part)
        except ValueError:
            deny("Unable to safely parse shell command.")
        if not tokens:
            continue
        check_tokens(tokens)


if __name__ == "__main__":
    main()

import json
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


def check_git(tokens):
    try:
        i = tokens.index("git")
    except ValueError:
        return
    args = tokens[i + 1:]
    while args and args[0].startswith("-"):
        if args[0] == "-C" and len(args) >= 2:
            args = args[2:]
        else:
            args = args[1:]
    if not args:
        return
    sub = args[0]
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
    for part in command.replace("&&", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part)
        except ValueError:
            deny("Unable to safely parse shell command.")
        if not tokens:
            continue
        if "git" in tokens:
            check_git(tokens)


if __name__ == "__main__":
    main()

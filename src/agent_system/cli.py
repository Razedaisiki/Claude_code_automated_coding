import argparse
import sys

from agent_system import __version__


def cmd_init(args):
    from agent_system.init import init_workspace

    agent_dir = init_workspace()
    print(f"Initialized workspace at {agent_dir}")


def cmd_run(args):
    from agent_system.supervisor.supervisor import Supervisor
    from agent_system.supervisor.state import StateManager

    sup = Supervisor()
    result = sup.start()
    state = StateManager().load()
    status = state.get("status", "")
    if status == "FAILED":
        sys.exit(1)
    if isinstance(result, object) and getattr(result, "status", None) == "FAILED":
        sys.exit(1)


def cmd_resume(args):
    from agent_system.supervisor.supervisor import Supervisor
    from agent_system.supervisor.state import StateManager

    sup = Supervisor()
    result = sup.resume()
    state = StateManager().load()
    status = state.get("status", "")
    if status == "FAILED":
        sys.exit(1)
    if isinstance(result, object) and getattr(result, "status", None) == "FAILED":
        sys.exit(1)


def cmd_remote(args):
    from pathlib import Path

    from agent_system.delivery import DeliveryConfig
    from agent_system.runtime.git import Git
    from agent_system.runtime.github import GitHub

    root = Path.cwd()
    action = getattr(args, "remote_action", None)
    if action == "status":
        cfg = DeliveryConfig.load(root)
        git = Git(root)
        gh = GitHub(root)
        print(f"Delivery mode: {cfg.mode}")
        print("")
        print("Git:")
        print(f"  repository: {'ready' if git.has_commits() else 'not initialized'}")
        print(f"  remote: {'detected' if git.has_remote() else 'not configured'}")
        if git.has_remote():
            print(f"  url: {git.remote_url()}")
        print(f"  push: user managed")
        print("")
        print("GitHub:")
        has_gh = gh._has_gh()
        print(f"  gh CLI: {'available' if has_gh else 'not found'}")
        if has_gh:
            r = gh.shell.run("gh auth status 2>&1 | head -5")
            authed = "authenticated" if "Logged in" in r.stdout or "active" in r.stdout else "not authenticated"
            print(f"  authentication: {authed}")
        else:
            print("  authentication: unknown")
        return
    if action in ("local", "gh"):
        cfg = DeliveryConfig(mode=action)
        cfg.save(root)
        print(f"Delivery mode set to: {action}")
        return
    print("Usage: workflow remote <local|gh|status>")


def cmd_milestone(args):
    from pathlib import Path

    from agent_system.supervisor.state import StateManager

    root = Path.cwd()
    if hasattr(args, "path") and args.path:
        root = Path(args.path)
    state = StateManager(root).load()
    if state.get("status") != "COMPLETED":
        print(f"Cannot create milestone. Current state: {state.get('status', 'UNKNOWN')}")
        sys.exit(1)
    feedback = getattr(args, "feedback", None)
    print("Generating milestone...")
    from agent_system.composition import build_default_workflow

    wf = build_default_workflow(root=root)
    if hasattr(wf, "tech_lead") and hasattr(wf.tech_lead, "create_milestone"):
        result = wf.tech_lead.create_milestone(feedback=feedback)
    elif hasattr(wf, "create_milestone"):
        result = wf.create_milestone(feedback=feedback)
    else:
        result = None
    if result:
        print(f"Milestone created: {result}")
    else:
        print("Milestone generation not yet implemented")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(prog="workflow", description="agent-system cli")
    parser.add_argument("--version", action="store_true", help="show version")

    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="initialize workspace")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="run workflow")
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume", help="resume workflow")
    p_resume.set_defaults(func=cmd_resume)

    p_remote = sub.add_parser("remote", help="set delivery mode")
    p_remote.add_argument("remote_action", nargs="?", choices=["local", "gh", "status"], help="delivery mode")
    p_remote.set_defaults(func=cmd_remote)

    p_milestone = sub.add_parser("milestone", help="create milestone from completed workflow")
    p_milestone.add_argument("--feedback", type=str, default=None, help="human feedback for milestone")
    p_milestone.add_argument("--path", type=str, default=None, help="workspace path")
    p_milestone.set_defaults(func=cmd_milestone)

    args = parser.parse_args()

    if args.version:
        print(f"workflow {__version__}")
        return

    if hasattr(args, "func"):
        args.func(args)
        return

    parser.print_help()

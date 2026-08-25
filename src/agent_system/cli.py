import argparse

from agent_system import __version__


def cmd_init(args):
    from agent_system.init import init_workspace

    agent_dir = init_workspace()
    print(f"Initialized workspace at {agent_dir}")


def cmd_run(args):
    from agent_system.supervisor.supervisor import Supervisor

    sup = Supervisor()
    sup.start()


def cmd_resume(args):
    from agent_system.supervisor.supervisor import Supervisor

    sup = Supervisor()
    sup.resume()


def cmd_milestone(args):
    from pathlib import Path

    from agent_system.supervisor.state import StateManager

    root = Path.cwd()
    if hasattr(args, "path") and args.path:
        root = Path(args.path)
    state = StateManager(root).load()
    if state.get("status") != "COMPLETED":
        print(f"Cannot create milestone. Current state: {state.get('status', 'UNKNOWN')}")
        return
    feedback = getattr(args, "feedback", None)
    print("Generating milestone...")
    from agent_system.agents.claude_parent import ClaudeParentAgent

    parent = ClaudeParentAgent(root=root)
    result = parent.create_milestone(feedback=feedback) if hasattr(parent, "create_milestone") else None
    if result:
        print(f"Milestone created: {result}")
    else:
        print("Milestone generation not yet implemented")


def main():
    parser = argparse.ArgumentParser(prog="xxx", description="agent-system cli")
    parser.add_argument("--version", action="store_true", help="show version")

    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="initialize workspace")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="run workflow")
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume", help="resume workflow")
    p_resume.set_defaults(func=cmd_resume)

    p_milestone = sub.add_parser("milestone", help="create milestone from completed workflow")
    p_milestone.add_argument("--feedback", type=str, default=None, help="human feedback for milestone")
    p_milestone.add_argument("--path", type=str, default=None, help="workspace path")
    p_milestone.set_defaults(func=cmd_milestone)

    args = parser.parse_args()

    if args.version:
        print(f"agent-system {__version__}")
        return

    if hasattr(args, "func"):
        args.func(args)
        return

    parser.print_help()

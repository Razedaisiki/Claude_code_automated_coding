import argparse
import sys

from agent_system import __version__


def main():
    parser = argparse.ArgumentParser(prog="xxx", description="agent-system cli")
    parser.add_argument("--version", action="store_true", help="show version")
    args = parser.parse_args()

    if args.version:
        print(f"agent-system {__version__}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()

"""ccli-prompt CLI entry point."""
from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ccli-prompt",
        description="Inline Claude prompt for your zsh terminal (Cmd+K style).",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("install",   help="Install the shell widget, wire ~/.zshrc, run auth + model wizard")
    sub.add_parser("configure", help="Re-run the auth + model wizard (no install side-effects)")
    sub.add_parser("uninstall", help="Remove the widget and ~/.ccli-prompt config")
    sub.add_parser("daemon",    help="Run the daemon (normally invoked by the widget on demand)")

    args = parser.parse_args(argv)

    if args.command == "install":
        from .install import install
        return install()
    if args.command == "configure":
        from . import wizard
        wizard.run()
        return 0
    if args.command == "uninstall":
        from .install import uninstall
        return uninstall()
    if args.command == "daemon":
        import asyncio
        from .daemon import main as daemon_main
        try:
            asyncio.run(daemon_main())
        except KeyboardInterrupt:
            pass
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

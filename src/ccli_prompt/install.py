"""Install / uninstall the shell widget, wire ~/.zshrc, run the wizard."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from . import wizard
from .wizard import bold, dim, ok, warn

INSTALL_DIR = Path.home() / ".ccli-prompt"
ZSHRC = Path(os.environ.get("ZDOTDIR", str(Path.home()))) / ".zshrc"
SOCKET_PATH = Path(f"/tmp/ccli-{os.environ.get('USER', 'user')}.sock")
SOURCE_LINE = f'source "{INSTALL_DIR}/ccli.zsh"'
MARKER = "# ccli-prompt"


def _require(cmd: str, hint: str = "") -> bool:
    if shutil.which(cmd) is None:
        warn(f"missing required command: {cmd}" + (f" — {hint}" if hint else ""))
        return False
    return True


def _kill_daemon() -> None:
    subprocess.run(["pkill", "-f", "ccli_prompt.daemon"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if SOCKET_PATH.exists():
        try:
            SOCKET_PATH.unlink()
        except OSError:
            pass


def _copy_package_data() -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    pkg = files("ccli_prompt") / "data"
    for name in ("ccli.zsh", "prompt.md"):
        content = (pkg / name).read_text(encoding="utf-8")
        (INSTALL_DIR / name).write_text(content, encoding="utf-8")
    ok(f"files written to {INSTALL_DIR}")


def _wire_zshrc() -> None:
    if not ZSHRC.exists():
        ZSHRC.write_text("")
    content = ZSHRC.read_text()
    if SOURCE_LINE in content:
        ok(f"{ZSHRC} already sources ccli.zsh")
        return
    with ZSHRC.open("a") as f:
        f.write(f"\n{MARKER}\n{SOURCE_LINE}\n")
    ok(f"added source line to {ZSHRC}")


def _unwire_zshrc() -> None:
    if not ZSHRC.exists():
        return
    lines = ZSHRC.read_text().splitlines()
    cleaned: list[str] = []
    skip = False
    for line in lines:
        if line.strip() == MARKER:
            skip = True
            continue
        if skip and ".ccli-prompt/ccli.zsh" in line:
            skip = False
            continue
        cleaned.append(line)
    ZSHRC.write_text("\n".join(cleaned) + ("\n" if cleaned else ""))
    ok(f"removed ccli-prompt lines from {ZSHRC}")


def install() -> int:
    missing = [c for c in ("python3", "nc", "zsh") if shutil.which(c) is None]
    if missing:
        warn(f"missing required commands: {', '.join(missing)}")
        if sys.platform == "darwin":
            warn(f"  install via: brew install {' '.join(missing)}")
        else:
            warn(f"  install via your package manager (e.g. apt install {' '.join(missing)})")
        return 1

    _copy_package_data()
    _kill_daemon()
    _wire_zshrc()
    wizard.run()

    print()
    print(bold("Next steps"))
    print(f"  1. Reload your shell:  source {ZSHRC}   (or open a new terminal tab)")
    print(f"  2. Trigger the prompt: press Esc then K  (or remap Cmd/Ctrl+K — see README)")
    print(f"  3. Type your request, Enter. The generated command lands on your command line —")
    print(f"     nothing runs until you press Enter to confirm.")
    return 0


def uninstall() -> int:
    _kill_daemon()
    _unwire_zshrc()
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
        ok(f"removed {INSTALL_DIR}")
    print("Reload your shell to finish.")
    return 0

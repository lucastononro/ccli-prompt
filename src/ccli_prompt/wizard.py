"""Interactive wizard: auth picker + model picker."""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

INSTALL_DIR = Path.home() / ".ccli-prompt"
CONFIG_FILE = INSTALL_DIR / "config"
CREDS_FILE = Path.home() / ".claude" / ".credentials.json"

# --- tiny ANSI helpers ------------------------------------------------------

_TTY = sys.stdout.isatty()
def _w(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s

def bold(s): return _w("1", s)
def dim(s):  return _w("2", s)
def ok(msg):   print(f"{_w('32', '✓')} {msg}")
def warn(msg): print(f"{_w('33', '⚠')}  {msg}")


# --- config read/write ------------------------------------------------------

def read_config() -> dict[str, str]:
    if not CONFIG_FILE.exists():
        return {}
    result: dict[str, str] = {}
    try:
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return result


def write_config(updates: dict[str, str]) -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    current = read_config()
    current.update({k: v for k, v in updates.items() if v})
    header = (
        "# ccli-prompt config — do not commit this file.\n"
        "# KEY=value pairs below are loaded as env vars by daemon.py on startup.\n"
    )
    body = "\n".join(f"{k}={v}" for k, v in current.items())
    CONFIG_FILE.write_text(header + body + "\n")
    os.chmod(CONFIG_FILE, 0o600)


# --- auth discovery ---------------------------------------------------------

def _keychain_json() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=3,
        )
    except FileNotFoundError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def detect_auth() -> tuple[str, str]:
    """Returns (source, human-readable description)."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CCLI_API_KEY"):
        return "env", "env var (ANTHROPIC_API_KEY)"
    cfg = read_config()
    if cfg.get("ANTHROPIC_API_KEY") or cfg.get("CCLI_API_KEY"):
        return "config", str(CONFIG_FILE)
    if _keychain_json() is not None:
        return "keychain", "macOS Keychain (Claude Code subscription)"
    if CREDS_FILE.exists():
        return "creds", str(CREDS_FILE)
    return "none", "none detected"


def get_active_key() -> str | None:
    for var in ("ANTHROPIC_API_KEY", "CCLI_API_KEY"):
        if v := os.environ.get(var):
            return v.strip()
    cfg = read_config()
    for var in ("ANTHROPIC_API_KEY", "CCLI_API_KEY"):
        if v := cfg.get(var):
            return v.strip()
    if kc := _keychain_json():
        try:
            return json.loads(kc)["claudeAiOauth"]["accessToken"]
        except (json.JSONDecodeError, KeyError):
            pass
    if CREDS_FILE.exists():
        try:
            return json.loads(CREDS_FILE.read_text())["claudeAiOauth"]["accessToken"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return None


# --- model listing ----------------------------------------------------------

def list_models(token: str) -> list[dict]:
    headers = {"anthropic-version": "2023-06-01"}
    if token.startswith("sk-ant-oat"):
        headers["Authorization"] = f"Bearer {token}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
    else:
        headers["x-api-key"] = token
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models?limit=100", headers=headers
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    models = data.get("data", [])
    is_haiku = lambda m: m.get("id", "").startswith("claude-haiku-4-5")
    default_first = sorted((m for m in models if is_haiku(m)),
                           key=lambda m: m.get("id", ""), reverse=True)
    others = sorted((m for m in models if not is_haiku(m)),
                    key=lambda m: m.get("id", ""), reverse=True)
    return default_first + others


# --- wizard steps -----------------------------------------------------------

def _auth_step() -> None:
    print()
    print(bold("Authentication"))
    source, desc = detect_auth()
    print(dim(f"Detected: {desc}"))
    print()

    first = {
        "keychain": "Auto-discover from Claude Code subscription [recommended]",
        "creds":    "Auto-discover from Claude Code subscription [recommended]",
        "env":      "Keep currently configured credentials [recommended]",
        "config":   "Keep currently configured credentials [recommended]",
        "none":     "Auto-discover from Claude Code subscription (not detected — will fail)",
    }[source]
    print(f"  1) {first}")
    print(f"  2) Enter an Anthropic API key or Claude Code OAuth token (sk-ant-...)")
    print(f"  3) Skip (configure later via ANTHROPIC_API_KEY env var or {CONFIG_FILE})")
    print()

    if not sys.stdin.isatty():
        print(dim("(non-interactive install, skipping wizard)"))
        return

    try:
        choice = input("Choose [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice == "1":
        if source == "none":
            warn("nothing to auto-discover. Install Claude Code (https://claude.com/claude-code)")
            warn("and run `claude`, or re-run `ccli-prompt configure` and pick option 2.")
        else:
            ok(f"using {desc}")
    elif choice == "2":
        print()
        print(dim("Accepts: sk-ant-api03-... (API key)  or  sk-ant-oat01-... (OAuth token)"))
        try:
            key = getpass.getpass("API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not key:
            warn("empty input, skipping")
            return
        if not key.startswith("sk-ant-"):
            warn("doesn't look like an Anthropic key (expected prefix sk-ant-...). Saving anyway.")
        write_config({"ANTHROPIC_API_KEY": key})
        ok(f"saved API key to {CONFIG_FILE} (mode 600)")
    elif choice == "3":
        print(dim("skipping — daemon will error on first call until credentials exist"))
    else:
        warn("invalid choice, skipping")


def _model_step() -> None:
    key = get_active_key()
    if not key:
        print(dim("(no credentials yet — skipping model selection)"))
        return

    print()
    print(bold("Model"))
    current = read_config().get("CCLI_MODEL", "claude-haiku-4-5")
    print(dim("fetching available models from /v1/models..."))

    try:
        models = list_models(key)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        warn(f"couldn't fetch models: HTTP {e.code}: {body}")
        warn(f"keeping {current} — you can change it later with `ccli-prompt configure`.")
        write_config({"CCLI_MODEL": current})
        return
    except Exception as e:
        warn(f"couldn't fetch models: {type(e).__name__}: {e}")
        warn(f"keeping {current}.")
        write_config({"CCLI_MODEL": current})
        return

    ids: list[str] = []
    saw_default = False
    for i, m in enumerate(models, 1):
        mid = m.get("id", "")
        name = m.get("display_name", mid)
        ids.append(mid)
        markers: list[str] = []
        if mid == current:
            markers.append("current")
        if not saw_default and mid.startswith("claude-haiku-4-5"):
            markers.append("default")
            saw_default = True
        marker = f" [{'/'.join(markers)}]" if markers else ""
        print(f"  {i:2d}) {mid:<30} {name}{marker}")

    if not sys.stdin.isatty():
        print(dim(f"(non-interactive — keeping {current})"))
        write_config({"CCLI_MODEL": current})
        return

    print()
    try:
        choice = input(f"Choose [keep {current}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        write_config({"CCLI_MODEL": current})
        return

    if not choice:
        write_config({"CCLI_MODEL": current})
        ok(f"model: {current}")
        return
    if not choice.isdigit() or not (1 <= int(choice) <= len(ids)):
        warn(f"invalid choice, keeping {current}")
        write_config({"CCLI_MODEL": current})
        return
    chosen = ids[int(choice) - 1]
    write_config({"CCLI_MODEL": chosen})
    ok(f"model: {chosen}")


def run() -> None:
    _auth_step()
    _model_step()

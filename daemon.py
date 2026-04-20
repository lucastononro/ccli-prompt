#!/usr/bin/env python3
"""ccli-prompt daemon — persistent HTTPS client to the Anthropic API.

Avoids `claude -p` subprocess boot on every keystroke. Auth reuses the OAuth
access token that Claude Code stores in the macOS Keychain under
"Claude Code-credentials", so no separate API key is needed.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
USER = os.environ.get("USER", "user")

API_HOST = "api.anthropic.com"
API_PATH = "/v1/messages"
KEYCHAIN_SERVICE = "Claude Code-credentials"
CREDS_FILE = Path.home() / ".claude" / ".credentials.json"
CCLI_CONFIG = Path(os.environ.get("CCLI_CONFIG", Path.home() / ".ccli-prompt" / "config"))


def _load_config_env() -> None:
    """Load KEY=value pairs from CCLI_CONFIG into os.environ without overriding
    values that the user already set in their shell environment."""
    if not CCLI_CONFIG.exists():
        return
    try:
        for line in CCLI_CONFIG.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


_load_config_env()

SOCKET_PATH = os.environ.get("CCLI_SOCKET", f"/tmp/ccli-{USER}.sock")
PROMPT_FILE = Path(os.environ.get("CCLI_SYSTEM_PROMPT_FILE", SCRIPT_DIR / "prompt.md"))
MODEL = os.environ.get("CCLI_MODEL", "claude-haiku-4-5")
MAX_TOKENS = int(os.environ.get("CCLI_MAX_TOKENS", "400"))
IDLE_TIMEOUT = int(os.environ.get("CCLI_IDLE_TIMEOUT", "1800"))  # 30 minutes


class TokenError(RuntimeError):
    pass


def _token_from_keychain() -> str | None:
    """macOS only. Returns None if unavailable."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=True, timeout=3,
        )
        data = json.loads(result.stdout.strip())
        return data["claudeAiOauth"]["accessToken"]
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        return None


def _token_from_creds_file() -> str | None:
    """Linux (and macOS fallback). Reads ~/.claude/.credentials.json."""
    if not CREDS_FILE.exists():
        return None
    try:
        data = json.loads(CREDS_FILE.read_text())
        return data["claudeAiOauth"]["accessToken"]
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def load_token() -> str:
    """Find a Claude API credential. Accepts either a normal API key
    (`sk-ant-api*`) or a Claude Code OAuth access token (`sk-ant-oat*`).

    Priority (config file values are pre-loaded into os.environ at import time):
      1. $ANTHROPIC_API_KEY / $CCLI_API_KEY  (shell env or config file)
      2. macOS Keychain ("Claude Code-credentials") — mac only
      3. ~/.claude/.credentials.json — Linux default, macOS fallback
    """
    for var in ("ANTHROPIC_API_KEY", "CCLI_API_KEY"):
        if tok := os.environ.get(var):
            return tok.strip()

    if tok := _token_from_keychain():
        return tok

    if tok := _token_from_creds_file():
        return tok

    raise TokenError(
        "no API key found. Either:\n"
        "  - export ANTHROPIC_API_KEY=sk-ant-... in your shell profile, or\n"
        "  - run `claude` (and log in) / `claude setup-token` to populate credentials."
    )


def is_oauth_token(token: str) -> bool:
    """Claude Code OAuth access tokens start with `sk-ant-oat`."""
    return token.startswith("sk-ant-oat")


class Client:
    """Holds a warm HTTPS connection + the OAuth token."""

    def __init__(self) -> None:
        self._ssl_ctx = ssl.create_default_context()
        self._conn: http.client.HTTPSConnection | None = None
        self._token = load_token()
        self._system_prompt = PROMPT_FILE.read_text() if PROMPT_FILE.exists() else ""

    def _conn_get(self) -> http.client.HTTPSConnection:
        if self._conn is None:
            self._conn = http.client.HTTPSConnection(API_HOST, context=self._ssl_ctx, timeout=30)
        return self._conn

    def _conn_reset(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def _build_body(self, user_prompt: str) -> bytes:
        payload: dict = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if self._system_prompt:
            payload["system"] = [{
                "type": "text",
                "text": self._system_prompt,
                "cache_control": {"type": "ephemeral"},
            }]
        return json.dumps(payload).encode("utf-8")

    def _headers(self) -> dict[str, str]:
        headers = {
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "User-Agent": "ccli-prompt/0.2",
        }
        if is_oauth_token(self._token):
            headers["Authorization"] = f"Bearer {self._token}"
            headers["anthropic-beta"] = "oauth-2025-04-20"
        else:
            headers["x-api-key"] = self._token
        return headers

    def ask(self, user_prompt: str) -> str:
        body = self._build_body(user_prompt)

        for attempt in range(2):
            try:
                conn = self._conn_get()
                conn.request("POST", API_PATH, body=body, headers=self._headers())
                resp = conn.getresponse()
                raw = resp.read()
                status = resp.status
            except (http.client.HTTPException, OSError) as e:
                self._conn_reset()
                if attempt == 0:
                    continue
                return f"# connection error: {e}"

            if status == 200:
                try:
                    data = json.loads(raw)
                    return "".join(
                        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
                    )
                except json.JSONDecodeError:
                    return "# invalid JSON in API response"

            if status == 401 and attempt == 0:
                # Token may have rotated — reload and retry once.
                try:
                    self._token = load_token()
                except TokenError as e:
                    return f"# auth error: {e}"
                self._conn_reset()
                continue

            # Non-200, non-recoverable
            detail = raw.decode("utf-8", errors="replace")[:300]
            return f"# api error {status}: {detail}"

        return "# request failed"


def parse_request(blob: str) -> tuple[str, str]:
    """Parse tab-separated 'key<TAB>value' lines. Returns (user_prompt, raw_query)."""
    fields: dict[str, str] = {}
    for line in blob.split("\n"):
        if "\t" not in line:
            continue
        k, v = line.split("\t", 1)
        fields[k.strip()] = v

    query = fields.get("query", "").strip()
    parts: list[str] = []
    if cwd := fields.get("cwd"):
        parts.append(f"Working directory: {cwd}")
    if shell := fields.get("shell"):
        parts.append(f"Shell: {shell}")
    if os_info := fields.get("os"):
        parts.append(f"OS: {os_info}")
    if draft := fields.get("draft"):
        parts.append(f"Current command line draft: {draft}")

    return "\n".join(parts) + f"\n\nRequest: {query}", query


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                        client: Client, state: dict) -> None:
    try:
        raw = await asyncio.wait_for(reader.read(65536), timeout=5)
        if not raw:
            return
        state["last_active"] = time.monotonic()

        user_prompt, query = parse_request(raw.decode("utf-8", errors="replace"))
        if not query:
            writer.write(b"# empty query")
            await writer.drain()
            return

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, client.ask, user_prompt)
        writer.write(response.encode("utf-8"))
        await writer.drain()
        state["last_active"] = time.monotonic()
    except Exception as e:
        try:
            writer.write(f"# daemon error: {e}".encode("utf-8"))
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def idle_watcher(state: dict, server: asyncio.base_events.Server) -> None:
    while True:
        await asyncio.sleep(60)
        idle = time.monotonic() - state["last_active"]
        if idle > IDLE_TIMEOUT:
            server.close()
            return


async def main() -> None:
    try:
        client = Client()
    except TokenError as e:
        print(f"ccli-daemon: {e}", file=sys.stderr)
        sys.exit(1)

    # If a stale socket exists, only unlink if no one is listening on it.
    if os.path.exists(SOCKET_PATH):
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            probe.connect(SOCKET_PATH)
            probe.close()
            print(f"ccli-daemon: another daemon is already running on {SOCKET_PATH}", file=sys.stderr)
            sys.exit(0)
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass
        finally:
            try:
                probe.close()
            except Exception:
                pass

    state = {"last_active": time.monotonic()}
    server = await asyncio.start_unix_server(
        lambda r, w: handle_client(r, w, client, state),
        path=SOCKET_PATH,
    )
    os.chmod(SOCKET_PATH, 0o600)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, server.close)

    asyncio.create_task(idle_watcher(state, server))

    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

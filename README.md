# ccli-prompt

Cursor's **Cmd+K** inline prompt, but for your zsh terminal.

Press **Cmd+K** at your prompt, type what you want ("kill port 9090", "find files bigger than 100mb", "tar this folder"), hit Enter, and a ready-to-run shell command lands on your command line. Review it, Enter to run it.

Fast because there's no `claude` CLI boot per call: a tiny Python daemon runs in the background, holds one open HTTPS connection to `api.anthropic.com`, and reuses the OAuth token you already have from Claude Code. Typical latency: ~500 ms on warm connection + Haiku inference.

## Install

```sh
git clone https://github.com/<you>/ccli-prompt.git
cd ccli-prompt
./install.sh
```

Requirements: macOS, zsh, Python 3, and Claude Code logged in (so the OAuth token is in your Keychain). If you haven't: `claude setup-token` or just run `claude` once and sign in.

## Trigger it

Two ways — pick either:

**Esc then K** — works immediately, no terminal config. Press `Esc`, then `K` (sequence, not together).

**Cmd+K** — map Cmd+K → `ESC+k` in your terminal:

| Terminal      | How                                                                                                  |
| ------------- | ---------------------------------------------------------------------------------------------------- |
| iTerm2        | Settings → Profiles → Keys → `+` · Shortcut `⌘K` · Action *Send Escape Sequence* · Esc+: `k`         |
| Terminal.app  | Settings → Profiles → Keyboard → `+` · `⌘` + `K` · *Send Text* · in field: `Ctrl+V` then `Esc`, then `k` |
| Ghostty       | `keybind = cmd+k=text:\x1bk` in `~/.config/ghostty/config`                                           |
| Warp          | Settings → Keyboard → bind `⌘K` → *Send Text* · `\x1bk`                                              |

## How it works

- **`ccli.zsh`** — a ZLE widget bound to `^[k` (what Esc+K and the remapped Cmd+K both send). It reads your query via `read-from-minibuffer`, sends it over a Unix socket to the daemon, and places the response into `$BUFFER` so nothing runs until you press Enter.
- **`daemon.py`** — stdlib-only Python 3 server. On first use the zsh widget spawns it in the background. It:
  - reads the Claude Code OAuth token from the macOS Keychain (`security find-generic-password -s "Claude Code-credentials"`),
  - opens one `HTTPSConnection` to `api.anthropic.com` and keeps it warm,
  - POSTs to `/v1/messages` with `Authorization: Bearer <oat>` + `anthropic-beta: oauth-2025-04-20`,
  - enables prompt caching on the system prompt so repeat calls skip re-processing,
  - exits after 30 minutes of idleness.
- **`prompt.md`** — system prompt. Edit to change behavior (raw commands vs. commentary, etc.).

## Why not claude-agent-sdk or `claude -p`?

Both spawn the `claude` Node binary per call — ~1–2 s cold boot before any inference starts. Calling the Anthropic API directly with the same OAuth bearer token Claude Code uses skips that entirely. The tradeoff: no tool-use loop and no file access from the model — which is exactly what you want for inline command generation. You review the output before anything runs.

## Safety

- The daemon's socket is `0600`, scoped to your user.
- The model only generates text — it can't execute anything. The command shows up on your prompt; you decide whether to run it.
- The system prompt in `prompt.md` explicitly refuses destructive commands (`rm -rf /`, `dd`, disk formatting, force-push to main, etc.) unless you ask for them in those exact terms.

## Configure

Export these in `~/.zshrc` before the `source` line:

```sh
export CCLI_MODEL="claude-haiku-4-5"        # any Claude model id
export CCLI_MAX_TOKENS=400                  # response cap
export CCLI_IDLE_TIMEOUT=1800               # daemon exits after N idle seconds
export CCLI_SYSTEM_PROMPT_FILE="$HOME/.ccli-prompt/prompt.md"
```

## Manual daemon control

```sh
# status
[[ -S /tmp/ccli-$USER.sock ]] && echo "running" || echo "not running"

# stop
pkill -f "python3 .*ccli-prompt/daemon.py"; rm -f /tmp/ccli-$USER.sock

# start (normally auto-starts on first Cmd+K)
python3 ~/.ccli-prompt/daemon.py &
```

## Uninstall

```sh
./uninstall.sh
```

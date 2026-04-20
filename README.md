# ccli-prompt

Cursor's **Cmd+K** inline prompt, but for your zsh terminal.

Press **Cmd+K** at your prompt, type what you want ("kill port 9090", "find files bigger than 100mb", "tar this folder"), hit Enter, and a ready-to-run shell command lands on your command line. Review it, Enter to run it.

Fast because there's no `claude` CLI boot per call: a tiny Python daemon runs in the background, holds one open HTTPS connection to `api.anthropic.com`, and reuses the OAuth token you already have from Claude Code (or a plain Anthropic API key). Typical latency: ~500 ms on warm connection + Haiku inference.

## Install

```sh
pipx install ccli-prompt      # recommended (isolated venv)
# or: pip install ccli-prompt

ccli-prompt install           # runs the interactive wizard (auth + model pick)
```

The wizard:
1. **Authentication** — auto-discover from Claude Code credentials, or paste a key (accepts both `sk-ant-api03-...` and `sk-ant-oat01-...`).
2. **Model** — lists live models from `/v1/models`. Haiku 4.5 is the default.

Requirements: macOS or Linux, zsh 5.x+, Python 3.10+. The daemon has zero dependencies (stdlib only).

## Trigger it

Two ways — pick either:

**Esc then K** — works immediately, no terminal config needed. Press `Esc`, then `K` (sequence, not chord).

**Cmd+K / Ctrl+K** — remap it in your terminal to send `ESC+k`:

| Terminal        | How                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------ |
| iTerm2          | Settings → Profiles → Keys → `+` · Shortcut `⌘K` · Action *Send Escape Sequence* · Esc+: `k`     |
| Terminal.app    | Settings → Profiles → Keyboard → `+` · `⌘` + `K` · *Send Text* · `Ctrl+V` then `Esc`, then `k`   |
| Ghostty         | `keybind = cmd+k=text:\x1bk` in `~/.config/ghostty/config`                                       |
| Warp            | Settings → Keyboard → bind `⌘K` → *Send Text* · `\x1bk`                                          |
| Alacritty       | `[[keyboard.bindings]]` with `key = "K"`, `mods = "Control"`, `chars = "\u001bk"`                |
| Kitty           | `map ctrl+k send_text all \x1bk` in `~/.config/kitty/kitty.conf`                                 |
| WezTerm         | `{key="k", mods="CTRL", action=wezterm.action.SendString("\x1bk")}`                              |

## How it works

- **`ccli-prompt install`** — copies a zsh widget to `~/.ccli-prompt/ccli.zsh`, appends a `source` line to your `~/.zshrc`, and runs the wizard.
- **The widget** (bound to `^[k` — what Esc+K and remapped Cmd+K send) reads your query via `read-from-minibuffer`, sends it over a Unix socket to the daemon, and places the response into `$BUFFER` so nothing runs until you press Enter.
- **The daemon** (`python3 -m ccli_prompt.daemon`, auto-spawned on first use) reads auth from: env var → `~/.ccli-prompt/config` → macOS Keychain → `~/.claude/.credentials.json`. It opens one `HTTPSConnection` to `api.anthropic.com` and keeps it warm, POSTs to `/v1/messages` (Bearer+OAuth-beta or `x-api-key` depending on token prefix), caches the system prompt, and exits after 30 minutes of idleness.

## Configuration

Environment variables (override the config file; set in `~/.zshrc` before the source line):

```sh
export ANTHROPIC_API_KEY="sk-ant-..."       # API key or OAuth token
export CCLI_MODEL="claude-haiku-4-5"
export CCLI_MAX_TOKENS=400
export CCLI_IDLE_TIMEOUT=1800
export CCLI_SYSTEM_PROMPT_FILE="$HOME/.ccli-prompt/prompt.md"
```

Re-run the wizard at any time: `ccli-prompt configure`.

Edit the system prompt at `~/.ccli-prompt/prompt.md` (copied from the package on install).

## Manual daemon control

```sh
# status
[[ -S /tmp/ccli-$USER.sock ]] && echo "running" || echo "not running"

# stop
pkill -f ccli_prompt.daemon; rm -f /tmp/ccli-$USER.sock

# start manually (normally auto-starts on first Cmd+K)
python3 -m ccli_prompt.daemon &
```

## Safety

- Daemon's Unix socket is `0600`, scoped to your user.
- Responses only land in `$BUFFER` — nothing executes until *you* press Enter.
- The default system prompt refuses destructive commands (`rm -rf /`, `dd`, disk formatting, force-push to `main`, etc.) unless you request them in those exact terms.

## Uninstall

```sh
ccli-prompt uninstall
pipx uninstall ccli-prompt   # or: pip uninstall ccli-prompt
```

## License

MIT

#!/usr/bin/env bash
# ccli-prompt installer (macOS + Linux)
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${CCLI_INSTALL_DIR:-$HOME/.ccli-prompt}"
CONFIG_FILE="$INSTALL_DIR/config"
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"
SOCKET_PATH="/tmp/ccli-${USER}.sock"
OS="$(uname -s)"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
dim()  { printf '\033[2m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m⚠\033[0m  %s\n' "$*"; }
err()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }

# --- Requirements -----------------------------------------------------------
missing=()
command -v python3 >/dev/null 2>&1 || missing+=(python3)
command -v nc      >/dev/null 2>&1 || missing+=(nc)
command -v zsh     >/dev/null 2>&1 || missing+=(zsh)
if (( ${#missing[@]} > 0 )); then
  err "missing dependencies: ${missing[*]}"
  case "$OS" in
    Darwin) echo "  install via: brew install ${missing[*]}" >&2 ;;
    Linux)  echo "  install via your package manager (e.g. apt install ${missing[*]})" >&2 ;;
  esac
  exit 1
fi

# --- Auth discovery ---------------------------------------------------------
detect_auth() {
  if [[ -n "${ANTHROPIC_API_KEY:-}" || -n "${CCLI_API_KEY:-}" ]]; then echo "env"; return; fi
  if [[ -f "$CONFIG_FILE" ]] && grep -qE "^(ANTHROPIC_API_KEY|CCLI_API_KEY)=" "$CONFIG_FILE"; then
    echo "config"; return
  fi
  if [[ "$OS" == "Darwin" ]] && security find-generic-password -s "Claude Code-credentials" -w >/dev/null 2>&1; then
    echo "keychain"; return
  fi
  if [[ -f "$HOME/.claude/.credentials.json" ]]; then echo "creds"; return; fi
  echo "none"
}

auth_label() {
  case "$1" in
    env)      echo "env var (ANTHROPIC_API_KEY)" ;;
    config)   echo "$CONFIG_FILE" ;;
    keychain) echo "macOS Keychain (Claude Code subscription)" ;;
    creds)    echo "~/.claude/.credentials.json (Claude Code subscription)" ;;
    none)     echo "none detected" ;;
  esac
}

ensure_config_file() {
  mkdir -p "$INSTALL_DIR"
  if [[ ! -f "$CONFIG_FILE" ]]; then
    umask 077
    cat > "$CONFIG_FILE" <<'EOF'
# ccli-prompt config — do not commit this file.
# KEY=value pairs below are loaded as env vars by daemon.py on startup.
EOF
  fi
  chmod 600 "$CONFIG_FILE"
}

set_config() {
  local k="$1" v="$2"
  ensure_config_file
  local tmp; tmp="$(mktemp)"
  grep -v "^${k}=" "$CONFIG_FILE" 2>/dev/null > "$tmp" || true
  echo "${k}=${v}" >> "$tmp"
  mv "$tmp" "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
}

get_config_value() {
  [[ -f "$CONFIG_FILE" ]] || return
  grep -E "^${1}=" "$CONFIG_FILE" | head -1 | sed -E "s/^[^=]+=//; s/^[\"']//; s/[\"']\$//"
}

save_config_key() {
  # Trim whitespace (leading/trailing spaces, tabs, newlines)
  local key="$1"
  key="${key#"${key%%[![:space:]]*}"}"
  key="${key%"${key##*[![:space:]]}"}"
  set_config ANTHROPIC_API_KEY "$key"
  ok "saved API key to $CONFIG_FILE (mode 600)"
}

get_active_key() {
  local v
  v="${ANTHROPIC_API_KEY:-${CCLI_API_KEY:-}}"
  [[ -n "$v" ]] && { echo "$v"; return; }
  v="$(get_config_value ANTHROPIC_API_KEY)"; [[ -n "$v" ]] && { echo "$v"; return; }
  v="$(get_config_value CCLI_API_KEY)";     [[ -n "$v" ]] && { echo "$v"; return; }
  if [[ "$OS" == "Darwin" ]]; then
    local json
    if json=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null); then
      python3 -c "import sys,json; print(json.loads(sys.argv[1])['claudeAiOauth']['accessToken'])" "$json" 2>/dev/null
      return
    fi
  fi
  if [[ -f "$HOME/.claude/.credentials.json" ]]; then
    python3 -c "import json,pathlib; print(json.loads(pathlib.Path('$HOME/.claude/.credentials.json').read_text())['claudeAiOauth']['accessToken'])" 2>/dev/null
  fi
}

list_models() {
  # Prints "<id>\t<display_name>" to stdout; on failure, writes a single
  # human-readable error line to stderr and exits non-zero. Haiku 4.5 first
  # (our default), then the rest by id descending (newest first).
  python3 - "$1" <<'PY'
import sys, json, urllib.request, urllib.error
token = sys.argv[1]
headers = {"anthropic-version": "2023-06-01"}
if token.startswith("sk-ant-oat"):
    headers["Authorization"] = f"Bearer {token}"
    headers["anthropic-beta"] = "oauth-2025-04-20"
else:
    headers["x-api-key"] = token
try:
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=100", headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")[:300]
    print(f"HTTP {e.code}: {body}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"{type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
models = data.get("data", [])
is_haiku = lambda m: m.get("id", "").startswith("claude-haiku-4-5")
default = sorted((m for m in models if is_haiku(m)), key=lambda m: m.get("id", ""), reverse=True)
others  = sorted((m for m in models if not is_haiku(m)), key=lambda m: m.get("id", ""), reverse=True)
for m in default + others:
    print(f"{m.get('id','')}\t{m.get('display_name', m.get('id',''))}")
PY
}

configure_model() {
  local key
  key="$(get_active_key)"
  if [[ -z "$key" ]]; then
    dim "(no credentials yet — skipping model selection)"
    return
  fi

  echo
  bold "Model"
  local current
  current="$(get_config_value CCLI_MODEL)" || true
  [[ -z "$current" ]] && current="claude-haiku-4-5"
  dim "fetching available models from /v1/models..."

  # Guarded substitution: set -e would otherwise silently kill the script
  # if list_models exits non-zero.
  local models="" err=""
  local tmp_err; tmp_err="$(mktemp)"
  if ! models="$(list_models "$key" 2>"$tmp_err")"; then
    err="$(<"$tmp_err")"
    rm -f "$tmp_err"
    warn "couldn't fetch models: ${err:-unknown error}"
    warn "keeping $current — you can change it later in $CONFIG_FILE."
    set_config CCLI_MODEL "$current"
    return
  fi
  rm -f "$tmp_err"

  local -a ids names
  local i=1 default_marked=0
  while IFS=$'\t' read -r id name; do
    [[ -z "$id" ]] && continue
    ids+=("$id"); names+=("$name")
    local marker=""
    [[ "$id" == "$current" ]] && marker=" [current]"
    # Mark the first haiku-4-5* model as [default] (API returns dated id, not alias)
    if (( default_marked == 0 )) && [[ "$id" == claude-haiku-4-5* ]]; then
      marker="${marker:+$marker } [default]"
      marker="${marker# }"
      [[ -z "$marker" ]] || marker=" $marker"
      default_marked=1
    fi
    printf "  %2d) %-30s %s%s\n" "$i" "$id" "$name" "$marker"
    i=$((i+1))
  done <<< "$models"

  if [[ ! -t 0 ]]; then
    dim "(non-interactive — keeping $current)"
    set_config CCLI_MODEL "$current"
    return
  fi

  echo
  local choice
  read -rp "Choose [keep $current]: " choice
  choice="${choice// }"

  if [[ -z "$choice" ]]; then
    set_config CCLI_MODEL "$current"
    ok "model: $current"
    return
  fi
  if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#ids[@]} )); then
    warn "invalid choice, keeping $current"
    set_config CCLI_MODEL "$current"
    return
  fi
  local chosen="${ids[$((choice-1))]}"
  set_config CCLI_MODEL "$chosen"
  ok "model: $chosen"
}

wizard() {
  local detected sub_hint
  detected="$(detect_auth)"

  echo
  bold "Authentication"
  dim  "Detected: $(auth_label "$detected")"
  echo

  if [[ ! -t 0 ]]; then
    dim "(non-interactive install, skipping wizard)"
    return
  fi

  case "$detected" in
    keychain|creds) sub_hint="Auto-discover from Claude Code subscription [recommended]" ;;
    env|config)     sub_hint="Keep currently configured credentials [recommended]" ;;
    none)           sub_hint="Auto-discover from Claude Code subscription (not detected — will fail)" ;;
  esac

  echo "  1) $sub_hint"
  echo "  2) Enter an Anthropic API key or Claude Code OAuth token (sk-ant-...)"
  echo "  3) Skip (configure later via ANTHROPIC_API_KEY env var or $CONFIG_FILE)"
  echo

  local choice
  read -rp "Choose [1]: " choice
  choice="${choice:-1}"

  case "$choice" in
    1)
      if [[ "$detected" == "none" ]]; then
        warn "nothing to auto-discover. Install Claude Code first (https://claude.com/claude-code),"
        warn "then run \`claude\` and log in, or run this installer again and pick option 2."
      else
        ok "using $(auth_label "$detected")"
      fi
      ;;
    2)
      echo
      dim "Accepts either: sk-ant-api03-...  (regular API key)"
      dim "             or: sk-ant-oat01-...  (Claude Code OAuth token)"
      local key=""
      # -s hides input; read from /dev/tty so pipes don't break it
      read -rsp "API key: " key </dev/tty
      echo
      if [[ -z "$key" ]]; then
        warn "empty input, skipping"
      elif [[ "$key" != sk-ant-* ]]; then
        warn "doesn't look like an Anthropic key (expected prefix sk-ant-...). Saving anyway."
        save_config_key "$key"
      else
        save_config_key "$key"
      fi
      ;;
    3)
      dim "skipping — daemon will error on first call until credentials exist"
      ;;
    *)
      warn "invalid choice, skipping"
      ;;
  esac
}

# --- Install files ----------------------------------------------------------
mkdir -p "$INSTALL_DIR"
cp "$SRC_DIR/ccli.zsh"  "$INSTALL_DIR/ccli.zsh"
cp "$SRC_DIR/daemon.py" "$INSTALL_DIR/daemon.py"
cp "$SRC_DIR/prompt.md" "$INSTALL_DIR/prompt.md"
chmod +x "$INSTALL_DIR/daemon.py"
ok "files installed to $INSTALL_DIR"

# Kill old daemon so next call picks up new code
if [[ -S "$SOCKET_PATH" ]]; then
  pkill -f "python3 .*ccli-prompt/daemon.py" 2>/dev/null || true
  rm -f "$SOCKET_PATH"
fi

# Wire .zshrc
SOURCE_LINE="source \"$INSTALL_DIR/ccli.zsh\""
MARKER="# ccli-prompt"
if ! grep -Fq "$SOURCE_LINE" "$ZSHRC" 2>/dev/null; then
  { echo ""; echo "$MARKER"; echo "$SOURCE_LINE"; } >> "$ZSHRC"
  ok "added source line to $ZSHRC"
else
  ok "$ZSHRC already sources ccli.zsh"
fi

wizard
configure_model

cat <<EOF

$(bold "Next steps")
  1. Reload your shell:  source "$ZSHRC"   (or open a new terminal tab)
  2. Trigger the prompt:  press Esc then K  (or remap Cmd/Ctrl+K — see README)
  3. Type your request, Enter. The generated command lands on your command line.
     Nothing runs until you press Enter to confirm.

Optional env vars (export in ~/.zshrc before the source line):
  ANTHROPIC_API_KEY=sk-ant-...           # regular API key OR sk-ant-oat... token
  CCLI_MODEL=claude-haiku-4-5
  CCLI_MAX_TOKENS=400
  CCLI_IDLE_TIMEOUT=1800
  CCLI_SYSTEM_PROMPT_FILE=/path/to.md
EOF

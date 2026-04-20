#!/usr/bin/env bash
# ccli-prompt uninstaller
set -euo pipefail

INSTALL_DIR="${CCLI_INSTALL_DIR:-$HOME/.ccli-prompt}"
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"
SOCKET_PATH="/tmp/ccli-${USER}.sock"

# Stop the running daemon, if any
pkill -f "python3 .*ccli-prompt/daemon.py" 2>/dev/null || true
[[ -S "$SOCKET_PATH" ]] && rm -f "$SOCKET_PATH"

if [[ -f "$ZSHRC" ]]; then
  tmp="$(mktemp)"
  awk '
    /^# ccli-prompt$/                                 { skip=1; next }
    skip && /^source .*\.ccli-prompt\/ccli\.zsh"?$/   { skip=0; next }
    { print }
  ' "$ZSHRC" > "$tmp" && mv "$tmp" "$ZSHRC"
  echo "✓ removed ccli-prompt lines from $ZSHRC"
fi

if [[ -d "$INSTALL_DIR" ]]; then
  rm -rf "$INSTALL_DIR"
  echo "✓ removed $INSTALL_DIR"
fi

echo "Reload your shell (or open a new tab) to finish."

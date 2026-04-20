#!/usr/bin/env zsh
# ccli-prompt — inline Claude prompt in your terminal.
# Trigger: Cmd+K (with terminal remap) or Esc then K.

autoload -Uz read-from-minibuffer

typeset -g CCLI_DIR="${${(%):-%x}:A:h}"
typeset -g CCLI_SOCKET="${CCLI_SOCKET:-/tmp/ccli-${USER}.sock}"
typeset -g CCLI_DAEMON="${CCLI_DAEMON:-$CCLI_DIR/daemon.py}"

# Give Esc-then-K more than the default 0.4s chord window, but don't override
# a larger value the user may have set.
if (( ${KEYTIMEOUT:-40} < 100 )); then
  KEYTIMEOUT=100
fi

_ccli_ensure_daemon() {
  if [[ -S "$CCLI_SOCKET" ]] && printf '' | nc -U -w 1 "$CCLI_SOCKET" >/dev/null 2>&1; then
    return 0
  fi
  [[ -S "$CCLI_SOCKET" ]] && rm -f "$CCLI_SOCKET"

  (nohup python3 "$CCLI_DAEMON" </dev/null >/dev/null 2>&1 &) 2>/dev/null

  local i
  for i in {1..40}; do
    [[ -S "$CCLI_SOCKET" ]] && return 0
    sleep 0.05
  done
  return 1
}

_ccli_widget() {
  emulate -L zsh

  local saved_buffer="$BUFFER"

  read-from-minibuffer '✦ ask claude> '
  local query="$REPLY"

  if [[ -z "${query// }" ]]; then
    zle reset-prompt
    return 0
  fi

  zle -I
  print -Pn "%F{240}… thinking%f"

  if ! _ccli_ensure_daemon; then
    print -n $'\r\e[2K'
    print -P "%F{red}✗ couldn't start ccli-daemon (python3 $CCLI_DAEMON)%f"
    BUFFER="$saved_buffer"
    CURSOR=${#BUFFER}
    zle reset-prompt
    return 1
  fi

  local req=$'cwd\t'"$PWD"$'\nshell\t'"${SHELL:t}"$'\nos\t'"$(uname -sr)"$'\ndraft\t'"${saved_buffer//$'\n'/ }"$'\nquery\t'"${query//$'\n'/ }"

  local output
  output=$(printf '%s' "$req" | nc -U "$CCLI_SOCKET" 2>/dev/null)

  print -n $'\r\e[2K'

  if [[ -z "$output" ]]; then
    print -P "%F{red}✗ no response from ccli-daemon%f"
    BUFFER="$saved_buffer"
    CURSOR=${#BUFFER}
    zle reset-prompt
    return 1
  fi

  # Daemon-level errors come back as "# <message>". Surface them above the
  # prompt instead of dropping into the command line where Enter would run them.
  if [[ "$output" == \#\ * ]]; then
    print -P "%F{red}$output%f"
    BUFFER="$saved_buffer"
    CURSOR=${#BUFFER}
    zle reset-prompt
    return 1
  fi

  output=$(printf '%s' "$output" | sed -E '/^[[:space:]]*```[a-zA-Z0-9_-]*[[:space:]]*$/d; /^[[:space:]]*```[[:space:]]*$/d')
  output="${output#"${output%%[![:space:]]*}"}"
  output="${output%"${output##*[![:space:]]}"}"

  BUFFER="$output"
  CURSOR=${#BUFFER}
  zle reset-prompt
}

zle -N _ccli_widget

# Cmd+K (after terminal remap) sends ESC+k. Esc then K works as a chord
# without any terminal config.
bindkey '^[k' _ccli_widget

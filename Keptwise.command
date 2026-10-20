#!/bin/bash
# Double-click in Finder to start Keptwise. Close this window to stop it.
# Finder starts scripts with a minimal PATH, so look where uv is usually installed first.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  LOGIN_PATH="$(/bin/zsh -ilc 'printf "%s" "$PATH"' </dev/null 2>/dev/null | tail -n 1)"
  [ -n "$LOGIN_PATH" ] && export PATH="$PATH:$LOGIN_PATH"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --project . keptwise serve
elif [ -x .venv/bin/keptwise ]; then
  exec .venv/bin/keptwise serve
fi
echo "Couldn't find uv. Install it from https://docs.astral.sh/uv/ and try again."
read -r -p "Press Enter to close."
exit 1

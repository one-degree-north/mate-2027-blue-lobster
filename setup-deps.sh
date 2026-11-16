#!/usr/bin/env bash
# Installs everything needed to run this project on macOS. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

[[ "$(uname -s)" == "Darwin" ]] || { echo "error: macOS only" >&2; exit 1; }

# Homebrew
if ! command -v brew > /dev/null; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
eval "$(/opt/homebrew/bin/brew shellenv 2> /dev/null || /usr/local/bin/brew shellenv)"

# uv, fnm, pnpm, GStreamer, just
brew bundle --file=Brewfile

# Node (version from .node-version)
eval "$(fnm env)"
fnm install

# Make new terminals pick the right Node automatically
HOOK='eval "$(fnm env --use-on-cd)"'
grep -qsF "$HOOK" ~/.zshrc || echo "$HOOK" >> ~/.zshrc

echo "Done. Open a new terminal, then run: just setup"

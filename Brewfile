# System dependencies. setup-deps.sh installs these with `brew bundle`.
#
# Python and UI packages are pinned by uv.lock and ui/pnpm-lock.yaml; Node's major version is
# in .node-version. Homebrew itself cannot pin versions.

# Python package manager; installs the Python version in .python-version itself.
brew "uv"

# Node version manager; reads .node-version.
brew "fnm"

# UI package manager. A standalone build, so it does not depend on the Node version.
brew "pnpm"

# Camera and video-file sources run `gst-launch-1.0`. Last tested with 1.28.9.
# The formula includes the base, good, bad, ugly and libav plugins the pipelines use.
brew "gstreamer"

# Task runner for the Justfile (optional for running the app, handy for development).
brew "just"

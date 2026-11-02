set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Install Python and UI dependencies
setup:
    uv sync
    pnpm --dir ui install

# Hub + modules (restart on save) and the Vite UI at http://localhost:5173; Ctrl-C stops both
dev:
    #!/usr/bin/env bash
    trap 'kill 0' EXIT   # on exit, stop everything this recipe started
    uv run rovkit run --reload &
    pnpm --dir ui dev &
    wait

# Build the UI and serve everything from the hub at http://127.0.0.1:8765
serve:
    pnpm --dir ui build
    uv run rovkit run

# Lint, type check, and test both halves
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check
    pnpm --dir ui check

test:
    uv run pytest

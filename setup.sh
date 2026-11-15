#!/usr/bin/env bash
# Re-runnable native installation for macOS and Linux.
set -euo pipefail

PROJECT_DIR="$( (cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) )"
if ! command -v uv >/dev/null 2>&1; then
    export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
    if [[ ! -t 0 ]]; then
        echo "uv is missing. Install it from https://docs.astral.sh/uv/ then rerun ./setup.sh."
        exit 1
    fi
    read -r -p "Install uv using the official astral.sh installer? [y/N] " reply
    if [[ "$reply" != y && "$reply" != Y && "$reply" != yes ]]; then
        echo "Install uv yourself, then rerun ./setup.sh."
        exit 1
    fi
    installer="$(mktemp)"
    trap 'rm -f "$installer"' EXIT
    curl --fail --silent --show-error --location https://astral.sh/uv/install.sh --output "$installer"
    sh "$installer"
fi
uv sync --project "$PROJECT_DIR" --frozen
uv run --project "$PROJECT_DIR" bio-jobs init
uv run --project "$PROJECT_DIR" bio-jobs setup

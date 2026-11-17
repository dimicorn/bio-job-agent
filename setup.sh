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
uv run --project "$PROJECT_DIR" playwright install chromium
uv run --project "$PROJECT_DIR" bio-jobs init
uv run --project "$PROJECT_DIR" bio-jobs setup

# Optional daily/twice-daily watch. Default no; re-running replaces only this agent.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
hours=(10)
configure_schedule=n
if [[ -t 0 ]]; then
    read -r -p "Schedule watch daily or twice daily? [y/N] " configure_schedule
fi
if [[ "$configure_schedule" == y || "$configure_schedule" == Y || "$configure_schedule" == yes ]]; then
    hour=""
    until [[ $hour =~ ^([01]?[0-9]|2[0-3])$ ]]; do
        read -r -p "First hour (0-23) [10]: " hour
        hour="${hour:-10}"
    done
    hours=("$((10#$hour))")
    read -r -p "Run twice daily? [y/N] " twice
    if [[ "$twice" == y || "$twice" == Y || "$twice" == yes ]]; then
        second=""
        until [[ $second =~ ^([01]?[0-9]|2[0-3])$ ]] && [[ $((10#${second:-0})) != ${hours[0]} ]]; do
            read -r -p "Second hour (0-23, different from first) [18]: " second
            second="${second:-18}"
        done
        hours+=("$((10#$second))")
    fi
    mkdir -p "$PROJECT_DIR/logs"
    if [[ $(uname) == Darwin ]]; then
        agent="$HOME/Library/LaunchAgents/ru.bio-job-agent.watch.plist"
        uv run --project "$PROJECT_DIR" python -m app.schedule --project "$PROJECT_DIR" \
            --uv "$(command -v uv)" --home "$HOME" --hours "${hours[@]}" --output "$agent"
        launchctl unload "$agent" 2>/dev/null || true
        launchctl load "$agent"
        echo "Watch scheduled at hours ${hours[*]} (local time); log: $PROJECT_DIR/logs/watch.log"
        printf 'Disable: launchctl unload "%s"\n' "$agent"
    fi
else
    echo "Schedule left unchanged. Rerun ./setup.sh to enable or update it."
fi
if [[ $(uname) != Darwin ]]; then
    echo "Linux: add the following to crontab -e (create $PROJECT_DIR/logs first):"
    uv run --project "$PROJECT_DIR" python -m app.schedule --project "$PROJECT_DIR" \
        --uv "$(command -v uv)" --home "$HOME" --hours "${hours[@]}"
fi

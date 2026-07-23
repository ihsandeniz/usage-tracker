#!/usr/bin/env bash
# usage-tracker başlat — port 8770, loopback-only, stdlib Python (sıfır bağımlılık)
cd "$(dirname "$0")" || exit 1

# Provider API keys (optional) — same file the systemd service uses.
# set -a exports every KEY=value so it reaches the Python process.
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/usage-tracker/env"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi

exec python3 server.py

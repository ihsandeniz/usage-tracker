#!/usr/bin/env bash
# usage-tracker başlat — port 8770, loopback-only, stdlib Python (sıfır bağımlılık)
cd "$(dirname "$0")" || exit 1
exec python3 server.py

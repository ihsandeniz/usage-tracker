#!/usr/bin/env bash
# usage-tracker autostart — installs a systemd USER service (no root, per-user).
# Works on any systemd distro (Arch, Fedora, Ubuntu, …). Starts on login.
#
#   ./service.sh install     generate + enable + start (autostart on login)
#   ./service.sh status       show service state
#   ./service.sh restart      restart the service
#   ./service.sh disable      stop + disable autostart (keeps the unit file)
#   ./service.sh uninstall    stop + disable + remove the unit file
#
# Not on systemd? See the "Autostart without systemd" section in the README.
set -eu

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
UNIT_NAME="usage-tracker.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"

need_systemd() {
  command -v systemctl >/dev/null 2>&1 || {
    echo "systemctl not found — your system doesn't use systemd." >&2
    echo "See the 'Autostart without systemd' section in the README (Hyprland exec-once / XDG autostart)." >&2
    exit 1
  }
}

do_install() {
  need_systemd
  local py; py="$(command -v python3 || true)"
  [ -n "$py" ] || { echo "python3 not found." >&2; exit 1; }
  mkdir -p "$UNIT_DIR"
  sed -e "s|@@ROOT@@|$ROOT|g" -e "s|@@PYTHON@@|$py|g" \
      "$ROOT/packaging/usage-tracker.service.in" > "$UNIT_PATH"
  echo "✓ wrote $UNIT_PATH"
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT_NAME"
  echo "✓ enabled + started (autostarts on login)"
  # Let a graphical login without lingering keep it alive across the session;
  # optional: 'loginctl enable-linger' to run even when logged out (not required for a badge).
  sleep 1
  systemctl --user --no-pager --lines=0 status "$UNIT_NAME" || true
  echo
  echo "Badge data at http://127.0.0.1:8770 — reload waybar if needed: killall -SIGUSR2 waybar"
}

case "${1:-}" in
  install)   do_install ;;
  status)    need_systemd; systemctl --user --no-pager status "$UNIT_NAME" ;;
  restart)   need_systemd; systemctl --user restart "$UNIT_NAME"; echo "✓ restarted" ;;
  disable)   need_systemd; systemctl --user disable --now "$UNIT_NAME"; echo "✓ disabled (unit file kept)" ;;
  uninstall)
    need_systemd
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$UNIT_PATH"
    systemctl --user daemon-reload
    echo "✓ removed $UNIT_PATH"
    ;;
  *) echo "usage: ./service.sh {install|status|restart|disable|uninstall}" >&2; exit 2 ;;
esac

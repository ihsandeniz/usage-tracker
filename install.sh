#!/usr/bin/env bash
# usage-tracker installer — makes the surface portable on any machine.
# Creates surface/surface.conf, marks scripts executable, and prints ready-to-paste
# waybar / autostart snippets. Safe to re-run; never touches ~/.config.
set -eu

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SURFACE="$ROOT/surface"
CONF="$SURFACE/surface.conf"

say()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }

echo "usage-tracker installer"
echo "  repo: $ROOT"
echo

# 1) Dependencies -----------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
  ok "python3 $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
else
  warn "python3 not found — the server needs Python 3.9+ (stdlib only)."
fi
for c in curl jq; do
  command -v "$c" >/dev/null 2>&1 && ok "$c" || warn "$c not found — required by the waybar feeder."
done
# any Chromium-family browser enables the floating widget
_br=""
for b in chromium chromium-browser google-chrome-stable google-chrome brave-browser brave vivaldi-stable microsoft-edge-stable; do
  command -v "$b" >/dev/null 2>&1 && { _br="$b"; break; }
done
[ -n "$_br" ] && ok "browser: $_br (floating widget)" || warn "no Chromium-family browser — the floating widget needs one (waybar badge works without it)."
command -v hyprctl >/dev/null 2>&1 && ok "hyprctl (auto float/pin the widget)" || warn "hyprctl not found — float/resize the widget with your compositor's own controls."
echo

# 2) surface.conf -----------------------------------------------------------
if [ ! -f "$CONF" ]; then
  cp "$SURFACE/surface.conf.example" "$CONF"
  ok "created surface/surface.conf"
else
  say "keeping existing surface/surface.conf"
fi

# 3) Executable bits --------------------------------------------------------
chmod +x "$ROOT/start.sh" "$ROOT/service.sh" "$ROOT/install.sh" \
         "$SURFACE/usage-widget" "$SURFACE/waybar-usage.sh" 2>/dev/null || true
ok "scripts marked executable"

echo
echo "──────────────────────────────────────────────────────────────"
echo "1) Start the server — pick one:"
echo "   ./start.sh                    # foreground, one-off"
echo "   ./service.sh install          # ★ autostart on login (systemd user service, no root)"
echo "   → http://127.0.0.1:8770"
echo
echo "The waybar badge and the floating widget are INDEPENDENT — install either, both, or neither."
echo
echo "2) waybar badge (optional) — add to ~/.config/waybar/config.jsonc:"
cat <<EOF
   "custom/usage": {
     "exec": "$SURFACE/waybar-usage.sh",
     "return-type": "json",
     "interval": 30,
     "on-click": "$SURFACE/usage-widget toggle"
   }
   // then add "custom/usage" to a modules-* list, and to style.css:
   //   #custom-usage.ok{color:#34d399} #custom-usage.warn{color:#fbbf24}
   //   #custom-usage.crit{color:#f87171} #custom-usage.off{color:#7b8ba0}
EOF
echo
echo "3) floating widget (optional) — a movable + resizable panel window:"
echo "   $SURFACE/usage-widget open        # show it (drag: Super+LMB · resize: Super+RMB/edges)"
echo "   $SURFACE/usage-widget toggle      # show/hide"
echo "   size: edit WIDGET_SIZE in $CONF (the browser remembers your drags after that)"
echo "   autostart (Hyprland): exec-once = $SURFACE/usage-widget open"
echo "──────────────────────────────────────────────────────────────"
ok "done"

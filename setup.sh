#!/usr/bin/env bash
# usage-tracker — guided setup wizard.
# Walks you through: dependencies → server autostart → waybar badge → widget → keys.
# Safe: only edits its own config dir and (with your OK) appends to waybar style.css
# / hyprland.conf. Never rewrites your waybar config.jsonc — that snippet you paste
# yourself (jsonc is too easy to break silently), but we copy it to your clipboard.
#
# No `set -e` on purpose: this is an interactive wizard, so a single optional step
# failing (clipboard, killall, systemctl) must NOT abort the whole flow — every
# step handles its own errors with `|| warn`.
set -u

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SURFACE="$ROOT/surface"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
ENV_FILE="$CFG/usage-tracker/env"
URL="http://127.0.0.1:8770"

# ── styling ────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; R=$'\033[31m'; X=$'\033[0m'
else C=""; G=""; Y=""; D=""; B=""; R=""; X=""; fi
say()  { printf '%s▸%s %s\n' "$C" "$X" "$*"; }
ok()   { printf '%s✓%s %s\n' "$G" "$X" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$X" "$*"; }
step() { printf '\n%s%s──%s %s%s\n' "$B" "$C" "$X" "$B$*" "$X"; }

ask() { # ask "question" default(y/n) → returns 0 for yes
  local q="$1" def="${2:-y}" a hint="[Y/n]"
  [ "$def" = n ] && hint="[y/N]"
  printf '%s?%s %s %s%s%s ' "$C" "$X" "$q" "$D" "$hint" "$X"
  read -r a || a=""
  a="${a:-$def}"
  case "$a" in [Yy]*) return 0;; *) return 1;; esac
}

copy_clip() { # copy stdin to clipboard if a tool works; echo the tool name or ""
  local data; data="$(cat)"
  if command -v wl-copy >/dev/null 2>&1 && printf '%s' "$data" | wl-copy 2>/dev/null; then echo wl-copy; return; fi
  if command -v xclip  >/dev/null 2>&1 && printf '%s' "$data" | xclip -selection clipboard 2>/dev/null; then echo xclip; return; fi
  if command -v xsel   >/dev/null 2>&1 && printf '%s' "$data" | xsel -b 2>/dev/null; then echo xsel; return; fi
  echo ""
}

printf '%s' "$C$B"
cat <<'BANNER'
  ┌───────────────────────────────────────────────┐
  │   usage-tracker · guided setup                 │
  │   AI usage · limits · real $ — one glance      │
  └───────────────────────────────────────────────┘
BANNER
printf '%s' "$X"
echo "  repo: $ROOT"

# ── 0) dependencies + base config (delegates to install.sh) ────────────────
step "1/5  Dependencies & base config"
"$ROOT/install.sh" >/tmp/ut-install.$$ 2>&1 || true
grep -E '✓|!' /tmp/ut-install.$$ | sed 's/^/   /' || true
rm -f /tmp/ut-install.$$
ok "base config ready (surface.conf + $ENV_FILE)"

# ── 1) server ──────────────────────────────────────────────────────────────
step "2/5  Server (needed for everything else)"
echo "   The server reads your local usage and serves the panel at $URL."
if command -v systemctl >/dev/null 2>&1 && ask "Autostart on login via systemd (recommended)?" y; then
  "$ROOT/service.sh" install || warn "service install failed — you can run ./start.sh by hand."
elif ask "Start it now in the background for this session?" y; then
  ("$ROOT/start.sh" >/dev/null 2>&1 &) ; sleep 1
  ok "server started (this session only)"
else
  warn "skipped — start later with ./start.sh or ./service.sh install"
fi

# ── 2) waybar badge ─────────────────────────────────────────────────────────
step "3/5  waybar badge (optional)"
WBAR=""
for f in "$CFG/waybar/config.jsonc" "$CFG/waybar/config"; do
  [ -f "$f" ] && { WBAR="$f"; break; }
done
if [ -z "$WBAR" ]; then
  warn "no waybar config found — skipping (that's fine if you don't use waybar)."
elif grep -q "custom/usage" "$WBAR" 2>/dev/null; then
  ok "already present in $(basename "$WBAR") — nothing to do."
elif ask "Add the usage badge to waybar?" y; then
  STYLE="$CFG/waybar/style.css"
  MODULE=$(cat <<EOF
  "custom/usage": {
    "exec": "$SURFACE/waybar-usage.sh",
    "return-type": "json",
    "interval": 30,
    "on-click": "$SURFACE/usage-widget toggle"
  },
EOF
)
  # 2a) style.css colours — safe to append (idempotent)
  if [ -f "$STYLE" ] && ! grep -q "custom-usage" "$STYLE"; then
    cp "$STYLE" "$STYLE.bak-usage-tracker"
    cat >>"$STYLE" <<'EOF'

/* usage-tracker badge */
#custom-usage.ok   { color: #34d399; }
#custom-usage.warn { color: #fbbf24; }
#custom-usage.crit { color: #f87171; }
#custom-usage.off  { color: #7b8ba0; }
EOF
    ok "added badge colours to style.css (backup: style.css.bak-usage-tracker)"
  fi
  # 2b) the module JSON — clipboard + instructions (we don't rewrite jsonc)
  tool=$(printf '%s' "$MODULE" | copy_clip)
  echo
  echo "$MODULE" | sed 's/^/   /'
  if [ -n "$tool" ]; then ok "^ copied to clipboard ($tool)"; else warn "install wl-copy/xclip to auto-copy next time"; fi
  echo
  say "Two small edits in ${B}$WBAR${X}:"
  echo "   1. paste the block above among your other module definitions"
  echo "   2. add ${B}\"custom/usage\"${X} to a \"modules-right\" (or -left/-center) list"
  if ask "Open $(basename "$WBAR") in \$EDITOR now?" y; then
    "${EDITOR:-nano}" "$WBAR" || true
    command -v killall >/dev/null 2>&1 && killall -SIGUSR2 waybar 2>/dev/null && ok "waybar reloaded" || true
  fi
else
  warn "skipped waybar badge."
fi

# ── 3) floating widget ──────────────────────────────────────────────────────
step "4/5  Floating widget (optional)"
echo "   The panel as a movable, resizable, always-on-top window."
if [ -z "${_br:-}" ] && ! command -v chromium >/dev/null 2>&1 && ! command -v google-chrome-stable >/dev/null 2>&1 && ! command -v brave >/dev/null 2>&1; then
  warn "no Chromium-family browser detected — the widget needs one. Skipping."
elif ask "Set it up?" n; then
  if command -v hyprctl >/dev/null 2>&1; then
    HYPR="$CFG/hypr/hyprland.conf"
    if [ -f "$HYPR" ] && ! grep -q "usage-widget" "$HYPR" && ask "Autostart the widget on Hyprland login?" n; then
      cp "$HYPR" "$HYPR.bak-usage-tracker"
      printf '\n# usage-tracker floating widget\nexec-once = %s/usage-widget open\n' "$SURFACE" >>"$HYPR"
      ok "added exec-once to hyprland.conf (backup: hyprland.conf.bak-usage-tracker)"
    fi
  fi
  if ask "Open the widget now?" y; then
    "$SURFACE/usage-widget" open || warn "couldn't open — try: $SURFACE/usage-widget open"
    ok "drag: Super+LMB · resize: Super+RMB or edges (the browser remembers size)"
  fi
else
  warn "skipped widget."
fi

# ── 4) provider keys ────────────────────────────────────────────────────────
step "5/5  Hosted provider keys (optional)"
echo "   Claude Code, Codex and local runners (Ollama/LM Studio/Jan) need ${B}no key${X}."
echo "   Add keys only for hosted providers (OpenRouter, OpenAI, DeepSeek, …)."
echo "   A missing key just hides that card — nothing breaks."
if ask "Edit your keys file now ($ENV_FILE)?" n; then
  "${EDITOR:-nano}" "$ENV_FILE" || true
  if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active usage-tracker.service >/dev/null 2>&1; then
    systemctl --user restart usage-tracker.service && ok "server restarted to pick up new keys"
  else
    say "restart the server to apply: ./service.sh restart  (or re-run ./start.sh)"
  fi
else
  say "later: edit $ENV_FILE, then restart the server."
fi

# ── done ────────────────────────────────────────────────────────────────────
step "Done"
if command -v curl >/dev/null 2>&1 && curl -sf -o /dev/null "$URL" 2>/dev/null; then
  ok "server is up → ${B}$URL${X}"
else
  warn "server not reachable yet — start it with ./start.sh, then open $URL"
fi
echo
echo "   panel:   $URL"
echo "   keys:    $ENV_FILE"
echo "   waybar:  reload with  killall -SIGUSR2 waybar"
echo "   help:    README.md"
echo

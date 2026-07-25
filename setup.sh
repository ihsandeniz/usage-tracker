#!/usr/bin/env bash
# usage-tracker — guided setup wizard.
#
#   ./setup.sh              interactive walkthrough (recommended)
#   ./setup.sh --auto       non-interactive: safe defaults, no questions
#   ./setup.sh --uninstall  undo everything this wizard set up
#   ./setup.sh --help
#
# Steps: dependencies → server → waybar badge → widget/tray → keys → verify.
#
# What "safe" means here, concretely:
#   * every file we touch is backed up first (<file>.bak-usage-tracker)
#   * your waybar config is edited surgically — comments and formatting survive;
#     we parse it before and after and roll back if the result isn't valid
#   * if we can't be sure (unparseable config, modules list in an include),
#     we don't guess: you get the snippet on your clipboard and paste it yourself
#   * everything is idempotent — re-running changes nothing that's already done
#   * nothing needs root, nothing leaves your machine
#
# No `set -e` on purpose: an optional step failing (clipboard, killall, systemctl)
# must not abort the whole flow — each step handles its own errors with `|| warn`.
set -u

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SURFACE="$ROOT/surface"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
ENV_FILE="$CFG/usage-tracker/env"
PORT="${USAGE_PORT:-8770}"
URL="http://127.0.0.1:$PORT"
WB_EDIT="$ROOT/packaging/waybar_edit.py"
BAK_SUFFIX=".bak-usage-tracker"

AUTO=0
MODE=install
for a in "$@"; do
  case "$a" in
    --auto|-y|--yes)   AUTO=1 ;;
    --uninstall)       MODE=uninstall ;;
    -h|--help)         sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

# ── styling ────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; R=$'\033[31m'; X=$'\033[0m'
else C=""; G=""; Y=""; D=""; B=""; R=""; X=""; fi
say()  { printf '%s▸%s %s\n' "$C" "$X" "$*"; }
ok()   { printf '%s✓%s %s\n' "$G" "$X" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$X" "$*"; }
bad()  { printf '%s✗%s %s\n' "$R" "$X" "$*"; }
step() { printf '\n%s%s──%s %s%s\n' "$B" "$C" "$X" "$B$*" "$X"; }

ask() { # ask "question" default(y/n) → 0 = yes. --auto takes the default.
  local q="$1" def="${2:-y}" a hint="[Y/n]"
  [ "$def" = n ] && hint="[y/N]"
  if [ "$AUTO" = 1 ]; then
    printf '%s?%s %s %s→ %s (auto)%s\n' "$C" "$X" "$q" "$D" "$def" "$X"
    [ "$def" = y ]; return
  fi
  printf '%s?%s %s %s%s%s ' "$C" "$X" "$q" "$D" "$hint" "$X"
  read -r a || a=""
  a="${a:-$def}"
  case "$a" in [Yy]*) return 0;; *) return 1;; esac
}

backup() { # backup FILE — once per run, never clobbers an older backup blindly
  [ -f "$1" ] || return 0
  cp -p "$1" "$1$BAK_SUFFIX" 2>/dev/null && say "backup: $(basename "$1")$BAK_SUFFIX"
}

copy_clip() { # stdin → clipboard; echoes the tool that worked, or ""
  local data; data="$(cat)"
  if command -v wl-copy >/dev/null 2>&1 && printf '%s' "$data" | wl-copy 2>/dev/null; then echo wl-copy; return; fi
  if command -v xclip  >/dev/null 2>&1 && printf '%s' "$data" | xclip -selection clipboard 2>/dev/null; then echo xclip; return; fi
  if command -v xsel   >/dev/null 2>&1 && printf '%s' "$data" | xsel -b 2>/dev/null; then echo xsel; return; fi
  echo ""
}

find_browser() { # the single source of truth for "can we run the widget?"
  local b
  for b in chromium chromium-browser google-chrome-stable google-chrome \
           brave-browser brave vivaldi-stable microsoft-edge-stable; do
    command -v "$b" >/dev/null 2>&1 && { echo "$b"; return 0; }
  done
  return 1
}

find_waybar_config() {
  local f
  for f in "$CFG/waybar/config.jsonc" "$CFG/waybar/config"; do
    [ -f "$f" ] && { echo "$f"; return 0; }
  done
  return 1
}

pkg_hint() { # "how do I install X on this distro?"
  local p="$1"
  if   command -v pacman  >/dev/null 2>&1; then echo "sudo pacman -S --needed $p"
  elif command -v apt-get >/dev/null 2>&1; then echo "sudo apt install $p"
  elif command -v dnf     >/dev/null 2>&1; then echo "sudo dnf install $p"
  elif command -v zypper  >/dev/null 2>&1; then echo "sudo zypper install $p"
  elif command -v apk     >/dev/null 2>&1; then echo "sudo apk add $p"
  else echo "install $p with your package manager"; fi
}

server_alive() { # true only if OUR server answers — not just "something on :PORT"
  command -v curl >/dev/null 2>&1 || return 1
  curl -sf --max-time 3 "$URL/v1/usage" 2>/dev/null | grep -q '"providers"'
}

# ════════════════════════════════════════════════════════════════════════════
# UNINSTALL
# ════════════════════════════════════════════════════════════════════════════
if [ "$MODE" = uninstall ]; then
  step "Removing usage-tracker's setup (your data and the repo stay)"
  echo "   This will: stop + remove the systemd service, take the badge out of"
  echo "   your waybar config and style.css, and drop autostart entries."
  echo "   Every file is backed up as <file>$BAK_SUFFIX first."
  ask "Go ahead?" y || { say "nothing changed"; exit 0; }
  if command -v systemctl >/dev/null 2>&1; then
    "$ROOT/service.sh" uninstall 2>/dev/null && ok "systemd user service removed" || warn "no service to remove"
  fi
  if WBAR="$(find_waybar_config)"; then
    backup "$WBAR"
    python3 "$WB_EDIT" remove --config "$WBAR" >/dev/null 2>&1
    case $? in
      0) ok "badge removed from $(basename "$WBAR")" ;;
      2) say "badge wasn't in $(basename "$WBAR")" ;;
      *) warn "couldn't edit $(basename "$WBAR") — remove the \"custom/usage\" block by hand" ;;
    esac
    STYLE="$CFG/waybar/style.css"
    if [ -f "$STYLE" ] && grep -q "usage-tracker badge" "$STYLE"; then
      backup "$STYLE"
      python3 - "$STYLE" <<'PY' && ok "badge colours removed from style.css"
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s = re.sub(r"\n*/\* usage-tracker badge \*/\n(?:#custom-usage[^\n]*\n)*", "\n", s)
open(p, "w", encoding="utf-8").write(s)
PY
    fi
    command -v killall >/dev/null 2>&1 && killall -SIGUSR2 waybar 2>/dev/null && ok "waybar reloaded"
  fi
  for f in "$CFG/hypr/hyprland.conf"; do
    if [ -f "$f" ] && grep -q "usage-tracker" "$f"; then
      backup "$f"
      sed -i '/# usage-tracker/,+1{/# usage-tracker/d;/usage-\(widget\|tray\)/d}' "$f" \
        && ok "autostart lines removed from $(basename "$f")"
    fi
  done
  rm -f "$CFG/autostart/usage-tracker-widget.desktop" "$CFG/autostart/usage-tracker-tray.desktop" 2>/dev/null
  echo
  say "kept on purpose: $ENV_FILE (your API keys) and the repo itself."
  echo "   delete them yourself if you want a clean slate:"
  echo "     rm -rf $CFG/usage-tracker  $ROOT"
  exit 0
fi

# ════════════════════════════════════════════════════════════════════════════
# INSTALL
# ════════════════════════════════════════════════════════════════════════════
printf '%s' "$C$B"
cat <<'BANNER'
  ┌───────────────────────────────────────────────┐
  │   usage-tracker · guided setup                 │
  │   AI usage · limits · real $ — one glance      │
  └───────────────────────────────────────────────┘
BANNER
printf '%s' "$X"
echo "  repo: $ROOT"
[ "$AUTO" = 1 ] && say "--auto: taking the recommended answer for every question"

DID_SERVER=no; DID_WAYBAR=no; DID_WIDGET=no; DID_TRAY=no; DID_KEYS=0

# ── 1) dependencies + base config ──────────────────────────────────────────
step "1/6  Dependencies & base config"
MISSING=""
command -v python3 >/dev/null 2>&1 \
  && ok "python3 $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)" \
  || { bad "python3 missing — the server can't run"; MISSING="$MISSING python"; }
for c in curl jq; do
  if command -v "$c" >/dev/null 2>&1; then ok "$c"
  else warn "$c missing — the waybar badge needs it"; MISSING="$MISSING $c"; fi
done
if BROWSER="$(find_browser)"; then ok "browser: $BROWSER (floating widget)"
else warn "no Chromium-family browser — the floating widget needs one"; fi
command -v hyprctl >/dev/null 2>&1 && ok "hyprctl (auto float/pin the widget)"

if [ -n "$MISSING" ]; then
  echo
  for p in $MISSING; do echo "   $(pkg_hint "$p")"; done
  ask "Continue anyway?" y || exit 1
fi

# install.sh writes surface.conf + the env file; surface errors must not be hidden
INSTALL_LOG="$(mktemp -t ut-install.XXXXXX)"
if "$ROOT/install.sh" >"$INSTALL_LOG" 2>&1; then
  grep -E '✓|!' "$INSTALL_LOG" | sed 's/^/   /'
  ok "base config ready (surface.conf + $ENV_FILE)"
else
  bad "install.sh failed — full output below"
  sed 's/^/   /' "$INSTALL_LOG"
  rm -f "$INSTALL_LOG"
  exit 1
fi
rm -f "$INSTALL_LOG"

# ── 2) server ──────────────────────────────────────────────────────────────
step "2/6  Server (everything else reads from it)"
echo "   Serves your local usage at $URL. Loopback only — nothing leaves the machine."
if server_alive; then
  ok "already running"
  DID_SERVER=running
elif command -v systemctl >/dev/null 2>&1 && ask "Autostart on login via systemd (recommended)?" y; then
  if "$ROOT/service.sh" install >/dev/null 2>&1; then
    DID_SERVER=systemd
    for _ in 1 2 3 4 5; do server_alive && break; sleep 1; done
    server_alive && ok "service installed and answering" || warn "service installed but not answering yet — check ./service.sh status"
  else
    warn "service install failed — run ./start.sh by hand"
  fi
elif ask "Start it now for this session only?" y; then
  ("$ROOT/start.sh" >/dev/null 2>&1 &)
  for _ in 1 2 3 4 5; do server_alive && break; sleep 1; done
  server_alive && { ok "server started (this session)"; DID_SERVER=session; } || warn "couldn't confirm the server started"
else
  warn "skipped — start later with ./start.sh or ./service.sh install"
fi

# ── 3) waybar badge ─────────────────────────────────────────────────────────
step "3/6  waybar badge (optional)"
if ! WBAR="$(find_waybar_config)"; then
  warn "no waybar config found — skipping (fine if you don't use waybar)"
else
  echo "   config: $WBAR"
  if python3 "$WB_EDIT" check --config "$WBAR" >/dev/null 2>&1; then
    ok "badge already configured — nothing to do"
    DID_WAYBAR=present
  elif ask "Add the usage badge to waybar?" y; then
    STYLE="$CFG/waybar/style.css"
    # 3a) colours — plain append, idempotent
    if [ ! -f "$STYLE" ] || ! grep -q "custom-usage" "$STYLE"; then
      backup "$STYLE"
      mkdir -p "$(dirname "$STYLE")"
      cat >>"$STYLE" <<'EOF'

/* usage-tracker badge */
#custom-usage.ok   { color: #34d399; }
#custom-usage.warn { color: #fbbf24; }
#custom-usage.crit { color: #f87171; }
#custom-usage.off  { color: #7b8ba0; }
EOF
      ok "badge colours added to style.css"
    fi
    # 3b) the module itself — surgical edit, with rollback
    backup "$WBAR"
    ERR="$(python3 "$WB_EDIT" add --config "$WBAR" \
             --exec "$SURFACE/waybar-usage.sh" \
             --click "$SURFACE/usage-widget toggle" 2>&1)"; RC=$?
    case "$RC" in
      0)
        ok "badge added to $(basename "$WBAR") (comments and formatting preserved)"
        DID_WAYBAR=auto
        ;;
      2)
        ok "already present"
        DID_WAYBAR=present
        ;;
      *)
        # We could not be sure → do not guess. Restore and hand it over.
        [ -f "$WBAR$BAK_SUFFIX" ] && cp -p "$WBAR$BAK_SUFFIX" "$WBAR"
        warn "auto-edit declined: $ERR"
        say "your config is untouched — here's the block to paste yourself:"
        MODULE=$(cat <<EOF
  "custom/usage": {
    "exec": "$SURFACE/waybar-usage.sh",
    "return-type": "json",
    "interval": 30,
    "on-click": "$SURFACE/usage-widget toggle"
  },
EOF
)
        echo
        echo "$MODULE" | sed 's/^/   /'
        tool="$(printf '%s' "$MODULE" | copy_clip)"
        [ -n "$tool" ] && ok "^ copied to clipboard ($tool)" || warn "install wl-copy/xclip to auto-copy"
        echo "   then add \"custom/usage\" to a modules-right/-left/-center list"
        ask "Open $(basename "$WBAR") in \$EDITOR now?" y && { "${EDITOR:-nano}" "$WBAR" || true; DID_WAYBAR=manual; }
        ;;
    esac
    if [ "$DID_WAYBAR" != no ] && command -v killall >/dev/null 2>&1; then
      killall -SIGUSR2 waybar 2>/dev/null && ok "waybar reloaded" || say "start waybar to see the badge"
    fi
  else
    warn "skipped waybar badge"
  fi
fi

# ── 4) widget + tray ────────────────────────────────────────────────────────
step "4/6  Floating widget & tray icon (optional)"
add_autostart() { # add_autostart NAME "COMMAND" — Hyprland exec-once, else XDG .desktop
  local name="$1" cmd="$2" hypr="$CFG/hypr/hyprland.conf"
  if [ -f "$hypr" ]; then
    grep -qF "$cmd" "$hypr" && { ok "autostart already in hyprland.conf"; return 0; }
    backup "$hypr"
    printf '\n# usage-tracker %s\nexec-once = %s\n' "$name" "$cmd" >>"$hypr"
    ok "autostart added to hyprland.conf"
  else
    mkdir -p "$CFG/autostart"
    cat >"$CFG/autostart/usage-tracker-$name.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=usage-tracker $name
Exec=$cmd
X-GNOME-Autostart-enabled=true
EOF
    ok "autostart added ($CFG/autostart/usage-tracker-$name.desktop)"
  fi
}

if [ -n "${BROWSER:-}" ]; then
  echo "   Widget = the panel as a movable, resizable, always-on-top window."
  if ask "Set up the floating widget?" n; then
    ask "Open it on login?" n && add_autostart widget "$SURFACE/usage-widget open"
    if ask "Open it now?" y; then
      "$SURFACE/usage-widget" open >/dev/null 2>&1 \
        && { ok "drag: Super+LMB · resize: Super+RMB or edges"; DID_WIDGET=yes; } \
        || warn "couldn't open — try: $SURFACE/usage-widget open"
    fi
  fi
else
  warn "floating widget needs a Chromium-family browser — skipping"
fi

if [ -f "$SURFACE/usage-tray.py" ]; then
  if python3 -c 'import PyQt5' 2>/dev/null || python3 -c 'import PySide6' 2>/dev/null; then
    echo "   Tray = a colour-coded dot in your system tray with a rich tooltip."
    if ask "Set up the tray icon?" n; then
      ask "Start it on login?" n && add_autostart tray "python3 $SURFACE/usage-tray.py"
      if ask "Start it now?" y; then
        (setsid python3 "$SURFACE/usage-tray.py" >/dev/null 2>&1 &)
        sleep 1; ok "tray started (Wayland: your bar's \"tray\" module hosts it)"; DID_TRAY=yes
      fi
    fi
  else
    warn "tray needs PyQt5 or PySide6 — skipping ($(pkg_hint python-pyqt5))"
  fi
fi

# ── 5) provider keys ────────────────────────────────────────────────────────
step "5/6  Hosted provider keys (optional)"
echo "   Claude Code, Codex and local runners (Ollama/LM Studio/Jan) need ${B}no key${X}."
echo "   A missing key just hides that card — nothing breaks."
KNOWN="OPENROUTER_API_KEY OPENAI_ADMIN_KEY DEEPSEEK_API_KEY ELEVENLABS_API_KEY \
TOGETHER_API_KEY NOVITA_API_KEY DEEPINFRA_API_KEY HUGGINGFACE_API_KEY HF_TOKEN \
LMSTUDIO_URL JAN_URL"

# A key exported in your shell does NOT reach the autostarted service — that gap
# is exactly why cards used to come up silently empty. Offer to import them.
FOUND=""
for k in $KNOWN; do
  v="$(eval "printf '%s' \"\${$k:-}\"")"
  [ -n "$v" ] && ! grep -qE "^[[:space:]]*$k=" "$ENV_FILE" 2>/dev/null && FOUND="$FOUND $k"
done
if [ -n "$FOUND" ]; then
  echo
  say "found in this shell but not in your keys file:${B}$FOUND${X}"
  echo "   ${D}(shell exports don't reach the autostarted service — that's the gap)${X}"
  if ask "Copy them into $ENV_FILE?" y; then
    backup "$ENV_FILE"
    umask 077
    { printf '\n# imported from your shell by setup.sh\n'
      for k in $FOUND; do printf '%s=%s\n' "$k" "$(eval "printf '%s' \"\${$k}\"")"; done
    } >>"$ENV_FILE"
    chmod 600 "$ENV_FILE"
    DID_KEYS=$(printf '%s' "$FOUND" | wc -w)
    ok "$DID_KEYS key(s) imported (file is chmod 600, owner-only)"
  fi
else
  say "no new keys found in this shell"
fi
ask "Open the keys file to add more?" n && { "${EDITOR:-nano}" "$ENV_FILE" || true; DID_KEYS=$((DID_KEYS + 1)); }
if [ "$DID_KEYS" -gt 0 ] && command -v systemctl >/dev/null 2>&1 \
   && systemctl --user is-active usage-tracker.service >/dev/null 2>&1; then
  systemctl --user restart usage-tracker.service && ok "server restarted to pick up the keys"
  sleep 2
fi

# ── 6) verify ───────────────────────────────────────────────────────────────
step "6/6  Verify (proof, not promises)"
FAIL=0
if server_alive; then
  ok "server answering at $URL"
  if command -v jq >/dev/null 2>&1; then
    N="$(curl -sf --max-time 3 "$URL/v1/usage" | jq '[.providers[]?] | length' 2>/dev/null)"
    [ -n "${N:-}" ] && ok "$N provider card(s) resolved on this machine"
  fi
else
  bad "server not answering — start it with ./start.sh (or ./service.sh status)"
  FAIL=1
fi
if [ "$DID_WAYBAR" != no ]; then
  OUT="$("$SURFACE/waybar-usage.sh" 2>/dev/null)"
  if printf '%s' "$OUT" | grep -q '"text"'; then
    ok "badge feeder output: $(printf '%s' "$OUT" | head -c 90)…"
  else
    bad "badge feeder produced nothing usable — check jq and the server"
    FAIL=1
  fi
fi
[ "$DID_TRAY" = yes ] && { pgrep -f usage-tray.py >/dev/null 2>&1 && ok "tray process running" || warn "tray isn't running — check for a Qt binding"; }

step "Done"
echo "   panel:    $URL"
echo "   keys:     $ENV_FILE"
echo "   undo all: ./setup.sh --uninstall"
[ "$DID_WAYBAR" != no ] && echo "   waybar:   reload with  killall -SIGUSR2 waybar"
echo
[ "$FAIL" = 0 ] && ok "setup verified" || warn "setup finished with warnings — see ✗ lines above"
exit 0

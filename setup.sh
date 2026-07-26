#!/usr/bin/env bash
# usage-tracker — guided setup wizard.
#
#   ./setup.sh              interactive walkthrough (recommended)
#   ./setup.sh --ui         same wizard, in a browser window
#   ./setup.sh --auto       non-interactive: safe defaults, no questions
#   ./setup.sh --uninstall  undo everything this wizard set up
#   ./setup.sh --help
#
# Steps: dependencies → server → waybar badge → widget/tray → keys → verify.
#
# Machine interface (used by --ui; stable enough to script against):
#   ./setup.sh probe                 JSON: what's installed right now, changes nothing
#   ./setup.sh preview <step>        JSON: the exact diff/lines a step would write
#   ./setup.sh do <step> [opts]      JSON: apply one step
#   ./setup.sh undo <step>           JSON: revert one step
#   steps: server · waybar · widget · tray · keys · verify
# In these modes stdout is JSON only; human lines go to stderr.
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
ENV_EDIT="$ROOT/packaging/env_edit.py"
KV2JSON="$ROOT/packaging/kv2json.py"
BAK_SUFFIX=".bak-usage-tracker"

AUTO=0
MACHINE=0          # 1 = stdout is JSON, human lines go to stderr
MODE=install       # install | uninstall | ui | probe | preview | do | undo
STEP=""
# step options (parsed from `do` flags)
O_MODE=""; O_AUTOSTART=0; O_OPEN=0; O_START=0
O_IMPORT=0; O_SET_STDIN=0; O_DELETE=""; O_LIST="modules-right"

case "${1:-}" in
  probe|preview|do|undo)
    MACHINE=1; MODE="$1"; shift
    if [ "$MODE" != probe ]; then
      STEP="${1:-}"; shift || true
      [ -n "$STEP" ] || { echo '{"ok":false,"error":"missing step"}'; exit 2; }
    fi
    while [ $# -gt 0 ]; do
      case "$1" in
        --mode)       O_MODE="${2:-}"; shift 2 ;;
        --list)       O_LIST="${2:-modules-right}"; shift 2 ;;
        --delete)     O_DELETE="${2:-}"; shift 2 ;;
        --autostart)  O_AUTOSTART=1; shift ;;
        --open)       O_OPEN=1; shift ;;
        --start)      O_START=1; shift ;;
        --import-shell) O_IMPORT=1; shift ;;
        --set-stdin)  O_SET_STDIN=1; shift ;;
        *) echo "{\"ok\":false,\"error\":\"unknown option: $1\"}"; exit 2 ;;
      esac
    done
    ;;
  *)
    for a in "$@"; do
      case "$a" in
        --auto|-y|--yes)   AUTO=1 ;;
        --uninstall)       MODE=uninstall ;;
        --ui)              MODE=ui ;;
        -h|--help)         sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
      esac
    done
    ;;
esac

# ── output plumbing ────────────────────────────────────────────────────────
# In machine mode the same say/ok/warn/bad calls still narrate to stderr, but
# they're also collected into the JSON so the browser can show the very same
# lines the terminal would have. One source of truth, two faces.
if [ -t 1 ] && [ "$MACHINE" = 0 ]; then
  C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; R=$'\033[31m'; X=$'\033[0m'
else C=""; G=""; Y=""; D=""; B=""; R=""; X=""; fi

KV_FILE=""
kv() { [ -n "$KV_FILE" ] && printf '%s\t%s\t%s\n' "$1" "$2" "$(printf '%s' "${3-}" | tr '\n\t' '  ')" >>"$KV_FILE"; }
kv_flush() {
  [ -n "$KV_FILE" ] || return 0
  python3 "$KV2JSON" <"$KV_FILE"
  rm -f "$KV_FILE"; KV_FILE=""
}
msg() { [ "$MACHINE" = 1 ] && kv messages a "$1|$2"; return 0; }
_out() { if [ "$MACHINE" = 1 ]; then printf '%s\n' "$*" >&2; else printf '%s\n' "$*"; fi; }

say()  { _out "$C▸$X $*"; msg info "$*"; }
ok()   { _out "$G✓$X $*"; msg ok   "$*"; }
warn() { _out "$Y!$X $*"; msg warn "$*"; }
bad()  { _out "$R✗$X $*"; msg bad  "$*"; }
step() { _out ""; _out "$B$C──$X $B$*$X"; }

die_json() { printf '{"ok":false,"error":"%s"}\n' "$1"; exit 1; }

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
  if command -v curl >/dev/null 2>&1; then
    curl -sf --max-time 3 "$URL/v1/usage" 2>/dev/null | grep -q '"providers"' && return 0
    return 1
  fi
  # no curl (the badge needs it, the wizard shouldn't) — python3 is already required
  python3 - "$URL/v1/usage" <<'PY' 2>/dev/null
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as r:
        sys.exit(0 if b'"providers"' in r.read(65536) else 1)
except Exception:
    sys.exit(1)
PY
}

qt_binding() { # which Qt binding the tray can use, if any
  python3 -c 'import PyQt5' 2>/dev/null && { echo PyQt5; return 0; }
  python3 -c 'import PySide6' 2>/dev/null && { echo PySide6; return 0; }
  return 1
}

waybar_snippet() {
  cat <<EOF
  "custom/usage": {
    "exec": "$SURFACE/waybar-usage.sh",
    "return-type": "json",
    "interval": 30,
    "on-click": "$SURFACE/usage-widget toggle"
  },
EOF
}

STYLE_BLOCK='
/* usage-tracker badge */
#custom-usage.ok   { color: #34d399; }
#custom-usage.warn { color: #fbbf24; }
#custom-usage.crit { color: #f87171; }
#custom-usage.off  { color: #7b8ba0; }'

# ════════════════════════════════════════════════════════════════════════════
# AUTOSTART (shared by the widget and tray steps)
# ════════════════════════════════════════════════════════════════════════════
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

has_autostart() { # has_autostart NAME COMMAND → 0 if present
  local name="$1" cmd="$2" hypr="$CFG/hypr/hyprland.conf"
  [ -f "$hypr" ] && grep -qF "$cmd" "$hypr" && return 0
  [ -f "$CFG/autostart/usage-tracker-$name.desktop" ] && return 0
  return 1
}

remove_autostart() { # remove_autostart NAME — exact removal, never a line more
  local name="$1" hypr="$CFG/hypr/hyprland.conf" hit=0
  if [ -f "$hypr" ] && grep -q "# usage-tracker $name\$" "$hypr"; then
    backup "$hypr"
    # Only drop the marker line and, if it directly follows, its exec-once line.
    # A blind `sed '/marker/,+1d'` would eat an innocent neighbour when the
    # exec-once was edited away by hand.
    python3 - "$hypr" "$name" <<'PY' && hit=1
import sys
path, name = sys.argv[1], sys.argv[2]
marker = f"# usage-tracker {name}"
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
out, i = [], 0
while i < len(lines):
    if lines[i].strip() == marker:
        i += 1
        if i < len(lines) and lines[i].lstrip().startswith("exec-once") and "usage-" in lines[i]:
            i += 1
        while out and out[-1].strip() == "":   # drop the blank line we added
            out.pop()
        out.append("\n")
        continue
    out.append(lines[i]); i += 1
open(path, "w", encoding="utf-8").writelines(out)
PY
    [ "$hit" = 1 ] && ok "autostart lines removed from hyprland.conf"
  fi
  if [ -f "$CFG/autostart/usage-tracker-$name.desktop" ]; then
    rm -f "$CFG/autostart/usage-tracker-$name.desktop" && { ok "autostart .desktop removed ($name)"; hit=1; }
  fi
  [ "$hit" = 1 ] || say "no autostart entry for $name"
  return 0
}

# ════════════════════════════════════════════════════════════════════════════
# PROBE — read-only picture of the machine
# ════════════════════════════════════════════════════════════════════════════
# Hosted providers we know how to look for. Claude Code, Codex and the local
# runners are deliberately absent: they need no key at all.
KNOWN_KEYS="OPENROUTER_API_KEY OPENAI_ADMIN_KEY DEEPSEEK_API_KEY ELEVENLABS_API_KEY \
TOGETHER_API_KEY NOVITA_API_KEY DEEPINFRA_API_KEY HUGGINGFACE_API_KEY HF_TOKEN \
LMSTUDIO_URL JAN_URL"

probe_all() {
  local v br wb rc out qt

  # 1) dependencies
  if command -v python3 >/dev/null 2>&1; then
    v="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
    kv deps.python3 s "$v"
  else kv deps.python3 z; fi
  for c in curl jq; do
    if command -v "$c" >/dev/null 2>&1; then kv "deps.$c" b true
    else kv "deps.$c" b false; kv "deps.pkg_hint.$c" s "$(pkg_hint "$c")"; fi
  done
  if br="$(find_browser)"; then kv deps.browser s "$br"; else kv deps.browser z; fi
  command -v hyprctl   >/dev/null 2>&1 && kv deps.hyprctl b true   || kv deps.hyprctl b false
  command -v systemctl >/dev/null 2>&1 && kv deps.systemctl b true || kv deps.systemctl b false
  command -v waybar    >/dev/null 2>&1 && kv deps.waybar b true    || kv deps.waybar b false
  if qt="$(qt_binding)"; then kv deps.qt s "$qt"; else kv deps.qt z; kv deps.pkg_hint.qt s "$(pkg_hint python-pyqt5)"; fi

  # 1b) our own config files (install.sh's output)
  [ -f "$SURFACE/surface.conf" ] && kv base.surface_conf b true || kv base.surface_conf b false
  [ -f "$ENV_FILE" ]             && kv base.env_file b true     || kv base.env_file b false

  # 2) server
  kv server.port n "$PORT"
  kv server.url  s "$URL"
  if server_alive; then kv server.alive b true; else kv server.alive b false; fi
  if command -v systemctl >/dev/null 2>&1 \
     && systemctl --user is-enabled usage-tracker.service >/dev/null 2>&1; then
    kv server.mode s systemd
  else
    kv server.mode z
  fi

  # 3) waybar
  if wb="$(find_waybar_config)"; then
    kv waybar.config s "$wb"
    if python3 "$WB_EDIT" check --config "$wb" >/dev/null 2>&1; then
      kv waybar.badge s present
      kv waybar.editable b true
    else
      kv waybar.badge s absent
      # Ask the real editor whether it *could* do it — so the card can say
      # "manual paste needed" before the user clicks, not after.
      python3 "$WB_EDIT" add --config "$wb" --exec "$SURFACE/waybar-usage.sh" \
              --click "$SURFACE/usage-widget toggle" --list "$O_LIST" --dry-run >/dev/null 2>&1
      rc=$?
      [ "$rc" = 0 ] && kv waybar.editable b true || kv waybar.editable b false
      [ "$rc" = 3 ] && kv waybar.reason s "config can't be edited safely — paste the snippet yourself"
    fi
    if [ -f "$CFG/waybar/style.css" ] && grep -q "custom-usage" "$CFG/waybar/style.css" 2>/dev/null; then
      kv waybar.styled b true
    else kv waybar.styled b false; fi
  else
    kv waybar.config z
    kv waybar.badge s no-config
    kv waybar.editable b false
  fi

  # 4) widget + 5) tray
  has_autostart widget "$SURFACE/usage-widget open" && kv widget.autostart b true || kv widget.autostart b false
  if [ -n "${br:-}" ] && command -v hyprctl >/dev/null 2>&1 \
     && "$SURFACE/usage-widget" status 2>/dev/null | grep -q open; then
    kv widget.running b true
  else kv widget.running b false; fi

  has_autostart tray "python3 $SURFACE/usage-tray.py" && kv tray.autostart b true || kv tray.autostart b false
  pgrep -f usage-tray.py >/dev/null 2>&1 && kv tray.running b true || kv tray.running b false

  # 6) keys — names only, never values
  kv keys.file s "$ENV_FILE"
  local k val
  for k in $KNOWN_KEYS; do
    kv keys.known a "$k"
    if grep -qE "^[[:space:]]*$k=." "$ENV_FILE" 2>/dev/null; then
      kv keys.set a "$k"
    else
      # indirect expansion, not eval: the name never reaches a parser
      val="${!k:-}"
      [ -n "$val" ] && kv keys.shell_only a "$k"
    fi
  done
}

# ════════════════════════════════════════════════════════════════════════════
# ACTIONS — the only code that changes anything. Both faces call these.
# ════════════════════════════════════════════════════════════════════════════
CHANGED=0
ACT_RC=0
ACT_SNIPPET=""
ENV_EDIT_N=0
ENV_EDIT_ERR=""

act_base_config() { # install.sh: surface.conf + env file + exec bits
  local log; log="$(mktemp -t ut-install.XXXXXX)"
  if "$ROOT/install.sh" >"$log" 2>&1; then
    grep -E '✓|!' "$log" | sed 's/^/   /' | while IFS= read -r l; do _out "$l"; done
    ok "base config ready (surface.conf + $ENV_FILE)"
    rm -f "$log"; CHANGED=1; return 0
  fi
  bad "install.sh failed — full output below"
  sed 's/^/   /' "$log" | while IFS= read -r l; do _out "$l"; done
  rm -f "$log"; return 1
}

act_server() { # act_server systemd|session
  local mode="${1:-systemd}"
  if server_alive; then ok "already running"; return 0; fi
  case "$mode" in
    systemd)
      command -v systemctl >/dev/null 2>&1 || { warn "systemctl not found — use session mode or ./start.sh"; return 1; }
      if "$ROOT/service.sh" install >/dev/null 2>&1; then
        CHANGED=1
        for _ in 1 2 3 4 5; do server_alive && break; sleep 1; done
        server_alive && ok "service installed and answering" \
                     || warn "service installed but not answering yet — check ./service.sh status"
        return 0
      fi
      warn "service install failed — run ./start.sh by hand"; return 1 ;;
    session)
      ("$ROOT/start.sh" >/dev/null 2>&1 &)
      for _ in 1 2 3 4 5; do server_alive && break; sleep 1; done
      if server_alive; then ok "server started (this session)"; CHANGED=1; return 0; fi
      warn "couldn't confirm the server started"; return 1 ;;
    *) warn "unknown server mode: $mode"; return 1 ;;
  esac
}

act_server_undo() {
  command -v systemctl >/dev/null 2>&1 || { say "no systemd — nothing to remove"; return 0; }
  if "$ROOT/service.sh" uninstall >/dev/null 2>&1; then ok "systemd user service removed"; CHANGED=1
  else warn "no service to remove"; fi
  return 0
}

act_waybar_style() { # colours — plain append, idempotent
  local style="$CFG/waybar/style.css"
  if [ -f "$style" ] && grep -q "custom-usage" "$style"; then return 0; fi
  backup "$style"
  mkdir -p "$(dirname "$style")"
  printf '%s\n' "$STYLE_BLOCK" >>"$style"
  ok "badge colours added to style.css"; CHANGED=1
}

act_waybar_add() { # sets ACT_RC: 0 added · 2 already · 3 needs manual paste · 1 error
  local wbar err rc
  if ! wbar="$(find_waybar_config)"; then
    warn "no waybar config found — skipping (fine if you don't use waybar)"
    ACT_RC=1; return 1
  fi
  if python3 "$WB_EDIT" check --config "$wbar" >/dev/null 2>&1; then
    ok "badge already configured — nothing to do"; ACT_RC=2; return 0
  fi
  act_waybar_style
  backup "$wbar"
  err="$(python3 "$WB_EDIT" add --config "$wbar" \
           --exec "$SURFACE/waybar-usage.sh" \
           --click "$SURFACE/usage-widget toggle" \
           --list "$O_LIST" 2>&1)"; rc=$?
  case "$rc" in
    0) ok "badge added to $(basename "$wbar") (comments and formatting preserved)"
       CHANGED=1; ACT_RC=0 ;;
    2) ok "already present"; ACT_RC=2 ;;
    *) # We could not be sure → do not guess. Restore and hand it over.
       [ -f "$wbar$BAK_SUFFIX" ] && cp -p "$wbar$BAK_SUFFIX" "$wbar"
       warn "auto-edit declined: $err"
       ACT_SNIPPET="$(waybar_snippet)"
       ACT_RC=3 ;;
  esac
  return 0
}

act_waybar_remove() {
  local wbar style
  if ! wbar="$(find_waybar_config)"; then say "no waybar config — nothing to remove"; return 0; fi
  backup "$wbar"
  python3 "$WB_EDIT" remove --config "$wbar" >/dev/null 2>&1
  case $? in
    0) ok "badge removed from $(basename "$wbar")"; CHANGED=1 ;;
    2) say "badge wasn't in $(basename "$wbar")" ;;
    *) warn "couldn't edit $(basename "$wbar") — remove the \"custom/usage\" block by hand" ;;
  esac
  style="$CFG/waybar/style.css"
  if [ -f "$style" ] && grep -q "usage-tracker badge" "$style"; then
    backup "$style"
    python3 - "$style" <<'PY' && { ok "badge colours removed from style.css"; CHANGED=1; }
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s = re.sub(r"\n*/\* usage-tracker badge \*/\n(?:#custom-usage[^\n]*\n)*", "\n", s)
open(p, "w", encoding="utf-8").write(s)
PY
  fi
  reload_waybar
  return 0
}

reload_waybar() {
  command -v killall >/dev/null 2>&1 || return 0
  killall -SIGUSR2 waybar 2>/dev/null && ok "waybar reloaded" || say "start waybar to see the badge"
  return 0
}

act_widget() { # act_widget autostart(0|1) open(0|1)
  local wa="${1:-0}" wo="${2:-0}"
  if ! find_browser >/dev/null; then
    warn "floating widget needs a Chromium-family browser — skipping"; return 1
  fi
  [ "$wa" = 1 ] && add_autostart widget "$SURFACE/usage-widget open"
  if [ "$wo" = 1 ]; then
    if "$SURFACE/usage-widget" open >/dev/null 2>&1; then
      ok "drag: Super+LMB · resize: Super+RMB or edges"; CHANGED=1
    else
      warn "couldn't open — try: $SURFACE/usage-widget open"; return 1
    fi
  fi
  return 0
}

act_widget_undo() {
  remove_autostart widget
  "$SURFACE/usage-widget" close >/dev/null 2>&1 && ok "widget closed"
  CHANGED=1; return 0
}

act_tray() { # act_tray autostart(0|1) start(0|1)
  local ta="${1:-0}" ts="${2:-0}"
  [ -f "$SURFACE/usage-tray.py" ] || { warn "tray script not found"; return 1; }
  if ! qt_binding >/dev/null; then
    warn "tray needs PyQt5 or PySide6 — skipping ($(pkg_hint python-pyqt5))"; return 1
  fi
  [ "$ta" = 1 ] && add_autostart tray "python3 $SURFACE/usage-tray.py"
  if [ "$ts" = 1 ]; then
    if pgrep -f usage-tray.py >/dev/null 2>&1; then
      ok "tray already running"
    else
      (setsid python3 "$SURFACE/usage-tray.py" >/dev/null 2>&1 &)
      sleep 1
      ok "tray started (Wayland: your bar's \"tray\" module hosts it)"; CHANGED=1
    fi
  fi
  return 0
}

act_tray_undo() {
  remove_autostart tray
  pkill -f "$SURFACE/usage-tray.py" 2>/dev/null && ok "tray stopped"
  CHANGED=1; return 0
}

shell_only_keys() { # names exported in this shell but missing from the env file
  local k v found=""
  for k in $KNOWN_KEYS; do
    v="${!k:-}"
    [ -n "$v" ] && ! grep -qE "^[[:space:]]*$k=." "$ENV_FILE" 2>/dev/null && found="$found $k"
  done
  printf '%s' "$found"
}

# Run env_edit and translate its exit code into an honest message.
# 0 = wrote N · 1 = the write itself failed (say why) · 2 = no valid names.
# Reporting a failed write as "nothing written — check the name" is worse than
# saying nothing: it points the user at the one thing that wasn't wrong.
run_env_edit() { # run_env_edit <verb> <args...>  (stdin = data for `set`)
  local errf rc out
  errf="$(mktemp -t ut-env.XXXXXX)" || errf=/dev/null
  out="$(python3 "$ENV_EDIT" "$@" 2>"$errf")"; rc=$?
  ENV_EDIT_N="${out:-0}"
  ENV_EDIT_ERR="$(head -1 "$errf" 2>/dev/null)"
  [ "$errf" = /dev/null ] || rm -f "$errf"
  return $rc
}

act_keys_import() { # copy shell exports into the env file
  local found k
  found="$(shell_only_keys)"
  [ -n "$found" ] || { say "no new keys found in this shell"; return 0; }
  backup "$ENV_FILE"
  # Same writer as the browser path: it replaces the template's commented line
  # in place instead of appending a second one for the same name.
  for k in $found; do printf '%s=%s\n' "$k" "${!k:-}"; done \
    | run_env_edit set --file "$ENV_FILE"
  case $? in
    0) ok "$ENV_EDIT_N key(s) imported (file is chmod 600, owner-only)"; CHANGED=1; return 0 ;;
    1) bad "couldn't write $ENV_FILE — ${ENV_EDIT_ERR:-unknown error}"; return 1 ;;
    *) warn "nothing imported"; return 1 ;;
  esac
}

act_keys_set_stdin() { # read NAME=VALUE lines from stdin — never from argv
  # Keys must not travel through the command line: argv is world-readable in
  # /proc and lands in shell history. stdin is the only sane channel, which is
  # also why the editor is a real file and not a heredoc (a heredoc *is* stdin).
  run_env_edit set --file "$ENV_FILE"
  case $? in
    0) ok "$ENV_EDIT_N key(s) written (file is chmod 600, owner-only)"; CHANGED=1; return 0 ;;
    1) bad "couldn't write $ENV_FILE — ${ENV_EDIT_ERR:-unknown error}"; return 1 ;;
    *) warn "nothing written — names must look like OPENROUTER_API_KEY"; return 1 ;;
  esac
}

act_keys_delete() { # act_keys_delete NAME — comment it out, keep the template line
  local name="$1"
  case "$name" in
    [A-Z][A-Z0-9_]*) ;;
    *) warn "not a valid key name: $name"; return 1 ;;
  esac
  [ -f "$ENV_FILE" ] || { warn "no keys file"; return 1; }
  backup "$ENV_FILE"
  run_env_edit delete --file "$ENV_FILE" --name "$name" </dev/null
  case $? in
    0) ok "$name removed"; CHANGED=1; return 0 ;;
    1) bad "couldn't write $ENV_FILE — ${ENV_EDIT_ERR:-unknown error}"; return 1 ;;
    # not in the file = already gone; the outcome the caller asked for holds
    *) say "$name wasn't in $ENV_FILE — nothing to remove"; return 0 ;;
  esac
}

restart_if_running() { # pick up new keys without asking the user to know how
  command -v systemctl >/dev/null 2>&1 || return 0
  systemctl --user is-active usage-tracker.service >/dev/null 2>&1 || return 0
  systemctl --user restart usage-tracker.service && ok "server restarted to pick up the keys"
  sleep 2
  return 0
}

act_verify() { # proof, not promises — sets VERIFY_FAIL
  VERIFY_FAIL=0
  if server_alive; then
    ok "server answering at $URL"
    kv verify.server b true
    if command -v jq >/dev/null 2>&1; then
      local n
      n="$(curl -sf --max-time 3 "$URL/v1/usage" 2>/dev/null | jq '[.providers[]?] | length' 2>/dev/null)"
      if [ -n "${n:-}" ]; then ok "$n provider card(s) resolved on this machine"; kv verify.providers n "$n"; fi
    fi
  else
    bad "server not answering — start it with ./start.sh (or ./service.sh status)"
    kv verify.server b false
    VERIFY_FAIL=1
  fi
  local wbar
  if wbar="$(find_waybar_config)" && python3 "$WB_EDIT" check --config "$wbar" >/dev/null 2>&1; then
    local out
    out="$("$SURFACE/waybar-usage.sh" 2>/dev/null)"
    if printf '%s' "$out" | grep -q '"text"'; then
      ok "badge feeder output: $(printf '%s' "$out" | head -c 90)…"
      kv verify.badge b true
      kv verify.badge_text s "$(printf '%s' "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("text",""))' 2>/dev/null)"
    else
      bad "badge feeder produced nothing usable — check jq and the server"
      kv verify.badge b false
      VERIFY_FAIL=1
    fi
  fi
  if pgrep -f usage-tray.py >/dev/null 2>&1; then ok "tray process running"; kv verify.tray b true; fi
  return 0
}

# ════════════════════════════════════════════════════════════════════════════
# MACHINE MODES — probe / preview / do / undo
# ════════════════════════════════════════════════════════════════════════════
if [ "$MACHINE" = 1 ]; then
  KV_FILE="$(mktemp -t ut-kv.XXXXXX)"
  trap '[ -n "$KV_FILE" ] && rm -f "$KV_FILE"' EXIT

  case "$MODE" in
    probe)
      probe_all
      kv ok b true
      kv_flush; exit 0 ;;

    preview)
      kv step s "$STEP"
      case "$STEP" in
        waybar)
          if wbar="$(find_waybar_config)"; then
            kv config s "$wbar"
            tmp="$(mktemp -t ut-dry.XXXXXX)"
            python3 "$WB_EDIT" add --config "$wbar" --exec "$SURFACE/waybar-usage.sh" \
                    --click "$SURFACE/usage-widget toggle" --list "$O_LIST" --dry-run >"$tmp" 2>/dev/null
            rc=$?
            if [ "$rc" = 0 ] && [ -s "$tmp" ]; then
              # A real diff of the real edit — not a mock-up of one.
              while IFS= read -r l; do kv diff a "$l"; done < <(diff -u "$wbar" "$tmp" | tail -n +3)
              kv state s editable
            elif [ "$rc" = 2 ]; then
              kv state s present
              kv note s "already present"
            else
              kv state s manual
              kv note s "can't edit safely — paste the snippet yourself"
              while IFS= read -r l; do kv snippet a "$l"; done < <(waybar_snippet)
            fi
            rm -f "$tmp"
          else
            kv state s no-config
            kv note s "no waybar config found"
          fi
          if [ ! -f "$CFG/waybar/style.css" ] || ! grep -q "custom-usage" "$CFG/waybar/style.css" 2>/dev/null; then
            while IFS= read -r l; do kv style a "$l"; done <<<"$STYLE_BLOCK"
          fi
          ;;
        widget|tray)
          if [ "$STEP" = widget ]; then cmd="$SURFACE/usage-widget open"
          else cmd="python3 $SURFACE/usage-tray.py"; fi
          if [ -f "$CFG/hypr/hyprland.conf" ]; then
            kv target s "$CFG/hypr/hyprland.conf"
            kv autostart a "# usage-tracker $STEP"
            kv autostart a "exec-once = $cmd"
          else
            kv target s "$CFG/autostart/usage-tracker-$STEP.desktop"
            kv autostart a "[Desktop Entry]"
            kv autostart a "Type=Application"
            kv autostart a "Name=usage-tracker $STEP"
            kv autostart a "Exec=$cmd"
            kv autostart a "X-GNOME-Autostart-enabled=true"
          fi
          ;;
        server)
          kv target s "$CFG/systemd/user/usage-tracker.service"
          kv note s "a systemd --user unit; no root, starts on login" ;;
        keys)
          kv target s "$ENV_FILE"
          for k in $(shell_only_keys); do kv importable a "$k"; done ;;
        *) die_json "no preview for step: $STEP" ;;
      esac
      kv ok b true
      kv_flush; exit 0 ;;

    do)
      kv step s "$STEP"
      ACT_OK=1
      case "$STEP" in
        base)   act_base_config || ACT_OK=0 ;;
        server) act_base_config >/dev/null 2>&1 || ACT_OK=0
                act_server "${O_MODE:-systemd}" || ACT_OK=0 ;;
        waybar) act_waybar_add || ACT_OK=0
                if [ "$ACT_RC" = 3 ]; then
                  kv needs_manual b true
                  while IFS= read -r l; do kv snippet a "$l"; done <<<"$ACT_SNIPPET"
                fi
                [ "$CHANGED" = 1 ] && reload_waybar
                ;;
        widget) act_widget "$O_AUTOSTART" "$O_OPEN" || ACT_OK=0 ;;
        tray)   act_tray   "$O_AUTOSTART" "$O_START" || ACT_OK=0 ;;
        keys)   [ "$O_IMPORT" = 1 ]    && { act_keys_import           || ACT_OK=0; }
                [ "$O_SET_STDIN" = 1 ] && { act_keys_set_stdin        || ACT_OK=0; }
                [ -n "$O_DELETE" ]     && { act_keys_delete "$O_DELETE" || ACT_OK=0; }
                [ "$CHANGED" = 1 ]     && restart_if_running
                ;;
        verify) act_verify; [ "${VERIFY_FAIL:-0}" = 0 ] || ACT_OK=0 ;;
        *) die_json "unknown step: $STEP" ;;
      esac
      kv ok b "$([ "$ACT_OK" = 1 ] && echo true || echo false)"
      kv changed b "$([ "$CHANGED" = 1 ] && echo true || echo false)"
      kv_flush; exit 0 ;;

    undo)
      kv step s "$STEP"
      ACT_OK=1
      case "$STEP" in
        server) act_server_undo || ACT_OK=0 ;;
        waybar) act_waybar_remove || ACT_OK=0 ;;
        widget) act_widget_undo || ACT_OK=0 ;;
        tray)   act_tray_undo || ACT_OK=0 ;;
        keys)   [ -n "$O_DELETE" ] || die_json "keys: pass --delete NAME (we never wipe your keys file)"
                act_keys_delete "$O_DELETE" || ACT_OK=0 ;;
        *) die_json "unknown step: $STEP" ;;
      esac
      kv ok b "$([ "$ACT_OK" = 1 ] && echo true || echo false)"
      kv changed b "$([ "$CHANGED" = 1 ] && echo true || echo false)"
      kv_flush; exit 0 ;;
  esac
fi

# ════════════════════════════════════════════════════════════════════════════
# --ui — the same wizard, in a browser window
# ════════════════════════════════════════════════════════════════════════════
if [ "$MODE" = ui ]; then
  command -v python3 >/dev/null 2>&1 || { bad "python3 is required for --ui"; exit 1; }
  say "starting the setup wizard (loopback only, one-time token, closes itself when idle)"
  UI_OUT="$(mktemp -t ut-ui.XXXXXX)" || UI_OUT=""
  [ -n "$UI_OUT" ] || { bad "cannot create a temp file — is \$TMPDIR writable?"; exit 1; }
  python3 "$ROOT/packaging/setup_server.py" --url-file "$UI_OUT" &
  SRV_PID=$!
  trap 'kill "$SRV_PID" 2>/dev/null; rm -f "$UI_OUT"' EXIT INT TERM
  for _ in $(seq 1 60); do [ -s "$UI_OUT" ] && break; sleep 0.1; done
  UI_URL="$(cat "$UI_OUT" 2>/dev/null)"
  if [ -z "$UI_URL" ]; then bad "the wizard server didn't start"; exit 1; fi
  ok "wizard: $UI_URL"
  say "the token in that URL is what authorises changes — don't share it"
  if BROWSER="$(find_browser)"; then
    PROFILE="${XDG_DATA_HOME:-$HOME/.local/share}/usage-tracker/setup-profile"
    mkdir -p "$PROFILE"
    setsid "$BROWSER" --app="$UI_URL" --user-data-dir="$PROFILE" --window-size=1020,860 \
      --no-first-run --no-default-browser-check --disable-features=Translate \
      >/dev/null 2>&1 </dev/null &
    disown 2>/dev/null || true
    ok "opened in $BROWSER"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$UI_URL" >/dev/null 2>&1 &
    ok "opened in your default browser"
  else
    warn "no browser found — open the URL above yourself"
  fi
  echo
  say "Ctrl+C here (or 'Finish' in the window) closes the wizard."
  wait "$SRV_PID"
  exit 0
fi

# ════════════════════════════════════════════════════════════════════════════
# UNINSTALL
# ════════════════════════════════════════════════════════════════════════════
if [ "$MODE" = uninstall ]; then
  step "Removing usage-tracker's setup (your data and the repo stay)"
  echo "   This will: stop + remove the systemd service, take the badge out of"
  echo "   your waybar config and style.css, and drop autostart entries."
  echo "   Every file is backed up as <file>$BAK_SUFFIX first."
  ask "Go ahead?" y || { say "nothing changed"; exit 0; }
  act_server_undo
  act_waybar_remove
  remove_autostart widget
  remove_autostart tray
  echo
  say "kept on purpose: $ENV_FILE (your API keys) and the repo itself."
  echo "   delete them yourself if you want a clean slate:"
  echo "     rm -rf $CFG/usage-tracker  $ROOT"
  exit 0
fi

# ════════════════════════════════════════════════════════════════════════════
# INSTALL (interactive)
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
[ "$AUTO" = 0 ] && say "prefer a window over a terminal? ${B}./setup.sh --ui${X}"

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

act_base_config || exit 1

# ── 2) server ──────────────────────────────────────────────────────────────
step "2/6  Server (everything else reads from it)"
echo "   Serves your local usage at $URL. Loopback only — nothing leaves the machine."
if server_alive; then
  ok "already running"
  DID_SERVER=running
elif command -v systemctl >/dev/null 2>&1 && ask "Autostart on login via systemd (recommended)?" y; then
  act_server systemd && DID_SERVER=systemd
elif ask "Start it now for this session only?" y; then
  act_server session && DID_SERVER=session
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
    act_waybar_add
    case "$ACT_RC" in
      0) DID_WAYBAR=auto ;;
      2) DID_WAYBAR=present ;;
      3) say "your config is untouched — here's the block to paste yourself:"
         echo
         printf '%s\n' "$ACT_SNIPPET" | sed 's/^/   /'
         tool="$(printf '%s' "$ACT_SNIPPET" | copy_clip)"
         [ -n "$tool" ] && ok "^ copied to clipboard ($tool)" || warn "install wl-copy/xclip to auto-copy"
         echo "   then add \"custom/usage\" to a modules-right/-left/-center list"
         ask "Open $(basename "$WBAR") in \$EDITOR now?" y && { "${EDITOR:-nano}" "$WBAR" || true; DID_WAYBAR=manual; } ;;
    esac
    [ "$DID_WAYBAR" != no ] && reload_waybar
  else
    warn "skipped waybar badge"
  fi
fi

# ── 4) widget + tray ────────────────────────────────────────────────────────
step "4/6  Floating widget & tray icon (optional)"
if [ -n "${BROWSER:-}" ]; then
  echo "   Widget = the panel as a movable, resizable, always-on-top window."
  if ask "Set up the floating widget?" n; then
    W_AUTO=0; W_OPEN=0
    ask "Open it on login?" n && W_AUTO=1
    ask "Open it now?" y && W_OPEN=1
    act_widget "$W_AUTO" "$W_OPEN" && [ "$W_OPEN" = 1 ] && DID_WIDGET=yes
  fi
else
  warn "floating widget needs a Chromium-family browser — skipping"
fi

if [ -f "$SURFACE/usage-tray.py" ]; then
  if qt_binding >/dev/null; then
    echo "   Tray = a colour-coded dot in your system tray with a rich tooltip."
    if ask "Set up the tray icon?" n; then
      T_AUTO=0; T_START=0
      ask "Start it on login?" n && T_AUTO=1
      ask "Start it now?" y && T_START=1
      act_tray "$T_AUTO" "$T_START" && [ "$T_START" = 1 ] && DID_TRAY=yes
    fi
  else
    warn "tray needs PyQt5 or PySide6 — skipping ($(pkg_hint python-pyqt5))"
  fi
fi

# ── 5) provider keys ────────────────────────────────────────────────────────
step "5/6  Hosted provider keys (optional)"
echo "   Claude Code, Codex and local runners (Ollama/LM Studio/Jan) need ${B}no key${X}."
echo "   A missing key just hides that card — nothing breaks."

# A key exported in your shell does NOT reach the autostarted service — that gap
# is exactly why cards used to come up silently empty. Offer to import them.
FOUND="$(shell_only_keys)"
if [ -n "$FOUND" ]; then
  echo
  say "found in this shell but not in your keys file:${B}$FOUND${X}"
  echo "   ${D}(shell exports don't reach the autostarted service — that's the gap)${X}"
  if ask "Copy them into $ENV_FILE?" y; then
    act_keys_import
    DID_KEYS=$(printf '%s' "$FOUND" | wc -w)
  fi
else
  say "no new keys found in this shell"
fi
ask "Open the keys file to add more?" n && { "${EDITOR:-nano}" "$ENV_FILE" || true; DID_KEYS=$((DID_KEYS + 1)); }
[ "$DID_KEYS" -gt 0 ] && restart_if_running

# ── 6) verify ───────────────────────────────────────────────────────────────
step "6/6  Verify (proof, not promises)"
act_verify
FAIL="$VERIFY_FAIL"

step "Done"
echo "   panel:    $URL"
echo "   keys:     $ENV_FILE"
echo "   undo all: ./setup.sh --uninstall"
[ "$DID_WAYBAR" != no ] && echo "   waybar:   reload with  killall -SIGUSR2 waybar"
echo
[ "$FAIL" = 0 ] && ok "setup verified" || warn "setup finished with warnings — see ✗ lines above"
exit 0

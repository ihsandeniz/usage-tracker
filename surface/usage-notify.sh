#!/usr/bin/env bash
# usage-notify — proaktif limit uyarısı; %75/%90 eşiklerinde sistem bildirimi gönder.
# CodexBar parity: uyarı (warn) + kritik (crit) bildirimleri.
#
# Kullanım:
#   usage-notify.sh once         tek kontrol (cron/systemd timer için)
#   usage-notify.sh watch [n]    döngü (n saniye aralık, varsayılan 300)
#
# Yapılandırma (bashrc / surface.conf):
#   USAGE_PORT=8770              (varsayılan; surface.conf'ta varsa okunur)
#   USAGE_NOTIFY_THRESHOLDS      "%75,%90" (virgülle ayrılmış; varsayılan)
#   USAGE_NOTIFY_STATE_DIR       ~/.local/share/usage-tracker (XDG fallback)
#
# Durum:
#   - ~/.local/share/usage-tracker/notify-state dosyası
#   - en son bildirilen eşik; yeni eşik geçildiğinde yeniden bildir
#   - limit reset olunca (pct düşünce) sıfırla
#
set -u

SELF_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CONF="$SELF_DIR/surface.conf"
[ -f "$CONF" ] && . "$CONF" || true

# config
PORT="${USAGE_PORT:-8770}"
THRESHOLDS="${USAGE_NOTIFY_THRESHOLDS:-75,90}"
STATE_DIR="${USAGE_NOTIFY_STATE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/usage-tracker}"
STATE_FILE="$STATE_DIR/notify-state"
URL="http://127.0.0.1:$PORT/v1/usage"

# parse thresholds
IFS=',' read -ra THRESH_ARRAY <<< "$THRESHOLDS"

# bağımlılık kontrolleri
check_deps() {
  if ! command -v notify-send >/dev/null 2>&1; then
    echo "ERROR: notify-send not found (install libnotify)" >&2
    return 1
  fi
  return 0
}

# jq VARSA kullan, yoksa python3 fallback
has_jq() { command -v jq >/dev/null 2>&1; }

# JSON'dan en yüksek Claude limit %'sini çıkar (oturum + haftalık + haftalık-model)
extract_max_claude_pct() {
  local json="$1"

  if has_jq; then
    echo "$json" | jq -r '
      (.providers // []) as $p
      | ([$p[]|select(.id=="claude")][0]) as $c
      | if $c then
          ([$c.limits.session.pct // 0, $c.limits.weekly.pct // 0] | max | floor)
        else
          0
        end
    '
  else
    # python3 fallback (jq yoksa)
    python3 -c "
import json, sys
try:
    data = json.loads(sys.argv[1])
    providers = data.get('providers', [])
    claude = next((p for p in providers if p.get('id') == 'claude'), None)
    if claude:
        limits = claude.get('limits', {})
        sess = limits.get('session', {}).get('pct', 0)
        week = limits.get('weekly', {}).get('pct', 0)
        pct = max(sess, week)
        print(int(pct))
    else:
        print(0)
except Exception as e:
    print(0, file=sys.stderr)
    sys.exit(1)
" "$json"
  fi
}

# durum dosyasını oku/yaz
read_state() {
  if [ -f "$STATE_FILE" ]; then
    cat "$STATE_FILE" 2>/dev/null || echo "0"
  else
    echo "0"
  fi
}

write_state() {
  mkdir -p "$STATE_DIR"
  echo "$1" > "$STATE_FILE"
}

# bildiriyi gönder
notify() {
  local pct="$1" threshold="$2" urgency="$3"
  local title="usage-tracker"
  local msg

  if [ "$urgency" = "critical" ]; then
    msg="Haftalık limit %${pct} — kritik"
  else
    msg="Haftalık limit %${pct} — dikkat"
  fi

  notify-send --urgency="$urgency" "$title" "$msg"
}

# tek kontrol ve bildiriyi yönet
check_once() {
  # server kapalıysa sessiz çık
  response="$(curl -s --max-time 3 -w '\n%{http_code}' "$URL" 2>/dev/null)"
  http_code="$(echo "$response" | tail -n1)"
  json="$(echo "$response" | head -n-1)"

  # Status 200 değilse veya boş yanıt → sessiz çık
  if [ "$http_code" != "200" ] || [ -z "$json" ]; then
    return 0
  fi

  # JSON geçerliliğini doğrula (bozuk JSON/HTML hata sayfası) → sessiz çık
  if ! echo "$json" | jq -e . >/dev/null 2>&1; then
    return 0
  fi

  pct=$(extract_max_claude_pct "$json")
  [ -z "$pct" ] && return 0

  # son bildirilen eşik
  last_threshold=$(read_state)

  # en yüksek eşiği bul (pct geçen)
  highest_triggered=""
  for t in "${THRESH_ARRAY[@]}"; do
    t="${t// }" # whitespace temizle
    if [ "$pct" -ge "$t" ]; then
      highest_triggered="$t"
    fi
  done

  # eşik geçildiyse ve daha önce bu eşikte bildirilmediyse → bildir
  if [ -n "$highest_triggered" ] && [ "$last_threshold" != "$highest_triggered" ]; then
    if [ "$highest_triggered" -ge 90 ]; then
      notify "$pct" "$highest_triggered" "critical"
    else
      notify "$pct" "$highest_triggered" "normal"
    fi
    write_state "$highest_triggered"
  elif [ "$pct" -lt "${THRESH_ARRAY[0]}" ]; then
    # limiten aşağı düştü — durum sıfırla (sonraki döngüde tekrar bildirebilmek için)
    if [ "$last_threshold" != "0" ]; then
      write_state "0"
    fi
  fi
}

# döngü modu
watch_loop() {
  local interval="${1:-300}"
  echo "watching (interval: ${interval}s). press ctrl+c to stop."
  while true; do
    check_once
    sleep "$interval"
  done
}

# main
if ! check_deps; then
  exit 1
fi

case "${1:-once}" in
  once)
    check_once
    ;;
  watch)
    watch_loop "${2:-300}"
    ;;
  *)
    echo "usage: usage-notify.sh {once|watch [interval]}" >&2
    exit 2
    ;;
esac

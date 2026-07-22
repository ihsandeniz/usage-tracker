#!/usr/bin/env bash
# waybar custom module feeder — usage-tracker /v1/usage → waybar JSON.
# Headline = highest Claude limit % (how close you are to the wall).
# Standalone — the badge works on its own. Install (add to waybar config.jsonc):
#   "custom/usage": {
#     "exec": "/ABS/PATH/surface/waybar-usage.sh",
#     "return-type": "json", "interval": 30,
#     "on-click": "/ABS/PATH/surface/usage-widget toggle"
#   }
# then add "custom/usage" to a modules list.  style.css: #custom-usage.crit {...}
set -u

SELF_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CONF="$SELF_DIR/surface.conf"
[ -f "$CONF" ] && . "$CONF" || true
URL="${USAGE_URL:-http://127.0.0.1:8770}/v1/usage"

json="$(curl -s --max-time 3 "$URL" 2>/dev/null)"
if [ -z "$json" ]; then
  printf '{"text":"○ —","tooltip":"usage-tracker offline (%s)","class":"off"}\n' "$URL"
  exit 0
fi

# Generic zenginleştirilmiş tooltip — tüm sağlayıcılar üzerinde döngü
# Kritik: opsiyonel alan erişiminde //null guard + select(.) kullan → boş stream hiç output vermez
echo "$json" | jq -c '
  def money(v): if v == null then "—" else "$" + (v*100|round/100|tostring) end;
  def fmt_pct(v):
    if v == null then "—"
    elif v >= 90 then "<span color=\"#ff6b6b\">" + ((v*10|round/10)|tostring) + "%</span>"
    elif v >= 75 then "<span color=\"#ffa500\">" + ((v*10|round/10)|tostring) + "%</span>"
    else ((v*10|round/10)|tostring) + "%"
    end;

  (.providers[] | select(.id == "claude")) as $claude
  | ($claude.limits.session.pct // 0) as $sess_pct
  | ($claude.limits.weekly.pct // 0) as $week_pct
  | ([$sess_pct, $week_pct] | max) as $hi_pct
  | (if $hi_pct >= 90 then "crit" elif $hi_pct >= 75 then "warn" else "ok" end) as $class

  # Claude line — zengin bilgi
  | (
      "<b>Claude</b>  session " + fmt_pct($sess_pct) +
      " · weekly " + fmt_pct($week_pct) +
      (if $claude.limits.weeklyModel then " · weekly-model " + fmt_pct($claude.limits.weeklyModel.pct) else "" end) +
      "\n  today " + money($claude.spend.today) +
      (if ($claude.spend.yesterday // 0) > 0 then " · yester " + money($claude.spend.yesterday) else "" end) +
      " · 30d " + money($claude.spend.last30d) +
      (if ($claude.limits.session.resetInSec // 0) > 0 then
        "\n  session reset: ~" + ((($claude.limits.session.resetInSec / 60)|floor)|tostring) + " dk"
      else "" end)
    ) as $claude_line

  # Other providers — sağlayıcı türüne göre zengin satırlar
  | (
      [.providers[] | select(.id != "claude") | (
        if .kind == "spend" then
          "\n<b>" + .name + "</b>" +
          (if (.spend.today // 0) > 0 then "  today " + money(.spend.today) else "" end) +
          (if (.spend.month // 0) > 0 then " · month " + money(.spend.month) else "" end) +
          (if .limit then " · limit " + fmt_pct(.limit.pct) +
            (if (.limit.used and .limit.amount) then " (" + money(.limit.used) + "/" + money(.limit.amount) + ")" else "" end)
          else "" end) +
          (if (.balance.remaining // 0) > 0 then " · credit " + money(.balance.remaining) else "" end)
        elif .kind == "tokens" then
          (if .status != "offline" then
            "\n<b>" + .name + "</b>  " + (((.tokens.total // 0)/1000000*10|round/10)|tostring) + "M tok" +
            (if (.total.usd // 0) > 0 then " ≈ " + money(.total.usd) else "" end) +
            (if (.today.usd // 0) > 0 then " · today " + money(.today.usd) else "" end)
          else "" end)
        elif .kind == "local" then
          (if (.status // "") != "offline" then
            "\n<b>" + .name + "</b>  models: " + ((.modelCount // 0) | tostring) +
            (if ((.running // []) | length) > 0 then " (running: " + ((.running | length) | tostring) + ")" else "" end)
          else "" end)
        elif .kind == "quota" then
          "\n<b>" + .name + "</b>  " + ((.quota.used // 0) | tostring) + "/" + ((.quota.limit // 0) | tostring) + " " + (.quota.unit // "birim") +
          (if .quota.pct then " · " + fmt_pct(.quota.pct) else "" end)
        else "" end
      )] | join("")
    ) as $others_line

  | {
      text: ("◐ S" + (($sess_pct*10|round/10)|tostring) + "% W" + (($week_pct*10|round/10)|tostring) + "%"),
      percentage: ($hi_pct|floor),
      class: $class,
      tooltip: $claude_line + $others_line
    }
'

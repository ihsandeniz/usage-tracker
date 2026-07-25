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
_env_url="${USAGE_URL:-}"          # env wins over the config file, not the other
[ -f "$CONF" ] && . "$CONF" || true
[ -n "$_env_url" ] && USAGE_URL="$_env_url"
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
  def clamp(p): (p // 0) | if . > 100 then 100 elif . < 0 then 0 else . end;
  # 10 segmentli unicode ilerleme çubuğu
  def bar(p):
    ((clamp(p) / 10) | floor) as $n
    | (if $n > 0 then ("█" * $n) else "" end) + (if (10 - $n) > 0 then ("░" * (10 - $n)) else "" end);
  def col(p): if clamp(p) >= 90 then "#ff6b6b" elif clamp(p) >= 75 then "#ffa500" else "#00d1ff" end;
  def cbar(p): "<span color=\"" + col(p) + "\">" + bar(p) + "</span>";
  def cpct(p):
    if (p // null) == null then "<span color=\"#8fb6d6\">—</span>"
    else "<span color=\"" + col(p) + "\">" + ((clamp(p)*10|round/10)|tostring) + "%</span>" end;
  # saniye → kısa süre (3g12s / 2s30dk / 45dk)
  def dur(sec):
    ((sec // 0) | floor) as $s
    | if $s <= 0 then "" else
      ($s / 86400 | floor) as $d | (($s % 86400) / 3600 | floor) as $h | (($s % 3600) / 60 | floor) as $m
      | (if $d > 0 then ($d|tostring)+"g"+($h|tostring)+"s"
         elif $h > 0 then ($h|tostring)+"s"+($m|tostring)+"dk"
         else ($m|tostring)+"dk" end)
    end;
  def rst(sec): (dur(sec)) as $t | (if $t == "" then "" else "  <span color=\"#8fb6d6\">↺" + $t + "</span>" end);

  # An unknown percentage (live endpoint down + no trustworthy calibration) must
  # not render as 0% — that reads as "nothing used", which is just as wrong as
  # the 400% it replaced. Unknown shows "—" and stays grey.
  def num(p): if p == null then "—" else ((p*10|round/10)|tostring) + "%" end;

  (.providers[] | select(.id == "claude")) as $claude
  | ($claude.limits.session.pct) as $sess_raw
  | ($claude.limits.weekly.pct)  as $week_raw
  | ($sess_raw // 0) as $sess_pct
  | ($week_raw // 0) as $week_pct
  | ([$sess_pct, $week_pct] | max) as $hi_pct
  | (if ($sess_raw == null and $week_raw == null) then "off"
     elif $hi_pct >= 90 then "crit" elif $hi_pct >= 75 then "warn" else "ok" end) as $class

  # Claude — bar + reset countdown + forecast
  | (
      "<b>Claude</b>"
      + "\n  " + cbar($sess_pct) + " session " + cpct($sess_pct) + rst($claude.limits.session.resetInSec)
      + "\n  " + cbar($week_pct) + " weekly  " + cpct($week_pct) + rst($claude.limits.weekly.resetInSec)
      + (if $claude.limits.weeklyModel and ($claude.limits.weeklyModel.pct != null) then
          "\n  " + cbar($claude.limits.weeklyModel.pct) + " " + ($claude.limits.weeklyModel.name // "model") + "   " + cpct($claude.limits.weeklyModel.pct)
        else "" end)
      + (if ($claude.limits.weekly.forecast.willExceed // false) and (($claude.limits.weekly.forecast.etaText // "") != "") then
          "\n  <span color=\"#ffa500\">⚠ " + $claude.limits.weekly.forecast.etaText + "</span>"
        else "" end)
      + "\n  <span color=\"#8fb6d6\">💰</span> today " + money($claude.spend.today)
        + (if ($claude.spend.yesterday // 0) > 0 then " · yester " + money($claude.spend.yesterday) else "" end)
        + " · 30d " + money($claude.spend.last30d)
    ) as $claude_line

  # Diğer sağlayıcılar — tür bazlı, pct varsa bar + reset
  | (
      [.providers[] | select(.id != "claude") | (
        if .kind == "spend" then
          "\n<b>" + .name + "</b>"
          + (if (.spend.today // 0) > 0 then "  today " + money(.spend.today) else "" end)
          + (if (.spend.month // 0) > 0 then " · month " + money(.spend.month) else "" end)
          + (if (.balance.remaining // 0) > 0 then " · credit " + money(.balance.remaining) else "" end)
          + (if .limit and (.limit.pct != null) then
              "\n  " + cbar(.limit.pct) + " limit " + cpct(.limit.pct)
              + (if (.limit.used != null and .limit.amount != null) then " (" + money(.limit.used) + "/" + money(.limit.amount) + ")" else "" end)
              + (if (.limit.reset // "") != "" then "  <span color=\"#8fb6d6\">↺" + (.limit.reset|tostring) + "</span>" else "" end)
            else "" end)
        elif .kind == "tokens" then
          (if .status != "offline" then
            "\n<b>" + .name + "</b>  " + (((.tokens.total // 0)/1000000*10|round/10)|tostring) + "M tok"
            + (if (.total.usd // 0) > 0 then " ≈ " + money(.total.usd) else "" end)
            + (if (.today.usd // 0) > 0 then " · today " + money(.today.usd) else "" end)
          else "" end)
        elif .kind == "local" then
          (if (.status // "") != "offline" then
            "\n<b>" + .name + "</b>  models: " + ((.modelCount // 0) | tostring)
            + (if ((.running // []) | length) > 0 then " (running: " + ((.running | length) | tostring) + ")" else "" end)
          else "\n<b>" + .name + "</b>  <span color=\"#8fb6d6\">servis kapalı</span>" end)
        elif .kind == "quota" then
          "\n<b>" + .name + "</b>"
          + "\n  " + cbar(.quota.pct) + " " + cpct(.quota.pct)
          + "  <span color=\"#8fb6d6\">" + ((.quota.used // 0)|tostring) + "/" + ((.quota.limit // 0)|tostring) + " " + (.quota.unit // "birim") + "</span>"
          + (if (.quota.reset // 0) > 0 then rst(.quota.reset - now) else "" end)
        else "" end
      )] | join("")
    ) as $others_line

  | {
      text: ("◐ S" + num($sess_raw) + " W" + num($week_raw)),
      percentage: ($hi_pct|floor),
      class: $class,
      tooltip: ($claude_line
        + (if ($others_line | length) > 0
           then "\n<span color=\"#45475a\">──────────────────────</span>" + $others_line
           else "" end))
    }
'

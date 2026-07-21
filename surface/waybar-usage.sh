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

# NOTE: `(stream) as $x` over an EMPTY stream yields NO output. Guard every optional
# provider with `([ ... ][0])` (→ single value or null) so a missing provider can't
# blank the whole module.
echo "$json" | jq -c '
  def money(v): if v == null then "—" else "$" + (v*100|round/100|tostring) end;
  def pctstr(v): if v == null then "—" else ((v*10|round/10)|tostring) + "%" end;

  (.providers // []) as $p
  | ([$p[]|select(.id=="claude")][0])     as $c
  | ([$p[]|select(.id=="openrouter")][0]) as $or
  | ([$p[]|select(.id=="codex")][0])      as $cx
  | ($c.limits.session.pct // 0) as $sess
  | ($c.limits.weekly.pct  // 0) as $week
  | ([$sess, $week] | max) as $hi
  | (if $hi >= 90 then "crit" elif $hi >= 75 then "warn" else "ok" end) as $cls
  | ($c.spend.today // 0) as $ctoday
  | {
      text: ("◐ S" + (($sess*10|round/10)|tostring) + "% W" + (($week*10|round/10)|tostring) + "%"),
      percentage: ($hi|floor),
      class: $cls,
      tooltip: (
        "<b>Claude</b>  session " + pctstr($sess) + " · weekly " + pctstr($week) + "\n" +
        "  today " + money($ctoday) + " · 30d " + money($c.spend.last30d) +
        (if $or then "\n<b>OpenRouter</b>  today " + money($or.spend.today) +
          (if $or.limit then " · limit " + money($or.limit.used) + "/" + money($or.limit.amount) else "" end) else "" end) +
        (if $cx then "\n<b>Codex</b>  " + ((($cx.tokens.total // 0)/1000000*10|round/10)|tostring) + "M tok ≈ " + money($cx.total.usd) else "" end)
      )
    }
'

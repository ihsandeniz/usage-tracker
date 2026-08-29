#!/usr/bin/env bash
# waybar custom module feeder — usage-tracker /v1/usage → waybar JSON.
# Headline = the highest limit % of the chosen provider (how close you are to the wall).
# Standalone — the badge works on its own. Install (add to waybar config.jsonc):
#   "custom/usage": {
#     "exec": "/ABS/PATH/surface/waybar-usage.sh",
#     "return-type": "json", "interval": 30,
#     "on-click": "/ABS/PATH/surface/usage-widget toggle"
#   }
# then add "custom/usage" to a modules list.  style.css: #custom-usage.crit {...}
#
# A SECOND badge for another provider — same script, one flag:
#   "custom/usage-codex": { "exec": "/ABS/PATH/surface/waybar-usage.sh --provider codex", ... }
# Any card that publishes Claude's `limits` shape drives a badge; nothing here knows the
# provider's name. The tooltip always shows every card, whichever one drives the headline.
set -u

PROVIDER=claude
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) PROVIDER="${2:-claude}"; shift 2 ;;
    --provider=*) PROVIDER="${1#*=}"; shift ;;
    -h|--help)
      printf 'usage: waybar-usage.sh [--provider ID]\n  ID: claude (default), codex, openrouter, …\n'
      exit 0 ;;
    *) shift ;;
  esac
done

SELF_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CONF="$SELF_DIR/surface.conf"
_env_url="${USAGE_URL:-}"          # env wins over the config file, not the other
[ -f "$CONF" ] && . "$CONF" || true
[ -n "$_env_url" ] && USAGE_URL="$_env_url"
URL="${USAGE_URL:-http://127.0.0.1:8770}/v1/usage"

response="$(curl -s --max-time 3 -w '\n%{http_code}' "$URL" 2>/dev/null)"
http_code="$(echo "$response" | tail -n1)"
json="$(echo "$response" | head -n-1)"

# Status 200 değilse veya boş yanıt → offline
if [ "$http_code" != "200" ] || [ -z "$json" ]; then
  printf '{"text":"○ —","tooltip":"usage-tracker offline (%s)","class":"off"}\n' "$URL"
  exit 0
fi

# JSON geçerliliğini doğrula (bozuk JSON/HTML hata sayfası) — jq -e . fail → offline
if ! echo "$json" | jq -e . >/dev/null 2>&1; then
  printf '{"text":"○ —","tooltip":"usage-tracker offline (%s)","class":"off"}\n' "$URL"
  exit 0
fi

# Generic zenginleştirilmiş tooltip — tüm sağlayıcılar üzerinde döngü
# Kritik: opsiyonel alan erişiminde //null guard + select(.) kullan → boş stream hiç output vermez
echo "$json" | jq -c --arg pid "$PROVIDER" '
  def money(v; curr):
    if v == null then "—"
    else
      (if curr == "USD" then "$"
       elif curr == "EUR" then "€"
       elif curr == "CNY" then "¥"
       elif curr == "GBP" then "£"
       elif curr == "TRY" then "₺"
       else (curr + " ") end) + (v*100|round/100|tostring)
    end;
  def clamp(p): (p // 0) | if . > 100 then 100 elif . < 0 then 0 else . end;
  # 10 segmentli unicode ilerleme çubuğu
  def bar(p):
    ((clamp(p) / 10) | floor) as $n
    | (if $n > 0 then ("█" * $n) else "" end) + (if (10 - $n) > 0 then ("░" * (10 - $n)) else "" end);
  # Eşikler sunucudan gelir (fallback: warn=75, crit=90)
  def col(p; $th): if clamp(p) >= $th.crit then "#ff6b6b" elif clamp(p) >= $th.warn then "#ffa500" else "#00d1ff" end;
  def cbar(p; $th): "<span color=\"" + col(p; $th) + "\">" + bar(p) + "</span>";
  def roundHalfUp: (. * 10 + 0.5 | floor) / 10;
  def cpct(p; $th):
    if (p // null) == null then "<span color=\"#8fb6d6\">—</span>"
    else "<span color=\"" + col(p; $th) + "\">" + ((clamp(p) | roundHalfUp)|tostring) + "%</span>" end;
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
  def num(p): if p == null then "—" else ((p | roundHalfUp)|tostring) + "%" end;

  # Claude şeklindeki bir `limits` bloğunu tooltip satırlarına çevir. Anahtar kartın ADI
  # değil ALANI: bu şekli yayınlayan her sağlayıcı (Codex) aynı satırları alır.
  # ⚠️ `((x) // null) as $b` bilinçli: `x as $b` boş akımda gövdeyi SIFIR kez çalıştırır
  # ve tüm çıktıyı sessizce yutar (ledger: jq/empty-stream-as).
  def limline(bar; $label; $th):
    ((bar) // null) as $b
    | if $b == null then ""
      else "\n  " + cbar($b.pct; $th) + " " + $label + " " + cpct($b.pct; $th)
           + rst($b.resetInSec)
           + (if ($b.expired // false) then "  <span color=\"#8fb6d6\">↺ pencere sıfırlandı</span>" else "" end)
      end;
  def limbars(lim; $th):
    ((lim) // null) as $l
    | if $l == null then ""
      else limline($l.session; "session"; $th) + limline($l.weekly; "weekly "; $th)
      end;

  (.thresholds // { warn: 75, crit: 90 }) as $th
  | (([.providers[] | select(.id == "claude")][0]) // null) as $claude
  # Rozeti süren kart: istenen saglayici. Yoksa Claude karti devralir — bir yazim hatasi
  # ("--provider codx") bos bir rozet degil, calisan varsayilani gostersin.
  # (Bu blok tek tirnakli bir kabuk dizesi: kesme isareti dizeyi KAPATIR, o yuzden yok.)
  | (([.providers[] | select(.id == $pid)][0]) // $claude) as $lead
  | ($lead.limits.session.pct) as $sess_raw
  | ($lead.limits.weekly.pct)  as $week_raw
  | ($sess_raw // 0) as $sess_pct
  | ($week_raw // 0) as $week_pct
  | ([$sess_pct, $week_pct] | max) as $hi_pct
  | (if ($sess_raw == null and $week_raw == null) then "off"
     elif $hi_pct >= $th.crit then "crit" elif $hi_pct >= $th.warn then "warn" else "ok" end) as $class

  # Claude — bar + reset countdown + forecast
  | (
      # DIKKAT: $sess_pct/$week_pct artik MANSETI suren karta ait ($lead). Claude bolumu
      # kendi sayilarini okumak zorunda — aksi halde `--provider codex`, Claude basliginin
      # altina Codex yuzdesini yazardi.
      "<b>Claude</b>"
      + "\n  " + cbar(($claude.limits.session.pct // 0); $th) + " session " + cpct($claude.limits.session.pct; $th) + rst($claude.limits.session.resetInSec)
      + "\n  " + cbar(($claude.limits.weekly.pct // 0); $th) + " weekly  " + cpct($claude.limits.weekly.pct; $th) + rst($claude.limits.weekly.resetInSec)
      + (if $claude.limits.weeklyModel and ($claude.limits.weeklyModel.pct != null) then
          "\n  " + cbar($claude.limits.weeklyModel.pct; $th) + " " + ($claude.limits.weeklyModel.name // "model") + "   " + cpct($claude.limits.weeklyModel.pct; $th)
        else "" end)
      + (if ($claude.limits.weekly.forecast.willExceed // false) and (($claude.limits.weekly.forecast.etaText // "") != "") then
          "\n  <span color=\"#ffa500\">⚠ " + $claude.limits.weekly.forecast.etaText + "</span>"
        else "" end)
      # Bayat veri işaretsiz gösterilmez. Ağ kesilince son iyi yüzde donuyor; rozet aynı
      # rengi göstermeye devam ettiği için kullanıcı taze bir sayıya baktığını sanıyordu.
      + (if ($claude.live.stale // false) then
          "\n  <span color=\"#ffa500\">⚠ bayat veri" + (if ($claude.live.ageSec // 0) > 0 then " · " + dur($claude.live.ageSec) + " önce" else "" end) + "</span>"
        else "" end)
      + "\n  <span color=\"#8fb6d6\">💰</span> today " + money($claude.spend.today; ($claude.spend.currency // "USD"))
        + (if ($claude.spend.yesterday // 0) > 0 then " · yester " + money($claude.spend.yesterday; ($claude.spend.currency // "USD")) else "" end)
        + " · 30d " + money($claude.spend.last30d; ($claude.spend.currency // "USD"))
    ) as $claude_line

  # Diğer sağlayıcılar — tür bazlı, pct varsa bar + reset
  | (
      [.providers[] | select(.id != "claude") | (
        if .kind == "spend" then
          "\n<b>" + .name + "</b>"
          + (if (.spend.today // 0) > 0 then "  today " + money(.spend.today; (.currency // "USD")) else "" end)
          + (if (.spend.month // 0) > 0 then " · month " + money(.spend.month; (.currency // "USD")) else "" end)
          + (if (.balance.remaining // 0) > 0 then " · credit " + money(.balance.remaining; (.currency // "USD")) else "" end)
          + (if .limit and (.limit.pct != null) then
              "\n  " + cbar(.limit.pct; $th) + " limit " + cpct(.limit.pct; $th)
              + (if (.limit.used != null and .limit.amount != null) then " (" + money(.limit.used; (.currency // "USD")) + "/" + money(.limit.amount; (.currency // "USD")) + ")" else "" end)
              + (if (.limit.reset // "") != "" then "  <span color=\"#8fb6d6\">↺" + (.limit.reset|tostring) + "</span>" else "" end)
            else "" end)
        elif .kind == "tokens" then
          (if .status != "offline" then
            "\n<b>" + .name + "</b>"
            + (if (.plan // "") != "" then " <span color=\"#8fb6d6\">" + .plan + "</span>" else "" end)
            + "  " + (((.tokens.total // 0)/1000000*10|round/10)|tostring) + "M tok"
            + (if (.total.usd // 0) > 0 then " ≈ " + money(.total.usd; (.currency // "USD")) else "" end)
            + (if (.today.usd // 0) > 0 then " · today " + money(.today.usd; (.currency // "USD")) else "" end)
            + limbars(.limits; $th)
          else "" end)
        elif .kind == "local" then
          (if (.status // "") != "offline" then
            "\n<b>" + .name + "</b>  models: " + ((.modelCount // 0) | tostring)
            + (if ((.running // []) | length) > 0 then " (running: " + ((.running | length) | tostring) + ")" else "" end)
          else "\n<b>" + .name + "</b>  <span color=\"#8fb6d6\">servis kapalı</span>" end)
        elif .kind == "quota" then
          "\n<b>" + .name + "</b>"
          + "\n  " + cbar(.quota.pct; $th) + " " + cpct(.quota.pct; $th)
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

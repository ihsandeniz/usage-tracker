#!/usr/bin/env python3
"""
Codex adaptörü — GERÇEK limit yüzdesi + token hacmi + API-eşdeğeri $ tahmini.

Kaynak (SALT-OKUNUR):
  ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
  event_msg → payload.type == 'token_count':
    · info.last_token_usage (turn deltası):
        input_tokens · cached_input_tokens · output_tokens · reasoning_output_tokens · total_tokens
    · payload.rate_limits — Codex sunucusunun KENDİ bildirdiği kota:
        primary   {used_percent, window_minutes: 300,   resets_at}   → 5 saatlik pencere
        secondary {used_percent, window_minutes: 10080, resets_at}   → haftalık pencere
        plan_type ('plus'/'pro') · limit_id · credits
  Model: aynı dosyadaki en son 'model' alanı (turn_context/session_meta), yoksa gpt-5-codex.

  auth_mode ('chatgpt' = abonelik → $ = API-eşdeğeri maliyet, gerçek fatura değil).
Dosya-mtime cache'li. Rollout dizini yoksa → None (kart açılmaz).

⚠️ Limit yüzdesi bir TAHMİN DEĞİL — Codex'in kendi yanıtından okunuyor, o yüzden Claude'un
kalibrasyona muhtaç barlarının aksine `calibSuspect` diye bir sorunu yok. Buna karşılık
**anlık değil**: son Codex turn'ünün anıdır. Pencere içinde `used_percent` yalnızca artar,
dolayısıyla okunan değer bugünün ALT SINIRIdır — `warn`/`crit` diyorsa doğrudur, `ok` diyorsa
o turn'den beri CLI dışı (ChatGPT web Codex) kullanım olmadıysa doğrudur. Pencere sıfırlandıysa
(`resets_at` geçmişte) yüzde **bilinmiyor**dur; 0 yazmak, engellemek istediğin pahalı işi
başlatırdı (bkz. `guard` çıkış kodu 3 sözleşmesi, docs/CLI.md).
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .. import pricing

PROVIDER_ID = 'codex'
PROVIDER_NAME = 'Codex'
CODEX_DIR = Path.home() / '.codex'
SESSIONS_DIR = CODEX_DIR / 'sessions'
AUTH_PATH = CODEX_DIR / 'auth.json'

# Codex'in en kısa penceresi (primary = 300 dk). `resets_at` bildirilmediğinde tazeliği
# neye göre ölçeceğimizin son çaresi.
USAGE_SESSION_WINDOW_SEC = 5 * 3600

# Büyük log dizininde asılmayı önlemek için tarama sınırı (ledger: python/executor-with-timeout-yalani)
#
# ⚠️ Tavan dolduğunda kart `status='partial'` + `truncated=True` ile DÜRÜSTÇE uyarır:
# token/$ toplamı eksiktir, limit barları (rate_limits) etkilenmez — onlar en yeni
# gözlemden okunur. Yani bu bir arıza değil, ilan edilmiş sınır. Çok oturumlu
# makinede toplamı tam istiyorsan yükselt; tarama süresi doğrusal artar. (BL-205)
def _tavan(ad, varsayilan):
    """Sınırı env'den oku. Bozuk/negatif değer varsayılana düşer — sessizce 0 tarama yapma."""
    try:
        n = int(os.environ.get(ad, '') or varsayilan)
    except ValueError:
        return varsayilan
    return n if n > 0 else varsayilan


MAX_FILES = _tavan('CODEX_MAX_FILES', 40)
MAX_LINES_PER_FILE = _tavan('CODEX_MAX_LINES', 20000)

_LOCK = threading.Lock()
_FILE_CACHE = {}         # {path: (mtime, size, [events], rate_obs, hit_lines)}
_MODEL_RE = re.compile(r'"model"\s*:\s*"([^"]+)"')

# `rate_limits` iki pencere bildirir; adları Claude kartıyla aynı olsun diye çevriliyor.
_RATE_SLOTS = (('primary', 'session'), ('secondary', 'weekly'))


def _rate_observation(payload: dict, ms: int):
    """token_count olayındaki `rate_limits`'i {slot: gözlem} sözlüğüne çevir.

    Boş/None gelen pencere ATLANIR (sözlükte yer almaz) — `primary: null` "kota sıfır"
    demek değil, "bu turn'de bildirilmedi" demektir. İkisini karıştırmak, bildirimsiz bir
    turn'ün kullanıcının barını sıfırlaması olurdu.
    """
    rl = payload.get('rate_limits')
    if not isinstance(rl, dict):
        return {}
    out = {}
    for src_key, slot in _RATE_SLOTS:
        win = rl.get(src_key)
        if not isinstance(win, dict):
            continue
        pct = win.get('used_percent')
        if not isinstance(pct, (int, float)) or isinstance(pct, bool):
            continue
        out[slot] = {
            'ms': ms,
            'pct': float(pct),
            'windowMinutes': win.get('window_minutes'),
            'resetsAt': win.get('resets_at'),
            'limitId': rl.get('limit_id'),
            'planType': rl.get('plan_type'),
            'reachedType': rl.get('rate_limit_reached_type'),
        }
    return out


def _merge_rate(into: dict, new: dict):
    """Her pencere için EN YENİ gözlemi tut (slot bazında bağımsız)."""
    for slot, obs in new.items():
        cur = into.get(slot)
        if cur is None or obs['ms'] >= cur['ms']:
            into[slot] = obs
    return into


# event: (ms, model, inp, cached_in, out)
def _parse_file(fp: Path):
    """(events, rate_obs, hit_line_limit) döndür. hit_line_limit=True ise dosyanın sonu
       OKUNMADI — çağıran bunu kullanıcıya söylemek zorunda (sessiz kesme yasak)."""
    events = []
    rate_obs = {}
    cur_model = ''
    hit_limit = False
    try:
        with fp.open(encoding='utf-8') as fh:
            for line_idx, line in enumerate(fh):
                if line_idx >= MAX_LINES_PER_FILE:
                    hit_limit = True
                    break
                if '"model"' in line:
                    m = _MODEL_RE.search(line)
                    if m:
                        cur_model = m.group(1)
                if '"token_count"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError as e:
                    print(f'Provider {PROVIDER_ID}: Bozuk JSON satırı {fp}:{line_idx}: {e}', file=sys.stderr)
                    continue
                payload = o.get('payload')
                if not isinstance(payload, dict) or payload.get('type') != 'token_count':
                    continue
                ts = o.get('timestamp')
                ms = _iso_to_ms(ts) if ts else None
                if ms is None:
                    try:
                        ms = int(fp.stat().st_mtime * 1000)
                    except OSError:
                        continue
                # Limit bildirimi token deltasından BAĞIMSIZ gelir: `info: null` olan bir
                # olay da geçerli bir `rate_limits` taşır. Aşağıdaki `continue`'nun üstünde
                # olmasının sebebi bu — altında olsaydı ölçümlerin çoğu düşerdi.
                _merge_rate(rate_obs, _rate_observation(payload, ms))
                info = payload.get('info') or {}
                last = info.get('last_token_usage') or {}
                if not last:
                    continue
                events.append((ms, cur_model,
                               int(last.get('input_tokens') or 0),
                               int(last.get('cached_input_tokens') or 0),
                               int(last.get('output_tokens') or 0)))
    except OSError:
        return [], {}, False
    return events, rate_obs, hit_limit


def _iso_to_ms(ts: str):
    try:
        return int(datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp() * 1000)
    except Exception:
        return None


def _scan(since_ms: int):
    """(events, files, truncated_reason, rate_obs) döndür. truncated_reason ∈ {None,'files','lines'}.

    Tavana çarpılırsa **en yeni** dosyalar tutulur. Eskiden `rglob` sırasına güveniliyordu;
    o sıra dosya sisteminin iç sırası olduğu için hangi 40 oturumun sayıldığı rastgeleydi
    ve iki çalıştırma farklı sonuç verebiliyordu.
    """
    if not SESSIONS_DIR.exists():
        return None                    # yapılandırılmamış
    cutoff = since_ms / 1000 - 86400

    candidates = []
    for fp in SESSIONS_DIR.rglob('rollout-*.jsonl'):
        try:
            st = fp.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff:
            continue
        candidates.append((st.st_mtime, st.st_size, fp))

    # en yeni önce; eşit mtime'da yol adıyla kararlı sırala (iki koşu aynı sonucu versin)
    candidates.sort(key=lambda c: (-c[0], str(c[2])))
    truncated_reason = 'files' if len(candidates) > MAX_FILES else None
    candidates = candidates[:MAX_FILES]

    events = []
    rate_obs = {}
    for mtime, size, fp in candidates:
        key = str(fp)
        with _LOCK:
            c = _FILE_CACHE.get(key)
        if c and c[0] == mtime and c[1] == size and len(c) == 5:
            evs, file_rate, hit_lines = c[2], c[3], c[4]
        else:
            evs, file_rate, hit_lines = _parse_file(fp)
            with _LOCK:
                _FILE_CACHE[key] = (mtime, size, evs, file_rate, hit_lines)
        if hit_lines and truncated_reason is None:
            truncated_reason = 'lines'
        events.extend(e for e in evs if e[0] >= since_ms)
        # Limit gözlemi `since_ms` ile SÜZÜLMEZ: 30 günlük harcama penceresi bir raporlama
        # aralığı, limit penceresi ise 5 saat/1 hafta. En yeni gözlem hangi dosyadaysa odur.
        _merge_rate(rate_obs, file_rate)
    return events, len(candidates), truncated_reason, rate_obs


def _limit_bar(obs: dict, now_ms: int) -> dict:
    """Bir `rate_limits` gözlemini Claude kartıyla AYNI ŞEKİLDEKİ bara çevir.

    Aynı şekil bir süs değil: `cli.scopes_of`, waybar besleyicisi, tepsi ipucu ve panel
    barı Claude'un alan adlarına göre yazılmış. İkinci bir şekil icat etmek, dört yüzeyi
    ikinci kez yazmak demekti.

    İki dürüstlük kuralı:
      · `resets_at` GEÇMİŞTE ise pencere döndü → o yüzde ölü bir pencereye ait. `pct=None`
        (bilinmiyor), `expired=True`. Yeni pencereye 0 yazmak, Codex'i CLI dışından
        (ChatGPT web) kullanmış birine "boş kota" derdi.
      · Pencere içinde `used_percent` yalnızca artar → okunan değer ALT SINIRdır. Bu yüzden
        `stale` yaşa göre değil pencereye göre işaretlenir; yaş `ageSec` ile ayrıca verilir.
    """
    resets_at = obs.get('resetsAt')
    reset_at_ms = int(resets_at) * 1000 if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool) else None
    window_min = obs.get('windowMinutes')
    window_ms = int(window_min) * 60_000 if isinstance(window_min, (int, float)) and not isinstance(window_min, bool) and window_min > 0 else None

    observed_ms = obs['ms']
    age_sec = round(max(0, now_ms - observed_ms) / 1000, 1)
    expired = bool(reset_at_ms) and reset_at_ms <= now_ms
    pct = None if expired else obs['pct']

    reset_in_sec = None
    if reset_at_ms and not expired:
        reset_in_sec = int((reset_at_ms - now_ms) / 1000)

    # `resets_at` YOKSA pencerenin hâlâ açık olduğunu doğrulayamayız. `expired` kontrolü
    # tam olarak o alana dayanıyor; o alan null gelince yüzde sessizce "canlı" sayılıyordu
    # ve haftalarca eski bir ölçüm taze gibi görünebiliyordu. Doğrulanamayan tazelik,
    # tazelik değildir: ölçüm bir pencere boyundan eskiyse bayat işaretlenir.
    stale = expired
    if pct is not None and reset_at_ms is None:
        horizon = (window_ms / 1000) if window_ms else USAGE_SESSION_WINDOW_SEC
        stale = age_sec > horizon

    forecast = None
    if pct is not None and reset_at_ms and window_ms:
        from ..engine import _compute_forecast          # döngüsel import'u geciktir
        forecast = _compute_forecast(pct, now_ms - (reset_at_ms - window_ms),
                                     reset_at_ms, now_ms, window_ms)

    return {
        # Claude barının sözleşmesi — dört yüzey bu adları okuyor (docs/WIRE.md)
        'pct': pct,
        'used': None, 'units': None, 'budget': None,   # Codex birim değil YÜZDE bildirir
        'calibSuspect': False,                         # kalibrasyon yok: sayı kaynağın kendisi
        'resetAtMs': reset_at_ms,
        'resetInSec': reset_in_sec,
        'forecast': forecast,
        'live': True,                                  # Codex'in kendi yanıtından
        'stale': stale,
        # Codex'e özgü ekler (bilinmeyen alanı yok sayma kuralı — docs/WIRE.md)
        'windowMinutes': window_min,
        'observedAtMs': observed_ms,
        'ageSec': age_sec,
        'expired': expired,
        'reportedPct': obs['pct'],                     # pencere ölmüş olsa da en son ne dediği
        'limitId': obs.get('limitId'),
        'reachedType': obs.get('reachedType'),
    }


def _build_limits(rate_obs: dict, now_ms: int):
    """({'session':bar|None,'weekly':bar|None} | None, plan_type|None) döndür."""
    if not rate_obs:
        return None, None
    bars = {slot: _limit_bar(obs, now_ms) for slot, obs in rate_obs.items()}
    newest = max(rate_obs.values(), key=lambda o: o['ms'])
    return ({'session': bars.get('session'), 'weekly': bars.get('weekly')},
            newest.get('planType'))


def _short(model: str) -> str:
    m = (model or '').lower()
    m = m.split('/')[-1]
    return m or 'gpt'


def collect(days: int = 30) -> dict:
    if not SESSIONS_DIR.exists():
        return None
    now = datetime.now()
    now_ms = int(now.timestamp() * 1000)
    today_start = int(datetime(now.year, now.month, now.day).timestamp() * 1000)
    window_start = today_start - (days - 1) * 86_400_000

    scan = _scan(window_start)
    if scan is None:
        return None
    events, files, truncated_reason, rate_obs = scan
    limits, plan_type = _build_limits(rate_obs, now_ms)

    auth_mode = 'chatgpt'
    try:
        auth_mode = (json.loads(AUTH_PATH.read_text(encoding='utf-8')) or {}).get('auth_mode') or 'chatgpt'
    except Exception:
        pass

    tot = {'input': 0, 'cached_input': 0, 'output': 0}
    today_tok = {'input': 0, 'cached_input': 0, 'output': 0}
    total_usd = today_usd = 0.0
    by_model = {}
    by_day = {}
    sources = set()

    for ms, model, inp, cin, out in events:
        usd, src = pricing.codex_cost_usd(inp, cin, out, model)
        total_usd += usd
        tot['input'] += inp; tot['cached_input'] += cin; tot['output'] += out
        if ms >= today_start:
            today_usd += usd
            today_tok['input'] += inp; today_tok['cached_input'] += cin; today_tok['output'] += out
        dk = datetime.fromtimestamp(ms / 1000).strftime('%Y-%m-%d')
        by_day[dk] = by_day.get(dk, 0.0) + usd
        mk = model or 'gpt-5-codex'
        e = by_model.setdefault(mk, {'short': _short(mk), 'usd': 0.0, 'source': src,
                                     'tokens': {'input': 0, 'cached_input': 0, 'output': 0}})
        e['usd'] += usd
        e['tokens']['input'] += inp; e['tokens']['cached_input'] += cin; e['tokens']['output'] += out
        sources.add(src)

    tok_total = tot['input'] + tot['output']
    day_list = [{'day': datetime.fromtimestamp((window_start + i * 86_400_000) / 1000).strftime('%Y-%m-%d'),
                 'usd': round(by_day.get(datetime.fromtimestamp((window_start + i * 86_400_000) / 1000).strftime('%Y-%m-%d'), 0.0), 4)}
                for i in range(days)]
    model_list = sorted(({'model': k, **v, 'usd': round(v['usd'], 4)} for k, v in by_model.items()),
                        key=lambda x: x['usd'], reverse=True)

    if truncated_reason == 'files':
        trunc_note = (f' ⚠️ Yalnız en yeni {MAX_FILES} oturum tarandı — gösterilen toplam '
                      f'EKSİK, gerçek kullanım daha yüksek.')
    elif truncated_reason == 'lines':
        trunc_note = (f' ⚠️ En az bir oturum günlüğü {MAX_LINES_PER_FILE} satırda kesildi — '
                      f'gösterilen toplam EKSİK.')
    else:
        trunc_note = ''

    # `note` uzun düzyazıdır (sözleşme — v0.2.0'dan beri yayınlanıyor, kaldırılmaz).
    # `warnings` onun YANINDA duran kısa satırlardır: bir kart uyarıyı okunabilir
    # göstermek zorunda, tek paragrafa yığılmış üç uyarı ekranda hiçbiri kadar okunur.
    warnings = []
    limit_note = ''
    if limits:
        live_bars = [b for b in limits.values() if b and b.get('pct') is not None]
        dead_bars = [b for b in limits.values() if b and b.get('expired')]
        if live_bars:
            oldest = max(b['ageSec'] for b in live_bars)
            limit_note = (f' Limit yüzdesi Codex\'in kendi yanıtından ({_fmt_age(oldest)} önceki '
                          f'turn); pencere içinde yalnız artar, yani ALT SINIR.')
        if dead_bars:
            limit_note += (' ⚠️ En az bir pencere son ölçümden sonra sıfırlandı — o bar '
                           '"bilinmiyor"; 0 yazmak yeni pencereyi ölçmüş gibi olurdu.')
            warnings.append('Bir limit penceresi son ölçümden sonra sıfırlandı — o bar '
                            '"bilinmiyor", sıfır değil.')
    if truncated_reason == 'files':
        warnings.append(f'Yalnız en yeni {MAX_FILES} oturum tarandı — token/$ toplamı EKSİK, '
                        f'gerçek kullanım daha yüksek. (Limit yüzdesi etkilenmez: en yeni '
                        f'oturumlar taranıyor.)')
    elif truncated_reason == 'lines':
        warnings.append(f'En az bir oturum günlüğü {MAX_LINES_PER_FILE} satırda kesildi — '
                        f'token/$ toplamı EKSİK.')

    return {
        'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'tokens',
        'available': True,
        # 'partial': sayı var ama eksik. 'ok' demek yalan olurdu.
        'status': ('partial' if (truncated_reason and events)
                   else ('ok' if (events or limits) else 'nodata')),
        'error': None,
        # Claude kartıyla aynı şekil — dört yüzey ek koda gerek kalmadan okur.
        'limits': limits,
        'plan': plan_type,
        'warnings': warnings,
        'truncated': truncated_reason is not None, 'truncatedReason': truncated_reason,
        'currency': 'USD', 'auth': auth_mode, 'windowDays': days, 'sessions': files,
        'tokens': {'input': tot['input'], 'cached_input': tot['cached_input'],
                   'output': tot['output'], 'total': tok_total},
        'today':  {'tokens': today_tok['input'] + today_tok['output'], 'usd': round(today_usd, 4)},
        'total':  {'tokens': tok_total, 'usd': round(total_usd, 4)},
        'byModel': model_list,
        'byDay': day_list,
        'usdSource': 'estimate' if ('estimate' in sources or 'unknown' in sources) else 'catalog',
        'note': ('ChatGPT aboneliği — $ API-eşdeğeri maliyettir (gerçek fatura sabit abonelik). '
                 if auth_mode == 'chatgpt' else '') +
                'Token, rollout günlüklerindeki turn deltalarından; reasoning output içinde.' +
                limit_note + trunc_note,
    }


def _fmt_age(seconds) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return '?'
    if total < 60:
        return f'{total} sn'
    if total < 3600:
        return f'{total // 60} dk'
    if total < 86400:
        return f'{total // 3600} sa'
    return f'{total // 86400} gün'


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

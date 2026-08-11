#!/usr/bin/env python3
"""
Codex adaptörü — token hacmi + API-eşdeğeri $ tahmini.

Kaynak (SALT-OKUNUR):
  ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
  event_msg → payload.type == 'token_count' → info.last_token_usage (turn deltası):
    input_tokens · cached_input_tokens · output_tokens · reasoning_output_tokens · total_tokens
  Model: aynı dosyadaki en son 'model' alanı (turn_context/session_meta), yoksa gpt-5-codex.

  auth_mode ('chatgpt' = abonelik → $ = API-eşdeğeri maliyet, gerçek fatura değil).
Dosya-mtime cache'li. Rollout dizini yoksa → None (kart açılmaz).
"""
import json
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

# Büyük log dizininde asılmayı önlemek için tarama sınırı (ledger: python/executor-with-timeout-yalani)
MAX_FILES = 40
MAX_LINES_PER_FILE = 20000

_LOCK = threading.Lock()
_FILE_CACHE = {}         # {path: (mtime, size, [events])}
_MODEL_RE = re.compile(r'"model"\s*:\s*"([^"]+)"')

# event: (ms, model, inp, cached_in, out)
def _parse_file(fp: Path):
    """(events, hit_line_limit) döndür. hit_line_limit=True ise dosyanın sonu OKUNMADI —
       çağıran bunu kullanıcıya söylemek zorunda (sessiz kesme yasak)."""
    events = []
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
                info = payload.get('info') or {}
                last = info.get('last_token_usage') or {}
                if not last:
                    continue
                ts = o.get('timestamp')
                ms = _iso_to_ms(ts) if ts else None
                if ms is None:
                    try:
                        ms = int(fp.stat().st_mtime * 1000)
                    except OSError:
                        continue
                events.append((ms, cur_model,
                               int(last.get('input_tokens') or 0),
                               int(last.get('cached_input_tokens') or 0),
                               int(last.get('output_tokens') or 0)))
    except OSError:
        return [], False
    return events, hit_limit


def _iso_to_ms(ts: str):
    try:
        return int(datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp() * 1000)
    except Exception:
        return None


def _scan(since_ms: int):
    """(events, files, truncated_reason) döndür. truncated_reason ∈ {None,'files','lines'}.

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
    for mtime, size, fp in candidates:
        key = str(fp)
        with _LOCK:
            c = _FILE_CACHE.get(key)
        if c and c[0] == mtime and c[1] == size:
            evs, hit_lines = c[2], c[3]
        else:
            evs, hit_lines = _parse_file(fp)
            with _LOCK:
                _FILE_CACHE[key] = (mtime, size, evs, hit_lines)
        if hit_lines and truncated_reason is None:
            truncated_reason = 'lines'
        events.extend(e for e in evs if e[0] >= since_ms)
    return events, len(candidates), truncated_reason


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
    events, files, truncated_reason = scan

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

    return {
        'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'tokens',
        'available': True,
        # 'partial': sayı var ama eksik. 'ok' demek yalan olurdu.
        'status': ('partial' if (truncated_reason and events)
                   else ('ok' if events else 'nodata')),
        'error': None,
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
                trunc_note,
    }


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

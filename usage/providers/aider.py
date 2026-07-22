#!/usr/bin/env python3
"""
Aider adaptörü — token + $ (Codex deseni, lokal-log). (FAZ 4d — ADAY)

Kaynak (SALT-OKUNUR): ~/.aider/analytics.jsonl (aider --analytics açıksa).
  Satır: {"event": "...", "properties": {...}, "time": <unix>}
  message_send olayı properties: total_tokens_sent · total_tokens_received · cost · model

Dizin/dosya yoksa → None (kart açılmaz). Var ama parse yoksa → status='nodata' + not.
mtime-cache. ⚠️ Alan adları ADAY — canlı doğrulanacak.
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

PROVIDER_ID = 'aider'
PROVIDER_NAME = 'Aider'
LOG_PATH = Path.home() / '.aider' / 'analytics.jsonl'
CACHE_TTL = 60.0

_LOCK = threading.Lock()
_CACHE = None            # (mtime, size, card)

# token alanı aday adları (defansif)
_SENT = ('total_tokens_sent', 'prompt_tokens', 'tokens_sent', 'input_tokens')
_RECV = ('total_tokens_received', 'completion_tokens', 'tokens_received', 'output_tokens')


def _pick(d, names):
    for n in names:
        if n in d:
            try:
                return int(d[n])
            except (TypeError, ValueError):
                pass
    return 0


def _parse(days: int) -> dict:
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'tokens',
            'available': True, 'currency': 'USD', 'error': None, 'windowDays': days,
            'auth': 'yerel-log', 'usdSource': 'estimate'}
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day).timestamp()
    cutoff = now.timestamp() - days * 86400

    sent = recv = 0
    cost = today_cost = today_tok = 0.0
    sessions = 0
    by_day = {}
    models = {}
    try:
        with LOG_PATH.open(encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                props = o.get('properties') or o
                ts = o.get('time') or o.get('timestamp')
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    ts = None
                if ts is not None and ts < cutoff:
                    continue
                s = _pick(props, _SENT); r = _pick(props, _RECV)
                if not (s or r):
                    if str(o.get('event', '')).startswith('message'):
                        sessions += 1
                    continue
                sessions += 1
                sent += s; recv += r
                try:
                    c = float(props.get('cost') or 0)
                except (TypeError, ValueError):
                    c = 0.0
                cost += c
                mk = props.get('model') or 'aider'
                m = models.setdefault(mk, {'short': str(mk).split('/')[-1], 'usd': 0.0,
                                           'tokens': {'input': 0, 'output': 0}})
                m['usd'] += c; m['tokens']['input'] += s; m['tokens']['output'] += r
                if ts is not None:
                    dk = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
                    by_day[dk] = by_day.get(dk, 0.0) + c
                    if ts >= today_start:
                        today_cost += c; today_tok += s + r
    except OSError:
        return {**card, 'status': 'nodata', 'tokens': {'total': 0}, 'total': {'usd': 0},
                'today': {'tokens': 0, 'usd': 0}, 'byModel': [{'short': '—', 'model': 'n/a'}],
                'byDay': [], 'sessions': 0, 'note': 'analytics.jsonl okunamadı.'}

    total_tok = sent + recv
    model_list = sorted(({'model': k, **v, 'usd': round(v['usd'], 4)} for k, v in models.items()),
                        key=lambda x: x['usd'], reverse=True) or [{'short': '—', 'model': 'n/a'}]
    return {**card,
            'status': 'ok' if total_tok else 'nodata',
            'tokens': {'input': sent, 'output': recv, 'total': total_tok},
            'total': {'tokens': total_tok, 'usd': round(cost, 4)},
            'today': {'tokens': int(today_tok), 'usd': round(today_cost, 4)},
            'byModel': model_list,
            'byDay': [{'day': d, 'usd': round(v, 4)} for d, v in sorted(by_day.items())],
            'sessions': sessions,
            'note': 'aider --analytics loglarından; $ tahmini (aider kendi hesabı). Alanlar ADAY.'}


def collect(days: int = 30) -> dict:
    global _CACHE
    if not LOG_PATH.exists():
        return None
    try:
        st = LOG_PATH.stat()
    except OSError:
        return None
    now = time.time()
    with _LOCK:
        if _CACHE and _CACHE[0] == st.st_mtime and _CACHE[1] == st.st_size and (now - _CACHE[3]) < CACHE_TTL:
            return _CACHE[2]
    card = _parse(days)
    with _LOCK:
        _CACHE = (st.st_mtime, st.st_size, card, now)
    return card


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

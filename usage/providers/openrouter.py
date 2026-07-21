#!/usr/bin/env python3
"""
OpenRouter adaptörü — GERÇEK $ (Pane'in çekirdek kartı).

Kaynak (API key ile):
  GET https://openrouter.ai/api/v1/key      → limit / limit_remaining / usage_daily·weekly·monthly / usage (total)
  GET https://openrouter.ai/api/v1/credits  → total_credits / total_usage

Key: $OPENROUTER_API_KEY (env). Yoksa → None (kart açılmaz).
Güvenlik: key sadece Authorization header'ında OpenRouter'a gider; loglanmaz. 90s TTL cache.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

PROVIDER_ID = 'openrouter'
PROVIDER_NAME = 'OpenRouter'
BASE = 'https://openrouter.ai/api/v1'
CACHE_TTL = 90.0

_LOCK = threading.Lock()
_CACHE = None            # (fetched_at, card)


def _key():
    k = os.environ.get('OPENROUTER_API_KEY', '').strip()
    return k or None


def _get(path: str, key: str):
    req = urllib.request.Request(BASE + path, headers={
        'Authorization': 'Bearer ' + key,
        'Accept': 'application/json',
        'User-Agent': 'usage-tracker/0.2',
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _round(v, n=4):
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def _build(days: int) -> dict:
    key = _key()
    if not key:
        return None
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'spend',
            'currency': 'USD', 'available': True, 'status': 'ok', 'error': None}
    try:
        k = (_get('/key', key) or {}).get('data') or {}
    except urllib.error.HTTPError as e:
        return {**card, 'status': 'error', 'error': f'HTTP {e.code} ({e.reason})'}
    except Exception as e:
        return {**card, 'status': 'error', 'error': f'{type(e).__name__}: {e}'}

    # kredi bakiyesi (ayrı uç — başarısızsa kartı düşürmez)
    total_credits = total_usage = None
    try:
        c = (_get('/credits', key) or {}).get('data') or {}
        total_credits = _round(c.get('total_credits'))
        total_usage = _round(c.get('total_usage'))
    except Exception:
        pass

    limit_amt = k.get('limit')                       # None = limitsiz
    limit_rem = k.get('limit_remaining')
    card['spend'] = {
        'today': _round(k.get('usage_daily')),
        'week':  _round(k.get('usage_weekly')),
        'month': _round(k.get('usage_monthly')),
        'total': _round(k.get('usage')),
    }
    if total_credits is not None:
        card['balance'] = {
            'total': total_credits,
            'used':  total_usage,
            'remaining': _round((total_credits or 0) - (total_usage or 0)),
        }
    if limit_amt is not None:
        used = None if limit_rem is None else _round(limit_amt - limit_rem)
        card['limit'] = {
            'amount': _round(limit_amt),
            'remaining': _round(limit_rem),
            'used': used,
            'reset': k.get('limit_reset'),           # 'daily' vb.
            'pct': (_round(used / limit_amt * 100, 1) if limit_amt and used is not None else None),
        }
    card['tier'] = 'free' if k.get('is_free_tier') else 'paid'
    return card


def collect(days: int = 30) -> dict:
    global _CACHE
    if _key() is None:
        return None
    now = time.time()
    with _LOCK:
        if _CACHE and (now - _CACHE[0]) < CACHE_TTL:
            return _CACHE[1]
    card = _build(days)
    if card and card.get('status') == 'ok':
        with _LOCK:
            _CACHE = (now, card)
    return card


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

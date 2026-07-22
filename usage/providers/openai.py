#!/usr/bin/env python3
"""
OpenAI adaptörü — GERÇEK $ (Costs API). (FAZ 4b)

⚠️ 2024 API yeniden yapılanması: eski `/v1/usage?date=` ve `/dashboard/billing/*`
dashboard-session-only oldu (API-key ile erişilemez). Güncel yol = organizasyon Costs API,
**Admin API key** gerektirir (`sk-admin-...` — Settings → Organization → Admin keys):
  GET https://api.openai.com/v1/organization/costs?start_time=<unix>&bucket_width=1d&limit=<n>
    → {data:[{start_time, results:[{amount:{value,currency}}]}], ...}

Key: $OPENAI_ADMIN_KEY (tercih) veya $OPENAI_API_KEY. Normal proje key'i → 401 (yetki yok)
→ 'admin key gerekli' hata kartı. Yoksa → None (kart açılmaz). 90s cache.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

PROVIDER_ID = 'openai'
PROVIDER_NAME = 'OpenAI'
BASE = 'https://api.openai.com/v1'
CACHE_TTL = 90.0

_LOCK = threading.Lock()
_CACHE = None


def _key():
    k = (os.environ.get('OPENAI_ADMIN_KEY') or os.environ.get('OPENAI_API_KEY') or '').strip()
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
    now = datetime.now()
    month_start = int(datetime(now.year, now.month, 1).timestamp())
    today_start = int(datetime(now.year, now.month, now.day).timestamp())
    try:
        page = _get(f'/organization/costs?start_time={month_start}&bucket_width=1d&limit=31', key)
    except urllib.error.HTTPError as e:
        hint = ' (Admin API key gerekli — proje key\'i Costs API\'ye erişemez)' if e.code in (401, 403) else ''
        return {**card, 'status': 'error', 'error': f'HTTP {e.code} ({e.reason}){hint}'}
    except Exception as e:
        return {**card, 'status': 'error', 'error': f'{type(e).__name__}: {e}'}

    month_usd = today_usd = 0.0
    by_day = []
    for bucket in (page.get('data') or []):
        bstart = bucket.get('start_time')
        amt = 0.0
        for res in (bucket.get('results') or []):
            v = ((res.get('amount') or {}).get('value'))
            try:
                amt += float(v)
            except (TypeError, ValueError):
                pass
        month_usd += amt
        if bstart is not None:
            dk = datetime.fromtimestamp(bstart).strftime('%Y-%m-%d')
            by_day.append({'day': dk, 'usd': round(amt, 4)})
            if bstart >= today_start:
                today_usd += amt

    card['spend'] = {'today': _round(today_usd), 'month': _round(month_usd)}
    card['byDay'] = by_day
    card['tier'] = 'org'
    card['note'] = 'Organizasyon Costs API (gerçek fatura). Admin key ile.'
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

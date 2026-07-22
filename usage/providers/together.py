#!/usr/bin/env python3
"""
Together AI adaptörü — kredi/bakiye (OpenRouter deseni). (FAZ 4c — ADAY)

⚠️ Endpoint ADAY: canlı key ile doğrulanacak. Together, OpenAI-uyumlu inference sunar;
hesap/bakiye ucu netleşene dek defansif parse (bilinen alan adları taranır).
  Denenen: GET https://api.together.xyz/v1/... (models doğrulama) → hesap alanları

Key: $TOGETHER_API_KEY. Yoksa → None. 200 ama tanınan alan yoksa → 'endpoint doğrulanacak'
hata kartı (yanlış veri göstermek yerine dürüst uyarı). 90s cache.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

PROVIDER_ID = 'together'
PROVIDER_NAME = 'Together AI'
BASE = 'https://api.together.xyz'
CACHE_TTL = 90.0

_LOCK = threading.Lock()
_CACHE = None

# Kredi/bakiye için taranan olası alan adları (defansif)
_BALANCE_FIELDS = ('balance', 'credit', 'credits', 'available_credit', 'remaining', 'available')


def _key():
    return (os.environ.get('TOGETHER_API_KEY') or '').strip() or None


def _get(path: str, key: str):
    req = urllib.request.Request(BASE + path, headers={
        'Authorization': 'Bearer ' + key,
        'Accept': 'application/json',
        'User-Agent': 'usage-tracker/0.2',
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _dig(obj, fields):
    """İç içe dict'te bilinen alanlardan ilk sayısal değeri bul."""
    if isinstance(obj, dict):
        for f in fields:
            if f in obj:
                try:
                    return float(obj[f])
                except (TypeError, ValueError):
                    pass
        for v in obj.values():
            r = _dig(v, fields)
            if r is not None:
                return r
    return None


def _build(days: int) -> dict:
    key = _key()
    if not key:
        return None
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'spend',
            'currency': 'USD', 'available': True, 'status': 'ok', 'error': None,
            'tier': 'paid'}
    for path in ('/v1/account', '/api/account', '/v1/user'):
        try:
            data = _get(path, key)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return {**card, 'status': 'error', 'error': f'HTTP {e.code} (geçersiz/yetersiz key)'}
            continue
        except Exception:
            continue
        bal = _dig(data, _BALANCE_FIELDS)
        if bal is not None:
            card['balance'] = {'remaining': round(bal, 4)}
            card['note'] = f'Bakiye {path} üzerinden (ADAY endpoint — doğrula).'
            return card
    return {**card, 'status': 'error',
            'error': 'Bakiye endpoint\'i doğrulanacak (aday) — key geçerli ama alan bulunamadı'}


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

#!/usr/bin/env python3
"""
ENDPOINT ÖLÇÜMÜ: 2026-08-11'de kimliksiz yoklandı: /v3/user → 400 (var). /api/v1/user/balance ve /v1/user → 404 (yok), kaldırıldı.

Novita AI adaptörü — kredi/bakiye (OpenRouter deseni).

Uç VAR (ölçüldü). ŞEMA hâlâ doğrulanmadı — anahtar yok, yanıtın içindeki alan adları
görülmedi. Tutar `_money.pick_amount` ile dar taranır; tanınmazsa rakam gösterilmez.
  Denenen: GET https://api.novita.ai/v3/user · /api/v1/user/balance

Key: $NOVITA_API_KEY. Yoksa → None. 90s cache.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import _money

PROVIDER_ID = 'novita'
PROVIDER_NAME = 'Novita AI'
BASE = 'https://api.novita.ai'
CACHE_TTL = 90.0

_LOCK = threading.Lock()
_CACHE = None


def _key():
    return (os.environ.get('NOVITA_API_KEY') or '').strip() or None


def _get(path: str, key: str):
    req = urllib.request.Request(BASE + path, headers={
        'Authorization': 'Bearer ' + key,
        'Accept': 'application/json',
        'User-Agent': 'usage-tracker/0.2',
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _build(days: int) -> dict:
    key = _key()
    if not key:
        return None
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'spend',
            'currency': 'USD', 'available': True, 'status': 'ok', 'error': None,
            'tier': 'paid'}
    for path in ('/v3/user',):        # tek yol: ölçüldü, ötekiler 404
        try:
            data = _get(path, key)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return {**card, 'status': 'error', 'error': f'HTTP {e.code} (geçersiz/yetersiz key)'}
            continue
        except Exception:
            continue
        bal = _money.pick_amount(data)
        if bal is not None:
            # Novita bakiyeyi bazen "kuruş" (1/100) tutar — ham değeri koru, not düş
            card['balance'] = {'remaining': round(bal, 4)}
            card['note'] = (f'Bakiye {path} üzerinden (uç ölçüldü; şema ve para birimi '
                            f'canlı anahtarla doğrulanmadı).')
            return card
    return {**card, 'status': 'error',
            'error': 'Uç yanıt verdi ama tanınan bir bakiye alanı yok — '
                     'şema canlı anahtarla doğrulanmalı (rakam uydurulmadı)'}


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

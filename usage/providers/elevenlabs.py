#!/usr/bin/env python3
"""
ElevenLabs adaptörü — karakter KOTASI (kind='quota').

ElevenLabs abonelik tabanlı: aylık karakter havuzu (TTS/ses). Doğrudan $ değil —
kullanılan/limit karakter + aylık reset. Kullanıcı Alice TTS için aktif kullanıyor.

Kaynak (API key ile):
  GET https://api.elevenlabs.io/v1/user/subscription
    → character_count / character_limit / next_character_count_reset_unix / tier / status

Key: $ELEVENLABS_API_KEY (env; AliceAi ile aynı ad). Yoksa → None (kart açılmaz).
Güvenlik: key sadece `xi-api-key` header'ında ElevenLabs'a gider; loglanmaz. 90s TTL cache.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

PROVIDER_ID = 'elevenlabs'
PROVIDER_NAME = 'ElevenLabs'
BASE = 'https://api.elevenlabs.io/v1'
CACHE_TTL = 90.0

_LOCK = threading.Lock()
_CACHE = None            # (fetched_at, card)


def _key():
    k = os.environ.get('ELEVENLABS_API_KEY', '').strip()
    return k or None


def _get(path: str, key: str):
    req = urllib.request.Request(BASE + path, headers={
        'xi-api-key': key,
        'Accept': 'application/json',
        'User-Agent': 'usage-tracker/0.2',
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse(sub: dict) -> dict:
    """Abonelik yükünü quota kartına çevir. `sub` = /user/subscription yanıtı."""
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'quota',
            'currency': None, 'available': True, 'status': 'ok', 'error': None}
    used = _int(sub.get('character_count'))
    limit = _int(sub.get('character_limit'))
    remaining = (limit - used) if (limit is not None and used is not None) else None
    pct = (round(used / limit * 100, 1) if (limit and used is not None) else None)
    card['quota'] = {
        'used': used,
        'limit': limit,
        'remaining': remaining,
        'pct': pct,
        'unit': 'karakter',
        'reset': _int(sub.get('next_character_count_reset_unix')),   # unix saniye
    }
    card['tier'] = sub.get('tier') or sub.get('status') or 'paid'
    return card


def _build(days: int) -> dict:
    key = _key()
    if not key:
        return None
    base = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'quota',
            'currency': None, 'available': True, 'status': 'ok', 'error': None}
    try:
        sub = _get('/user/subscription', key) or {}
    except urllib.error.HTTPError as e:
        return {**base, 'status': 'error', 'error': f'HTTP {e.code} ({e.reason})'}
    except Exception as e:
        return {**base, 'status': 'error', 'error': f'{type(e).__name__}: {e}'}
    return _parse(sub)


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

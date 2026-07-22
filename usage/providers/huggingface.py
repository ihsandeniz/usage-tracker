#!/usr/bin/env python3
"""
HuggingFace adaptörü — kota (quota). (FAZ 4c — ADAY)

⚠️ HF'nin temiz bir public "kullanım" API'si yok. `whoami-v2` ile key doğrulanır;
kota için ADAY `/api/quota` denenir (plan iddiası — canlı doğrulanacak).
  GET https://huggingface.co/api/whoami-v2  → key geçerlilik + plan
  GET https://huggingface.co/api/quota      → (aday) quota_used / quota_limit

Key: $HUGGINGFACE_API_KEY veya $HF_TOKEN. Yoksa → None. 90s cache.
Kota bulunursa kind='quota'; bulunmazsa geçerli-key ama 'kullanım API yok' hata kartı.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

PROVIDER_ID = 'huggingface'
PROVIDER_NAME = 'HuggingFace'
BASE = 'https://huggingface.co'
CACHE_TTL = 90.0

_LOCK = threading.Lock()
_CACHE = None


def _key():
    return (os.environ.get('HUGGINGFACE_API_KEY') or os.environ.get('HF_TOKEN') or '').strip() or None


def _get(path: str, key: str):
    req = urllib.request.Request(BASE + path, headers={
        'Authorization': 'Bearer ' + key,
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


def _build(days: int) -> dict:
    key = _key()
    if not key:
        return None
    base = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'currency': None,
            'available': True, 'status': 'ok', 'error': None}
    # 1) Key doğrula
    try:
        who = _get('/api/whoami-v2', key) or {}
    except urllib.error.HTTPError as e:
        return {**base, 'kind': 'quota', 'status': 'error', 'error': f'HTTP {e.code} (geçersiz key)'}
    except Exception as e:
        return {**base, 'kind': 'quota', 'status': 'error', 'error': f'{type(e).__name__}: {e}'}
    plan = (who.get('plan') or (who.get('periodEnd') and 'pro') or 'free')
    # 2) Kota dene (aday)
    try:
        q = _get('/api/quota', key) or {}
        used = _int(q.get('quota_used') or q.get('used'))
        limit = _int(q.get('quota_limit') or q.get('limit'))
        if used is not None and limit:
            return {**base, 'kind': 'quota', 'tier': str(plan),
                    'quota': {'used': used, 'limit': limit,
                              'remaining': limit - used,
                              'pct': round(used / limit * 100, 1),
                              'unit': 'kredi', 'reset': None},
                    'note': 'Kota /api/quota (ADAY — doğrula).'}
    except Exception:
        pass
    return {**base, 'kind': 'quota', 'tier': str(plan), 'status': 'error',
            'error': 'Key geçerli ama HF kullanım/kota API\'si doğrulanmadı'}


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

#!/usr/bin/env python3
"""
Jan adaptörü — yerel/ücretsiz ($ yok). Offline-aware. (FAZ 4b)

Jan, OpenAI-uyumlu yerel sunucu (varsayılan :1337).
  GET http://127.0.0.1:1337/v1/models  → yüklü model id listesi

Algılama ("no dead cards"):
  - Sunucu erişilebilir → status='ok'.
  - Erişilemez ama Jan veri dizini (~/.jan) var → status='offline'.
  - İkisi de yok → None (kart açılmaz).
Config: $JAN_URL (varsayılan http://127.0.0.1:1337). Kısa timeout + 30s cache.
"""
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

PROVIDER_ID = 'jan'
PROVIDER_NAME = 'Jan'
BASE = os.environ.get('JAN_URL', 'http://127.0.0.1:1337').rstrip('/')
DATA_DIR = Path.home() / '.jan'
CACHE_TTL = 30.0

_LOCK = threading.Lock()
_CACHE = None


def _get(path: str):
    req = urllib.request.Request(BASE + path, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.load(r)


def _configured() -> bool:
    return DATA_DIR.exists() or 'JAN_URL' in os.environ


def _build(days: int) -> dict:
    if not _configured():
        return None
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'local',
            'available': True, 'currency': None, 'error': None,
            'note': 'Yerel modeller — ücretsiz, $ maliyeti yok.'}
    try:
        data = (_get('/v1/models') or {}).get('data')
    except Exception:
        return {**card, 'status': 'offline',
                'note': 'Jan kurulu, yerel API sunucusu kapalı. Jan → Settings → Local API Server.',
                'models': [], 'running': [], 'modelCount': 0}

    models = []
    for m in (data or []):
        name = m.get('id') or '?'
        models.append({'name': name, 'size': None,
                       'family': (m.get('owned_by') or m.get('object'))})
    return {**card, 'status': 'ok', 'models': models, 'running': [],
            'modelCount': len(models)}


def collect(days: int = 30) -> dict:
    global _CACHE
    if not _configured():
        return None
    now = time.time()
    with _LOCK:
        if _CACHE and (now - _CACHE[0]) < CACHE_TTL:
            return _CACHE[1]
    card = _build(days)
    with _LOCK:
        _CACHE = (now, card)
    return card


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

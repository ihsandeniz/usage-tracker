#!/usr/bin/env python3
"""
LM Studio adaptörü — yerel/ücretsiz ($ yok). Offline-aware. (FAZ 4b)

LM Studio, OpenAI-uyumlu yerel sunucu (varsayılan :1234). Native REST v0 daha zengin:
  GET http://127.0.0.1:1234/api/v0/models  → id·type·state(loaded/not-loaded)·max_context_length
  (fallback) GET /v1/models                → OpenAI-uyumlu id listesi

Algılama ("no dead cards"):
  - Sunucu erişilebilir → status='ok' (+ yüklü/çalışan model).
  - Erişilemez ama `lms` CLI kurulu → status='offline' (kullanıcı sunucuyu açabilir).
  - İkisi de yok → None (kart açılmaz).
Config: $LMSTUDIO_URL (varsayılan http://127.0.0.1:1234). Kısa timeout + 30s cache.
"""
import json
import os
import shutil
import threading
import time
import urllib.request

PROVIDER_ID = 'lmstudio'
PROVIDER_NAME = 'LM Studio'
BASE = os.environ.get('LMSTUDIO_URL', 'http://127.0.0.1:1234').rstrip('/')
CACHE_TTL = 30.0

_LOCK = threading.Lock()
_CACHE = None


def _get(path: str):
    req = urllib.request.Request(BASE + path, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.load(r)


def _has_binary() -> bool:
    return shutil.which('lms') is not None


def _configured() -> bool:
    """Kart açmaya değer mi? CLI kurulu VEYA özel URL verilmiş."""
    return _has_binary() or 'LMSTUDIO_URL' in os.environ


def _build(days: int) -> dict:
    if not _configured():
        return None
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'local',
            'available': True, 'currency': None, 'error': None,
            'note': 'Yerel modeller — ücretsiz, $ maliyeti yok.'}
    # Native v0 (zengin) → fallback OpenAI-uyumlu
    data = None
    try:
        data = (_get('/api/v0/models') or {}).get('data')
    except Exception:
        try:
            data = (_get('/v1/models') or {}).get('data')
        except Exception:
            return {**card, 'status': 'offline',
                    'note': 'CLI kurulu, sunucu kapalı. `lms server start` ile başlat.',
                    'models': [], 'running': [], 'modelCount': 0}

    models, running = [], []
    for m in (data or []):
        name = m.get('id') or m.get('key') or '?'
        state = m.get('state')            # 'loaded' | 'not-loaded' | None
        models.append({'name': name, 'size': None,
                       'family': m.get('type') or m.get('arch')})
        if state == 'loaded':
            running.append({'name': name, 'expires': None})
    return {**card, 'status': 'ok', 'models': models, 'running': running,
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

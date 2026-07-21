#!/usr/bin/env python3
"""
Ollama adaptörü — yerel/ücretsiz ($ yok). Offline-aware.

Kaynak:
  GET http://127.0.0.1:11434/api/tags  → kurulu modeller (ad/boyut)
  GET http://127.0.0.1:11434/api/ps    → şu an RAM'de yüklü (çalışan) modeller

Binary yoksa → None (kart açılmaz). Binary var ama servis kapalıysa → status='offline'
(kullanıcı `ollama serve` ile açabilir). Kısa timeout (bağlantı reddi anında döner) + 30s cache.
"""
import json
import shutil
import threading
import time
import urllib.request

PROVIDER_ID = 'ollama'
PROVIDER_NAME = 'Ollama'
BASE = 'http://127.0.0.1:11434'
CACHE_TTL = 30.0

_LOCK = threading.Lock()
_CACHE = None


def _get(path: str):
    req = urllib.request.Request(BASE + path, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.load(r)


def _has_binary() -> bool:
    return shutil.which('ollama') is not None


def _build(days: int) -> dict:
    if not _has_binary():
        return None
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'local',
            'available': True, 'currency': None, 'error': None,
            'note': 'Yerel modeller — ücretsiz, $ maliyeti yok.'}
    try:
        tags = _get('/api/tags') or {}
    except Exception:
        return {**card, 'status': 'offline',
                'note': 'Binary kurulu, servis kapalı. `ollama serve` ile başlat.',
                'models': [], 'running': []}

    models = []
    for m in (tags.get('models') or []):
        models.append({'name': m.get('name') or m.get('model') or '?',
                       'size': m.get('size'),
                       'family': ((m.get('details') or {}).get('family'))})
    running = []
    try:
        ps = _get('/api/ps') or {}
        for m in (ps.get('models') or []):
            running.append({'name': m.get('name') or m.get('model') or '?',
                            'expires': m.get('expires_at')})
    except Exception:
        pass

    models.sort(key=lambda x: -(x['size'] or 0))
    return {**card, 'status': 'ok', 'models': models, 'running': running,
            'modelCount': len(models)}


def collect(days: int = 30) -> dict:
    global _CACHE
    if not _has_binary():
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

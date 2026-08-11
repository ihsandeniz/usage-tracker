#!/usr/bin/env python3
"""
Together AI adaptörü — **anahtar doğrulama, kullanım verisi YOK.**

ENDPOINT ÖLÇÜMÜ (2026-08-11, kimliksiz yoklama):
    https://api.together.xyz/v1/models        → 401 "Missing API key"   ✅ taban doğru
    /v1/account · /api/account · /v1/user
    /v1/account/info · /api/v1/account
    /v1/organizations · /v1/me · /v1/usage    → hepsi 404 + HTML sayfası
    api.together.ai üzerinde de aynı sonuç.

Yani **Together AI hesap/bakiye/kullanım ucu yayınlamıyor.** Bu adaptörün eski hâli
"FAZ 4c ADAY" etiketiyle üç yol deniyor, hiçbiri var olmadığı için yanıtı defansif bir
alan taramasından geçiriyor ve `balance/credit/remaining/available` adlı ilk sayıyı
bakiye diye gösteriyordu. Var olmayan bir ucun yanıtından okunan sayı, olsa olsa
başka bir şeydir.

Bugün yaptığı: anahtar geçerli mi, onu söyler. Kullanım rakamı **uydurmaz**.
Together bir gün böyle bir uç yayınlarsa buraya eklenir — ölçülerek, tahmin edilerek değil.

Key: $TOGETHER_API_KEY. Yoksa → None ("no dead cards"). 90s cache.
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
# Kullanım ucu değil — yalnız anahtarın geçerliliğini kanıtlayan, VAR OLDUĞU ÖLÇÜLMÜŞ uç.
PROBE_PATH = '/v1/models'
CACHE_TTL = 90.0

NO_USAGE_NOTE = ('Anahtar geçerli. Together AI hesap/bakiye/kullanım ucu yayınlamıyor '
                 '(2026-08-11: 7 aday yol ölçüldü, hepsi 404) — bu yüzden burada rakam yok. '
                 'Harcamanı together.ai panelinden görebilirsin.')

_LOCK = threading.Lock()
_CACHE = None


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


def _build(days: int) -> dict:
    key = _key()
    if not key:
        return None
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'spend',
            'currency': 'USD', 'available': True, 'error': None, 'tier': 'paid'}
    try:
        _get(PROBE_PATH, key)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {**card, 'status': 'error',
                    'error': f'HTTP {e.code} (geçersiz/yetersiz key)'}
        return {**card, 'status': 'error', 'error': f'HTTP {e.code}'}
    except Exception as e:
        return {**card, 'status': 'error', 'error': f'{type(e).__name__}'}
    # Anahtar çalışıyor ama sağlayıcıda okunacak kullanım verisi yok — dürüst boş kart.
    return {**card, 'status': 'nodata', 'note': NO_USAGE_NOTE}


def collect(days: int = 30) -> dict:
    global _CACHE
    if _key() is None:
        return None
    now = time.time()
    with _LOCK:
        if _CACHE and (now - _CACHE[0]) < CACHE_TTL:
            return _CACHE[1]
    card = _build(days)
    if card and card.get('status') in ('ok', 'nodata'):
        with _LOCK:
            _CACHE = (now, card)
    return card


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

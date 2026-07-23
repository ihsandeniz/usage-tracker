#!/usr/bin/env python3
"""
DeepSeek adaptörü — kredi bakiyesi (OpenRouter deseni). (FAZ 5a)

Endpoint BELGELİ (tahmin değil, platform.deepseek.com/api-docs → "Get User Balance"):
  GET https://api.deepseek.com/user/balance
      Authorization: Bearer $DEEPSEEK_API_KEY
  Yanıt: {"is_available": bool,
          "balance_infos": [{"currency":"USD"|"CNY",
                             "total_balance":"110.00",
                             "granted_balance":"10.00",
                             "topped_up_balance":"100.00"}]}

Key: $DEEPSEEK_API_KEY. Yoksa → None ("no dead cards"). 401/403 → dürüst hata kartı.
Değerler string gelir → float'a çevrilir. USD tercih; yoksa ilk currency. 90s cache.
NOT: endpoint belgeli ama canlı key ile HENÜZ doğrulanmadı (ihsan'da DeepSeek key yok) → 'beta'.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

PROVIDER_ID = 'deepseek'
PROVIDER_NAME = 'DeepSeek'
BALANCE_URL = 'https://api.deepseek.com/user/balance'
CACHE_TTL = 90.0

_LOCK = threading.Lock()
_CACHE = None


def _key():
    return (os.environ.get('DEEPSEEK_API_KEY') or '').strip() or None


def _num(v):
    """String/sayı bakiyeyi float'a çevir (DeepSeek string döndürür: '110.00')."""
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _pick(infos):
    """balance_infos[] içinden USD'yi tercih et, yoksa ilkini al."""
    if not isinstance(infos, list) or not infos:
        return None
    for it in infos:
        if isinstance(it, dict) and (it.get('currency') or '').upper() == 'USD':
            return it
    return infos[0] if isinstance(infos[0], dict) else None


def _build(days: int) -> dict:
    key = _key()
    if not key:
        return None
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'spend',
            'currency': 'USD', 'available': True, 'status': 'ok', 'error': None,
            'tier': 'kullandıkça-öde'}
    req = urllib.request.Request(BALANCE_URL, headers={
        'Authorization': 'Bearer ' + key,
        'Accept': 'application/json',
        'User-Agent': 'usage-tracker/0.2',
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {**card, 'status': 'error', 'error': f'HTTP {e.code} (geçersiz/yetersiz key)'}
        return {**card, 'status': 'error', 'error': f'HTTP {e.code}'}
    except Exception as e:
        return {**card, 'status': 'error', 'error': f'Erişilemedi ({type(e).__name__})'}

    info = _pick(data.get('balance_infos'))
    if not info:
        return {**card, 'status': 'error',
                'error': 'Bakiye alanı bulunamadı (endpoint doğrulanacak)'}
    cur = (info.get('currency') or 'USD').upper()
    total = _num(info.get('total_balance'))
    granted = _num(info.get('granted_balance'))
    topped = _num(info.get('topped_up_balance'))
    card['currency'] = cur
    card['balance'] = {'remaining': round(total, 4) if total is not None else None}
    if granted is not None:
        card['balance']['granted'] = round(granted, 4)
    if topped is not None:
        card['balance']['toppedUp'] = round(topped, 4)
    if data.get('is_available') is False:
        card['note'] = 'Bakiye tükendi / hesap kullanılamıyor (is_available=false).'
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

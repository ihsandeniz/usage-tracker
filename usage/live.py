#!/usr/bin/env python3
"""
Canlı gerçek limit — Anthropic'in kendi usage endpoint'i (Claude Code'un /usage komutunun kaynağı).
  GET https://api.anthropic.com/api/oauth/usage
      Authorization: Bearer <~/.claude/.credentials.json accessToken>
      anthropic-beta: oauth-2025-04-20

Kalibrasyon GEREKTİRMEZ — Anthropic'in kendi utilization %'si. Yanıt alanları (binary'den):
  five_hour · seven_day · seven_day_opus · seven_day_sonnet → utilization / resets_at / remaining.

GÜVENLİK & NEZAKET:
  - Token'ı SADECE OKUR, asla yazmaz/yenilemez (paylaşılan credentials'ı bozmamak için).
  - Arka planda poll YOK; yalnız istek geldiğinde çeker + TTL cache (varsayılan 120s) → 429 riski minimal.
  - Token süresi dolmuşsa (Claude Code kapalı) canlı veri 'yok' döner; çağıran kalibrasyona düşer.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CREDS_PATH = Path.home() / '.claude' / '.credentials.json'
USAGE_URL  = 'https://api.anthropic.com/api/oauth/usage'
BETA       = 'oauth-2025-04-20'
UA         = 'claude-cli/2.1.197 (external, cli)'
CACHE_TTL  = 120.0          # sn — bu süre içinde tekrar çağrı ağı vurmaz (429 koruması)
# En kısa kota penceresi 5 saat. 10 dakika, bu pencerenin yaklaşık %3'ü ve
# normal 2 dakikalık cache TTL'inin 5 katı: kısa kesintileri tolere ederken
# saatler/günler önceki bir yüzdeyi "canlı" saymaz.
LIVE_FRESHNESS_SEC = 10 * 60.0

_LOCK  = threading.Lock()
_CACHE = None               # (fetched_at, result_dict)


def _read_token():
    try:
        d = json.loads(CREDS_PATH.read_text(encoding='utf-8'))
        o = d.get('claudeAiOauth') or {}
        return o.get('accessToken'), int(o.get('expiresAt') or 0), o.get('rateLimitTier')
    except Exception:
        return None, 0, None


def _norm_window(v):
    """Bir pencere sözlüğünü normalize et — utilization/resets_at/remaining varsa çıkar."""
    if not isinstance(v, dict):
        return None
    keys = set(v.keys())
    if not (keys & {'utilization', 'resets_at', 'remaining', 'overage'}):
        return None
    # Anthropic utilization'ı ZATEN yüzde (0–100) verir: seven_day=51.0 ↔ limits weekly_all percent=51,
    # five_hour=2.0 ↔ session percent=2. Eski `<=1.01 → *100` heuristiği, limit YENİ sıfırlanınca
    # session %0–1'e düştüğünde onu %100 sanıyordu (util=1.0 → 100). Kaldırıldı — util ham yüzde.
    util = v.get('utilization')
    if isinstance(util, (int, float)):
        util = round(float(util), 1)
    return {
        'utilization': util,
        'resets_at':   v.get('resets_at'),
        'remaining':   v.get('remaining'),
        'overage':     v.get('overage'),
    }


def _parse(payload: dict) -> dict:
    """Ham yanıtı normalize et. Bilinen pencereler + otomatik keşif; ham veri de saklanır."""
    windows = {}
    known = ('five_hour', 'seven_day', 'seven_day_opus', 'seven_day_sonnet',
             'seven_day_oauth_apps')
    for k in known:
        if k in payload:
            nw = _norm_window(payload[k])
            if nw:
                windows[k] = nw
    # şemada beklenmeyen ek pencereler olursa yakala
    for k, v in payload.items():
        if k in windows:
            continue
        nw = _norm_window(v)
        if nw:
            windows[k] = nw
    return windows


def _fetch_raw():
    tok, exp, tier = _read_token()
    now_ms = int(time.time() * 1000)
    if not tok:
        return {'ok': False, 'error': 'token okunamadı', 'raw': None}
    if exp and exp <= now_ms:
        return {'ok': False, 'error': 'token süresi dolmuş (Claude Code açıkken tazelenir)', 'raw': None}
    req = urllib.request.Request(USAGE_URL, headers={
        'Authorization': 'Bearer ' + tok,
        'anthropic-beta': BETA,
        'User-Agent': UA,
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = json.load(r)
        return {'ok': True, 'error': None, 'raw': raw, 'rateLimitTier': tier,
                'windows': _parse(raw if isinstance(raw, dict) else {})}
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': f'HTTP {e.code} ({e.reason})', 'raw': None,
                'rateLimited': e.code == 429}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'raw': None}


def _disk_cache_path():
    # XDG'yi elle çözüyordu — Windows'ta $XDG_STATE_HOME yok ve `~/.local/state`
    # oraya ait bir kural değil. Yol çözümlemesi artık tek yerde: usage/platform.py
    from . import platform as _paths
    return _paths.state_dir() / 'live-last-good.json'


def _load_disk_cache():
    """Son iyi sonucu diskten oku — süreç yeniden başladığında elimiz boş kalmasın.

    Bellekteki cache restart'ta uçuyordu; hemen ardından uç 429 verirse
    gösterecek hiçbir şey kalmıyor ve rozet boşalıyordu. Diskteki kopya bu
    boşluğu kapatır: gerçek değeri 'stale' işaretiyle göstermek, hiç
    göstermemekten de uydurmaktan da dürüst.
    """
    try:
        d = json.loads(_disk_cache_path().read_text(encoding='utf-8'))
        ts, res = d.get('at'), d.get('res')
        if isinstance(ts, (int, float)) and isinstance(res, dict) and res.get('ok'):
            return (ts, res)
    except Exception:
        pass
    return None


def _save_disk_cache(at: float, res: dict):
    try:
        from . import platform as _paths
        # atomik + benzersiz geçici ad: sabit '.tmp' iki eşzamanlı yazarı çarpıştırıyordu
        _paths.atomic_write_text(_disk_cache_path(), json.dumps({'at': at, 'res': res}))
    except Exception:
        pass                    # cache lüks; yazamazsak sessizce devam


def _with_freshness(res: dict, fetched_at: float, now: float) -> dict:
    """Son başarılı ağ yanıtının zamanını ve türetilmiş yaşını ekle."""
    out = dict(res)
    age = round(max(0.0, now - fetched_at), 1)
    out['fetchedAtMs'] = int(fetched_at * 1000)
    out['ageSec'] = age
    out['stale'] = age > LIVE_FRESHNESS_SEC
    return out


def fetch(force: bool = False) -> dict:
    """TTL-cache'li canlı limit. force=True cache'i atlar (dikkat: 429 riski)."""
    import os
    if os.environ.get('USAGE_DEMO') == '1':
        from . import demo
        return demo.fetch()
    global _CACHE
    now = time.time()
    now_ms = int(now * 1000)
    # Token süresi dolmuşsa cache'i geçersiz kıl
    _, exp, _ = _read_token()
    if exp and exp <= now_ms:
        _CACHE = None
    with _LOCK:
        if _CACHE is None:
            _CACHE = _load_disk_cache()      # restart sonrası ilk çağrı
        if _CACHE and not force and (now - _CACHE[0]) < CACHE_TTL:
            res = _with_freshness(_CACHE[1], _CACHE[0], now)
            res['cached'] = True
            return res
    res = _fetch_raw()
    with _LOCK:
        # başarılı sonucu cache'le; başarısızsa son iyi sonucu koru ama hatayı da bildir
        if res.get('ok'):
            # İstek başlatıldığı an değil, veri gerçekten alındıktan sonraki an.
            fetched_at = time.time()
            _CACHE = (fetched_at, res)
            _save_disk_cache(fetched_at, res)
            res = _with_freshness(res, fetched_at, fetched_at)
        elif _CACHE:
            stale = dict(_CACHE[1])
            stale.update({'ok': True, 'cached': True, 'staleReason': res.get('error')})
            return _with_freshness(stale, _CACHE[0], now)
    res['cached'] = False
    if not res.get('ok'):
        # Başarılı temel veri yok; yaş hesabı uygulanamaz. `ok` zaten yokluğu ayırır.
        res.update({'fetchedAtMs': None, 'ageSec': None, 'stale': False})
    return res


if __name__ == '__main__':
    import pprint
    pprint.pprint(fetch(force=True))

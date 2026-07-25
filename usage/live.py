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
    import os
    base = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return Path(base) / 'usage-tracker' / 'live-last-good.json'


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
        p = _disk_cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps({'at': at, 'res': res}), encoding='utf-8')
        tmp.replace(p)          # atomik — yarım dosya okunmasın
    except Exception:
        pass                    # cache lüks; yazamazsak sessizce devam


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
            res = dict(_CACHE[1]); res['cached'] = True
            res['ageSec'] = round(now - _CACHE[0], 1)
            return res
    res = _fetch_raw()
    with _LOCK:
        # başarılı sonucu cache'le; başarısızsa son iyi sonucu koru ama hatayı da bildir
        if res.get('ok'):
            _CACHE = (now, res)
            _save_disk_cache(now, res)
        elif _CACHE:
            stale = dict(_CACHE[1])
            stale.update({'ok': True, 'stale': True, 'staleReason': res.get('error'),
                          'ageSec': round(now - _CACHE[0], 1)})
            return stale
    res['cached'] = False
    return res


if __name__ == '__main__':
    import pprint
    pprint.pprint(fetch(force=True))

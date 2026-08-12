#!/usr/bin/env python3
"""
FAZ 6/B: Kullanıcının değiştirmesine izin verilen ayarlar — ve verilmeyenler.

Tek dosya: `settings.json` (kullanıcının config dizini, `usage/platform.py`).
Şema:
    {"thresholds": {"warn": 75, "crit": 90},
     "refreshSeconds": 30,
     "display": {"currency": "USD", "rate": null, "rateSetAtMs": null}}

**Eşik neden burada:** `thresholds` FAZ 0'dan beri `usage_calib.json` içinde yaşıyordu —
ama eşik bir ölçüm değil, bir tercih; kalibrasyonla birlikte silinmesi/taşınması yanlış.
Sahiplik buraya alındı, eski dosya **salt-okunur geri düşüş** olarak kaldı: 60/80 yazmış
kimse bu sürümde 75/90'a dönmez.

**Eşiğin bedeli sıfır:** `engine.compute_usage()` buradan okur → `/v1/usage` üst seviye
`thresholds` → panel · waybar · tray · CLI hepsi aynı çifti görür. Y6'nın karşılığı bu:
tek yerden değiştir, dört yüzey uy.

**Anahtar YAZILMAZ.** Buradaki `key_status()` yalnız *değişken adı* + set/unset döndürür,
değeri asla. Anahtar yazma işi `setup.sh --ui` içindeki geçici sunucuda kalır (rastgele
port + tek kullanımlık jeton + kendini kapatma); kalıcı panel sunucusunun üçü de yok ve
gün boyu açık duruyor.
"""
import json
import time

from . import platform as _paths

SETTINGS_PATH = _paths.config_dir() / 'settings.json'

DEFAULTS = {
    'thresholds': {'warn': 75, 'crit': 90},
    'refreshSeconds': 30,
    'display': {'currency': 'USD', 'rate': None, 'rateSetAtMs': None},
}

# Panelin 30 sn'lik yoklaması her seferinde diskteki bütün transcript'leri tarar.
# Alt sınır o taramanın maliyeti, üst sınır "bayat panel yok panelden kötüdür".
REFRESH_MIN, REFRESH_MAX = 5, 3600

# Sağlayıcı → ortam değişkeni. **Tek harita** — `doctor` ve panel aynı listeyi göstermek
# zorunda; iki kopya olsa biri diğerini yalanlardı (cli.py buradan alır).
PROVIDER_ENV = {
    'openrouter': ('OPENROUTER_API_KEY',),
    'openai': ('OPENAI_ADMIN_KEY',),
    'deepseek': ('DEEPSEEK_API_KEY',),
    'elevenlabs': ('ELEVENLABS_API_KEY',),
    'together': ('TOGETHER_API_KEY',),
    'novita': ('NOVITA_API_KEY',),
    'deepinfra': ('DEEPINFRA_API_KEY',),
    'huggingface': ('HUGGINGFACE_API_KEY', 'HF_TOKEN'),
}


def _is_number(x) -> bool:
    """bool, Python'da int'tir. `float(True)` bir kez "$1,00 kalan" diye gösterildi (FAZ 3)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _read_file() -> dict:
    try:
        raw = SETTINGS_PATH.read_text(encoding='utf-8')
    except (OSError, ValueError):
        return {}
    try:
        obj = json.loads(raw)
    except ValueError:
        return {}                      # bozuk dosya paneli düşürmez, varsayılana döner
    return obj if isinstance(obj, dict) else {}


def _legacy_thresholds():
    """FAZ 0-6 arası eşikler `usage_calib.json` içindeydi. settings.json yoksa oradan oku."""
    try:
        from . import engine
        path = _paths.pick_existing(engine.CALIB_PATH, engine.LEGACY_CALIB_PATH)
        th = json.loads(path.read_text(encoding='utf-8')).get('thresholds')
    except Exception:
        return None
    if isinstance(th, dict) and _is_number(th.get('warn')) and _is_number(th.get('crit')):
        return {'warn': th['warn'], 'crit': th['crit']}
    return None


def get_settings() -> dict:
    """Her zaman tam ve geçerli bir sözlük — çağıran tarafta savunma kodu gerekmesin."""
    stored = _read_file()
    out = {
        'thresholds': dict(DEFAULTS['thresholds']),
        'refreshSeconds': DEFAULTS['refreshSeconds'],
        'display': dict(DEFAULTS['display']),
    }

    th = stored.get('thresholds')
    if isinstance(th, dict) and _is_number(th.get('warn')) and _is_number(th.get('crit')):
        out['thresholds'] = {'warn': th['warn'], 'crit': th['crit']}
    else:
        legacy = _legacy_thresholds()
        if legacy:
            out['thresholds'] = legacy

    if _is_number(stored.get('refreshSeconds')):
        n = int(stored['refreshSeconds'])
        if REFRESH_MIN <= n <= REFRESH_MAX:
            out['refreshSeconds'] = n

    d = stored.get('display')
    if isinstance(d, dict):
        cur = d.get('currency')
        if isinstance(cur, str) and cur.strip():
            out['display']['currency'] = cur.strip().upper()
        if _is_number(d.get('rate')) and d['rate'] > 0:
            out['display']['rate'] = float(d['rate'])
        if _is_number(d.get('rateSetAtMs')):
            out['display']['rateSetAtMs'] = int(d['rateSetAtMs'])
    if out['display']['currency'] == 'USD':
        out['display']['rate'] = None
        out['display']['rateSetAtMs'] = None
    return out


def thresholds() -> dict:
    """engine bunu çağırır — eşiğin tek okuma yolu."""
    return get_settings()['thresholds']


def _validate(patch: dict, current: dict, now_ms: int):
    """(yeni_ayarlar, hata) — hata varsa hiçbir şey yazılmaz."""
    if not isinstance(patch, dict):
        return None, 'body must be an object'

    unknown = set(patch) - set(DEFAULTS)
    if unknown:
        # 'keys' özel olarak adlandırılır: "henüz yok" değil, **bilinçli yok**.
        if 'keys' in unknown:
            return None, ('this server never writes API keys — use the setup wizard '
                          '(./setup.sh --ui, step "keys")')
        return None, f'unknown setting(s): {", ".join(sorted(unknown))}'

    out = json.loads(json.dumps(current))    # derin kopya; kısmi yama diğerlerine dokunmaz

    if 'thresholds' in patch:
        th = patch['thresholds']
        if not isinstance(th, dict):
            return None, 'thresholds must be an object'
        w, c = th.get('warn'), th.get('crit')
        if not (_is_number(w) and _is_number(c)):
            return None, 'warn and crit must be numbers'
        if not (0 < w < c <= 100):
            return None, 'thresholds must satisfy 0 < warn < crit <= 100'
        out['thresholds'] = {'warn': w, 'crit': c}

    if 'refreshSeconds' in patch:
        n = patch['refreshSeconds']
        if not _is_number(n) or int(n) != n:
            return None, 'refreshSeconds must be a whole number'
        if not (REFRESH_MIN <= n <= REFRESH_MAX):
            return None, f'refreshSeconds must be between {REFRESH_MIN} and {REFRESH_MAX}'
        out['refreshSeconds'] = int(n)

    if 'display' in patch:
        d = patch['display']
        if not isinstance(d, dict):
            return None, 'display must be an object'
        cur = d.get('currency', out['display']['currency'])
        if not isinstance(cur, str) or not cur.strip().isalpha() or len(cur.strip()) != 3:
            return None, 'currency must be a three-letter code'
        cur = cur.strip().upper()
        if cur == 'USD':
            # Dönüşüm yok → kur da yok. Kalan bayat kur bir sonraki yüzeyde kullanılırdı.
            out['display'] = {'currency': 'USD', 'rate': None, 'rateSetAtMs': None}
        else:
            rate = d.get('rate')
            if not _is_number(rate) or rate <= 0:
                return None, f'a positive rate is required to display {cur} (1 USD = ? {cur})'
            # Tarihi **sunucu** basar: kurun yaşı ekranda gösterilecek, istemci onu genç
            # gösterebilseydi gösterge anlamını yitirirdi.
            out['display'] = {'currency': cur, 'rate': float(rate), 'rateSetAtMs': now_ms}

    return out, None


def save_settings(patch: dict, now_ms: int = None):
    """Doğrula, sonra atomik yaz. Reddedilen yazma **hiçbir dosyaya dokunmaz**."""
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    new, err = _validate(patch, get_settings(), now_ms)
    if err:
        return False, err
    try:
        _paths.atomic_write_text(SETTINGS_PATH, json.dumps(new, ensure_ascii=False, indent=2))
    except Exception:
        return False, 'could not write the settings file'
    return True, None


def convert(usd, display: dict = None):
    """USD → görüntüleme para birimi. Kur yoksa **None** — tahmin yok.

    USD için de None döner: dolar zaten gerçek sayı, ikinci kez göstermek çift gösterimdir.
    """
    d = display if isinstance(display, dict) else get_settings()['display']
    rate = d.get('rate')
    if (d.get('currency') or 'USD').upper() == 'USD' or not _is_number(rate) or rate <= 0:
        return None
    if not _is_number(usd):
        return None
    return round(usd * rate, 2)


def key_status() -> list:
    """[{provider, env, set}] — **yalnız ad ve durum.** Değer asla dönmez: bu çıktı
    herkese açık hata raporuna yapıştırılıyor (`doctor` ile aynı kural)."""
    import os
    out = []
    for provider, names in sorted(PROVIDER_ENV.items()):
        chosen = next((n for n in names if os.environ.get(n)), names[0])
        out.append({'provider': provider, 'env': chosen,
                    'set': bool(os.environ.get(chosen))})
    return out

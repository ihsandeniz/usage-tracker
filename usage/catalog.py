#!/usr/bin/env python3
"""
Fiyat kataloğu kaynağı — models.dev fiyatlarını nereden alacağımıza karar veren tek yer.

Neden ayrı bir modül: 2026-08-11'e kadar fiyatlar YALNIZ `~/.hermes/models_dev_cache.json`
dosyasından okunuyordu. O dosyayı **başka bir ürün** (Hermes CLI) üretiyor. Bu makinede ölçüldü:
dosya varken 2731 model / 178 sağlayıcı, yokken **0 / 0** — ve hata çıplak bir `except Exception`
tarafından yutuluyordu. Yani Hermes kurulu olmayan her kullanıcıda, ki bu neredeyse herkes,
ürünün çekirdek vaadi ("ne harcadığımı göster") sessizce $0,00 döndürüyordu.

Kaynak sırası (ilk bulunan kazanır):
  1. kullanıcı cache'i  — `catalog.py --update` ile models.dev'den çekilir, EN TAZE
  2. Hermes cache'i     — kuruluysa fırsatçı olarak kullanılır (günlük tazeleniyor)
  3. gömülü snapshot    — `data/models_dev_prices.json.gz`, depoyla birlikte gelir

Üçü de yoksa katalog boştur — ama artık **sessiz değildir**: `status()` bunu söyler,
sunucu da yüzeylere taşır.

Stdlib-only. Ağ yalnız açıkça `--update` çağrılırsa kullanılır.
"""
import gzip
import json
import os
import sys
import threading
from datetime import date, datetime
from pathlib import Path

from . import platform as _paths

# Salt-okunur paket verisi — donmuş pakette sys._MEIPASS altında
BUNDLED_PATH = _paths.resource_dir() / 'data' / 'models_dev_prices.json.gz'
HERMES_CACHE = Path.home() / '.hermes' / 'models_dev_cache.json'
MODELS_DEV_URL = 'https://models.dev/api.json'

# Bir katalog kaç gün sonra "bayat" sayılır. models.dev'de fiyatlar ayda birkaç kez değişiyor;
# 45 gün, "muhtemelen hâlâ doğru" ile "artık güvenme" arasındaki makul çizgi.
STALE_AFTER_DAYS = 45

_COST_FIELDS = ('input', 'output', 'cache_read', 'cache_write')

_USER_CACHE_OVERRIDE = None        # testler ve USAGE_PRICES için; None = normal yol
_LOCK = threading.Lock()
_CACHE = None                      # (key, loaded_dict)


def user_cache_path() -> Path:
    """Kendi çektiğimiz katalogun yeri. Yol çözümlemesi `usage/platform.py`'de."""
    if _USER_CACHE_OVERRIDE is not None:
        return Path(_USER_CACHE_OVERRIDE)
    env = os.environ.get('USAGE_PRICES')
    if env:
        return Path(env).expanduser()
    from . import platform as _paths
    return _paths.cache_dir() / 'models_dev_prices.json'


def invalidate() -> None:
    """Cache'i düşür. Testler yolları değiştirdikten sonra çağırır."""
    global _CACHE
    with _LOCK:
        _CACHE = None


# ── okuyucular: hepsi (providers, meta) döndürür ─────────────────────────────
def _trim_cost(cost) -> dict:
    if not isinstance(cost, dict):
        return {}
    out = {}
    for k in _COST_FIELDS:
        v = cost.get(k)
        if v is None:
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _from_models_dev_shape(raw) -> dict:
    """models.dev / Hermes ham biçimi → {provider: {model_id: {cost alanları}}}"""
    providers = {}
    if not isinstance(raw, dict):
        return providers
    for prov, pv in raw.items():
        models = (pv or {}).get('models') if isinstance(pv, dict) else None
        if not isinstance(models, dict):
            continue
        for mid, mv in models.items():
            if not isinstance(mv, dict):
                continue
            cost = _trim_cost(mv.get('cost'))
            if cost:
                providers.setdefault(prov, {})[mid.strip().lower()] = cost
    return providers


def _from_own_shape(payload) -> tuple:
    """Bizim kırpılmış biçimimiz: {'_meta': {...}, 'providers': {prov: {mid: cost}}}"""
    if not isinstance(payload, dict):
        return {}, {}
    providers = payload.get('providers')
    if not isinstance(providers, dict):
        return {}, {}
    clean = {}
    for prov, models in providers.items():
        if not isinstance(models, dict):
            continue
        for mid, cost in models.items():
            c = _trim_cost(cost)
            if c:
                clean.setdefault(prov, {})[str(mid).strip().lower()] = c
    meta = payload.get('_meta') if isinstance(payload.get('_meta'), dict) else {}
    return clean, dict(meta)


def _read_bundled() -> tuple:
    try:
        with gzip.open(BUNDLED_PATH, 'rt', encoding='utf-8') as fh:
            payload = json.load(fh)
    except (OSError, ValueError, EOFError):
        return {}, {}
    return _from_own_shape(payload)


def _read_user() -> tuple:
    p = user_cache_path()
    try:
        text = p.read_text(encoding='utf-8')
    except OSError:
        return {}, {}
    try:
        payload = json.loads(text)
    except ValueError:
        print(f'warning: {p} bozuk JSON — yok sayılıyor', file=sys.stderr)
        return {}, {}
    providers, meta = _from_own_shape(payload)
    meta.setdefault('generatedAt', _mtime_date(p))
    return providers, meta


def _read_hermes() -> tuple:
    try:
        raw = json.loads(HERMES_CACHE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}, {}
    providers = _from_models_dev_shape(raw)
    if not providers:
        return {}, {}
    return providers, {'generatedAt': _mtime_date(HERMES_CACHE),
                       'source': 'hermes-cli (~/.hermes/models_dev_cache.json)'}


def _mtime_date(p: Path):
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
    except OSError:
        return None


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


# ── yükleme + durum ──────────────────────────────────────────────────────────
_SOURCES = (
    ('user', _read_user),
    ('hermes', _read_hermes),
    ('bundled', _read_bundled),
)


def load() -> dict:
    """{'providers', 'meta', 'source'} döndür. Sonuç mtime'lara göre cache'lenir."""
    global _CACHE
    key = (_mtime(user_cache_path()), _mtime(HERMES_CACHE), _mtime(BUNDLED_PATH))
    with _LOCK:
        if _CACHE and _CACHE[0] == key:
            return _CACHE[1]
    loaded = {'providers': {}, 'meta': {}, 'source': 'none'}
    for name, reader in _SOURCES:
        providers, meta = reader()
        if providers:
            loaded = {'providers': providers, 'meta': meta, 'source': name}
            break
    with _LOCK:
        _CACHE = (key, loaded)
    return loaded


def _age_days(generated_at):
    if not generated_at:
        return None
    try:
        return (date.today() - date.fromisoformat(str(generated_at)[:10])).days
    except ValueError:
        return None


_SOURCE_LABEL = {
    'user': 'kendi çektiğimiz katalog',
    'hermes': 'Hermes CLI cache',
    'bundled': 'pakete gömülü snapshot',
    'none': 'yok',
}


def status() -> dict:
    """Fiyatların nereden geldiği + güvenilir olup olmadığı. Yüzeylere bu taşınır.

    Sessiz $0 yasak: katalog boşsa ya da bayatsa `warning` dolu bir cümle döndürür.
    """
    loaded = load()
    providers = loaded['providers']
    src = loaded['source']
    generated_at = loaded['meta'].get('generatedAt')
    age = _age_days(generated_at)
    model_count = sum(len(m) for m in providers.values())
    stale = bool(age is not None and age > STALE_AFTER_DAYS)

    warning = None
    if src == 'none' or model_count == 0:
        warning = ('Fiyat kataloğu yüklenemedi — gösterilen $ tutarları eksik olabilir. '
                   '`python3 -m usage.catalog --update` ile tazeleyin.')
    elif stale:
        warning = (f'Fiyat kataloğu {age} günlük ({_SOURCE_LABEL[src]}) — fiyatlar değişmiş '
                   f'olabilir. `python3 -m usage.catalog --update` ile tazeleyin.')

    return {
        'source': src,
        'sourceLabel': _SOURCE_LABEL[src],
        'path': str({'user': user_cache_path(), 'hermes': HERMES_CACHE,
                     'bundled': BUNDLED_PATH}.get(src, '')),
        'generatedAt': generated_at,
        'ageDays': age,
        'stale': stale,
        'modelCount': model_count,
        'providerCount': len(providers),
        'warning': warning,
    }


# ── tazeleme / paket üretimi (ağ — yalnız açıkça çağrılınca) ─────────────────
def _fetch(url: str) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': 'usage-tracker'})
    with urllib.request.urlopen(req, timeout=60) as resp:      # noqa: S310 (sabit https URL)
        return json.loads(resp.read().decode('utf-8'))


def _payload_from_remote(url: str) -> dict:
    providers = _from_models_dev_shape(_fetch(url))
    if not providers:
        raise RuntimeError(f'{url} beklenen biçimi döndürmedi (fiyatlı model bulunamadı)')
    return {'_meta': {'generatedAt': date.today().isoformat(), 'source': url},
            'providers': providers}


def update(url: str = MODELS_DEV_URL) -> dict:
    """models.dev'den çek, kullanıcı cache'ine yaz. Döner: status()."""
    payload = _payload_from_remote(url)
    from . import platform as _paths
    dest = _paths.atomic_write_text(
        user_cache_path(), json.dumps(payload, separators=(',', ':'), sort_keys=True))
    invalidate()
    return status()


def build_bundle(url: str = MODELS_DEV_URL, dest: Path = None) -> Path:
    """Depoyla birlikte gidecek gömülü snapshot'ı yeniden üret. Sürüm öncesi çalıştırılır."""
    payload = _payload_from_remote(url)
    dest = Path(dest) if dest else BUNDLED_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f'.{os.getpid()}.tmp')
    with gzip.open(tmp, 'wt', encoding='utf-8') as fh:
        json.dump(payload, fh, separators=(',', ':'), sort_keys=True)
    tmp.replace(dest)
    invalidate()
    return dest


def _main(argv) -> int:
    if '--update' in argv:
        st = update()
        print(f"güncellendi → {st['path']}\n"
              f"{st['modelCount']} model / {st['providerCount']} sağlayıcı · {st['generatedAt']}")
        return 0
    if '--build-bundle' in argv:
        dest = build_bundle()
        size_kb = dest.stat().st_size / 1024
        st = status()
        print(f'gömülü snapshot yazıldı → {dest} ({size_kb:.1f} KB)\n'
              f"{st['modelCount']} model / {st['providerCount']} sağlayıcı")
        return 0
    st = status()
    for k in ('source', 'sourceLabel', 'path', 'generatedAt', 'ageDays', 'stale',
              'modelCount', 'providerCount'):
        print(f'{k:15s} {st[k]}')
    if st['warning']:
        print(f"\n⚠️  {st['warning']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(_main(sys.argv[1:]))

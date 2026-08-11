#!/usr/bin/env python3
"""
Fiyat motoru — models.dev fiyat kataloğundan gerçek $ maliyet.
  Kaynak: `usage/catalog.py` karar verir (kullanıcı cache → Hermes → gömülü snapshot).
  Fiyat birimi: USD / 1M token (input · output · cache_read · cache_write)

Kataloğda OLMAYAN modeller (fable-5, sonnet-5 gibi yeni sürümler) için
proje kökündeki price_overrides.json kullanılır ve source='estimate' işaretlenir.
Böylece hiçbir maliyet sessizce uydurulmaz — tahmin olan tahmin olarak görünür.

Bir override'ın **son kullanma tarihi** olabilir:
    "claude-sonnet-5": { ...tanıtım fiyatı..., "until": "2026-08-31",
                         "then": { ...liste fiyatı... } }
`until` geçince `then` kendiliğinden devreye girer. Eskiden bu bilgi serbest metin bir
`_note` alanındaydı ve birinin tarihi hatırlayıp dosyayı elle düzenlemesi gerekiyordu.

Stdlib-only. Dosya-mtime cache'li (katalog kaynağı değişince otomatik yeniden yükler).
"""
import json
import sys
import threading
from datetime import date
from pathlib import Path

from . import catalog
from . import platform as _paths

# Salt-okunur paket verisi: donmuş pakette sys._MEIPASS altındadır (usage/platform.py)
OVERRIDES_PATH = _paths.resource_dir() / 'price_overrides.json'

ZERO = {'input': 0.0, 'output': 0.0, 'cache_read': 0.0, 'cache_write': 0.0}

_LOCK = threading.Lock()
_CATALOG_CACHE = None          # (key, catalog_dict)


def _norm(model: str) -> str:
    """Model string'i normalize et: küçük harf, kenar boşluk, '[1m]' gibi ekleri at."""
    m = (model or '').strip().lower()
    if '[' in m:                       # 'claude-opus-4-8[1m]' -> 'claude-opus-4-8'
        m = m.split('[', 1)[0]
    return m


def _tier(norm: str) -> str:
    for t in ('opus', 'sonnet', 'haiku', 'fable'):
        if t in norm:
            return t
    return ''


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _clean_cost(cost: dict) -> dict:
    """models.dev cost sözlüğünü 4 alana indir; eksikleri türet."""
    if not isinstance(cost, dict):
        return dict(ZERO)
    inp = float(cost.get('input', 0) or 0)
    out = float(cost.get('output', 0) or 0)
    cr = cost.get('cache_read')
    cw = cost.get('cache_write')
    # Bazı modellerde cache fiyatı yok — Anthropic oranıyla türet (read≈0.1×input, write≈1.25×input)
    cr = float(cr) if cr is not None else round(inp * 0.1, 4)
    cw = float(cw) if cw is not None else round(inp * 1.25, 4)
    return {'input': inp, 'output': out, 'cache_read': cr, 'cache_write': cw}


def _build_catalog() -> dict:
    """{'anthropic', 'all', 'by_provider', 'overrides', 'override_src',
        'override_sched', 'tier'} döndür."""
    anthropic, allm, overrides, override_src = {}, {}, {}, {}
    override_sched = {}       # {model: (until_iso, then_cost, then_source)}
    by_provider = {}          # {provider: {modelid: cost}} — native fiyat (reseller markup'sız)

    # 1) fiyat kataloğu — kaynağa catalog.py karar verir (kullanıcı/Hermes/gömülü)
    for prov, models in catalog.load()['providers'].items():
        for key, raw_cost in models.items():
            cost = _clean_cost(raw_cost)
            by_provider.setdefault(prov, {}).setdefault(key, cost)
            # anthropic-native fiyatları ayrı tut (openrouter vb. markup'tan kaçın)
            if prov == 'anthropic':
                anthropic[key] = cost
            # ilk gelen kazanır (aynı id farklı provider'da tekrar edebilir)
            allm.setdefault(key, cost)

    # 2) kullanıcı override'ları (kataloğda olmayan yeni modeller)
    try:
        od = json.loads(OVERRIDES_PATH.read_text(encoding='utf-8'))
        if isinstance(od, dict):
            for k, v in od.items():
                if k.startswith('_'):        # _note gibi meta alanları atla
                    continue
                if isinstance(v, dict):
                    key = k.strip().lower()
                    overrides[key] = _clean_cost(v)
                    # source: 'official' (doğrulanmış → gerçek sayılır) | 'estimate' (tahmin)
                    override_src[key] = v.get('source', 'estimate')
                    # tarih kontrollü fiyat: 'until' geçince 'then' devreye girer
                    then, until = v.get('then'), v.get('until')
                    if isinstance(then, dict) and isinstance(until, str):
                        override_sched[key] = (until.strip()[:10], _clean_cost(then),
                                               then.get('source', override_src[key]))
    except FileNotFoundError:
        # Dosya yoksa OK — override yok
        pass
    except OSError as e:
        print(f'warning: price_overrides.json okunamadı: {e}', file=sys.stderr)
    except json.JSONDecodeError as e:
        # JSON syntax hatası — uyarı ver ama devam et
        print(f'warning: price_overrides.json parse hatası: {e}', file=sys.stderr)

    # 3) tier temsilcileri (kısa ad / bilinmeyen sürüm fallback'i)
    def pick(*keys, default=None):
        for k in keys:
            if k in anthropic:
                return anthropic[k]
            if k in overrides:
                return overrides[k]
        return default
    tier = {
        'opus':   pick('claude-opus-4-8', 'claude-opus-4-6', 'claude-opus-4-5', default=dict(ZERO)),
        'sonnet': pick('claude-sonnet-4-6', 'claude-sonnet-4-5', 'claude-sonnet-5', default=dict(ZERO)),
        'haiku':  pick('claude-haiku-4-5', 'claude-haiku-4-5-20251001', default=dict(ZERO)),
        'fable':  pick('claude-fable-5', default=dict(ZERO)),
    }
    return {'anthropic': anthropic, 'all': allm, 'by_provider': by_provider,
            'overrides': overrides, 'override_src': override_src,
            'override_sched': override_sched, 'tier': tier}


def _cache_key():
    src = catalog.load()
    return (src['source'], src['meta'].get('generatedAt'),
            sum(len(m) for m in src['providers'].values()), _mtime(OVERRIDES_PATH))


def invalidate() -> None:
    """Cache'i düşür. Testler yolları değiştirdikten sonra çağırır."""
    global _CATALOG_CACHE
    with _LOCK:
        _CATALOG_CACHE = None
    catalog.invalidate()


def catalog_status() -> dict:
    """Fiyatların hangi kaynaktan geldiği + uyarı. Sunucu bunu wire'a taşır."""
    return catalog.status()


def _catalog() -> dict:
    global _CATALOG_CACHE
    key = _cache_key()
    with _LOCK:
        if _CATALOG_CACHE and _CATALOG_CACHE[0] == key:
            return _CATALOG_CACHE[1]
        cat = _build_catalog()
        _CATALOG_CACHE = (key, cat)
        return cat


def resolve_price(model: str, today: date = None):
    """(cost_dict, source) döndür. source ∈ {'catalog','official','estimate','free','unknown'}.

    `today` yalnız tarih kontrollü override'lar için — testler sabitler, üretimde bugündür.
    """
    norm = _norm(model)
    if norm in ('', '<synthetic>', 'synthetic'):
        return dict(ZERO), 'free'
    cat = _catalog()
    if norm in cat['overrides']:                       # override: source 'official' | 'estimate'
        sched = cat['override_sched'].get(norm)
        if sched:
            until, then_cost, then_src = sched
            # 'until' dahildir: o günün sonuna kadar eski fiyat geçerli
            if (today or date.today()).isoformat() > until:
                return then_cost, then_src
        return cat['overrides'][norm], cat['override_src'].get(norm, 'estimate')
    if norm in cat['anthropic']:
        return cat['anthropic'][norm], 'catalog'
    if norm in cat['all']:
        return cat['all'][norm], 'catalog'
    tier = _tier(norm)                                 # 'opus'/'sonnet'/'haiku'/'fable' kısa ad
    if tier:
        price = cat['tier'].get(tier)
        if price and any(price.values()):
            return price, 'estimate'
    return dict(ZERO), 'unknown'


def turn_cost_usd(inp: int, out: int, cc: int, cr: int, model: str):
    """Bir turn'ün gerçek $ maliyeti + fiyat kaynağı. Anthropic faturalama modeli:
       input_tokens=cache'siz girdi · cache_creation=cache_write · cache_read=cache_read · output=output."""
    price, source = resolve_price(model)
    usd = (
        inp * price['input'] +
        out * price['output'] +
        cc * price['cache_write'] +
        cr * price['cache_read']
    ) / 1_000_000.0
    return usd, source


# ── generic (Claude-dışı sağlayıcılar için) ──────────────────────────────────
def resolve_provider_price(model: str, provider: str, fallbacks=()):
    """Bir sağlayıcının native fiyatını çöz. (cost, source) döndür.
       source ∈ {'catalog','estimate','unknown'}. fallbacks: model bulunamazsa
       denenecek temsili model id'leri (source='estimate')."""
    cat = _catalog()
    prov_map = cat.get('by_provider', {}).get(provider, {})
    norm = _norm(model)
    if norm and norm in prov_map:
        return prov_map[norm], 'catalog'
    if norm and norm in cat['all']:
        return cat['all'][norm], 'catalog'
    for fb in fallbacks:
        fbn = _norm(fb)
        if fbn in prov_map:
            return prov_map[fbn], 'estimate'
        if fbn in cat['all']:
            return cat['all'][fbn], 'estimate'
    return dict(ZERO), 'unknown'


# Codex modelleri (gpt-5.x-codex / gpt-5.x). Bulunamazsa makul temsilciye düş.
_CODEX_FALLBACKS = ('gpt-5.2-codex', 'gpt-5.1-codex', 'gpt-5-codex', 'gpt-5.2', 'gpt-5-mini')


def codex_cost_usd(inp: int, cached_in: int, out: int, model: str):
    """Codex turn'ünün API-eşdeğeri $ maliyeti. OpenAI faturalama:
       cached_input, input_tokens'ın alt kümesidir → cache'siz = input - cached.
       output_tokens reasoning'i zaten içerir (tekrar sayma)."""
    price, source = resolve_provider_price(model, 'openai', _CODEX_FALLBACKS)
    uncached = max(0, (inp or 0) - (cached_in or 0))
    usd = (
        uncached * price['input'] +
        (cached_in or 0) * price['cache_read'] +
        (out or 0) * price['output']
    ) / 1_000_000.0
    return usd, source


if __name__ == '__main__':
    # elle doğrulama: python3 -m usage.pricing
    for m in ('claude-opus-4-8', 'claude-haiku-4-5-20251001', 'claude-sonnet-4-6',
              'claude-fable-5', 'claude-sonnet-5', 'opus', '<synthetic>', 'garbage-x'):
        price, src = resolve_price(m)
        print(f'{m:32s} [{src:8s}] {price}')
    print('--- codex ---')
    for m in ('gpt-5.5', 'gpt-5.2-codex', 'gpt-5-codex', 'bilinmeyen-x'):
        price, src = resolve_provider_price(m, 'openai', _CODEX_FALLBACKS)
        print(f'{m:32s} [{src:8s}] in={price["input"]} out={price["output"]} cr={price["cache_read"]}')

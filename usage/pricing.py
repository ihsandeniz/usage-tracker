#!/usr/bin/env python3
"""
Fiyat motoru — models.dev fiyat kataloğundan gerçek $ maliyet.
  Kaynak: ~/.hermes/models_dev_cache.json  (Hermes CLI'ın günlük tazelediği 137-provider cache)
  Fiyat birimi: USD / 1M token (input · output · cache_read · cache_write)

Kataloğda OLMAYAN modeller (fable-5, sonnet-5 gibi yeni sürümler) için
proje kökündeki price_overrides.json kullanılır ve source='estimate' işaretlenir.
Böylece hiçbir maliyet sessizce uydurulmaz — tahmin olan tahmin olarak görünür.

Stdlib-only. Dosya-mtime cache'li (Hermes cache değişince otomatik yeniden yükler).
"""
import json
import sys
import threading
from pathlib import Path

HERMES_CACHE = Path.home() / '.hermes' / 'models_dev_cache.json'
OVERRIDES_PATH = Path(__file__).resolve().parent.parent / 'price_overrides.json'

ZERO = {'input': 0.0, 'output': 0.0, 'cache_read': 0.0, 'cache_write': 0.0}

_LOCK = threading.Lock()
_CATALOG_CACHE = None          # (hermes_mtime, overrides_mtime, catalog_dict)


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
    """{'anthropic', 'all', 'by_provider', 'overrides', 'override_src', 'tier'} döndür."""
    anthropic, allm, overrides, override_src = {}, {}, {}, {}
    by_provider = {}          # {provider: {modelid: cost}} — native fiyat (reseller markup'sız)

    # 1) models.dev kataloğu
    try:
        raw = json.loads(HERMES_CACHE.read_text(encoding='utf-8'))
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for prov, pv in raw.items():
            models = (pv or {}).get('models') if isinstance(pv, dict) else None
            if not isinstance(models, dict):
                continue
            for mid, mv in models.items():
                if not isinstance(mv, dict):
                    continue
                cost = _clean_cost(mv.get('cost'))
                key = mid.strip().lower()
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
    except FileNotFoundError:
        # Dosya yoksa OK — override yok
        pass
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
            'overrides': overrides, 'override_src': override_src, 'tier': tier}


def _catalog() -> dict:
    global _CATALOG_CACHE
    hm, om = _mtime(HERMES_CACHE), _mtime(OVERRIDES_PATH)
    with _LOCK:
        if _CATALOG_CACHE and _CATALOG_CACHE[0] == hm and _CATALOG_CACHE[1] == om:
            return _CATALOG_CACHE[2]
        cat = _build_catalog()
        _CATALOG_CACHE = (hm, om, cat)
        return cat


def resolve_price(model: str):
    """(cost_dict, source) döndür. source ∈ {'catalog','estimate','free','unknown'}."""
    norm = _norm(model)
    if norm in ('', '<synthetic>', 'synthetic'):
        return dict(ZERO), 'free'
    cat = _catalog()
    if norm in cat['overrides']:                       # override: source 'official' | 'estimate'
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

#!/usr/bin/env python3
"""
FAZ 4a: Veri Seçim Katmanı — Kullanıcı hangi sağlayıcıların panelde/waybar'da/widget'ta
görüneceğini seçebilsin.

Backend TEK KAYNAK: `view_config.json` — kullanıcının config dizininde
(usage/platform.py). Eski sürümlerin kod yanına yazdığı dosya da okunur.
Şema: {"hidden_providers": ["provider_id", ...]}
  — Liste gizlenen sağlayıcıları tutar
  — Varsayılan: boş liste = hepsi görünür
  — Bu yaklaşım: yeni sağlayıcı otomatik görünür, güvenli

`/v1/usage` çıktısı `filter_wire()` ile süzülür → waybar/panel/widget otomatik uyar.
`/api/view-config` endpoint'leri: GET (config + available listesi), POST (kaydet).

stdlib-only, atomik yazma (tmp+rename), path-safe.
"""
import json
from pathlib import Path

from . import platform as _paths

# Kullanıcının bilerek yaptığı seçim (hangi kartlar gizli) → config dizini.
# Eski sürümlerin kodun yanına yazdığı dosya, yenisi oluşana kadar okunur.
CONFIG_PATH = _paths.config_dir() / 'view_config.json'
LEGACY_CONFIG_PATH = Path(__file__).resolve().parent.parent / 'view_config.json'

# Gizlenemeyen kart. `docs/WIRE.md:37` "providers[0] is always the Claude card" diye söz
# veriyor ve bu tur o wire'ı PUBLIC ilan etti; waybar rozeti, tepsi ikonu ve `guard` hepsi
# o konuma bakıyor. 2026-08-12 ölçümü: `config --hide claude` kabul edilince `/v1/usage`
# başında `ollama` kaldı, rozet boşaldı, `guard` 3 döndü — kullanıcı bir *görünüm* tercihi
# yaparken uyarı hattını kapatmış oldu, üstelik hiçbir uyarı almadan.
PROTECTED_PROVIDERS = ('claude',)


def validate_config(cfg) -> tuple:
    """(ok, hata) — reddedilen yazma hiçbir dosyaya dokunmaz (FAZ 6/B kuralı)."""
    if not isinstance(cfg, dict):
        return False, 'config must be an object'
    hidden = cfg.get('hidden_providers')
    if not isinstance(hidden, list):
        return False, 'hidden_providers must be a list'
    if not all(isinstance(x, str) for x in hidden):
        return False, 'hidden_providers must contain only strings'
    blocked = [p for p in hidden if p in PROTECTED_PROVIDERS]
    if blocked:
        return False, (f'{", ".join(blocked)} cannot be hidden: /v1/usage guarantees '
                       f'providers[0] is the Claude card, and the waybar badge, the tray '
                       f'and `guard` all read it')
    return True, None


def get_config() -> dict:
    """
    view_config.json'u oku. Yoksa varsayılan (boş hidden listesi) dön.

    Returns:
        dict: {"hidden_providers": [...]} — her zaman valid dict
    """
    path = _paths.pick_existing(CONFIG_PATH, LEGACY_CONFIG_PATH)
    if not path.exists():
        return {"hidden_providers": []}
    try:
        content = path.read_text(encoding='utf-8')
        cfg = json.loads(content)
        if not isinstance(cfg, dict):
            return {"hidden_providers": []}
        # Güvenli: hidden_providers listesi değilse boşla
        if not isinstance(cfg.get("hidden_providers"), list):
            cfg["hidden_providers"] = []
        # Yazma yolu artık reddediyor ama diskte eski bir sürümün yazdığı dosya olabilir.
        # Okurken de süzmek, o dosyanın wire sözleşmesini kıramayacağı anlamına gelir.
        cfg["hidden_providers"] = [p for p in cfg["hidden_providers"]
                                   if p not in PROTECTED_PROVIDERS]
        return cfg
    except Exception:
        return {"hidden_providers": []}


def save_config(cfg: dict) -> bool:
    """
    Config'i atomik kaydet (tmp+rename). Yarım yazım riski yok.

    Args:
        cfg: {"hidden_providers": [...]}} — list-of-string doğrulama expected

    Returns:
        bool: Başarılı True, hata False (gerekçe için `validate_config`)
    """
    ok, _err = validate_config(cfg)
    if not ok:
        return False

    try:
        # Yazma her zaman yeni konuma — göç ilk kaydetmede kendiliğinden tamamlanır.
        _paths.atomic_write_text(CONFIG_PATH, json.dumps(cfg, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def is_visible(provider_id: str) -> bool:
    """
    Sağlayıcı panelde görünür mü?

    Args:
        provider_id: sağlayıcı ID'si (e.g. "claude", "openrouter", "codex", "ollama")

    Returns:
        bool: True ise göster, False ise gizle
    """
    cfg = get_config()
    hidden = cfg.get("hidden_providers", [])
    return provider_id not in hidden


def filter_wire(wire: dict) -> dict:
    """
    usage_wire() çıktısındaki providers[] listesini gizlenmeyenlere göre süz.

    Args:
        wire: engine.usage_wire() tam çıktısı

    Returns:
        dict: Orijinal copy ama providers[] süzülmüş (hidden_providers dışındakiler)
    """
    if not isinstance(wire, dict):
        return wire

    cfg = get_config()
    hidden = set(cfg.get("hidden_providers", [])) - set(PROTECTED_PROVIDERS)

    # Shallow copy — providers listesini sadece süz
    out = dict(wire)
    if "providers" in wire and isinstance(wire["providers"], list):
        out["providers"] = [p for p in wire["providers"] if not (isinstance(p, dict) and p.get("id") in hidden)]

    return out

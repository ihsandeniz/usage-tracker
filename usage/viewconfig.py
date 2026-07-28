#!/usr/bin/env python3
"""
FAZ 4a: Veri Seçim Katmanı — Kullanıcı hangi sağlayıcıların panelde/waybar'da/widget'ta
görüneceğini seçebilsin.

Backend TEK KAYNAK: `view_config.json` dosyası (proje-yerel, gitignore'da).
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

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'view_config.json'


def get_config() -> dict:
    """
    view_config.json'u oku. Yoksa varsayılan (boş hidden listesi) dön.

    Returns:
        dict: {"hidden_providers": [...]} — her zaman valid dict
    """
    if not CONFIG_PATH.exists():
        return {"hidden_providers": []}
    try:
        content = CONFIG_PATH.read_text(encoding='utf-8')
        cfg = json.loads(content)
        if not isinstance(cfg, dict):
            return {"hidden_providers": []}
        # Güvenli: hidden_providers listesi değilse boşla
        if not isinstance(cfg.get("hidden_providers"), list):
            cfg["hidden_providers"] = []
        return cfg
    except Exception:
        return {"hidden_providers": []}


def save_config(cfg: dict) -> bool:
    """
    Config'i atomik kaydet (tmp+rename). Yarım yazım riski yok.

    Args:
        cfg: {"hidden_providers": [...]}} — list-of-string doğrulama expected

    Returns:
        bool: Başarılı True, hata False
    """
    if not isinstance(cfg, dict):
        return False
    if not isinstance(cfg.get("hidden_providers"), list):
        return False

    try:
        tmp = CONFIG_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(CONFIG_PATH)
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
    hidden = set(cfg.get("hidden_providers", []))

    # Shallow copy — providers listesini sadece süz
    out = dict(wire)
    if "providers" in wire and isinstance(wire["providers"], list):
        out["providers"] = [p for p in wire["providers"] if not (isinstance(p, dict) and p.get("id") in hidden)]

    return out

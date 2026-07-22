#!/usr/bin/env python3
"""
Cody (Sourcegraph) adaptörü — token (lokal-log, tespit-tabanlı). (FAZ 4d — ADAY)

⚠️ Cody'nin temiz bir yerel token log formatı belgelenmemiş. Tespit-tabanlı:
veri dizini varsa hafif jsonl taraması (bilinen token alanları); bulunamazsa 'nodata'.
  Aday dizinler: ~/.cody · ~/.config/Code/User/globalStorage/sourcegraph.cody-ai

Hiçbir dizin yoksa → None (kart açılmaz).
"""
import json
from pathlib import Path

from . import _genlog

PROVIDER_ID = 'cody'
PROVIDER_NAME = 'Cody'
_DIRS = [
    Path.home() / '.cody',
    Path.home() / '.config' / 'Code' / 'User' / 'globalStorage' / 'sourcegraph.cody-ai',
]


def _configured():
    return any(d.exists() for d in _DIRS)


def collect(days: int = 30) -> dict:
    if not _configured():
        return None
    scanned = _genlog.scan([d for d in _DIRS if d.exists()])
    if scanned['found']:
        return _genlog.tokens_card(PROVIDER_ID, PROVIDER_NAME, scanned,
                                   'Cody yerel loglarından (tespit-tabanlı; $ yok). Format ADAY.')
    return _genlog.nodata_card(PROVIDER_ID, PROVIDER_NAME,
                               'Cody kurulu (tespit edildi) ama okunur token logu yok/format doğrulanacak.')


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

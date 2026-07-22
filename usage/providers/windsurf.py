#!/usr/bin/env python3
"""
Windsurf (Codeium) adaptörü — token (lokal-log, tespit-tabanlı). (FAZ 4d — ADAY)

⚠️ Codeium/Windsurf yerel token log formatı belgelenmemiş. Tespit-tabanlı:
veri dizini varsa hafif jsonl taraması; bulunamazsa 'nodata'.
  Aday dizinler: ~/.codeium/windsurf · ~/.codeium

Hiçbir dizin yoksa → None (kart açılmaz).
"""
import json
from pathlib import Path

from . import _genlog

PROVIDER_ID = 'windsurf'
PROVIDER_NAME = 'Windsurf'
_DIRS = [
    Path.home() / '.codeium' / 'windsurf',
    Path.home() / '.codeium',
]


def _configured():
    return any(d.exists() for d in _DIRS)


def collect(days: int = 30) -> dict:
    if not _configured():
        return None
    scanned = _genlog.scan([d for d in _DIRS if d.exists()])
    if scanned['found']:
        return _genlog.tokens_card(PROVIDER_ID, PROVIDER_NAME, scanned,
                                   'Windsurf/Codeium yerel loglarından (tespit-tabanlı; $ yok). Format ADAY.')
    return _genlog.nodata_card(PROVIDER_ID, PROVIDER_NAME,
                               'Windsurf kurulu (tespit edildi) ama okunur token logu yok/format doğrulanacak.')


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

#!/usr/bin/env python3
"""Claude Code durum satırı → canlı limit kaynağı (ağsız).

Claude Code her API cevabındaki limit başlıklarını durum satırı betiğine stdin JSON'unda
`rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}` olarak verir. Bu betik onu
`state_dir()/statusline-limits.json`'a yazar; `usage/live.py` taze kayıt varken
`api/oauth/usage` ucuna hiç çıkmaz. O uç agresif rate-limit'li (2026-10-02: 429 +
Retry-After 2685 sn, panel 79 dk donuk) — bu yol ek istek üretmez.

Kurulum (~/.claude/settings.json):
    "statusLine": {"type": "command", "command": "python3 /…/surface/statusline.py"}

Hiçbir koşulda hata basmaz ve 0 dışında çıkmaz: durum satırı her oturumda koşar.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FILE_NAME = 'statusline-limits.json'
WINDOWS = ('five_hour', 'seven_day')


def extract(payload) -> dict:
    """stdin JSON'undan yalnız bilinen pencereleri al; sayı olmayan yüzdeyi at."""
    rl = payload.get('rate_limits') if isinstance(payload, dict) else None
    out = {}
    if not isinstance(rl, dict):
        return out
    for k in WINDOWS:
        w = rl.get(k)
        if not isinstance(w, dict):
            continue
        pct = w.get('used_percentage')
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue
        out[k] = {'used_percentage': float(pct), 'resets_at': w.get('resets_at')}
    return out


def line(windows: dict) -> str:
    parts = []
    for k, label in (('five_hour', '5s'), ('seven_day', '7g')):
        w = windows.get(k)
        if w:
            parts.append(f"{label} %{w['used_percentage']:.0f}")
    return ' · '.join(parts)


def main():
    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except Exception:
        return
    windows = extract(payload)
    if not windows:
        return
    try:
        from usage import platform as _paths
        _paths.atomic_write_text(_paths.state_dir() / FILE_NAME,
                                 json.dumps({'at': time.time(), 'windows': windows}))
    except Exception:
        pass
    print(line(windows))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

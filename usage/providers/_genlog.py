#!/usr/bin/env python3
"""
Genel lokal-log token tarayıcı — belirsiz formatlı araçlar için hafif ortak yardımcı.
(Cody · Windsurf gibi FAZ 4d ADAY adaptörleri kullanır.)

Bilinen token alan adlarını (defansif küme) herhangi bir jsonl'de arar; toplar.
Format tutmazsa 0 döner → çağıran 'nodata' kartı açar (dürüst: tespit edildi, parse yok).
Ağır taramadan kaçınmak için dosya başına satır sınırı + toplam dosya sınırı.
"""
import json
from pathlib import Path

_INPUT_FIELDS = ('promptTokens', 'prompt_tokens', 'inputTokens', 'input_tokens',
                 'tokensContext', 'contextTokens')
_OUTPUT_FIELDS = ('generatedTokens', 'generated_tokens', 'completionTokens',
                  'completion_tokens', 'outputTokens', 'output_tokens')

MAX_FILES = 40
MAX_LINES = 20000


def _pick(d, names):
    for n in names:
        if n in d:
            try:
                return int(d[n])
            except (TypeError, ValueError):
                pass
    return 0


def scan(dirs, patterns=('*.jsonl',)) -> dict:
    """dirs içindeki jsonl'lerde token alanlarını topla.
    Döner: {'input','output','total','files','found'} — found=False ise parse edilemedi."""
    inp = out = 0
    files = 0
    for d in dirs:
        p = Path(d).expanduser()
        if not p.exists():
            continue
        for pat in patterns:
            for fp in p.rglob(pat):
                if files >= MAX_FILES:
                    break
                files += 1
                try:
                    with fp.open(encoding='utf-8', errors='ignore') as fh:
                        for i, line in enumerate(fh):
                            if i >= MAX_LINES:
                                break
                            line = line.strip()
                            if not line or '{' not in line:
                                continue
                            try:
                                o = json.loads(line)
                            except ValueError:
                                continue
                            if isinstance(o, dict):
                                inp += _pick(o, _INPUT_FIELDS)
                                out += _pick(o, _OUTPUT_FIELDS)
                except OSError:
                    continue
    return {'input': inp, 'output': out, 'total': inp + out,
            'files': files, 'found': (inp + out) > 0}


def nodata_card(pid, name, note):
    return {'id': pid, 'name': name, 'kind': 'tokens', 'available': True,
            'status': 'nodata', 'currency': None, 'error': None, 'windowDays': 30,
            'auth': 'yerel-log', 'usdSource': 'catalog',
            'tokens': {'total': 0}, 'total': {'tokens': 0, 'usd': 0},
            'today': {'tokens': 0, 'usd': 0}, 'byModel': [{'short': '—', 'model': 'n/a'}],
            'byDay': [], 'sessions': 0, 'note': note}


def tokens_card(pid, name, scanned, note):
    total = scanned['total']
    return {'id': pid, 'name': name, 'kind': 'tokens', 'available': True,
            'status': 'ok' if total else 'nodata', 'currency': None, 'error': None,
            'windowDays': 30, 'auth': 'yerel-log', 'usdSource': 'catalog',
            'tokens': {'input': scanned['input'], 'output': scanned['output'], 'total': total},
            'total': {'tokens': total, 'usd': 0},
            'today': {'tokens': 0, 'usd': 0},
            'byModel': [{'short': '—', 'model': 'n/a'}], 'byDay': [],
            'sessions': scanned['files'], 'note': note}

#!/usr/bin/env python3
"""
Continue.dev adaptörü — token (Codex deseni, lokal-log). (FAZ 4d — ADAY)

Kaynak (SALT-OKUNUR): ~/.continue/dev_data/**/(tokens_generated|tokensGenerated).jsonl
  Satır: {"model":..., "provider":..., "promptTokens":N, "generatedTokens":N}
  (Continue dev_data şeması sürümlü — alan adları defansif taranır.)

Dizin yoksa → None. Var ama parse yoksa → status='nodata'. mtime-cache.
⚠️ $ yok (Continue token verir, maliyet vermez) → 'tokens' kartı, usd=0. Alanlar ADAY.
"""
import json
import sys
import threading
import time
from pathlib import Path

PROVIDER_ID = 'continue'
PROVIDER_NAME = 'Continue.dev'
DATA_DIR = Path.home() / '.continue' / 'dev_data'
CACHE_TTL = 60.0

# Büyük log dizininde asılmayı önlemek için tarama sınırı (ledger: python/executor-with-timeout-yalani)
MAX_FILES = 40
MAX_LINES_PER_FILE = 20000

_LOCK = threading.Lock()
_CACHE = None            # (fingerprint, card, ts)

_PROMPT = ('promptTokens', 'prompt_tokens', 'tokensContext', 'contextTokens')
_GEN = ('generatedTokens', 'generated_tokens', 'tokensGenerated', 'completionTokens')


def _pick(d, names):
    for n in names:
        if n in d:
            try:
                return int(d[n])
            except (TypeError, ValueError):
                pass
    return 0


def _token_files():
    if not DATA_DIR.exists():
        return []
    out = []
    for pat in ('tokens_generated.jsonl', 'tokensGenerated.jsonl'):
        out.extend(DATA_DIR.rglob(pat))
    return out


def _fingerprint(files):
    sig = []
    for fp in files:
        try:
            st = fp.stat()
            sig.append((str(fp), st.st_mtime, st.st_size))
        except OSError:
            pass
    return tuple(sorted(sig))


def _parse(days: int) -> dict:
    card = {'id': PROVIDER_ID, 'name': PROVIDER_NAME, 'kind': 'tokens',
            'available': True, 'currency': None, 'error': None, 'windowDays': days,
            'auth': 'yerel-log', 'usdSource': 'catalog'}
    prompt = gen = 0
    models = {}
    files_scanned = 0
    for fp in _token_files():
        if files_scanned >= MAX_FILES:
            break
        files_scanned += 1
        try:
            with fp.open(encoding='utf-8') as fh:
                for line_idx, line in enumerate(fh):
                    if line_idx >= MAX_LINES_PER_FILE:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError as e:
                        print(f'Provider {PROVIDER_ID}: Bozuk JSON satırı {fp}:{line_idx}: {e}', file=sys.stderr)
                        continue
                    if not isinstance(o, dict):
                        continue
                    p = _pick(o, _PROMPT); g = _pick(o, _GEN)
                    if not (p or g):
                        continue
                    prompt += p; gen += g
                    mk = o.get('model') or 'continue'
                    m = models.setdefault(mk, {'short': str(mk).split('/')[-1], 'usd': 0.0,
                                               'tokens': {'input': 0, 'output': 0}})
                    m['tokens']['input'] += p; m['tokens']['output'] += g
        except OSError:
            continue

    total = prompt + gen
    model_list = sorted(({'model': k, **v} for k, v in models.items()),
                        key=lambda x: x['tokens']['output'], reverse=True) or [{'short': '—', 'model': 'n/a'}]
    return {**card,
            'status': 'ok' if total else 'nodata',
            'tokens': {'input': prompt, 'output': gen, 'total': total},
            'total': {'tokens': total, 'usd': 0},
            'today': {'tokens': 0, 'usd': 0},   # Continue log'unda güvenilir zaman damgası yok
            'byModel': model_list,
            'byDay': [],
            'sessions': len(models),
            'note': 'Continue dev_data token loglarından (kümülatif; $ yok). Alanlar ADAY.'}


def collect(days: int = 30) -> dict:
    global _CACHE
    if not DATA_DIR.exists():
        return None
    files = _token_files()
    fp = _fingerprint(files)
    now = time.time()
    with _LOCK:
        if _CACHE and _CACHE[0] == fp and (now - _CACHE[2]) < CACHE_TTL:
            return _CACHE[1]
        # Cache miss → _parse() işini de lock içinde yap, çift-fetch'i önle (MNT-F002)
        card = _parse(days)
        _CACHE = (fp, card, now)
    return card


if __name__ == '__main__':
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

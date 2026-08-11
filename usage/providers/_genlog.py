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

    Döner: {'input','output','total','files','found','truncated','truncatedReason'}
      found=False           → parse edilemedi (format tutmadı)
      truncated=True        → tavana çarpıldı, sayı EKSİK. reason ∈ {'files','lines'}

    Aynı dosyayı iki kez saymaz. Çağıranlar iç içe dizinler verebiliyor
    (windsurf: ~/.codeium/windsurf **ve** ~/.codeium) — `rglob` ikisinde de aynı
    dosyayı döndürürdü ve her token iki kere toplanırdı.
    """
    inp = out = 0
    seen = set()                      # resolve edilmiş dosya yolları — çift sayımı kesen şey
    truncated_reason = None

    for d in dirs:
        if truncated_reason == 'files':
            break
        p = Path(d).expanduser()
        if not p.exists():
            continue
        for pat in patterns:
            if truncated_reason == 'files':
                break
            for fp in p.rglob(pat):
                try:
                    key = fp.resolve()
                except OSError:
                    key = fp.absolute()
                if key in seen:
                    continue
                if len(seen) >= MAX_FILES:
                    truncated_reason = 'files'
                    break
                seen.add(key)
                try:
                    with fp.open(encoding='utf-8', errors='ignore') as fh:
                        for i, line in enumerate(fh):
                            if i >= MAX_LINES:
                                truncated_reason = truncated_reason or 'lines'
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
            'files': len(seen), 'found': (inp + out) > 0,
            'truncated': truncated_reason is not None,
            'truncatedReason': truncated_reason}


def nodata_card(pid, name, note):
    return {'id': pid, 'name': name, 'kind': 'tokens', 'available': True,
            'status': 'nodata', 'currency': None, 'error': None, 'windowDays': 30,
            'auth': 'yerel-log', 'usdSource': 'catalog',
            'tokens': {'total': 0}, 'total': {'tokens': 0, 'usd': 0},
            'today': {'tokens': 0, 'usd': 0}, 'byModel': [{'short': '—', 'model': 'n/a'}],
            'byDay': [], 'sessions': 0, 'truncated': False, 'note': note}


def _truncation_note(scanned) -> str:
    """Kullanıcıya neyin eksik olduğunu söyleyen cümle. Sessiz kesme yasak."""
    if scanned.get('truncatedReason') == 'files':
        return (f'⚠️ Tarama {MAX_FILES} dosyada durdu — gösterilen toplam EKSİK, '
                f'gerçek kullanım daha yüksek.')
    return (f'⚠️ En az bir dosya {MAX_LINES:,} satırda kesildi — gösterilen toplam EKSİK, '
            f'gerçek kullanım daha yüksek.').replace(',', '.')


def tokens_card(pid, name, scanned, note):
    total = scanned['total']
    truncated = bool(scanned.get('truncated'))
    if truncated:
        note = f'{note} {_truncation_note(scanned)}'
    # 'partial': sayı var ama eksik. 'ok' demek olurdu ve yalan olurdu.
    status = 'partial' if (truncated and total) else ('ok' if total else 'nodata')
    return {'id': pid, 'name': name, 'kind': 'tokens', 'available': True,
            'status': status, 'currency': None, 'error': None,
            'windowDays': 30, 'auth': 'yerel-log', 'usdSource': 'catalog',
            'tokens': {'input': scanned['input'], 'output': scanned['output'], 'total': total},
            'total': {'tokens': total, 'usd': 0},
            'today': {'tokens': 0, 'usd': 0},
            'byModel': [{'short': '—', 'model': 'n/a'}], 'byDay': [],
            'sessions': scanned['files'],
            'truncated': truncated, 'truncatedReason': scanned.get('truncatedReason'),
            'note': note}

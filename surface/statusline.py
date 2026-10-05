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
from __future__ import annotations  # README Python 3.9 vaat ediyor: `X | None` 3.9'da yüklenirken patlar

import json
import os
import re
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


# Aynı pencere sayılan sıfırlanma farkı (sn). Claude Code `resets_at`'i yuvarlanmış verir;
# küçük kayma yeni pencere demek değildir.
AYNI_PENCERE_SN = 60


def _reset(w):
    r = w.get('resets_at') if isinstance(w, dict) else None
    return r if isinstance(r, (int, float)) and not isinstance(r, bool) else None


def merge(eski: dict, yeni: dict, now: float) -> dict:
    """Dosyadaki kayıtla bu oturumun gördüğünü birleştir — son yazan kazanmasın.

    Açık her oturum durum satırını çizerken bu dosyaya yazar; boşta duran bir oturum ise
    SON API cevabındaki eski sayıyı taşır. Körlemesine yazınca taze %69'u bayat %59 ezdi
    (2026-10-05, rozet 59↔69 gidip geldi). Kurallar:
      - sıfırlanma anı geçmiş eski pencere atılır (artık geçerli değil);
      - yeni gelende pencere yoksa eldeki geçerli kayıt korunur;
      - yeni gelen daha ESKİ bir pencereye aitse yok sayılır;
      - aynı pencerede yüzde yalnız artar (kullanım pencere içinde azalmaz) → büyüğü kalır.
    Oku-birleştir-yaz atomik değil; iki oturumun aynı milisaniyede yazması nadirdir ve
    sonraki çizim düzeltir.
    """
    out = {}
    for k in WINDOWS:
        o, n = (eski or {}).get(k), (yeni or {}).get(k)
        if isinstance(o, dict):
            r = _reset(o)
            if r is not None and r <= now or not isinstance(o.get('used_percentage'), (int, float)):
                o = None
        else:
            o = None
        if not n:
            if o:
                out[k] = o
            continue
        if not o:
            out[k] = n
            continue
        ro, rn = _reset(o), _reset(n)
        if ro is not None and rn is not None and rn < ro - AYNI_PENCERE_SN:
            out[k] = o                                   # bayat oturum, eski pencere
        elif ro is not None and rn is not None and abs(rn - ro) <= AYNI_PENCERE_SN:
            out[k] = n if n['used_percentage'] >= o['used_percentage'] else o
        else:
            out[k] = n                                   # yeni pencere (ya da reset bilinmiyor)
    return out


def line(windows: dict) -> str:
    parts = []
    for k, label in (('five_hour', '5s'), ('seven_day', '7g')):
        w = windows.get(k)
        if w:
            parts.append(f"{label} %{w['used_percentage']:.0f}")
    return ' · '.join(parts)


# ── Bağlam bandı (vault BL-519 #1; kaynak: dev-mod baglam-bandi, token-weather tabanlı)
# Kademeler pencereye değil COMPACT NOKTASINA göre: bu makinede
# CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=55 → 1M pencerede compact ~550k. "%40" rahat
# görünür ama compact'ın %73'üdür.
KADEME = ((50, '○', 'Rahat', '32'), (75, '◐', 'Olağan', '36'),
          (90, '◕', 'Kapanış noktası ara', '33'),
          (float('inf'), '●', 'Compact yakın — kapat ya da devret', '31'))
CUBUK = '▁▂▃▄▅▆▇█'
GECMIS = 12


def kisa(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace('.0M', 'M')
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(int(n))


def compact_pct() -> float:
    try:
        v = float(os.environ.get('CLAUDE_AUTOCOMPACT_PCT_OVERRIDE', ''))
        return v if 0 < v <= 100 else 95.0
    except ValueError:
        return 95.0


def baglam_okuma(payload) -> tuple[int, int] | None:
    """stdin JSON'undan (tokens, pencere); yoksa None."""
    cw = payload.get('context_window') if isinstance(payload, dict) else None
    if not isinstance(cw, dict):
        return None
    pencere = cw.get('context_window_size')
    if not isinstance(pencere, (int, float)) or pencere <= 0:
        return None
    pct = cw.get('used_percentage')
    cu = cw.get('current_usage')
    if isinstance(cu, dict):
        tokens = sum(v for k, v in cu.items()
                     if k.endswith('input_tokens') and isinstance(v, (int, float)))
    elif isinstance(pct, (int, float)):
        tokens = pencere * pct / 100
    else:
        return None
    return int(tokens), int(pencere)


def gecmis_guncelle(sid: str, tokens: int) -> list[int]:
    """Oturum başına son GECMIS okuma; aynı değer tekrar yazılmaz."""
    try:
        from usage import platform as _paths
        yol = _paths.state_dir() / f'statusline-ctx-{sid or "x"}.json'
        try:
            dizi = json.loads(yol.read_text(encoding='utf-8'))
        except Exception:
            dizi = []
        dizi = [t for t in dizi if isinstance(t, int) and t > 0]
        if tokens > 0 and (not dizi or dizi[-1] != tokens):
            dizi = (dizi + [tokens])[-GECMIS:]
            _paths.atomic_write_text(yol, json.dumps(dizi))
        return dizi
    except Exception:
        return [tokens] if tokens else []


def bant(tokens: int, pencere: int, gecmis: list[int]) -> str:
    hedef = pencere * compact_pct() / 100
    oran = 100 * tokens / hedef
    _, ikon, soz, renk = next(k for k in KADEME if oran < k[0])
    kalan = max(0, hedef - tokens)
    parca = [f"\033[1;{renk}m{ikon} {soz}\033[0m",
             f"%{round(100 * tokens / pencere)}",
             f"\033[2m{kisa(tokens)}/{kisa(pencere)}\033[0m",
             f"compact'a {kisa(kalan)}"]
    if len(gecmis) >= 3:
        ort = (gecmis[-1] - gecmis[0]) / (len(gecmis) - 1)
        if ort > 500:
            parca[-1] += f" (~{int(kalan // ort)} tur)"
    if len(gecmis) >= 2:
        tepe = max(gecmis) or 1
        grafik = ''.join(CUBUK[min(7, int(t / tepe * 7))] for t in gecmis)
        d = gecmis[-1] - gecmis[-2]
        egilim = f"▲ +{kisa(d)}" if d > 0 else (f"▼ {kisa(-d)}" if d < 0 else 'sabit')
        parca.append(f"\033[{renk}m{grafik}\033[0m \033[2m{egilim}\033[0m")
    return '  '.join(parca)


def main():
    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except Exception:
        return
    windows = extract(payload)
    if windows:
        try:
            from usage import platform as _paths
            yol = _paths.state_dir() / FILE_NAME
            now = time.time()
            try:
                eski = json.loads(yol.read_text(encoding='utf-8')).get('windows') or {}
            except Exception:
                eski = {}
            birlesik = merge(eski, windows, now)
            if birlesik:
                _paths.atomic_write_text(yol, json.dumps({'at': now, 'windows': birlesik}))
        except Exception:
            pass
    parcalar = []
    okuma = baglam_okuma(payload)
    if okuma and okuma[0] > 0:
        # dosya adına girer: yalnız güvenli karakterler
        sid = re.sub(r'[^A-Za-z0-9_-]', '', str(payload.get('session_id') or ''))[:64]
        parcalar.append(bant(*okuma, gecmis_guncelle(sid, okuma[0])))
    if windows:
        parcalar.append(line(windows))
    if parcalar:
        metin = '  · '.join(parcalar)
        try:
            print(metin)
        except UnicodeEncodeError:  # Windows cp1252 konsolu bant karakterlerini basamıyor
            sys.stdout.buffer.write((metin + '\n').encode('utf-8', 'replace'))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

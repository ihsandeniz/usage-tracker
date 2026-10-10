#!/usr/bin/env python3
"""Limit yüzdesinin zaman serisi (Limit Kara Kutusu, v0.6 FAZ 1).

Canlı yüzde iki yerden gelir: `surface/statusline.py` (asıl kaynak, ağsız) ve
`live.fetch()`'in başarılı ağ yanıtı. İkisi de yalnız SON değeri tutuyordu; burada her
değişim bir satır olarak `state_dir()/limit-seri.jsonl`'a eklenir. "Limitin %1'i kaç
token ediyor" sorusu (FAZ 2-3) bu seriden cevaplanır.

Satır biçimi (bir çağrı = en çok bir satır, yalnız değişen pencereler):
    {"t": 1791148000.0, "k": "statusline", "w": {"five_hour": {"p": 41.0, "r": 1791166000}}}
  t = gözlem anı (epoch sn) · k = kaynak · p = yüzde · r = sıfırlanma anı (epoch sn, yoksa null)

Masaüstü Claude uygulaması kendi yüzde geçmişini `~/.config/Claude/plan-usage-history.json`'da
tutar (resmî değil, `version: 2`). `geri_al()` onu SALT-OKUR, kopyalamaz: her örnekte yalnız
`t`, `u.fh`, `u.sd` alınır. Hesap kimliği (`org`) ayrıştırma anında atılır — hiçbir dönüşe,
dosyaya ya da hataya girmez.

Kanca çağıranları (statusline, live) hata görmez: `ekle()` hiçbir koşulda istisna atmaz.
Ağ isteği yapmaz.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime

from . import platform as _paths

DOSYA = 'limit-seri.jsonl'
PENCERELER = ('five_hour', 'seven_day')
# Sıfırlanma anındaki bu kadarlık kayma aynı pencere sayılır (statusline.AYNI_PENCERE_SN ile aynı).
AYNI_PENCERE_SN = 60
# Son değeri bulmak için dosyanın yalnız sonu okunur. Bir satır ~150 bayt → ~400 satır.
KUYRUK_BAYT = 64 * 1024
# Yüzde değişince yazılır: 5 saatte ≤ ~100, haftada ≤ ~100 + sıfırlamalar. 2 MB ≈ 13 bin satır
# ≈ aylarca kayıt. Aşınca en yeni yarısı kalır.
AZAMI_BAYT = 2 * 1024 * 1024
KALAN_SATIR = 6000

MASAUSTU_GECMIS = ('Claude', 'plan-usage-history.json')     # config kökü altında
MASAUSTU_SURUM = 2
_MASAUSTU_ALAN = {'fh': 'five_hour', 'sd': 'seven_day'}


def yol():
    return _paths.state_dir() / DOSYA


# ── normalleştirme ───────────────────────────────────────────────────────────────────

def _sayi(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _yuzde(v):
    """0-100 arası sonlu sayı ya da None. Bozuk yüzde (#52326: alana sıfırlanma anı
    yazılabiliyor) seriye girmez."""
    if not _sayi(v) or not 0 <= v <= 100:
        return None
    return round(float(v), 1)


def _reset_sn(v):
    """resets_at → epoch sn (int). Durum satırı sayı, `oauth/usage` ISO metni verir."""
    if _sayi(v):
        v = float(v)
        return int(v / 1000) if v > 1e11 else int(v)           # ms geldiyse
    if isinstance(v, str) and v:
        try:
            return int(datetime.fromisoformat(v.replace('Z', '+00:00')).timestamp())
        except ValueError:
            return None
    return None


def _ayni(a, b):
    """İki gözlem aynı değer mi: aynı yüzde ve aynı pencere (sıfırlanma ±60 sn)."""
    if a is None or b is None or a.get('p') != b.get('p'):
        return False
    ra, rb = a.get('r'), b.get('r')
    if ra is None or rb is None:
        return ra is None and rb is None
    return abs(ra - rb) <= AYNI_PENCERE_SN


# ── okuma ────────────────────────────────────────────────────────────────────────────

def _satir_coz(satir):
    try:
        d = json.loads(satir)
    except ValueError:
        return None
    if not isinstance(d, dict) or not _sayi(d.get('t')) or not isinstance(d.get('w'), dict):
        return None
    w = {}
    for k, v in d['w'].items():
        if k in PENCERELER and isinstance(v, dict) and _yuzde(v.get('p')) is not None:
            w[k] = {'p': _yuzde(v['p']), 'r': v.get('r') if _sayi(v.get('r')) else None}
    if not w:
        return None
    return {'t': float(d['t']), 'k': d.get('k') if isinstance(d.get('k'), str) else '?', 'w': w}


def _satirlar(metin):
    for s in metin.splitlines():
        if s.strip():
            r = _satir_coz(s)
            if r:
                yield r


def son_degerler(dosya=None) -> dict:
    """Her pencerenin dosyadaki en yeni (t'si en büyük) değeri: {pencere: {'p','r','t'}}."""
    dosya = dosya or yol()
    try:
        with open(dosya, 'rb') as f:
            f.seek(0, os.SEEK_END)
            boy = f.tell()
            f.seek(max(0, boy - KUYRUK_BAYT))
            ham = f.read().decode('utf-8', 'replace')
        if boy > KUYRUK_BAYT:
            ham = ham.split('\n', 1)[-1]                 # yarım ilk satırı at
    except OSError:
        return {}
    son = {}
    for r in _satirlar(ham):
        for k, v in r['w'].items():
            if k not in son or r['t'] >= son[k]['t']:
                son[k] = dict(v, t=r['t'])
    return son


def oku(since=None, pencere=None, dosya=None) -> list:
    """Seriyi düz liste olarak döndür, t'ye göre sıralı: [{'t','w','p','r','k'}].

    since = epoch sn (dahil) · pencere = 'five_hour' | 'seven_day' | None (hepsi).
    Bozuk satırlar atlanır.
    """
    dosya = dosya or yol()
    try:
        metin = open(dosya, encoding='utf-8', errors='replace').read()
    except OSError:
        return []
    out = []
    for r in _satirlar(metin):
        if since is not None and r['t'] < since:
            continue
        for k, v in r['w'].items():
            if pencere is None or k == pencere:
                out.append({'t': r['t'], 'w': k, 'p': v['p'], 'r': v['r'], 'k': r['k']})
    out.sort(key=lambda x: x['t'])
    return out


# ── yazma ────────────────────────────────────────────────────────────────────────────

def _normalle(gozlem) -> dict:
    """{pencere: {used_percentage|utilization|p, resets_at|r}} → {pencere: {'p','r'}}."""
    out = {}
    if not isinstance(gozlem, dict):
        return out
    for k in PENCERELER:
        w = gozlem.get(k)
        if not isinstance(w, dict):
            continue
        for alan in ('p', 'used_percentage', 'utilization'):
            if alan in w:
                p = _yuzde(w[alan])
                break
        else:
            p = None
        if p is None:
            continue
        out[k] = {'p': p, 'r': _reset_sn(w.get('r', w.get('resets_at')))}
    return out


def ekle(gozlem, t, kaynak, dosya=None) -> bool:
    """Değişen pencereleri tek satır olarak ekle. Yazdıysa True.

    Aynı değer (aynı yüzde + aynı pencere) ikinci kez yazılmaz; böylece boşta oturumların her
    durum satırı çizimi dosyayı büyütmez. Hiçbir koşulda istisna atmaz.

    Bilinen sınır: iki süreç aynı anda aynı yeni değeri görürse ikisi de yazabilir (kuyruk okuma
    ile ekleme arası kilitsiz). Okuyan taraf (FAZ 2-3) ardışık eşit değeri zaten tek sayar.
    """
    try:
        if not _sayi(t):
            return False
        dosya = dosya or yol()
        yeni = _normalle(gozlem)
        if not yeni:
            return False
        son = son_degerler(dosya)
        degisen = {k: v for k, v in yeni.items() if not _ayni(v, son.get(k))}
        if not degisen:
            return False
        satir = json.dumps({'t': round(float(t), 3), 'k': str(kaynak), 'w': degisen},
                           separators=(',', ':')) + '\n'
        dosya.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND + tek write: satır PIPE_BUF'tan kısa, eşzamanlı yazıcılar satır bölmez.
        fd = os.open(dosya, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, satir.encode('utf-8'))
        finally:
            os.close(fd)
        buda(dosya)
        return True
    except Exception:
        return False


def buda(dosya=None, azami_bayt=AZAMI_BAYT, kalan=KALAN_SATIR) -> bool:
    """Dosya `azami_bayt`'ı aşınca en yeni `kalan` geçerli satırı tut (atomik yeniden yazım)."""
    try:
        dosya = dosya or yol()
        if dosya.stat().st_size <= azami_bayt:
            return False
        metin = dosya.read_text(encoding='utf-8', errors='replace')
        satirlar = [s for s in metin.splitlines() if s.strip() and _satir_coz(s)]
        _paths.atomic_write_text(dosya, ''.join(s + '\n' for s in satirlar[-kalan:]))
        return True
    except Exception:
        return False


# ── masaüstü geçmişi (salt-okuma) ──────────────────────────────────────────────────────

def masaustu_yolu():
    """Masaüstü uygulamasının yüzde geçmişi. Windows'ta APPDATA\\Claude, diğerlerinde
    $XDG_CONFIG_HOME/Claude (yoksa ~/.config/Claude)."""
    if _paths.is_windows():
        kok = _paths._windows_base('APPDATA')
    else:
        base = os.environ.get('XDG_CONFIG_HOME')
        kok = _paths.Path(base) if base else _paths.home() / '.config'
    return kok.joinpath(*MASAUSTU_GECMIS)


def _yalniz_izinli(ciftler):
    """json object_pairs_hook: `org` ayrıştırma anında atılır, belleğe bile alınmaz."""
    return {k: v for k, v in ciftler if k != 'org'}


def geri_al(kaynak=None) -> dict:
    """Masaüstü geçmişini oku → {'durum': 'ok'|'yok'|'okunamadi', 'ornekler': [...], 'neden'}.

    ornekler: t'ye göre sıralı [{'t': sn, 'w': pencere, 'p': yüzde, 'r': None, 'k': 'masaustu'}].
    Biçim değişirse (sürüm ≠ 2, `samples` yok) çökmez, 'okunamadi' döner. Dosyaya yazmaz;
    kaynak dosya uygulamanın kendi kaydı olduğu için kopyalanmaz, her seferinde okunur.
    """
    kaynak = kaynak or masaustu_yolu()
    try:
        ham = _paths.Path(kaynak).read_text(encoding='utf-8')
    except FileNotFoundError:
        return {'durum': 'yok', 'ornekler': [], 'neden': 'dosya yok'}
    except OSError as e:
        return {'durum': 'okunamadi', 'ornekler': [], 'neden': type(e).__name__}
    try:
        d = json.loads(ham, object_pairs_hook=_yalniz_izinli)
    except ValueError:
        return {'durum': 'okunamadi', 'ornekler': [], 'neden': 'JSON bozuk'}
    del ham
    if not isinstance(d, dict) or d.get('version') != MASAUSTU_SURUM \
            or not isinstance(d.get('samples'), list):
        surum = d.get('version') if isinstance(d, dict) else None
        return {'durum': 'okunamadi', 'ornekler': [],
                'neden': f'beklenmeyen biçim (version={surum!r})'}
    out = []
    for s in d['samples']:
        if not isinstance(s, dict) or not _sayi(s.get('t')) or not isinstance(s.get('u'), dict):
            continue
        t = s['t'] / 1000.0                               # ms → sn
        for alan, pencere in _MASAUSTU_ALAN.items():
            p = _yuzde(s['u'].get(alan))
            if p is not None:
                out.append({'t': t, 'w': pencere, 'p': p, 'r': None, 'k': 'masaustu'})
    out.sort(key=lambda x: (x['t'], x['w']))
    return {'durum': 'ok', 'ornekler': out, 'neden': None}


if __name__ == '__main__':
    g = geri_al()
    zamanlar = sorted({o['t'] for o in g['ornekler']})
    print(f"masaüstü geçmişi: {g['durum']} · {len(zamanlar)} örnek · {len(g['ornekler'])} gözlem"
          + (f" · {g['neden']}" if g['neden'] else ''))
    print(f"seri: {yol()} · {len(oku())} gözlem")

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
from datetime import datetime, timezone

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


# ── FAZ 2: aralık dökümü (iki gözlem arası turn'ler) ───────────────────────────────────

def _pay(sozluk, toplam):
    return {k: round(v / toplam, 4) for k, v in sorted(sozluk.items(), key=lambda x: -x[1])} \
        if toplam > 0 else {}


def aralik_dokumu(turns, model_kisa=None) -> dict:
    """Bir aralığa düşen turn'leri ağırlıklı birim üzerinden paylara böl.

    turns = engine.Turn listesi (zaten aralığa süzülmüş). Paylar 0-1, her döküm kendi içinde 1'e
    toplanır. Oturum anahtarı ham `sessionId`dir — yalnız yerel gösterim içindir, dışa aktarım
    (FAZ 5) bunu taşımaz.
      oturum  {sid: pay} · model {kısa ad: pay} · surum {Claude Code sürümü: pay}
      alt_ajan = alt ajan turn'lerinin payı
      onbellek_okuma = birimin önbellek-okuma (cache_read × 0,1 × model ağırlığı) bileşeninin payı
    """
    from .engine import _model_weight, _short_model
    model_kisa = model_kisa or _short_model
    birim = ham = cr_birim = alt = 0.0
    oturum, model, surum = {}, {}, {}
    n = 0
    for t in turns:
        n += 1
        birim += t.units
        ham += t.raw
        cr_birim += _model_weight(t.model) * t.cr * 0.1
        if getattr(t, 'side', False):
            alt += t.units
        sid = getattr(t, 'sid', '') or '?'
        oturum[sid] = oturum.get(sid, 0.0) + t.units
        m = model_kisa(t.model)
        model[m] = model.get(m, 0.0) + t.units
        v = getattr(t, 'ver', '') or '?'
        surum[v] = surum.get(v, 0.0) + t.units
    return {
        'turn': n, 'birim': round(birim, 1), 'ham': int(ham),
        'oturum': _pay(oturum, birim), 'model': _pay(model, birim), 'surum': _pay(surum, birim),
        'alt_ajan': round(alt / birim, 4) if birim > 0 else 0.0,
        'onbellek_okuma': round(cr_birim / birim, 4) if birim > 0 else 0.0,
    }


# ── FAZ 3: aralıklar · temel çizgi · sapma ────────────────────────────────────────────

PENCERE_SN = {'five_hour': 5 * 3600, 'seven_day': 7 * 24 * 3600}
ASGARI_PUAN = 5            # tam sayı yüzde: < 5 puanlık aralık gürültü (PLAN §6)
AZAMI_BOSLUK_SN = 10 * 60  # sıfırlanma anı bilinmeyen kaynakta (masaüstü nabzı 5 dk) boşluk = kopuş
HIZLI_ESIK = 0.5           # aralık oranı ≤ 0,5 × temel → "2× hızlı"
TEMEL_ASGARI = 3           # bayrak için en az bu kadar önceki aralık
TEMEL_KAYAN = 30           # temel = son bu kadar geçerli aralığın medyanı


def _kesintisiz(gozlemler, pencere):
    """Gözlemleri (tek pencere, t sıralı) kesintisiz parçalara böl.

    Kopuş: yüzde düşüşü · sıfırlanma anı değişimi (±60 sn) ya da geçilmesi · `r` bilinmiyorsa
    AZAMI_BOSLUK_SN'den uzun boşluk ve pencere süresini aşan parça (gizli sıfırlanma).
    Ardışık eşit değerler (çift yazım) tek sayılır.
    """
    pen = PENCERE_SN[pencere]
    parcalar, cur = [], []
    for g in gozlemler:
        if cur:
            o = cur[-1]
            kop = g['p'] < o['p']
            if o['r'] is not None and g['r'] is not None:
                kop = kop or abs(g['r'] - o['r']) > AYNI_PENCERE_SN or g['t'] >= o['r']
            else:
                kop = kop or g['t'] - o['t'] > AZAMI_BOSLUK_SN or g['t'] - cur[0]['t'] > pen
            if kop:
                parcalar.append(cur)
                cur = []
            elif g['p'] == o['p'] and o['r'] is not None:
                continue                      # değişim serisinde tekrar satırı
        cur.append(g)
    if cur:
        parcalar.append(cur)
    return parcalar


def _araliklar(gozlemler, pencere, asgari_puan, dilim=False):
    """Ölçülecek aralıklar → [(g0, g1)].

    Varsayılan: her kesintisiz parça tek aralık (≥ asgari_puan). dilim=True: parça ≥ asgari_puan
    puanlık ardışık dilimlere bölünür — pencere içi ani sıçramayı daha erken görür ama tam sayı
    yüzde yuvarlaması kısa dilimde ±%20'ye varır (gerçek veride CV %17 → %26, bkz. F2F3 raporu).
    """
    out = []
    for parca in _kesintisiz(gozlemler, pencere):
        if not dilim:
            if parca[-1]['p'] - parca[0]['p'] >= asgari_puan:
                out.append((parca[0], parca[-1]))
            continue
        bas = parca[0]
        for g in parca[1:]:
            if g['p'] - bas['p'] >= asgari_puan:
                out.append((bas, g))
                bas = g
    return out


def gozlemler(pencere, seri_dosya=None, masaustu=None) -> list:
    """Bir pencerenin tüm gözlemleri: masaüstü geçmişi (seri başlamadan öncesi) + kendi seri.

    İki kaynak aynı zaman aralığında karışmaz: seri başladığı andan sonra masaüstü örnekleri
    atılır (biri tam sayı 5 dk nabız, öbürü değişim kaydı; karışınca sahte düşüş görülür).
    """
    seri = oku(pencere=pencere, dosya=seri_dosya)
    g = geri_al(masaustu) if masaustu is not False else {'ornekler': []}
    ilk = seri[0]['t'] if seri else float('inf')
    mas = [o for o in g['ornekler'] if o['w'] == pencere and o['t'] < ilk]
    return mas + seri


def olc(gozlem_listesi, turns, pencere, asgari_puan=ASGARI_PUAN, hizli_esik=HIZLI_ESIK,
        temel_asgari=TEMEL_ASGARI, temel_kayan=TEMEL_KAYAN, dilim=False, model_kisa=None) -> list:
    """Her aralık için oran (%1 başına birim) + kayan temel + bayrak.

    turns = engine.Turn listesi (herhangi sıra). Aralığa (t0, t1] içindeki turn'ler düşer.
    Dönen her öğe:
      t0, t1, p0, p1, puan, sure_sn, kaynak, oran, temel, kat (oran/temel), bayrak, durum, dokum
    durum: 'tamam' · 'temel-yok' (önceki aralık < temel_asgari) · 'yerel-kayit-yok' (aralıkta
    turn yok: başka makine/uygulama tüketimi; temele girmez, "hızlı" sayılmaz).
    Bayraklı aralık da temele girer (medyan kayar, tek olay temeli bozmaz).
    """
    import bisect
    import statistics
    ts = sorted(turns, key=lambda x: x.ms)
    ms = [x.ms for x in ts]
    gecmis, out = [], []
    for g0, g1 in _araliklar(gozlem_listesi, pencere, asgari_puan, dilim):
        i = bisect.bisect_right(ms, g0['t'] * 1000)
        j = bisect.bisect_right(ms, g1['t'] * 1000)
        dok = aralik_dokumu(ts[i:j], model_kisa)
        puan = round(g1['p'] - g0['p'], 1)
        o = {'t0': g0['t'], 't1': g1['t'], 'p0': g0['p'], 'p1': g1['p'], 'puan': puan,
             'sure_sn': round(g1['t'] - g0['t']), 'kaynak': g1['k'], 'oran': None,
             'temel': None, 'kat': None, 'bayrak': False, 'durum': 'tamam', 'dokum': dok}
        if dok['turn'] == 0:
            o['durum'] = 'yerel-kayit-yok'
            out.append(o)
            continue
        o['oran'] = round(dok['birim'] / puan, 1)
        onceki = gecmis[-temel_kayan:]
        if not onceki or len(onceki) < temel_asgari:
            o['durum'] = 'temel-yok'
        else:
            temel = statistics.median(onceki)
            o['temel'] = round(temel, 1)
            o['kat'] = round(o['oran'] / temel, 3) if temel > 0 else None
            o['bayrak'] = temel > 0 and o['oran'] <= hizli_esik * temel * (1 + 1e-9)
        gecmis.append(o['oran'])
        out.append(o)
    return out


def ozet(araliklar) -> dict:
    """Aralık listesinin özeti: n, medyan, CV (pstdev/ortalama), bayrak sayısı."""
    import statistics
    v = [a['oran'] for a in araliklar if a['oran']]
    r = {'aralik': len(araliklar), 'olculen': len(v),
         'yerel_kayit_yok': sum(a['durum'] == 'yerel-kayit-yok' for a in araliklar),
         'bayrak': sum(a['bayrak'] for a in araliklar), 'medyan': None, 'cv': None}
    if v:
        r['medyan'] = round(statistics.median(v), 1)
    if len(v) > 1:
        r['cv'] = round(statistics.pstdev(v) / statistics.mean(v), 4)
    return r


def sinav(seri_dosya=None, masaustu=None, asgari_puan=ASGARI_PUAN, turns=None,
          dilim=False) -> dict:
    """Geriye dönük sınav: eldeki tüm yüzde geçmişi × yerel turn'ler. Salt-okuma, ağ yok.

    Çıktıda oturum kimliği yok: dökümden yalnız en büyük oturumun payı (`tepe_oturum`) kalır.
    """
    from . import engine
    pen = {k: gozlemler(k, seri_dosya, masaustu) for k in PENCERELER}
    ilk = min((g[0]['t'] for g in pen.values() if g), default=None)
    if turns is None:
        turns = engine._scan_turns(int((ilk - 6 * 3600) * 1000)) if ilk is not None else []
    out = {'turn': len(turns), 'dilim': dilim, 'pencere': {}}
    for k, g in pen.items():
        ar = olc(g, turns, k, asgari_puan=asgari_puan, dilim=dilim)
        for a in ar:
            d = a.pop('dokum')
            a['turn'] = d['turn']
            a['model'] = d['model']
            a['alt_ajan'] = d['alt_ajan']
            a['onbellek_okuma'] = d['onbellek_okuma']
            a['tepe_oturum'] = next(iter(d['oturum'].values()), 0.0)
        out['pencere'][k] = {'gozlem': len(g), 'ozet': ozet(ar), 'araliklar': ar}
    return out


# ── FAZ 4: panel kartı (/api/limit-kutu) ───────────────────────────────────────────

KUTU_TTL = 60.0            # sn — tarama motor önbelleğinden gelir ama medyan tüm geçmişten
KUTU_EN_COK_OTURUM = 5
_kutu_onbellek = {'t': 0.0, 'v': None}
_kutu_kilit = __import__('threading').Lock()


def kutu(now=None, seri_dosya=None, masaustu=None, turns=None) -> dict:
    """Panel kartı: "son 5 saatte limitin %'sini ne yedi" + oran + temel + bayrak.

    Yan etkisiz, ağsız: yalnız seri dosyası, masaüstü geçmişi (salt-okuma) ve transcript'ler.
    Oturum kimliği taşımaz — oturumlar yalnız pay olarak, büyükten küçüğe.

    durum: 'gozlem-yok' (hiç yüzde geçmişi yok) · 'bayat' (son gözlem bu pencereden değil) ·
           'kisa' (bu pencerede < ASGARI_PUAN puanlık kesintisiz aralık, oran yok) · 'tamam'
    """
    import statistics
    import time as _time
    from . import engine
    now = float(now if now is not None else _time.time())
    pen = 'five_hour'
    uzun = PENCERE_SN[pen]
    g = gozlemler(pen, seri_dosya, masaustu)
    out = {'pencere': pen, 'durum': 'gozlem-yok', 'son': None, 'pencereBasi': None,
           'dokum': None, 'oran': None, 'temel': None, 'kat': None, 'bayrak': False,
           'esik': HIZLI_ESIK, 'olculen': 0, 'cv': None, 'sonBayrak': None,
           'asgariPuan': ASGARI_PUAN}
    if not g:
        return out
    son = g[-1]
    out['son'] = {'p': son['p'], 'yasSn': max(0, round(now - son['t'])), 'kaynak': son['k']}
    if turns is None:
        turns = engine._scan_turns(int((g[0]['t'] - 6 * 3600) * 1000))
    ar = olc(g, turns, pen)
    oz = ozet(ar)
    out['olculen'], out['cv'] = oz['olculen'], oz['cv']
    v = [a['oran'] for a in ar if a['oran']]
    if v:
        out['temel'] = round(statistics.median(v[-TEMEL_KAYAN:]), 1)
    bay = [a for a in ar if a['bayrak']]
    if bay:
        out['sonBayrak'] = {'yasSn': max(0, round(now - bay[-1]['t1'])), 'kat': bay[-1]['kat']}

    # Bu pencere: sıfırlanma anı biliniyorsa ondan geriye 5 saat; bilinmiyorsa son 5 saat.
    guncel = (son['r'] is not None and son['r'] > now) or \
             (son['r'] is None and now - son['t'] <= AZAMI_BOSLUK_SN)
    if not guncel:
        out['durum'] = 'bayat'
        return out
    bas = son['r'] - uzun if son['r'] is not None else now - uzun
    out['pencereBasi'] = round(bas)
    ms0, ms1 = bas * 1000, now * 1000
    d = aralik_dokumu([x for x in turns if ms0 < x.ms <= ms1])
    oturumlar = list(d.pop('oturum').values())
    d['oturumSayisi'] = len(oturumlar)
    d['oturumlar'] = oturumlar[:KUTU_EN_COK_OTURUM]
    out['dokum'] = d
    # Bu pencerenin oranı = son ölçülen aralık, eğer bu pencerenin içinde ve son gözlemde bitiyorsa.
    a = ar[-1] if ar else None
    if a and a['t1'] == son['t'] and a['t0'] >= bas - AYNI_PENCERE_SN and a['oran']:
        out.update(durum='tamam', oran=a['oran'], kat=a['kat'], bayrak=a['bayrak'])
        if a['temel'] is not None:
            out['temel'] = a['temel']
    else:
        out['durum'] = 'kisa'
    return out


def kutu_onbellekli(**kw) -> dict:
    """kutu() + KUTU_TTL'lik tek uçuş önbelleği (panel her yenilemede çağırır)."""
    import time as _time
    with _kutu_kilit:
        if _kutu_onbellek['v'] is not None and _time.time() - _kutu_onbellek['t'] < KUTU_TTL:
            return _kutu_onbellek['v']
        try:
            v = kutu(**kw)
        except Exception as e:                      # kart düşer, panel düşmez
            v = {'pencere': 'five_hour', 'durum': 'hata', 'hata': type(e).__name__}
        _kutu_onbellek.update(t=_time.time(), v=v)
        return v


# ── FAZ 5: kanıt dışa aktarımı (+ Codex, deneysel) ─────────────────────────────────────

KANIT_BICIM = 'usage-tracker-limit-evidence/1'
KANIT_PENCERE = {'5h': 'five_hour', '7d': 'seven_day'}
CODEX_GURULTU_CV = 0.5     # PLAN §6: Codex CV'si bunu aşarsa oran/bayrak verilmez


def _utc_dk(t):
    """Epoch sn → 'YYYY-MM-DDTHH:MMZ' (UTC; biçim saniyeyi taşımaz → dakikaya aşağı yuvarlanır)."""
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def _baskin(paylar):
    return next(iter(paylar), None) if paylar else None


def _kanit_araliklari(ar, model_adi=None):
    """olc() çıktısı → yalnız izinli alanlar. Oturum, yol, istem, kimlik buraya HİÇ girmez:
    alan listesi beyaz listedir, kaynak sözlükten kopyalama yapılmaz."""
    out = []
    for a in ar:
        if a['oran'] is None:
            continue
        d = a['dokum']
        modeller = {}
        for m, v in d['model'].items():
            ad = (model_adi or _claude_aile)(m)
            modeller[ad] = modeller.get(ad, 0.0) + v
        modeller = {m: round(v, 2) for m, v in modeller.items() if v >= 0.005}
        out.append({
            'start': _utc_dk(a['t0']), 'end': _utc_dk(a['t1']),
            'points': a['puan'], 'minutes': round(a['sure_sn'] / 60),
            'units_per_pct': round(a['oran']), 'baseline': round(a['temel']) if a['temel'] else None,
            'ratio_to_baseline': a['kat'], 'flagged_2x_faster': a['bayrak'],
            'models': modeller, 'subagent_share': round(d['alt_ajan'], 2),
            'cache_read_share': round(d['onbellek_okuma'], 2),
            'client_version': _surum_adi(_baskin(d.get('surum', {}))),
        })
    return out


def _claude_aile(m):
    """Kanıtta model adı yalnız aile: opus/sonnet/haiku/fable, gerisi 'other' (ör. '<synthetic>',
    vekil sunucuların özel adları)."""
    return m if m in ('opus', 'sonnet', 'haiku', 'fable') else 'other'


def _codex_aile(m):
    """Codex model adı yalnız 'gpt-…'/'o…' biçimindeyse ve kısaysa geçer; gerisi 'other'."""
    import re
    from .providers import codex as _codex
    k = _codex._short(m)
    return k if re.fullmatch(r'(gpt|o\d|codex)[a-z0-9.\-]{0,30}', k) else 'other'


def _surum_adi(v):
    """Yalnız sürüm biçimindeki değer geçer (ör. 2.1.197); başka her şey None."""
    import re
    return v if isinstance(v, str) and re.fullmatch(r'\d+(\.\d+){1,3}', v) else None


def _codex_kanit(pencere, veri=None):
    from .providers import codex as _codex
    from .engine import Turn
    if veri is None:
        veri = _codex.limit_gecmisi(pencere)
    if veri is None:
        return {'experimental': True, 'status': 'no-data'}
    gozlem, olaylar = veri
    turns = [Turn(ms, model, float(inp + out), inp + out, inp, out, 0, 0, '')
             for ms, model, inp, out in olaylar]
    ar = olc(gozlem, turns, pencere, model_kisa=_codex._short)
    oz = ozet(ar)
    r = {'experimental': True, 'unit': 'uncached input + output tokens',
         'intervals': oz['aralik'], 'measured': oz['olculen'], 'cv': oz['cv']}
    if oz['olculen'] < 2:
        r['status'] = 'no-data'
    elif oz['cv'] is None or oz['cv'] > CODEX_GURULTU_CV:
        r['status'] = 'noisy'           # oran ve bayrak bilerek verilmez
    else:
        r.update(status='ok', median_units_per_pct=round(oz['medyan']), flagged=oz['bayrak'],
                 flagged_intervals=[a for a in _kanit_araliklari(ar, _codex_aile)
                                    if a['flagged_2x_faster']])
    return r


def kanit(pencere='5h', seri_dosya=None, masaustu=None, turns=None, codex=True,
          codex_veri=None, arac_surumu=None) -> dict:
    """Paylaşılabilir, isimsiz limit kanıtı. Ağsız, salt-okuma.

    Yalnız: UTC saat (dakika), oran, temel, kat, bayrak, model kısa adı, istemci sürümü, paylar.
    İstem, yol, proje adı, oturum kimliği, git dalı, hesap (org) YOK — çıktı beyaz listeden
    kurulur ve basılmadan önce `sizinti_bul` ile taranır (cmd_kanit).
    """
    from . import engine
    w = KANIT_PENCERE[pencere]
    g = gozlemler(w, seri_dosya, masaustu)
    if turns is None:
        turns = engine._scan_turns(int((g[0]['t'] - 6 * 3600) * 1000)) if g else []
    ar = olc(g, turns, w)
    oz = ozet(ar)
    out = {
        'format': KANIT_BICIM, 'tool': f'usage-tracker {arac_surumu}' if arac_surumu else 'usage-tracker',
        'window': pencere,
        'method': ('units_per_pct = weighted local Claude Code units consumed per 1% of the '
                   f'official limit, over continuous stretches of >= {ASGARI_PUAN} points without '
                   'a reset. weights: opus/fable 1, sonnet 0.25, haiku 0.05 x (out*4 + in + '
                   f'cache_write + cache_read*0.1). flagged = <= {HIZLI_ESIK} x rolling median of '
                   'earlier stretches. Usage outside this machine (claude.ai, other computers) is '
                   'not in the units.'),
        'claude': {
            'summary': {'intervals': oz['aralik'], 'measured': oz['olculen'],
                        'median_units_per_pct': round(oz['medyan']) if oz['medyan'] else None,
                        'cv': oz['cv'], 'flagged': oz['bayrak'],
                        'first': _utc_dk(ar[0]['t0']) if ar else None,
                        'last': _utc_dk(ar[-1]['t1']) if ar else None},
            'intervals': _kanit_araliklari(ar),
        },
    }
    if codex:
        out['codex'] = _codex_kanit(w, codex_veri)
    return out


def sizinti_bul(metin) -> list:
    """Kanıt metninde kişisel/tanımlayıcı veri ara → bulunan desen adları (boş = temiz).

    Son savunma hattı: çıktı zaten beyaz listeden kuruluyor; bu tarama bir alan kazara
    eklenirse kanıtın basılmasını durdurur.
    """
    import getpass
    import re
    bulgu = []
    ev = str(_paths.home())
    if ev and len(ev) > 1 and ev in metin:
        bulgu.append('home-dir')
    for ad, desen in (
            ('unix-path', r'(?<![\w.])/(home|Users|root|mnt|tmp|var|opt)/'),
            ('windows-path', r'[A-Za-z]:\\\\'),
            ('uuid', r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'),
            ('email', r'[\w.+-]+@[\w-]+\.[\w.]+'),
            ('org-key', r'"org(_?id|anization)?"\s*:'),
            ('org-id', r'\borg-[A-Za-z0-9]{6,}'),
            ('api-key', r'\bsk-[A-Za-z0-9_-]{10,}'),
            ('message-id', r'\b(msg|req|toolu)_[A-Za-z0-9]{8,}'),
            ('session-key', r'"(sessionId|session_id|cwd|gitBranch|prompt|content)"\s*:')):
        if re.search(desen, metin):
            bulgu.append(ad)
    try:
        kul = getpass.getuser()
    except Exception:
        kul = ''
    if len(kul) >= 4 and re.search(r'\b' + re.escape(kul) + r'\b', metin, re.I):
        bulgu.append('username')
    return bulgu


def kanit_metni(k) -> str:
    """İnsan okunur (İngilizce — bir GitHub başlığına yapıştırılır) özet."""
    c = k['claude']['summary']
    cv = f"{c['cv'] * 100:.0f}%" if c['cv'] is not None else '—'
    s = [f"{k['tool']} · limit evidence · window {k['window']}",
         f"Claude: {c['measured']} measured stretches ({c['first']} → {c['last']}), "
         f"median {c['median_units_per_pct']} units per 1%, CV {cv}, flagged 2x-faster: {c['flagged']}",
         '', 'start (UTC)        min  points  units/1%   x usual  flag  models  client']
    for a in k['claude']['intervals']:
        m = ' '.join(f'{n} {v:.0%}' for n, v in a['models'].items())
        s.append(f"{a['start']}  {a['minutes']:4d}  {a['points']:6.0f}  {a['units_per_pct']:9d}  "
                 f"{a['ratio_to_baseline'] if a['ratio_to_baseline'] is not None else '—':>7}  "
                 f"{'2x' if a['flagged_2x_faster'] else '':4}  {m}  {a['client_version'] or '—'}")
    cx = k.get('codex')
    if cx:
        s.append('')
        if cx['status'] == 'noisy':
            s.append(f"Codex (experimental): noisy — {cx['measured']} stretches, CV "
                     f"{cx['cv'] * 100:.0f}% > {CODEX_GURULTU_CV:.0%}; no ratio or flag reported.")
        elif cx['status'] == 'ok':
            s.append(f"Codex (experimental): median {cx['median_units_per_pct']} tokens per 1%, "
                     f"CV {cx['cv'] * 100:.0f}%, flagged {cx['flagged']}")
        else:
            s.append('Codex (experimental): no data.')
    s += ['', 'method: ' + k['method']]
    return '\n'.join(s)


def _saat(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime('%m-%d %H:%M')


def _sinav_yazdir(s):
    print(f"turn: {s['turn']} · {'dilimli (≥5 puan)' if s['dilim'] else 'kesintisiz parça'}")
    for k, v in s['pencere'].items():
        o = v['ozet']
        cv = f"%{o['cv'] * 100:.0f}" if o['cv'] is not None else '—'
        print(f"\n[{k}] gözlem {v['gozlem']} · aralık {o['aralik']} · ölçülen {o['olculen']} · "
              f"medyan {o['medyan']} birim/%1 · CV {cv} · bayrak {o['bayrak']} · "
              f"yerel kayıt yok {o['yerel_kayit_yok']}")
        print('  başlangıç(UTC)  süre_dk  %0→%1   turn  oran      kat    durum')
        for a in v['araliklar']:
            print(f"  {_saat(a['t0'])}     {a['sure_sn'] / 60:6.0f}  {a['p0']:4.0f}→{a['p1']:<4.0f} "
                  f"{a['turn']:5d}  {a['oran'] if a['oran'] is not None else '—':<9} "
                  f"{a['kat'] if a['kat'] is not None else '—':<6} "
                  f"{'⚑ 2× HIZLI' if a['bayrak'] else a['durum']}")
        for a in v['araliklar']:
            if a['bayrak']:
                print(f"  ⚑ {_saat(a['t0'])}: oran {a['oran']} = temelin {a['kat']}×'i · "
                      f"model {a['model']} · alt ajan %{a['alt_ajan'] * 100:.0f} · "
                      f"önbellek-okuma %{a['onbellek_okuma'] * 100:.0f} · "
                      f"tepe oturum %{a['tepe_oturum'] * 100:.0f}")


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'sinav':
        s = sinav(dilim='--dilim' in sys.argv)
        if '--json' in sys.argv:
            print(json.dumps(s, ensure_ascii=False, indent=1))
        else:
            _sinav_yazdir(s)
        sys.exit(0)
    g = geri_al()
    zamanlar = sorted({o['t'] for o in g['ornekler']})
    print(f"masaüstü geçmişi: {g['durum']} · {len(zamanlar)} örnek · {len(g['ornekler'])} gözlem"
          + (f" · {g['neden']}" if g['neden'] else ''))
    print(f"seri: {yol()} · {len(oku())} gözlem")

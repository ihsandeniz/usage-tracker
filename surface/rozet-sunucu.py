#!/usr/bin/env python3
"""Telefon/saat rozeti — tailnet'e açılan küçük yüzey.

NEDEN AYRI BİR SUNUCU: `server.py` loopback-only ve Host allowlist'i var
(DNS-rebinding koruması, `server.py:133`). `tailscale serve` proxy'lediğinde
Host `<makine>.ts.net` olur ve panel **403** döner — ölçüldü. Allowlist'i
gevşetmek panelin TAMAMINI (harcama, $ rakamları, model listesi) tailnet'e
açardı. Bunun yerine: yalnız yüzdeleri taşıyan ~270 baytlık bir yüzey.

🔴 `spend` alanları BİLEREK yok. Bu yüzey ileride `tailscale funnel` ile
public HTTPS'e çıkabilir; oraya gelir bilgisi düşmemeli.

Kurulum:
    tailscale serve --bg --set-path /usage http://localhost:8771
    → https://<makine>.ts.net/usage/rozet.json

KWGT/KWCH formülü:
    $wg("https://<makine>.ts.net/usage/rozet.json", json, .text)$
"""
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get('ROZET_PORT', '8771'))
KAYNAK = os.environ.get('ROZET_KAYNAK', 'http://127.0.0.1:8770/v1/usage')
YOL = '/rozet.json'


def _bar(kart: dict, kapsam: str) -> dict:
    """Tek bir limit barından yüzde + reset saniyesi.

    ⚠️ Alan adı `resetInSec` — TEKİL. `resetsInSec` yazıp sessizce null almak
    bu turda bir kez yapıldı ve ölçümle yakalandı.
    """
    bar = (kart.get('limits') or {}).get(kapsam) or {}
    return {'pct': bar.get('pct'), 'reset': bar.get('resetInSec'),
            'stale': bool(bar.get('stale'))}


def rozet() -> dict:
    with urllib.request.urlopen(KAYNAK, timeout=10) as r:
        wire = json.load(r)

    kartlar = {p.get('id'): p for p in wire.get('providers', [])}
    esik = (kartlar.get('claude', {}).get('limits') or {}).get('thresholds') or {}
    warn, crit = esik.get('warn', 75), esik.get('crit', 90)

    cikti = {'at': int(time.time() * 1000)}
    yuzdeler = []
    for kimlik in ('claude', 'codex'):
        kart = kartlar.get(kimlik)
        if not kart:
            continue
        for kapsam in ('session', 'weekly'):
            d = _bar(kart, kapsam)
            cikti[f'{kimlik}_{kapsam}'] = d['pct']
            cikti[f'{kimlik}_{kapsam}_reset'] = d['reset']
            # Bayat bar manşete girmez: yüzde ölçülmüş olsa da eskimişse
            # "şu an %X" demek yalan olur.
            if d['pct'] is not None and not d['stale']:
                yuzdeler.append(d['pct'])

    # Manşet = en yüksek yüzde — waybar-usage.sh ile AYNI tanım. Yeni bir yüzey
    # kendi "ne kadar dolu" tanımını icat etmemeli (FAZ 7b dersi).
    tepe = max(yuzdeler) if yuzdeler else None
    cikti['pct'] = tepe
    cikti['text'] = f'◐ {tepe:.0f}%' if tepe is not None else '○ —'
    # pct yoksa 'unknown' — 'ok' demek, engellenmek istenen işi başlatır.
    cikti['class'] = ('crit' if tepe >= crit else 'warn' if tepe >= warn else 'ok') \
        if tepe is not None else 'unknown'

    # --- hazır metin alanları ---------------------------------------------
    # 🔑 KWGT aynı ifade içinde İKİNCİ `wg()` çağrısını null döndürüyor
    # ("err: read(...) must not be null", canlı ölçüldü 2026-09-11). Bu yüzden
    # birleştirmeyi istemciye bırakmıyoruz: her satır TEK alanda hazır gelir,
    # widget tarafında tek `wg()` yeter. Biçim sunucuda = tüm yüzeylerde aynı.
    cikti['claude_text'] = _satir('Claude', cikti.get('claude_session'),
                                  cikti.get('claude_weekly'))
    cikti['codex_text'] = _satir('Codex', cikti.get('codex_session'),
                                 cikti.get('codex_weekly'))
    # Kart YOKSA satırdan düşer; kart VAR ama yüzde ölçülemediyse '—' ile durur.
    # İkisi farklı: "Codex kurulu değil" ile "Codex penceresi sıfırlandı" aynı
    # ekranda aynı görünmemeli.
    cikti['kirilim'] = ' · '.join(
        _kisa(ad, cikti.get(f'{kimlik}_session'))
        for ad, kimlik in (('Claude', 'claude'), ('Codex', 'codex'))
        if f'{kimlik}_session' in cikti)
    cikti['reset_text'] = _sure(cikti.get('claude_session_reset'))
    return cikti


def _yuzde(v) -> str:
    """None → '—'. '0%' yazmak 'ölçemedim'i 'boş' gibi gösterirdi."""
    return '—' if v is None else f'{v:.0f}%'


def _kisa(ad, pct) -> str:
    return f'{ad} {_yuzde(pct)}'


def _satir(ad, oturum, hafta) -> str:
    return f'{ad} {_yuzde(oturum)} · hafta {_yuzde(hafta)}'


def _sure(saniye) -> str:
    """4 sa 34 dk. Gün kademesi var — haftalık reset '274 dk' diye basılmasın
    (FAZ 7'nin countdown kusurunun aynısı)."""
    if not saniye or saniye < 0:
        return '—'
    dk = int(saniye // 60)
    if dk < 60:
        return f'{dk} dk'
    sa, dk = divmod(dk, 60)
    if sa < 24:
        return f'{sa} sa {dk} dk'
    gun, sa = divmod(sa, 24)
    return f'{gun} gün {sa} sa'


class Handler(BaseHTTPRequestHandler):
    server_version = 'usage-rozet/1.0'

    def log_message(self, fmt, *args):
        pass                                      # sessiz — journal'ı kirletme

    def do_GET(self):
        if self.path.split('?')[0] != YOL:
            self.send_error(404)                  # tek yüzey, tek yol
            return
        try:
            veri = rozet()
        except Exception as e:
            # Panel kapalıysa rozet SUSMAZ ama YALAN da söylemez.
            veri = {'text': '○ —', 'pct': None, 'class': 'unknown',
                    'at': int(time.time() * 1000), 'error': type(e).__name__}
        govde = json.dumps(veri, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(govde)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(govde)


if __name__ == '__main__':
    # Host allowlist YOK — tailscale serve Host'u <makine>.ts.net olarak iletir
    # ve allowlist onu reddederdi (panelin 403'ünün sebebi tam olarak buydu).
    # Karşılığında yüzey tek yol + salt-okunur + $ taşımıyor.
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

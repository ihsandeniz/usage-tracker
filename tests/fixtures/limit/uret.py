#!/usr/bin/env python3
"""Limit Kara Kutusu sentetik fikstürlerini üret (deterministik; rastgelelik yok).

    python3 tests/fixtures/limit/uret.py      # bu dizine yazar

Gerçek veri KULLANILMAZ: hesap kimliği, yol, istem yok. Beklenen sayılar README.md'de.
"""
import json
from pathlib import Path

DIZIN = Path(__file__).resolve().parent
ORG_KANARYA = 'org-kanarya-7f3e9c1d'          # masaüstü dosyasındaki hesap alanı; hiçbir çıktıda görünmemeli

T0_MS = 1_790_105_765_502                       # 2026-09-22, gerçek dosyanın ilk örneğiyle aynı gün
ADIM_MS = 300_000                               # uygulamanın 5 dk aralığı
ORNEK = 465
BOSLUK_SONRASI = (120, 300)                     # bu örneklerden önce uygulama kapalı: 6 sa boşluk
BOSLUK_MS = 6 * 3_600_000


def masaustu():
    samples, t, fh, sd = [], T0_MS, 0, 10
    for i in range(ORNEK):
        if i in BOSLUK_SONRASI:
            t += BOSLUK_MS
            fh = 0                              # boşlukta 5 saat penceresi döndü
        samples.append({'t': t, 'org': ORG_KANARYA, 'u': {'fh': fh, 'sd': sd}})
        t += ADIM_MS
        if i % 3 == 2:
            fh = min(100, fh + 1)
        if i % 20 == 19:
            sd = min(100, sd + 1)
        if fh >= 60:                            # pencere sıfırlandı
            fh = 0
    return {'version': 2, 'samples': samples}


def masaustu_v3():
    """Uygulama biçimi değiştirirse: içe alım 'okunamadi' demeli, çökmemeli."""
    return {'version': 3, 'history': [{'ts': T0_MS, 'org': ORG_KANARYA, 'usage': {'5h': 4}}]}


# ── seri senaryosu (FAZ 3 sapma bayrağı için) ──
# t0 = 2026-03-23 09:00 UTC ("Mart 23" — claude-code#38335 şikâyetlerinin başladığı gün)
S0 = 1_774_256_400
R1 = S0 + 5 * 3600                              # 1. pencere sıfırlanması
R2 = R1 + 5 * 3600


def seri():
    s = []

    def ekle(t, p, r, k='statusline'):
        s.append({'t': t, 'k': k, 'w': {'five_hour': {'p': p, 'r': r}}})
    # sakin aralık: 4 saatte %0 → %20 (12 dk'da 1 puan)
    for i in range(21):
        ekle(S0 + i * 720, float(i), R1)
    # sıfırlanma: yüzde düşer, resets_at ileri atlar
    ekle(R1 + 30, 0.0, R2)
    # "Mart 23": aynı iş temposunda yüzde 2 kat hızlı (6 dk'da 1 puan), 2 saatte %0 → %20
    for i in range(1, 21):
        ekle(R1 + 30 + i * 360, float(i), R2)
    return s


# #52326: yüzde alanına sıfırlanma anı (epoch sn) yazılabiliyor
BOZUK_SERI = [
    '{"t": %d, "k": "statusline", "w": {"five_hour": {"p": %d, "r": %d}}}' % (R2 - 600, R2, R2),
    '{"t": %d, "k": "statusline", "w": {"five_hour": {"p": -3, "r": %d}}}' % (R2 - 500, R2),
    '{"t": %d, "k": "statusline", "w": {"five_hour": {"p": "41", "r": %d}}}' % (R2 - 400, R2),
    '{"t": "dun", "k": "statusline", "w": {"five_hour": {"p": 4, "r": %d}}}' % R2,
    'yarım satır {"t": 1',
]


def statusline_bozuk():
    return {'session_id': 'sentetik-bozuk',
            'rate_limits': {'five_hour': {'used_percentage': R2, 'resets_at': R2},
                            'seven_day': {'used_percentage': 12, 'resets_at': R2 + 6 * 86400}}}


# ── 3 oturumluk transcript'ler (FAZ 2 aralık birleştirme için) ──
# Her turn: input 100 · output 50 · cache_creation 1000 · cache_read 5000.
TURN_USAGE = {'input_tokens': 100, 'output_tokens': 50,
              'cache_creation_input_tokens': 1000, 'cache_read_input_tokens': 5000}
OTURUMLAR = (
    # ad, model, turn sayısı, alt ajan mı
    ('a', 'claude-opus-5-5', 10, False),
    ('b', 'claude-sonnet-5-5', 20, False),
    ('c', 'claude-opus-5-5', 5, True),
)


def transcript(ad, model, n, yan):
    satirlar = []
    for i in range(n):
        ts = S0 + 60 + i * 600 + {'a': 0, 'b': 7, 'c': 13}[ad]
        satirlar.append({
            'type': 'assistant', 'sessionId': f'sentetik-{ad}', 'isSidechain': yan,
            'requestId': f'req_{ad}{i:03d}', 'version': '2.1.197',
            'timestamp': f'{_iso(ts)}',
            'message': {'id': f'msg_{ad}{i:03d}', 'model': model, 'role': 'assistant',
                        'usage': dict(TURN_USAGE)},
        })
    if ad == 'a':
        # aynı mesaj, aynı istek: transcript'te tekrar yazılmış → 1 sayılmalı
        satirlar.append(dict(satirlar[0]))
    return satirlar


def _iso(sn):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(sn, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def yaz():
    (DIZIN / 'masaustu-gecmis.json').write_text(json.dumps(masaustu()), encoding='utf-8')
    (DIZIN / 'masaustu-gecmis-v3.json').write_text(json.dumps(masaustu_v3()), encoding='utf-8')
    (DIZIN / 'seri-senaryo.jsonl').write_text(
        ''.join(json.dumps(x, separators=(',', ':')) + '\n' for x in seri()), encoding='utf-8')
    (DIZIN / 'seri-bozuk.jsonl').write_text(''.join(x + '\n' for x in BOZUK_SERI), encoding='utf-8')
    (DIZIN / 'statusline-bozuk.json').write_text(json.dumps(statusline_bozuk()), encoding='utf-8')
    td = DIZIN / 'transcripts'
    td.mkdir(exist_ok=True)
    for ad, model, n, yan in OTURUMLAR:
        (td / f'oturum-{ad}.jsonl').write_text(
            ''.join(json.dumps(x) + '\n' for x in transcript(ad, model, n, yan)), encoding='utf-8')


if __name__ == '__main__':
    yaz()

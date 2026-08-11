#!/usr/bin/env python3
"""
Sağlayıcı uçlarını **kimliksiz** yokla: hangi URL gerçekten var?

    python3 packaging/probe_endpoints.py            # tabloyu bas
    python3 packaging/probe_endpoints.py --check    # ölü uç varsa exit 1

Neden: bu proje anahtarı olmayan uçlara adaptör yazdı ve yıllarca (README'de "candidate"
etiketiyle) hiç çalışmayacak kod taşıdı. Anahtar olmadan **şema** doğrulanamaz — ama
**URL'nin varlığı** doğrulanabilir ve bu, uydurulmuş bir ucu anında yakalar:

    401 / 403 → uç var, anahtar bekliyor          ✅
    400 / 422 → uç var, parametre bekliyor        ✅
    404       → uç YOK; anahtar gelse de çalışmaz 🔴

2026-08-11'de bu araç Together AI adaptörünün tamamen kurgu olduğunu ortaya çıkardı:
denenen 7 yolun hepsi 404 + HTML döndürdü, çünkü Together böyle bir uç yayınlamıyor.

Hiçbir kimlik bilgisi gönderilmez. Salt-okunur, GET, sabit URL listesi.
"""
import argparse
import concurrent.futures as cf
import sys
import urllib.error
import urllib.request

# (sağlayıcı, URL, adaptörde kullanılıyor mu)
ENDPOINTS = [
    ('openrouter',  'https://openrouter.ai/api/v1/key', True),
    ('openrouter',  'https://openrouter.ai/api/v1/credits', True),
    ('openai',      'https://api.openai.com/v1/organization/costs', True),
    ('deepseek',    'https://api.deepseek.com/user/balance', True),
    ('elevenlabs',  'https://api.elevenlabs.io/v1/user/subscription', True),
    ('huggingface', 'https://huggingface.co/api/whoami-v2', True),
    ('huggingface', 'https://huggingface.co/api/quota', True),
    ('deepinfra',   'https://api.deepinfra.com/v1/me', True),
    ('novita',      'https://api.novita.ai/v3/user', True),
    ('together',    'https://api.together.xyz/v1/models', True),
    # Together'ın bakiye ucu olmadığının kanıtı — kayıt olarak duruyor, adaptör denemiyor.
    ('together',    'https://api.together.xyz/v1/account', False),
    ('together',    'https://api.together.xyz/v1/usage', False),
]

ALIVE = {400, 401, 403, 405, 422}
DEAD = {404, 410}


def probe(item, timeout=15):
    provider, url, used = item
    req = urllib.request.Request(url, headers={
        'User-Agent': 'usage-tracker-probe',
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return provider, url, used, r.status, ''
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read(120).decode('utf-8', 'replace').replace('\n', ' ').strip()
        except Exception:
            pass
        return provider, url, used, e.code, body
    except Exception as e:
        return provider, url, used, None, f'{type(e).__name__}: {e}'


def verdict(code):
    if code in ALIVE:
        return '✅ var'
    if code in DEAD:
        return '🔴 YOK'
    if code == 200:
        return '⚠️ auth istemiyor'
    return '❓ ulaşılamadı'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--check', action='store_true',
                    help='adaptörün KULLANDIĞI bir uç ölü ise exit 1')
    ap.add_argument('--timeout', type=float, default=15)
    args = ap.parse_args(argv)

    with cf.ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(lambda i: probe(i, args.timeout), ENDPOINTS))

    print(f"{'sağlayıcı':13s} {'kod':>4s}  {'hüküm':18s} {'kullanılıyor':12s} url")
    print('-' * 104)
    broken = []
    for provider, url, used, code, body in rows:
        print(f'{provider:13s} {str(code):>4s}  {verdict(code):18s} '
              f'{("evet" if used else "kayıt"):12s} {url.split("://")[1]}')
        if body and code in DEAD:
            print(f'{"":13s} {"":>4s}  {"":18s} {"":12s}   ↳ {body[:72]}')
        if used and code in DEAD:
            broken.append(f'{provider}: {url} → {code}')

    if broken:
        print('\n🔴 Adaptörün kullandığı ölü uçlar:')
        for b in broken:
            print(f'   {b}')
        print('   Bu adaptör anahtar gelse de çalışmaz. Yolu düzelt ya da iddiayı geri çek.')
    else:
        print('\n✅ Adaptörlerin kullandığı her uç yanıt veriyor.')

    print('\nNot: bu, ŞEMANIN doğru olduğunu göstermez — yalnız URL\'nin var olduğunu.\n'
          'Alan adları ancak gerçek bir anahtarla görülebilir.')
    return 1 if (args.check and broken) else 0


if __name__ == '__main__':
    raise SystemExit(main())

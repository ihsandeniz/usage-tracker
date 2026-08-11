#!/usr/bin/env python3
"""
Sentetik demo verisi üreteci. USAGE_DEMO=1 ayarlıysa engine ve live modülleri demo verisi döndürür.
Amaç: (a) README için mahremiyet-güvenli screenshot, (b) yeni kullanıcı kendi verisi olmadan aracı deneyebilsin.

Veri kaynakları:
- compute_usage() → gerçekçi limit %'leri (oturum ~%35, hafta ~%52, model ~%40)
- compute_spend() → bugün ~$12-18, dün ~$15, 30 gün ~$280-350, deterministik sparkline
- collect() → openrouter (demo bakiye) + codex (offline) + ollama (offline)

Determinizm: aynı çalıştırmada aynı sonuç. Math.random() yok, timestamp-bağımlı hesaplamalar yok.
Schema: engine fonksiyonlarıyla AYNI yapı ve alan adları.
"""
import time
from datetime import datetime, timedelta


# ── Sentez kontrol — demo veriye sabit bir seed'lik determinizm ──
def _demo_seed() -> dict:
    """Her çalıştırmada aynı veri döndürmek için sabit tarih/saat temelinde dizin oluştur."""
    # Sabit referans zamanı: 2025-07-20 14:30:00 UTC (arbitrer, repeatable)
    ref_ts = 1753079400  # datetime(2025, 7, 20, 14, 30, 0).timestamp()
    now_ms = int(time.time() * 1000)
    offset_days = (now_ms - ref_ts * 1000) // (86_400_000)  # bugünden kaç gün geçti
    return {
        'ref_ts': ref_ts,
        'now_ms': now_ms,
        'offset_days': offset_days,
        'now': datetime.fromtimestamp(now_ms / 1000),
    }


# ── FAZ 0: LİMİTLER ──────────────────────────────────────────────────────────
def compute_usage() -> dict:
    """Sentetik limit paneli. oturum ~%35, hafta ~%52, model ~%40."""
    seed = _demo_seed()
    now_ms = seed['now_ms']

    # Reset zamanları: yakında gelmeli ama başta değil
    # Oturum: 5 saat, reset 2.5 saat sonra
    # Hafta: 7 gün, reset 3.5 gün sonra
    sess_reset_ms = now_ms + 2.5 * 3600 * 1000
    week_reset_ms = now_ms + 3.5 * 86_400 * 1000

    # Forecast helpers (demo için tutarlı hesaplar)
    def demo_forecast(pct, reset_ms, window_ms, now=now_ms):
        if pct < 10:
            return {'willExceed': False, 'etaMs': None, 'etaText': None}
        window_start = reset_ms - window_ms
        elapsed_ms = now - window_start
        if elapsed_ms < max(window_ms * 0.05, 300_000):
            return {'willExceed': False, 'etaMs': None, 'etaText': None}
        burn_rate = pct / elapsed_ms
        time_to_full = (100 - pct) / burn_rate
        full_at = now + time_to_full
        if full_at >= reset_ms:
            return {'willExceed': False, 'etaMs': None, 'etaText': None}
        eta_sec = int(time_to_full / 1000)
        hours = eta_sec // 3600
        minutes = (eta_sec % 3600) // 60
        parts = []
        if hours > 0:
            parts.append(f'{hours}s')
        if minutes > 0 or hours == 0:
            parts.append(f'{minutes}dk')
        eta_text = ' '.join(parts) if parts else '≈ şimdi'
        return {'willExceed': True, 'etaMs': int(full_at), 'etaText': f'≈ {eta_text} sonra dolar'}

    sess_win = 5 * 3600 * 1000  # 5 saat
    week_win = 7 * 24 * 3600 * 1000  # 7 gün

    return {
        'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        'calibrated': True,
        'source': 'demo',
        'session': {
            'units': 3847,
            'rawTokens': 98234,
            'budget': 11000,
            'pct': 35.0,
            'resetAtMs': int(sess_reset_ms),
            'resetInSec': int((sess_reset_ms - now_ms) / 1000),
            'forecast': demo_forecast(35.0, sess_reset_ms, sess_win),
        },
        'weeklyAll': {
            'units': 5624,
            'rawTokens': 243891,
            'budget': 10800,
            'pct': 52.1,
            'resetAtMs': int(week_reset_ms),
            'resetInSec': int((week_reset_ms - now_ms) / 1000),
            'forecast': demo_forecast(52.1, week_reset_ms, week_win),
        },
        'weeklyModel': {
            'units': 2156,
            'rawTokens': 89234,
            'budget': 5400,
            'pct': 39.9,
            'resetAtMs': int(week_reset_ms),
            'resetInSec': int((week_reset_ms - now_ms) / 1000),
            'name': 'opus',
            'forecast': demo_forecast(39.9, week_reset_ms, week_win),
        },
        'thresholds': {'warn': 75, 'crit': 90},
        'live': {
            'ok': False,
            'error': 'demo mode — canlı veri kapalı',
            'cached': False,
            'stale': False,
            'rateLimited': False,
            'rateLimitTier': None,
        },
    }


# ── FAZ 1: HARCAMA ──────────────────────────────────────────────────────────
def compute_spend(days: int = 30) -> dict:
    """Sentetik gerçek $ harcama. bugün ~$12-18, dün ~$15, 30 gün ~$280-350."""
    seed = _demo_seed()
    now = seed['now']
    now_ms = seed['now_ms']

    # Harcama değerleri: yaklaşık deterministik trendi yansıt
    # Bugün: morning ~$4, afternoon ~$8-14 → 12-18 toplam
    today_spend = 15.23
    yesterday_spend = 14.87

    # 30 günlük sparkline: artan trend + dalgalanma
    # Gün 1 (30 gün önce) ~$8, son gün ~$18 trend, +/- rastgelelik
    by_day = []
    window_start = now - timedelta(days=days-1)
    total_spend = 0.0

    for i in range(days):
        day_dt = window_start + timedelta(days=i)
        day_key = day_dt.strftime('%Y-%m-%d')

        # Deterministik "rastgelelik": tarih hash'i + offset ile
        day_num = i + 1  # 1-30
        base = 8.0 + (day_num / days) * 10.0  # 8→18 trend
        # Dalgalanma: sin wave + gün numarası hash
        wave = ((day_num * 13) % 100) / 100.0 * 6.0 - 3.0  # -3 to +3
        day_spend = max(0.5, base + wave)

        by_day.append({'day': day_key, 'usd': round(day_spend, 4)})
        total_spend += day_spend

    # Per-model
    # Opus ~%55, Sonnet ~%35, Haiku ~%10 dağılım
    opus_spend = round(total_spend * 0.55, 4)
    sonnet_spend = round(total_spend * 0.35, 4)
    haiku_spend = round(total_spend * 0.10, 4)

    by_model = [
        {
            'model': 'claude-opus-4-8',
            'short': 'opus',
            'usd': opus_spend,
            'source': 'catalog',
            'tokens': {
                'input': int(opus_spend * 1_000_000 / 3.75),
                'output': int(opus_spend * 1_000_000 / 15.0),
                'cache_write': int(opus_spend * 1_000_000 / 4.6875),
                'cache_read': 0,
            },
        },
        {
            'model': 'claude-sonnet-4-6',
            'short': 'sonnet',
            'usd': sonnet_spend,
            'source': 'catalog',
            'tokens': {
                'input': int(sonnet_spend * 1_000_000 / 0.9375),
                'output': int(sonnet_spend * 1_000_000 / 3.75),
                'cache_write': int(sonnet_spend * 1_000_000 / 1.171875),
                'cache_read': 0,
            },
        },
        {
            'model': 'claude-haiku-4-5-20251001',
            'short': 'haiku',
            'usd': haiku_spend,
            'source': 'catalog',
            'tokens': {
                'input': int(haiku_spend * 1_000_000 / 0.08),
                'output': int(haiku_spend * 1_000_000 / 0.24),
                'cache_write': int(haiku_spend * 1_000_000 / 0.1),
                'cache_read': 0,
            },
        },
    ]

    tokens_total = {
        'input': sum(m['tokens']['input'] for m in by_model),
        'output': sum(m['tokens']['output'] for m in by_model),
        'cache_write': sum(m['tokens']['cache_write'] for m in by_model),
        'cache_read': 0,
    }

    return {
        'currency': 'USD',
        'updated': now.strftime('%H:%M:%S'),
        'windowDays': days,
        'today': round(today_spend, 4),
        'yesterday': round(yesterday_spend, 4),
        'total': round(total_spend, 4),
        'tokensTotal': tokens_total,
        'byModel': by_model,
        'byDay': by_day,
        'priceComplete': True,
        'estimatedModels': [],
        'source': 'demo',
        'note': 'Demo verisi — sentetik ama gerçekçi. Gerçek veri USAGE_DEMO ayarlanmadan yüklenir.',
    }


# ── FAZ 2: ÇOK-SAĞLAYICI ──────────────────────────────────────────────────────
def collect(days: int = 30) -> list:
    """Sentetik sağlayıcı kartları. OpenRouter demo bakiye + Codex/Ollama offline."""
    seed = _demo_seed()
    now_ms = seed['now_ms']
    now = seed['now']

    # Demo sparkline: son 30 günün spend trend'i
    demo_byDay = []
    window_start = now - timedelta(days=days-1)
    for i in range(days):
        day_dt = window_start + timedelta(days=i)
        day_key = day_dt.strftime('%Y-%m-%d')
        day_num = i + 1
        base = 0.5 + (day_num / days) * 2.5  # 0.5 → 3.0 trend
        wave = ((day_num * 17) % 100) / 100.0 * 1.0 - 0.5  # -0.5 to +0.5
        day_spend = max(0.05, base + wave)
        demo_byDay.append({'day': day_key, 'usd': round(day_spend, 4)})

    return [
        # OpenRouter: demo bakiye ve limit — GERÇEK ŞEMA
        {
            'id': 'openrouter',
            'name': 'OpenRouter',
            'kind': 'spend',
            'status': 'ok',
            'currency': 'USD',
            'tier': 'pro',
            'spend': {
                'today': 3.45,
                'month': 89.34,  # DÜZELTME: monthly → month (frontend beklentisi)
            },
            'balance': {
                'remaining': 156.23,  # DÜZELTME: üst seviyede (c.balance.remaining)
            },
            'limit': {
                'pct': 38.5,
                'used': 46.20,
                'amount': 120.00,
                'reset': '+18s',
            },
            'byModel': [
                {'model': 'gpt-4-turbo', 'short': 'gpt4', 'usd': 45.67},
                {'model': 'gpt-3.5-turbo', 'short': 'gpt35', 'usd': 43.67},
            ],
            'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        },
        # Codex: DÜZELTME — status=offline ama minimal geçerli kart şeması
        # byModel en az 1 eleman (frontend byModel[0] okuyor)
        {
            'id': 'codex',
            'name': 'Codex',
            'kind': 'tokens',
            'status': 'offline',
            'currency': None,
            'windowDays': 30,
            'tokens': {'total': 0},
            'total': {'usd': 0},
            'today': {'tokens': 0, 'usd': 0},
            'byModel': [
                {'short': '—', 'model': 'n/a'},  # DÜZELTME: boş array → 1 elem
            ],
            'byDay': [],
            'sessions': 0,
            'auth': 'çevrimdışı',
            'usdSource': 'catalog',
            'error': 'Codex sesyonları bulunamadı',
            'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        },
        # Ollama: local/offline — minimal geçerli kart
        {
            'id': 'ollama',
            'name': 'Ollama',
            'kind': 'local',
            'status': 'offline',
            'currency': None,
            'modelCount': 0,
            'models': [],
            'running': [],
            'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        },
        # ElevenLabs: karakter kotası — GERÇEK ŞEMA (kind='quota')
        {
            'id': 'elevenlabs',
            'name': 'ElevenLabs',
            'kind': 'quota',
            'status': 'ok',
            'currency': None,
            'tier': 'creator',
            'quota': {
                'used': 82345,
                'limit': 100000,
                'remaining': 17655,
                'pct': 82.3,
                'unit': 'karakter',
                'reset': int((now + timedelta(days=15)).timestamp()),
            },
            'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        },
        # OpenAI: gerçek $ (Costs API) — spend + sparkline
        {
            'id': 'openai',
            'name': 'OpenAI',
            'kind': 'spend',
            'status': 'ok',
            'currency': 'USD',
            'tier': 'org',
            'spend': {'today': 2.14, 'month': 47.80},
            'byDay': demo_byDay[-14:],
            'note': 'Organizasyon Costs API (demo).',
            'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        },
        # Together AI: anahtar geçerli ama sağlayıcı okunacak bir şey yayınlamıyor.
        # Demo, ürünün YAPABİLDİĞİNİ göstermeli — yapamadığını değil. Eskiden burada
        # $23.45 bakiye vardı; Together'ın böyle bir ucu yok (2026-08-11 ölçümü,
        # 7 yol 404) ve demo bunu vaat ederse ilk gerçek kullanıcıda yalan çıkar.
        # 'nodata' aynı zamanda panelin uzun-kuyruk bölümünü de egzersiz eder.
        {
            'id': 'together',
            'name': 'Together AI',
            'kind': 'spend',
            'status': 'nodata',
            'currency': 'USD',
            'tier': 'paid',
            'note': 'Anahtar geçerli. Together AI kullanım/bakiye ucu yayınlamıyor.',
            'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        },
        # LM Studio: yerel, sunucu açık (local ok)
        {
            'id': 'lmstudio',
            'name': 'LM Studio',
            'kind': 'local',
            'status': 'ok',
            'currency': None,
            'modelCount': 3,
            'models': [
                {'name': 'qwen2.5-coder-7b', 'size': None},
                {'name': 'llama-3.1-8b', 'size': None},
                {'name': 'mistral-7b', 'size': None},
            ],
            'running': [{'name': 'qwen2.5-coder-7b'}],
            'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        },
        # Aider: lokal-log token + $ tahmini (tokens kartı)
        {
            'id': 'aider',
            'name': 'Aider',
            'kind': 'tokens',
            'status': 'ok',
            'currency': 'USD',
            'windowDays': 30,
            'auth': 'yerel-log',
            'usdSource': 'estimate',
            'tokens': {'input': 180000, 'output': 42000, 'total': 222000},
            'total': {'tokens': 222000, 'usd': 2.34},
            'today': {'tokens': 12000, 'usd': 0.18},
            'byModel': [{'short': 'gpt-4o', 'model': 'gpt-4o'}],
            'byDay': [],
            'sessions': 14,
            'updated': datetime.fromtimestamp(now_ms / 1000).strftime('%H:%M:%S'),
        },
    ]


# ── /v1/usage wire-format ────────────────────────────────────────────────────
def usage_wire() -> dict:
    """Sentetik interop wire format (overlay/waybar uyumu)."""
    seed = _demo_seed()
    now_ms = seed['now_ms']

    limits = compute_usage()
    spend = compute_spend(30)

    def limit_bar(b):
        if not b:
            return None
        return {
            'pct': b.get('pct'),
            'used': b.get('units'),      # gerçek usage_wire() ile aynı sözleşme
            'units': b.get('units'),
            'budget': b.get('budget'),
            'resetAtMs': b.get('resetAtMs'),
            'resetInSec': b.get('resetInSec'),
            'forecast': b.get('forecast'),
        }

    claude = {
        'id': 'claude',
        'name': 'Claude Code',
        'calibrated': limits['calibrated'],
        # Demo'da canlı uç kapalı — ama alan var olmalı, yoksa demo yüzeyin 'live' yolunu
        # hiç egzersiz etmez ve o yol ilk kez gerçek kullanıcıda çalışır.
        'live': limits.get('live') or fetch(),
        'limits': {
            'session': limit_bar(limits['session']),
            'weekly': limit_bar(limits['weeklyAll']),
            'weeklyModel': (
                {**limit_bar(limits['weeklyModel']), 'name': limits['weeklyModel']['name']}
                if limits.get('weeklyModel')
                else None
            ),
            'thresholds': limits['thresholds'],
        },
        'spend': {
            'currency': spend['currency'],
            'today': spend['today'],
            'yesterday': spend['yesterday'],
            'last30d': spend['total'],
            'byModel': [
                {'model': m['model'], 'short': m['short'], 'usd': m['usd'], 'source': m['source']}
                for m in spend['byModel']
            ],
            'priceComplete': spend['priceComplete'],
            'estimatedModels': spend['estimatedModels'],
            'unknownPriceModels': spend.get('unknownPriceModels', []),
            'catalog': spend.get('catalog'),
        },
    }

    return {
        'schema': 'usage/v1',
        'generatedAtMs': now_ms,
        'generatedAt': datetime.fromtimestamp(now_ms / 1000).isoformat(timespec='seconds'),
        'thresholds': limits['thresholds'],
        'providers': [claude] + collect(30),
    }


# ── Live fetch (demo — API hatasını simüle et) ──────────────────────────────
def fetch() -> dict:
    """Canlı fetch demo — token olmadığını veya ağ hatası olduğunu simüle et."""
    return {
        'ok': False,
        'error': 'demo mode — canlı veri kapalı',
        'cached': False,
        'stale': False,
        'rateLimited': False,
        'rateLimitTier': None,
        'raw': None,
        'windows': {},
    }


if __name__ == '__main__':
    # Test
    import pprint
    print('=== LİMİTLER ===')
    pprint.pprint(compute_usage())
    print('\n=== HARCAMA ===')
    pprint.pprint(compute_spend(30))
    print('\n=== SAĞLAYICILAR ===')
    pprint.pprint(collect(30))
    print('\n=== WIRE ===')
    pprint.pprint(usage_wire())

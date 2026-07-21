#!/usr/bin/env python3
"""
Çok-sağlayıcı adaptör kayıt defteri (FAZ 2).

Her adaptör tek bir `collect(days) -> dict | None` fonksiyonu sunar:
  - None  → sağlayıcı yapılandırılmamış/bulunamadı → UI kart AÇMAZ ("no dead cards").
  - dict  → {'id','name','kind','status','available',...} → UI kart açar
            (status='offline'/'error' olsa bile — yapılandırılmış ama şu an erişilemez demek).

Kart 'kind' türleri:
  'spend'  → gerçek $ (currency/spend/balance/limit)     — OpenRouter
  'tokens' → token hacmi + $ tahmini (subscription)       — Codex
  'local'  → yerel/ücretsiz (models/running)              — Ollama

Claude ayrı tutulur (engine.compute_spend/compute_usage) — burada tekrar edilmez.
Tümü stdlib-only, salt-okunur, kısa timeout + TTL cache (429/ağ nezaketi).
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from . import openrouter, codex, ollama

_ADAPTERS = (openrouter, codex, ollama)


def _collect_single(mod, days):
    """Tek bir adaptörü çalıştır ve sonucunu iste. İstisnalar caller'da işlenir."""
    return mod.collect(days)


def collect(days: int = 30) -> list:
    """Tüm yapılandırılmış sağlayıcıların kartlarını paralel topla. Bir adaptörün patlaması
       diğerlerini düşürmez (izole try/except)."""
    cards = []

    # ThreadPoolExecutor ile 3 adaptörü paralel çalıştır
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Her adaptör için future oluştur, sırayı tut
        futures = {i: executor.submit(_collect_single, mod, days) for i, mod in enumerate(_ADAPTERS)}

        # Tamamlanma sırasına bakılmaksızın, _ADAPTERS sırasında sonuçları topla
        for i, mod in enumerate(_ADAPTERS):
            try:
                card = futures[i].result(timeout=10)  # adaptör başına maksimal 10s timeout
            except TimeoutError:
                provider_id = getattr(mod, 'PROVIDER_ID', mod.__name__.split('.')[-1])
                provider_name = getattr(mod, 'PROVIDER_NAME', '?')
                print(f'Provider {provider_id} error: TimeoutError: exceeded 10s', file=sys.stderr)
                card = {'id': provider_id,
                        'name': provider_name, 'kind': 'spend',
                        'status': 'error', 'available': True,
                        'error': f'Yapılandırma hatası (TimeoutError)'}
            except Exception as e:                       # adaptör beklenmedik hata → kart açma
                provider_id = getattr(mod, 'PROVIDER_ID', mod.__name__.split('.')[-1])
                provider_name = getattr(mod, 'PROVIDER_NAME', '?')
                # Tam exception detayını stderr'e logla (iç tanı için)
                print(f'Provider {provider_id} error: {type(e).__name__}: {e}', file=sys.stderr)
                # UI'da gösterilebilir mesaj: sadece exception tipi
                card = {'id': provider_id,
                        'name': provider_name, 'kind': 'spend',
                        'status': 'error', 'available': True,
                        'error': f'Yapılandırma hatası ({type(e).__name__})'}
            if card and card.get('available'):
                cards.append(card)
    return cards


if __name__ == '__main__':
    import json
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

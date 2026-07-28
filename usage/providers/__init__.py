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
  'quota'  → karakter/birim kotası + reset ($ yok)        — ElevenLabs

Claude ayrı tutulur (engine.compute_spend/compute_usage) — burada tekrar edilmez.
Tümü stdlib-only, salt-okunur, kısa timeout + TTL cache (429/ağ nezaketi).
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from . import (openrouter, openai, deepseek, together, novita, deepinfra, huggingface,
               elevenlabs, ollama, lmstudio, jan,
               codex, aider, continuedev, cody, windsurf)

# Sıra = UI kart sırası: gerçek-$ API → kota → yerel → lokal-log
_ADAPTERS = (openrouter, openai, deepseek, together, novita, deepinfra, huggingface,  # spend/quota API
             elevenlabs,                                                     # quota (karakter)
             ollama, lmstudio, jan,                                          # yerel/ücretsiz
             codex, aider, continuedev, cody, windsurf)                      # lokal-log token

TOTAL_TIMEOUT = 10  # tüm adaptörlerin toplam bütçesi (saniye) — deadline-based paylaşım

def _collect_single(mod, days):
    """Tek bir adaptörü çalıştır ve sonucunu iste. İstisnalar caller'da işlenir."""
    return mod.collect(days)


def collect(days: int = 30) -> list:
    """Tüm yapılandırılmış sağlayıcıların kartlarını paralel topla. Bir adaptörün patlaması
       diğerlerini düşürmez (izole try/except).

       Timeout: deadline-based (tüm adaptörlerin toplam bütçesi TOTAL_TIMEOUT saniye).
       Adaptörler paralel koşuyor; her biri deadline'a kadar bekler.
       Örn: 3 asılı → ~10s (3×10 değil), 8 asılı → ~10s, hep doğru.
    """
    cards = []
    executor = ThreadPoolExecutor(max_workers=max(1, len(_ADAPTERS)))
    deadline = time.monotonic() + TOTAL_TIMEOUT

    try:
        # Her adaptör için future oluştur, sırayı tut
        futures = {i: executor.submit(_collect_single, mod, days) for i, mod in enumerate(_ADAPTERS)}

        # Tamamlanma sırasına bakılmaksızın, _ADAPTERS sırasında sonuçları topla
        # Her adaptör için kalan bütçeyi hesapla (deadline paylaşımı)
        for i, mod in enumerate(_ADAPTERS):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                card = futures[i].result(timeout=remaining)
            except TimeoutError:
                provider_id = getattr(mod, 'PROVIDER_ID', mod.__name__.split('.')[-1])
                provider_name = getattr(mod, 'PROVIDER_NAME', '?')
                provider_kind = getattr(mod, 'PROVIDER_KIND', 'spend')
                print(f'Provider {provider_id} error: TimeoutError: exceeded {TOTAL_TIMEOUT}s toplam', file=sys.stderr)
                card = {'id': provider_id,
                        'name': provider_name, 'kind': provider_kind,
                        'status': 'error', 'available': True,
                        'error': f'Yanıt vermedi ({TOTAL_TIMEOUT}s aşıldı)'}
            except Exception as e:                       # adaptör beklenmedik hata → kart açma
                provider_id = getattr(mod, 'PROVIDER_ID', mod.__name__.split('.')[-1])
                provider_name = getattr(mod, 'PROVIDER_NAME', '?')
                provider_kind = getattr(mod, 'PROVIDER_KIND', 'spend')
                # Tam exception detayını stderr'e logla (iç tanı için)
                print(f'Provider {provider_id} error: {type(e).__name__}: {e}', file=sys.stderr)
                # UI'da gösterilebilir mesaj: sadece exception tipi
                card = {'id': provider_id,
                        'name': provider_name, 'kind': provider_kind,
                        'status': 'error', 'available': True,
                        'error': f'Hata ({type(e).__name__})'}
            if card and card.get('available'):
                cards.append(card)
    finally:
        # Asılabilen thread'leri iptal et; shutdown'u beklemeden hemen dön (wait=False)
        # cancel_futures=True (py3.9+) henüz çalışmayan future'ları iptal eder
        executor.shutdown(wait=False, cancel_futures=True)

    return cards


if __name__ == '__main__':
    import json
    print(json.dumps(collect(30), ensure_ascii=False, indent=2))

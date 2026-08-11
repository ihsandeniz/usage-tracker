#!/usr/bin/env python3
"""
Bir API yanıtından para tutarı çıkarmanın **tek** yeri.

Neden var: `together.py`, `novita.py` ve `deepinfra.py` aynı `_dig()` fonksiyonunun üç
kopyasını taşıyordu. O fonksiyon yanıtın **her** iç içe sözlüğüne iniyor ve
`balance/credit/remaining/available` adlı ilk alanı alıp ekrana dolar diye basıyordu.
İki sessiz yalan üretiyordu:

    {"available": true}                    → float(True) == 1.0  → "$1,00 kalan"
    {"rate_limit": {"remaining": 4999}}    → "$4999,00 kalan"

İkisi de hata gibi görünmez. Bu proje bu sınıf hatayı daha önce de yedi
([[ledger]] `api/alan-adi-sapmasi-sessiz-sifir`) — kural şu: **tanımadığın şekli
tahmin etme, tanımadığını söyle.**

Bu yüzden tarama artık dar:
  · yalnız en üst seviye ve **adı bilinen** bir zarfın (`data`, `result`, …) bir kademe altı
  · `bool` sayı değildir
  · NaN/Inf sayı değildir
  · sayıya benzeyen metin ('3.50') kabul edilir — API'ler bunu sık yapar
  · negatif kabul edilir: eksideki hesap gerçektir, sıfıra kırpmak sorunu gizler
"""
import math

# Bakiye/kredi anlamına gelen alan adları. `available` BİLEREK yok: neredeyse her zaman
# bir bayraktır ve `float(True)` sessizce 1.0 verir.
AMOUNT_FIELDS = ('balance', 'credit', 'credits', 'credit_balance',
                 'available_credit', 'remaining', 'amount', 'total_credits')

# Yanıtı saran, içine bakılması güvenli kapsayıcılar. Bunun dışına İNİLMEZ —
# `rate_limit.remaining` gibi alakasız bir sayacı bakiye sanmanın tek çaresi bu.
ENVELOPES = ('data', 'result', 'results', 'account', 'user', 'billing',
             'balance', 'credits', 'wallet', 'usage')


def _as_amount(value):
    """Sayıya çevir, ama yalnız gerçekten sayıysa. bool ve NaN sayı DEĞİLDİR."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(value) or math.isinf(value) else float(value)
    if isinstance(value, str):
        try:
            v = float(value.strip().replace('$', '').replace(',', ''))
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) or math.isinf(v) else v
    return None


def _scan_level(obj):
    if not isinstance(obj, dict):
        return None
    for field in AMOUNT_FIELDS:
        if field in obj:
            amount = _as_amount(obj[field])
            if amount is not None:
                return amount
    return None


def pick_amount(payload):
    """Yanıttan para tutarını çıkar; emin değilsen **None** dön.

    None, "bulunamadı" demektir ve çağıran bunu kullanıcıya dürüstçe söylemek zorundadır.
    Uydurulmuş bir 0.0 döndürmek, hiç döndürmemekten kötüdür.
    """
    amount = _scan_level(payload)
    if amount is not None:
        return amount
    if not isinstance(payload, dict):
        return None
    for key in ENVELOPES:
        inner = payload.get(key)
        if isinstance(inner, dict):
            amount = _scan_level(inner)
            if amount is not None:
                return amount
        elif isinstance(inner, list):
            for item in inner[:5]:           # {"data": [{...}]} deseni
                amount = _scan_level(item)
                if amount is not None:
                    return amount
    return None

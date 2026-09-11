#!/usr/bin/env python3
"""usage-tracker rozeti → KWGT preset (.kwgt).

Şema, çalışan bir preset'ten (AumGupta/KWGT-Widgets) okunarak taklit edildi —
uydurulmadı. .kwgt = zip{preset.json, preset_thumb_*.jpg}.

Tasarım kararları:
- URL bir GLOBAL: ihsan KWGT'de tek yerden değiştirebilir, 6 formülü tek tek
  düzenlemek zorunda kalmaz.
- Font GÖMÜLMEDİ: sistem fontu kullanılıyor. Gömülü font dosyası presetin
  boyutunu 200 KB'a çıkarır ve taşınırken kopan ilk şey odur.
- Renk `class` alanından: sunucu eşiği değiştirirse renk kendiliğinden uyar
  (eşiği preset'e sabitlemek, iki yerde iki gerçek demek olurdu).
"""
import json
import zipfile
from pathlib import Path

URL = 'https://ihsan.tailf382a6.ts.net/usage/rozet.json'
CIKTI = Path(__file__).parent / 'AI-Kullanim.kwgt'

CYAN, GOLD, KIRMIZI, SOLUK = '#FF22D3EE', '#FFFFC857', '#FFFF5555', '#FF8B9BB4'


def metin(baslik, ifade, boyut, *, renk_global='renk', **ek):
    d = {
        'internal_type': 'TextModule',
        'internal_title': baslik,
        'internal_toggles': {'paint_color': 100},
        'internal_globals': {'paint_color': renk_global},
        'text_expression': ifade,
        'text_size': float(boyut),
    }
    d.update(ek)
    return d


def wg(yol):
    """Tek kaynaktan okuma. gv(url) → global; Kustom aynı URL'i cache'ler."""
    return f'$wg(gv(url), json, {yol})$'



preset = {
    'preset_info': {
        'version': 13,
        'title': 'AI Kullanım',
        'description': 'Claude + Codex limit rozeti — usage-tracker',
        'author': 'Gölge Atölyesi',
        'email': '',
        'width': 918,
        'height': 442,
        'features': '',
        'release': 376422110,
        'locked': False,
        'pflags': 0,
    },
    'preset_root': {
        'internal_events': [{'action': 'KUSTOM_ACTION'}],
        'internal_type': 'RootLayerModule',
        'globals_list': {
            'url': {
                'index': 1, 'type': 'TEXT', 'title': 'Rozet adresi',
                'description': 'usage-tracker rozet JSON adresi',
                'value': URL,
            },
            'renk': {
                'index': 2, 'type': 'COLOR', 'title': 'Durum rengi',
                # class ok/warn/crit/unknown → cyan/gold/kırmızı/soluk
                'global_formula': (
                    f'$if({wg(".class")[1:-1]}=crit, {KIRMIZI},'
                    f' if({wg(".class")[1:-1]}=warn, {GOLD},'
                    f' if({wg(".class")[1:-1]}=unknown, {SOLUK}, {CYAN})))$'
                ),
                'value': CYAN,
            },
            'bgcolor': {
                'index': 3, 'type': 'COLOR', 'title': 'Arka plan',
                'value': '#E60B1220',
            },
            'soluk': {
                'index': 4, 'type': 'COLOR', 'title': 'İkincil yazı',
                'value': SOLUK,
            },
        },
        'config_scale_value': 100.0,
        'viewgroup_items': [{
            'internal_type': 'OverlapLayerModule',
            'internal_title': 'Rozet',
            'viewgroup_items': [
                # 1 — arka plan
                {
                    'internal_type': 'ShapeModule',
                    'internal_title': 'BG',
                    'internal_toggles': {'paint_color': 100},
                    'internal_globals': {'paint_color': 'bgcolor'},
                    'shape_type': 'RECT',
                    'shape_width': 918.0,
                    'shape_height': 442.0,
                    'shape_corners': 48.0,
                    'paint_color': '#E60B1220',
                },
                # 2 — başlık
                metin('Baslik', 'AI KULLANIM', 34,
                      renk_global='soluk',
                      position_anchor='TOPLEFT',
                      position_offset_x=44.0, position_offset_y=38.0,
                      text_letter_spacing=4.0),
                # 3 — manşet yüzde (rozetin 'text' alanı: "◐ 42%" / "○ —")
                metin('Yuzde', wg('.text'), 150,
                      position_anchor='CENTERLEFT',
                      position_offset_x=44.0, position_offset_y=10.0),
                # 4 — kırılım
                # 🔑 Her satır TEK wg() — birleştirme ve yuvarlama SUNUCUDA.
                # KWGT aynı ifadedeki ikinci wg()'yi null döndürüyor ve
                # mu(round, null) "invalid numeric argument" veriyor (canlı
                # ölçüldü 2026-09-11). Sunucu hazır metin yollayınca sorun yok.
                metin('Kirilim', wg('.claude_text'), 30, renk_global='soluk',
                      position_anchor='BOTTOMLEFT',
                      position_offset_x=44.0, position_offset_y=-86.0),
                metin('Kirilim2', wg('.codex_text'), 30, renk_global='soluk',
                      position_anchor='BOTTOMLEFT',
                      position_offset_x=44.0, position_offset_y=-40.0),
                # 5 — sıfırlanmaya kalan süre (gün kademeli, sunucudan hazır)
                metin('Reset', wg('.reset_text'), 28, renk_global='soluk',
                      position_anchor='TOPRIGHT',
                      position_offset_x=-44.0, position_offset_y=38.0),
            ],
        }],
    },
}


if __name__ == '__main__':
    thumb = (Path(__file__).parent / 'preset_thumb.jpg').read_bytes()
    with zipfile.ZipFile(CIKTI, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('preset.json', json.dumps(preset, ensure_ascii=False, indent=1))
        # KWGT ikisini de arar; aynı görseli iki ada yazmak listede boş kutu
        # görünmesini engeller.
        z.writestr('preset_thumb_portrait.jpg', thumb)
        z.writestr('preset_thumb_landscape.jpg', thumb)
    print(f'{CIKTI}  ({CIKTI.stat().st_size} bayt)')

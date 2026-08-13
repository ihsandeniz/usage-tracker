"""Kullanıcıya *ne yapacağını* söyleyen satırlar — Türkçe ve İngilizce.

Kapsam bilinçli olarak dar. Log satırları, hata ayıklama çıktısı ve `--json` İngilizce
kalır (terminal çıktısının her makinede birebir aynı olması bir özelliktir, ve bir hata
raporu çevrilirse aranamaz). Çevrilen şey yalnızca **kullanıcının bir sonraki adımı**:
açılış penceresi, sihirbazın soruları, "şimdi ne oldu" cümleleri.

Neden iki dil birden basılıyor: makinenin dilini yanlış tahmin etmenin bedeli, iki satır
fazladan metinden büyük. Windows'ta çift tıklayan biri için o pencere tek yönlendirme.

Dil seçimi: `UT_LANG=tr|en` > Windows kullanıcı arayüzü dili > `LANG`/`LC_ALL` > İngilizce.
"""
from __future__ import annotations

import os

DEFAULT = 'en'
LANGS = ('en', 'tr')

MESSAGES = {
    'panel_running': {
        'en': 'The panel is running at {url}',
        'tr': 'Panel çalışıyor: {url}',
    },
    'panel_opening': {
        'en': "Opening it in your browser. If nothing opens, type that address yourself.",
        'tr': 'Tarayıcında açıyorum. Açılmazsa adresi kendin yaz.',
    },
    'keep_open': {
        'en': 'Keep this window open — closing it stops the program. Ctrl+C also stops it.',
        'tr': 'Bu pencereyi açık bırak — kapatırsan program durur. Ctrl+C de durdurur.',
    },
    'hide_window': {
        'en': 'Want it to start by itself, with no window? Run the setup wizard: usage-tracker setup',
        'tr': 'Kendiliğinden ve penceresiz başlasın mı? Kurulum sihirbazını çalıştır: usage-tracker setup',
    },
    'first_run': {
        'en': 'First run — the setup wizard is opening in your browser.',
        'tr': 'İlk çalıştırma — kurulum sihirbazı tarayıcında açılıyor.',
    },
    'wizard_done': {
        'en': 'Setup finished. Opening the panel.',
        'tr': 'Kurulum bitti. Panel açılıyor.',
    },
    'stopped': {
        'en': 'Stopped.',
        'tr': 'Durduruldu.',
    },
    'setup_intro': {
        'en': 'Four steps and a check. Each one shows what it will write before writing it, '
              'and can be undone.',
        'tr': 'Dört adım ve bir kontrol. Her adım yazacağını önce gösterir, sonra geri alınabilir.',
    },
    'setup_apply': {
        'en': '  Apply this step?',
        'tr': '  Bu adımı uygulayalım mı?',
    },
    'setup_skipped': {
        'en': '  · skipped',
        'tr': '  · atlandı',
    },
    'setup_keys_q': {
        'en': '\n  Add provider API keys now? (optional — Claude usage needs no key)',
        'tr': '\n  Sağlayıcı API anahtarı eklensin mi? (isteğe bağlı — Claude için gerekmez)',
    },
    'setup_done': {
        'en': '\n  Done. The panel lives at {url} — `usage-tracker panel` opens it.',
        'tr': '\n  Bitti. Panel şurada: {url} — `usage-tracker panel` onu açar.',
    },
    'setup_undo_hint': {
        'en': '  Changed your mind? `usage-tracker setup --uninstall` puts it all back.',
        'tr': '  Vazgeçtin mi? `usage-tracker setup --uninstall` hepsini geri alır.',
    },
    'step_install': {
        'en': 'Put the program somewhere permanent',
        'tr': 'Programı kalıcı bir yere koy',
    },
    'step_autostart': {
        'en': 'Start it automatically when you log in',
        'tr': 'Oturum açınca kendiliğinden başlasın',
    },
    'step_shortcut': {
        'en': 'Add a shortcut that opens the panel',
        'tr': 'Paneli açan bir kısayol ekle',
    },
    'step_keys': {
        'en': 'Provider API keys (optional)',
        'tr': 'Sağlayıcı API anahtarları (isteğe bağlı)',
    },
    'step_verify': {
        'en': 'Check that everything works',
        'tr': 'Her şey çalışıyor mu, bak',
    },
}


def _windows_ui_language() -> str:
    """Windows'un kendi arayüz dili. `LANG` orada genelde yoktur, o yüzden tek güvenilir
    kaynak budur; 0x1F Türkçe'nin birincil dil kimliği."""
    try:
        import ctypes
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return 'tr' if (lang_id & 0x3FF) == 0x1F else 'en'
    except Exception:
        return ''


def language() -> str:
    chosen = (os.environ.get('UT_LANG') or '').strip().lower()[:2]
    if chosen in LANGS:
        return chosen
    from . import platform as _paths
    if _paths.is_windows():
        detected = _windows_ui_language()
        if detected:
            return detected
    locale = (os.environ.get('LC_ALL') or os.environ.get('LANG') or '').lower()
    return 'tr' if locale.startswith('tr') else DEFAULT


def t(key: str, lang=None, **fmt) -> str:
    entry = MESSAGES.get(key, {})
    text = entry.get(lang or language()) or entry.get(DEFAULT) or key
    return text.format(**fmt) if fmt else text


def both(key: str, **fmt) -> list:
    """Önce makinenin dili, sonra öteki. Bir kullanıcının okuyamadığı tek satır, hiç
    yazılmamış satırdır."""
    primary = language()
    other = 'en' if primary == 'tr' else 'tr'
    return [t(key, primary, **fmt), t(key, other, **fmt)]

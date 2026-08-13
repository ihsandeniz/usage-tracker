# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — tek dosyalık usage-tracker.

    pyinstaller packaging/usage-tracker.spec --noconfirm

🔴 **Yerelde derleyip dağıtma.** GalleryWeb'de Linux paketi Arch'ta derlendi, oradaki
`GLIBC_2.43`'e bağlandı ve hiçbir LTS dağıtımında açılmadı. Sürüm ikilileri CI'da,
kasıtlı olarak eski bir taban üzerinde üretilir (`.github/workflows/package.yml`).
Buradaki yerel derleme yalnızca *paketlemenin doğru olduğunu* sınamak içindir.

Paketin içine giren salt-okunur veri: `web/` · `data/` · `price_overrides.json`.
Bunlar çalışma anında `sys._MEIPASS` altına açılır ve `usage/platform.py:resource_dir()`
oradan okur. Kullanıcı verisi (kalibrasyon, config, cache) buraya **yazılmaz** —
_MEIPASS geçicidir, çıkışta silinir.

Sağlayıcı adaptörleri `usage/providers/__init__.py` içinde statik import edilir, bu yüzden
`hiddenimports` gerekmez. Dinamik import'a geçilirse burası güncellenmeli — yoksa paket
sorunsuz açılır ve tek bir sağlayıcı bile görünmez.
"""
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 (SPECPATH PyInstaller'dan gelir)

a = Analysis(                                    # noqa: F821
    [str(ROOT / 'server.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'web'), 'web'),
        (str(ROOT / 'data'), 'data'),
        (str(ROOT / 'price_overrides.json'), '.'),
    ],
    # `usage.cli` yalnızca `server.main()` içinde, çalışma anında import ediliyor (CLI alt
    # komutu verilmedikçe yüklenmesin diye). Analiz bunu bulur ama bulamazsa hata sessizdir:
    # paket açılır, sunucu çalışır ve `usage-tracker guard` "böyle bir komut yok" der.
    # Bu üçü de çalışma anında import ediliyor (CLI alt komutu verilmedikçe yüklenmesinler
    # diye). Analiz genelde bulur; bulamazsa hata **sessizdir**: paket açılır, sunucu çalışır
    # ve `usage-tracker setup` "böyle bir komut yok" der. 08-12'de tam bu sınıf kusur
    # donmuş paketten çıktı (bkz. dağıtıcı notu), o yüzden burada açıkça yazılıyorlar.
    hiddenimports=['usage.cli', 'usage.wizard', 'usage.wizard_server'],
    hookspath=[],
    runtime_hooks=[],
    # Sıfır bağımlılık sözü ikili boyutunda da tutulsun: GUI araç setlerini ve test
    # altyapısını dışarıda bırak. Tray ayrı ve opsiyonel bir yüzey (Qt gerektirir),
    # bu pakete dahil değil.
    excludes=['tkinter', 'unittest', 'pydoc', 'doctest', 'test',
              'PyQt5', 'PySide6', 'PIL', 'numpy'],
    noarchive=False,
)

pyz = PYZ(a.pure)                                # noqa: F821

exe = EXE(                                       # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='usage-tracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX antivirüs yanlış-pozitifini artırır — imzasız bir .exe'de
                        # zaten SmartScreen'le uğraşacağız, üstüne paketleyici koymayalım
    console=True,       # sunucu süreci: konsol çıktısı teşhis için gerekli
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

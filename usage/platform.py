#!/usr/bin/env python3
"""
Yol çözümlemesinin **tek** kaynağı — hangi işletim sistemindeyiz ve kullanıcının
verisi nereye yazılır.

Neden var: 2026-08-11'e kadar kod platformu hiç sormuyordu (`platform.system()` hiçbir
yerde geçmiyordu) ve kullanıcı durumu **kodun yanına** yazılıyordu
(`Path(__file__).parent.parent / 'usage_calib.json'`). Bu, kod yazılabilir bir yerde
durduğu sürece çalışır. PyInstaller paketinde, `Program Files`'ta, `/usr/lib`'de ya da
salt-okunur bir konteynerde çalışmaz — ve sonuç bir uyarı değil, bozuk bir kurulumdur.

Kural: **kurulum dizini salt-okunur kabul edilir.** Oraya hiçbir şey yazılmaz.

  | | Linux/BSD (XDG)              | Windows                                  |
  |---|---|---|
  | config | `~/.config/usage-tracker`      | `%APPDATA%\\usage-tracker`               |
  | state  | `~/.local/state/usage-tracker` | `%LOCALAPPDATA%\\usage-tracker\\State`    |
  | cache  | `~/.cache/usage-tracker`       | `%LOCALAPPDATA%\\usage-tracker\\Cache`    |

macOS Apple'ın `~/Library/Application Support` kuralı yerine XDG'yi izler: doğrulayacak
makine yok ve stdlib bir CLI aracı için XDG yaygın davranış. Makine gelince ölçülecek.

⚠️ Bu modül stdlib `platform`'u **import etmez** — `usage/` dizini bir gün `sys.path`'e
düşerse bu dosya stdlib'i gölgeler ve onu import eden herkes sessizce kırılırdı.
Karar `sys.platform` ile veriliyor. (`tests/test_portability.py` bunu zorlar.)
"""
import os
import sys
import threading

from pathlib import Path

APP_NAME = 'usage-tracker'

# Testler bunu yamalar — `sys.platform`'u doğrudan okusaydık Windows dalı hiç
# çalıştırılamazdı ve test edilmeyen dal, kırık dal demektir.
SYS_PLATFORM = sys.platform

# Kurulum dizini: bu paketin kökü. **Yazılabilir varsayılmaz.**
INSTALL_DIR = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """PyInstaller (veya benzeri) ile tek dosyaya paketlenmiş miyiz?"""
    return bool(getattr(sys, 'frozen', False))


def resource_dir() -> Path:
    """Paketle birlikte gelen **salt-okunur** veri nerede: `web/`, `data/`,
    `price_overrides.json`.

    PyInstaller tek-dosya paketi kendini geçici bir dizine açar ve `sys._MEIPASS`'i
    oraya bakar. `Path(__file__).parent.parent` başka bir yeri gösterir; sonuç, sorunsuz
    açılan ama web varlıkları ve fiyat kataloğu **kayıp** bir uygulamadır.

    Dikkat: burası geçicidir, çalışma bitince silinir — kullanıcı verisi buraya değil,
    `state_dir()`/`config_dir()`/`cache_dir()`'a yazılır.
    """
    meipass = getattr(sys, '_MEIPASS', None)
    return Path(meipass) if meipass else INSTALL_DIR

_WRITE_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def is_windows() -> bool:
    return SYS_PLATFORM.startswith('win')


def is_macos() -> bool:
    return SYS_PLATFORM == 'darwin'


def system() -> str:
    """'windows' | 'macos' | 'linux' — dallanmanın okunur adı."""
    if is_windows():
        return 'windows'
    if is_macos():
        return 'macos'
    return 'linux'


def home() -> Path:
    """Kullanıcının ev dizini. Windows'ta `Path.home()` USERPROFILE'a bakar; env
    temizlenmiş bir servis hesabında bile bir şey döndürebilmek için elle de deniyoruz."""
    if is_windows():
        p = os.environ.get('USERPROFILE')
        if p:
            return Path(p)
    p = os.environ.get('HOME')
    if p:
        return Path(p)
    try:
        return Path.home()
    except (RuntimeError, OSError):        # ev dizini çözülemiyor
        return Path(os.getcwd())


def _windows_base(env_name: str, *sub) -> Path:
    base = os.environ.get(env_name)
    if base:
        return Path(base).joinpath(*sub)
    # APPDATA yoksa (soyulmuş servis hesabı) profil altına düş — düşmekten iyidir
    return home() / 'AppData' / ('Roaming' if env_name == 'APPDATA' else 'Local')


def config_dir() -> Path:
    """Kullanıcının bilerek düzenlediği ayarlar."""
    if is_windows():
        return _windows_base('APPDATA') / APP_NAME
    base = os.environ.get('XDG_CONFIG_HOME')
    return (Path(base) if base else home() / '.config') / APP_NAME


def state_dir() -> Path:
    """Kullanıcıya ait ama elle düzenlemediği veri: kalibrasyon, son iyi cevap."""
    if is_windows():
        return _windows_base('LOCALAPPDATA') / APP_NAME / 'State'
    base = os.environ.get('XDG_STATE_HOME')
    return (Path(base) if base else home() / '.local' / 'state') / APP_NAME


def cache_dir() -> Path:
    """Silinse kendini yeniden üretebilen şeyler: fiyat kataloğu kopyası."""
    if is_windows():
        return _windows_base('LOCALAPPDATA') / APP_NAME / 'Cache'
    base = os.environ.get('XDG_CACHE_HOME')
    return (Path(base) if base else home() / '.cache') / APP_NAME


def pick_existing(preferred: Path, legacy: Path) -> Path:
    """Yeni yol yoksa ama eski yol varsa eskisini döndür.

    Bir yolu taşımak, kullanıcının çoktan yaptığı kalibrasyonu sessizce çöpe atmak
    demektir. Eski dosya yerinde durduğu sürece okunur; yeni dosya bir kez oluştuğunda
    o kazanır. Kimsenin bir şeyi elle taşıması gerekmez.
    """
    preferred, legacy = Path(preferred), Path(legacy)
    if preferred.exists():
        return preferred
    if legacy.exists():
        return legacy
    return preferred


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = _WRITE_LOCKS[key] = threading.Lock()
        return lock


_counter = 0
_counter_guard = threading.Lock()


def atomic_write_text(path, text: str, encoding: str = 'utf-8') -> Path:
    """Ya tamamı yazılır ya hiçbiri. Yarım dosya okunmaz.

    Geçici ad **benzersizdir**. Eskiden `with_suffix('.tmp')` kullanılıyordu — sabit bir
    ad: aynı anda yazan iki süreç aynı scratch dosyasını paylaşır ve biri diğerinin
    yarısını hedefe taşır. `os.replace` hem POSIX'te hem Windows'ta atomiktir.
    """
    global _counter
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _counter_guard:
        _counter += 1
        seq = _counter
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{seq}.tmp')
    with _lock_for(path):
        try:
            tmp.write_text(text, encoding=encoding)
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()          # replace başarılıysa zaten yok; hata olduysa temizle
            except OSError:
                pass
    return path


def describe() -> dict:
    """Teşhis için: `doctor` komutu ve hata raporları bunu basar."""
    return {
        'system': system(),
        'sysPlatform': SYS_PLATFORM,
        'home': str(home()),
        'installDir': str(INSTALL_DIR),
        'configDir': str(config_dir()),
        'stateDir': str(state_dir()),
        'cacheDir': str(cache_dir()),
        'installDirWritable': os.access(INSTALL_DIR, os.W_OK),
    }


if __name__ == '__main__':
    for k, v in describe().items():
        print(f'{k:20s} {v}')

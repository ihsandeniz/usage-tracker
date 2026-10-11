"""Wheel derlemesi için tek kanca: panelin salt-okunur kaynaklarını pakete kopyala.

`web/`, `data/` ve `price_overrides.json` kaynak ağacında depo kökünde durur — kaynaktan
koşu (`python3 server.py`), AUR paketi, PyInstaller spec'i, CI ve testler onları orada
arar. Wheel'de ise depo kökü diye bir yer yoktur; `web`/`data` gibi jenerik adları
site-packages'ın tepesine koymak da başkalarının paketleriyle çakışır. Bu yüzden derleme
sırasında `usage/_res/` altına kopyalanırlar ve `usage/platform.py:resource_dir()` orayı
bulur. Kaynak dosyalar taşınmaz; ayar `pyproject.toml`'dadır.
"""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
RESOURCES = ('web', 'data', 'price_overrides.json')


class build_py_with_resources(build_py):
    def run(self):
        super().run()
        dest = Path(self.build_lib) / 'usage' / '_res'
        for name in RESOURCES:
            src = ROOT / name
            if not src.exists():
                raise SystemExit(f'setup.py: {name} missing — the panel would 404 from the wheel')
            target = dest / name
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)


setup(cmdclass={'build_py': build_py_with_resources})

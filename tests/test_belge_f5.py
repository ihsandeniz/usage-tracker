"""F5 — belge ve güvenlik başlığı iddiaları (2026-11-05 taraması Y15, Y16, Y18)."""
import os
import re
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _oku(ad):
    return (ROOT / ad).read_text(encoding='utf-8')


class BelgelerKodlaAyni(unittest.TestCase):
    def test_rozet_bicimi_gercek(self):
        # Gerçek çıktı `◐ S13% W18%`; `◐ NN%` kalmamalı. CLI besleyicileri ayrı.
        for ad in ('README.md', 'README.tr.md', 'PUBLISH.md'):
            with self.subTest(ad):
                self.assertNotIn('◐ NN%', _oku(ad))
                self.assertIn('◐ S', _oku(ad))

    def test_iki_onbellek_fiyati_anlatilir(self):
        self.assertIn('1-hour cache writes', _oku('README.md'))
        self.assertIn('1 saatlik önbellek', _oku('README.tr.md'))

    def test_limit_kaynagi_status_line_once(self):
        self.assertRegex(_oku('README.md'), r'\*\*Limits: real[^\n]*status line')
        self.assertRegex(_oku('README.tr.md'), r'\*\*Limitler[^\n]*durum satır')

    def test_codex_olculemeyen_sapma_yazili(self):
        self.assertIn('token_usage_record', _oku('README.md'))
        self.assertIn('token_usage_record', _oku('README.tr.md'))

    def test_pypi_vaadi_yok(self):
        self.assertIn('Not published to PyPI', _oku('pyproject.toml'))

    def test_uyari_kodlari_belgeli(self):
        self.assertIn('warningCodes', _oku('docs/WIRE.md'))

    def test_panel_onbellegi_kirildi(self):
        self.assertNotIn('app.js?v=12', _oku('web/index.html'))


class PanelGuvenlikBasliklari(unittest.TestCase):
    """Y18 — sihirbazda vardı, panelde yoktu."""

    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            cls.port = s.getsockname()[1]
        env = dict(os.environ, USAGE_DEMO='1', USAGE_PORT=str(cls.port), USAGE_CONTEXT_BREAKDOWN='0')
        cls.proc = subprocess.Popen([sys.executable, 'server.py'], cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{cls.port}/', timeout=1).close()
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError('sunucu kalkmadı')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)

    def test_html_basliklari(self):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/', timeout=5) as r:
            csp = r.headers.get('Content-Security-Policy') or ''
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertIn("default-src 'self'", csp)
            self.assertIn("script-src 'self'", csp)
            self.assertNotIn('unsafe-eval', csp)
            self.assertEqual(r.headers.get('X-Content-Type-Options'), 'nosniff')
            self.assertEqual(r.headers.get('X-Frame-Options'), 'DENY')

    def test_html_disinda_sniff_kapali(self):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/app.js', timeout=5) as r:
            self.assertEqual(r.headers.get('X-Content-Type-Options'), 'nosniff')


if __name__ == '__main__':
    unittest.main()

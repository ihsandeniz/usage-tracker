"""Y5 — Codex %99 iken waybar yeşil, telefon kırmızı gösteriyordu.

Rozet kodu "waybar ile AYNI tanım" diyordu ama waybar yalnız Claude'a bakıyordu. Karar
(ihsan, 2026-11-05): waybar Codex'i de sayar, kural telefonla aynı. Bu test ikisini aynı
wire ile GERÇEKTEN çalıştırıp karşılaştırır: jq programı koşar, grep yok.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('rozet_sunucu_y5', REPO / 'surface' / 'rozet-sunucu.py')
rozet_sunucu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rozet_sunucu)


def _bar(pct, stale=False):
    return {'pct': pct, 'resetInSec': 3600, 'stale': stale, 'live': True}


def _wire(claude, codex=None):
    th = {'warn': 75, 'crit': 90}
    kartlar = [{'id': 'claude', 'name': 'Claude Code', 'calibrated': True,
                'limits': {'session': claude[0], 'weekly': claude[1], 'weeklyModel': None,
                           'thresholds': th},
                'spend': {'currency': 'USD', 'today': 1.0, 'yesterday': 1.0, 'last30d': 1.0}}]
    if codex:
        kartlar.append({'id': 'codex', 'name': 'Codex', 'kind': 'tokens', 'status': 'partial',
                        'tokens': {'total': 5}, 'limits': {'session': codex[0], 'weekly': codex[1]}})
    return {'schema': 'usage/v1', 'thresholds': th, 'providers': kartlar}


@unittest.skipIf(sys.platform.startswith('win') or not shutil.which('jq') or not shutil.which('bash'),
                 'waybar besleyicisi Linux yüzeyi')
class WaybarTelefonlaAyni(unittest.TestCase):
    def _waybar(self, wire, *args):
        govde = json.dumps(wire).encode()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(govde)

            def log_message(self, *a):
                pass

        srv = HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = dict(os.environ, USAGE_URL=f'http://127.0.0.1:{srv.server_address[1]}',
                   XDG_RUNTIME_DIR=self._xdg())
        try:
            p = subprocess.run(['bash', str(REPO / 'surface' / 'waybar-usage.sh'), *args],
                               capture_output=True, text=True, encoding='utf-8', timeout=30, env=env)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def _xdg(self):
        import tempfile
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td.name

    def _telefon(self, wire):
        class Sahte:
            def __enter__(s):
                import io
                return io.BytesIO(json.dumps(wire).encode())

            def __exit__(s, *a):
                return False
        eski = rozet_sunucu.urllib.request.urlopen
        rozet_sunucu.urllib.request.urlopen = lambda *a, **k: Sahte()
        try:
            return rozet_sunucu.rozet()
        finally:
            rozet_sunucu.urllib.request.urlopen = eski

    def test_codex_99_iken_yesil_kalmaz(self):
        b = self._waybar(_wire((_bar(11), _bar(18)), (_bar(99), _bar(63))))
        self.assertEqual(b['class'], 'crit')
        self.assertEqual(b['percentage'], 99)

    def test_metin_claude_kalir(self):
        b = self._waybar(_wire((_bar(11), _bar(18)), (_bar(99), _bar(63))))
        self.assertIn('S11', b['text'])
        self.assertIn('W18', b['text'])

    def test_renk_her_durumda_telefonla_ayni(self):
        durumlar = {
            'ikisi düşük': ((_bar(11), _bar(18)), (_bar(20), _bar(30))),
            'codex uyarı': ((_bar(11), _bar(18)), (_bar(80), _bar(30))),
            'codex kritik': ((_bar(11), _bar(18)), (_bar(99), _bar(63))),
            'claude kritik': ((_bar(95), _bar(18)), (_bar(1), _bar(3))),
            'codex yok': ((_bar(11), _bar(18)), None),
            'bayat codex sayılmaz': ((_bar(11), _bar(18)), (_bar(99, stale=True), _bar(63, stale=True))),
            'bayat claude sayılmaz': ((_bar(95, stale=True), _bar(18, stale=True)), (_bar(10), _bar(5))),
            'sıfırlanmış codex': ((_bar(11), _bar(18)), (_bar(None), _bar(None))),
            'hepsi bilinmiyor': ((_bar(None), _bar(None)), None),
            'hepsi bayat': ((_bar(95, stale=True), _bar(18, stale=True)), None),
        }
        karsilik = {'ok': 'ok', 'warn': 'warn', 'crit': 'crit', 'off': 'unknown'}
        for ad, (claude, codex) in durumlar.items():
            with self.subTest(ad):
                wire = _wire(claude, codex)
                self.assertEqual(karsilik[self._waybar(wire)['class']], self._telefon(wire)['class'])

    def test_acik_saglayici_rozeti_kendi_rengini_tutar(self):
        # `--provider codex` ikinci rozet: yalnız kendi kartı. Codex rozeti Claude ile boyanmaz.
        wire = _wire((_bar(95), _bar(18)), (_bar(10), _bar(20)))
        self.assertEqual(self._waybar(wire, '--provider', 'codex')['class'], 'ok')


if __name__ == '__main__':
    unittest.main()

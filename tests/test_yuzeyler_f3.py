"""F3 yüzeyler — 2026-11-05 taramasının Y5 (kalanı), Y6, Y11, Y13, Y14 bulguları."""
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from usage import cli, engine
from usage.providers import codex

from tests.test_tray_reporting import load_tray

REPO = Path(__file__).resolve().parent.parent
tray = load_tray()
_spec = importlib.util.spec_from_file_location('rozet_f3', REPO / 'surface' / 'rozet-sunucu.py')
rozet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rozet)


def _bar(pct, stale=False):
    return {'pct': pct, 'resetInSec': 3600, 'stale': stale, 'live': True}


def _wire(claude, codex_=None):
    th = {'warn': 75, 'crit': 90}
    kartlar = [{'id': 'claude', 'name': 'Claude Code',
                'limits': {'session': claude[0], 'weekly': claude[1], 'thresholds': th},
                'spend': {'currency': 'USD', 'today': 1.0, 'yesterday': 1.0, 'last30d': 1.0}}]
    if codex_:
        kartlar.append({'id': 'codex', 'name': 'Codex', 'kind': 'tokens', 'status': 'partial',
                        'limits': {'session': codex_[0], 'weekly': codex_[1]}})
    return {'schema': 'usage/v1', 'thresholds': th, 'providers': kartlar}


DURUMLAR = {
    'ikisi düşük': ((_bar(11), _bar(18)), (_bar(20), _bar(30))),
    'codex uyarı': ((_bar(11), _bar(18)), (_bar(80), _bar(30))),
    'codex kritik': ((_bar(11), _bar(18)), (_bar(99), _bar(63))),
    'claude kritik': ((_bar(95), _bar(18)), (_bar(1), _bar(3))),
    'codex yok': ((_bar(11), _bar(18)), None),
    'bayat codex': ((_bar(11), _bar(18)), (_bar(99, True), _bar(63, True))),
    'bayat claude': ((_bar(95, True), _bar(18, True)), (_bar(10), _bar(5))),
    'bilinmiyor': ((_bar(None), _bar(None)), None),
    'hepsi bayat': ((_bar(95, True), _bar(18, True)), None),
}


def _telefon(wire):
    class Sahte:
        def __enter__(s):
            import io
            return io.BytesIO(json.dumps(wire).encode())

        def __exit__(s, *a):
            return False
    with mock.patch.object(rozet.urllib.request, 'urlopen', lambda *a, **k: Sahte()):
        return rozet.rozet()


class HerYuzeyAyniRenk(unittest.TestCase):
    """Y5 bitti ölçüsü: aynı wire, bütün yüzeyler aynı renk."""
    KARSILIK = {'ok': 'ok', 'warn': 'warn', 'crit': 'crit', 'off': 'unknown'}

    def test_cli_waybar_telefonla_ayni(self):
        for ad, (c, x) in DURUMLAR.items():
            with self.subTest(ad):
                w = _wire(c, x)
                self.assertEqual(self.KARSILIK[cli.render_waybar(w, 'live')['class']], _telefon(w)['class'])

    def test_tepsi_telefonla_ayni(self):
        for ad, (c, x) in DURUMLAR.items():
            with self.subTest(ad):
                w = _wire(c, x)
                self.assertEqual(self.KARSILIK[tray._summarize(w)[0]], _telefon(w)['class'])

    def test_guard_hala_saglayici_basina(self):
        # Kapı kararı başka iş: Codex %99 diye Claude işi durmaz.
        w = _wire((_bar(11), _bar(18)), (_bar(99), _bar(63)))
        self.assertEqual(cli.evaluate(w, provider='claude')['level'], 'ok')

    def test_cli_saglayici_secilince_yalniz_kendi_karti(self):
        w = _wire((_bar(95), _bar(18)), (_bar(10), _bar(20)))
        self.assertEqual(cli.render_waybar(w, 'live', 'codex')['class'], 'ok')


class BayatTahminYok(unittest.TestCase):
    """Y6 — son ölçüm 38 dk önceyken de "~1 dk içinde dolar" diyordu."""
    NOW = 1_800_000_000_000

    def _gozlem(self, yas_dk):
        return {'ms': self.NOW - yas_dk * 60_000, 'pct': 99, 'windowMinutes': 300,
                'resetsAt': (self.NOW + 2 * 3600 * 1000) // 1000}

    def test_taze_olcumde_tahmin_var(self):
        self.assertTrue(codex._limit_bar(self._gozlem(1), self.NOW)['forecast']['willExceed'])

    def test_bayat_olcumde_dolar_uyarisi_yok(self):
        self.assertIsNone(codex._limit_bar(self._gozlem(38), self.NOW)['forecast'])

    def test_yuzde_bayat_olcumde_de_gorunur(self):
        self.assertEqual(codex._limit_bar(self._gozlem(38), self.NOW)['pct'], 99)


@unittest.skipUnless(shutil.which('node'), 'node yok')
class AktifSayisi(unittest.TestCase):
    """Y11 — Codex canlı sayı gösterirken `partial` olduğu için "0/1 aktif" yazıyordu."""

    def test_partial_aktif_sayilir(self):
        src = (REPO / 'web' / 'app.js').read_text(encoding='utf-8')
        m = re.search(r'^function aktifSayisi\(list\) \{.*?^\}', src, re.M | re.S)
        self.assertIsNotNone(m, 'aktifSayisi bulunamadı')
        js = m.group(0) + ("\nconsole.log(aktifSayisi([{status:'ok'},{status:'partial'},"
                           "{status:'offline'},{status:'nodata'}]));")
        out = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), '2', out.stderr)


class SureMetni(unittest.TestCase):
    """Y13 — "≈ 69s 16dk": burada s saat demek, saniye ya da gün gibi okunuyor."""

    def _metin(self, saat):
        now = 1_800_000_000_000
        w = 7 * 24 * 3600 * 1000
        gecen = 24 * 3600 * 1000
        # %10'luk tempo: kalan %90 için tam `saat` kadar süre verecek yüzdeyi kur.
        pct = 100 * gecen / (gecen + saat * 3600 * 1000)
        f = engine._compute_forecast(pct, gecen, now + 30 * 24 * 3600 * 1000, now, w)
        return f['etaText']

    def test_gun_kademesi_ve_sa(self):
        self.assertRegex(self._metin(69), r'2g 2[01]sa')

    def test_saat_sa_yazilir(self):
        self.assertRegex(self._metin(2), r'[12]sa')
        self.assertNotRegex(self._metin(2), r'\d+s\b')

    @unittest.skipUnless(shutil.which('jq'), 'jq yok')
    def test_waybar_sure_birimi(self):
        src = (REPO / 'surface' / 'waybar-usage.sh').read_text(encoding='utf-8')
        self.assertIn('+"sa"', src.replace(' ', ''))
        self.assertNotIn('($h|tostring)+"s"', src.replace(' ', ''))

    def test_panel_birimi(self):
        src = (REPO / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("uDay: 'g', uHour: 'sa', uMin: 'dk'", src)


class AbartiliUyariYok(unittest.TestCase):
    """Y14 — "EKSİK" kartın en büyük metniydi; ölçüm: 49 dosyanın 9'u dışarıda, kayıp %0,06."""

    def _kart(self, n):
        with tempfile.TemporaryDirectory() as td:
            kok = Path(td) / 'sessions'
            kok.mkdir()
            for i in range(n):
                (kok / f'rollout-{i}.jsonl').write_text(json.dumps({
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'type': 'event_msg',
                    'payload': {'type': 'token_count', 'info': {'last_token_usage': {
                        'input_tokens': 1, 'output_tokens': 1, 'cached_input_tokens': 0}}}}) + '\n',
                    encoding='utf-8')
            with mock.patch.object(codex, 'SESSIONS_DIR', kok), mock.patch.object(codex, 'MAX_FILES', 2):
                codex._CACHE.clear() if hasattr(codex, '_CACHE') else None
                return codex.collect(30)

    def test_dosya_tavani_sakin_anlatilir(self):
        k = self._kart(4)
        self.assertTrue(k['truncated'])
        metin = ' '.join(k['warnings'])
        self.assertNotIn('EKSİK', metin)
        self.assertIn('en yeni 2 oturum', metin)
        self.assertIn('Limit yüzdesi etkilenmez', metin)
        self.assertNotIn('EKSİK', k.get('note', ''))


if __name__ == '__main__':
    unittest.main()

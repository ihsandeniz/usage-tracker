"""surface/statusline.py — oturumlar arası birleştirme: bayat oturum taze sayıyı ezmez."""
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'statusline', Path(__file__).resolve().parent.parent / 'surface' / 'statusline.py')
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)

NOW = 1_791_148_000.0
W5, W7 = NOW + 18_000, NOW + 280_000


def w(pct, reset):
    return {'used_percentage': pct, 'resets_at': reset}


class MergeTest(unittest.TestCase):
    def test_ayni_pencerede_buyuk_kalir(self):
        out = sl.merge({'seven_day': w(69.0, W7)}, {'seven_day': w(59.0, W7)}, NOW)
        self.assertEqual(out['seven_day']['used_percentage'], 69.0)
        out = sl.merge({'seven_day': w(59.0, W7)}, {'seven_day': w(70.0, W7 + 1)}, NOW)
        self.assertEqual(out['seven_day']['used_percentage'], 70.0)

    def test_eksik_pencere_mevcudu_silmez(self):
        out = sl.merge({'five_hour': w(12.0, W5), 'seven_day': w(69.0, W7)},
                       {'seven_day': w(69.0, W7)}, NOW)
        self.assertEqual(out['five_hour']['used_percentage'], 12.0)

    def test_eski_penceredeki_bayat_kayit_yok_sayilir(self):
        out = sl.merge({'five_hour': w(3.0, W5)}, {'five_hour': w(80.0, W5 - 18_000)}, NOW)
        self.assertEqual(out['five_hour']['used_percentage'], 3.0)

    def test_yeni_pencere_dusuk_yuzdeyle_gecer(self):
        out = sl.merge({'five_hour': w(90.0, NOW + 60)}, {'five_hour': w(0.0, W5)}, NOW)
        self.assertEqual(out['five_hour']['used_percentage'], 0.0)

    def test_suresi_dolmus_kayit_atilir(self):
        out = sl.merge({'five_hour': w(90.0, NOW - 10), 'seven_day': w(69.0, W7)},
                       {'seven_day': w(69.0, W7)}, NOW)
        self.assertNotIn('five_hour', out)

    def test_bozuk_eski_kayit(self):
        out = sl.merge({'five_hour': 'x', 'seven_day': {'used_percentage': None}},
                       {'five_hour': w(1.0, W5)}, NOW)
        self.assertEqual(out, {'five_hour': w(1.0, W5)})


    def test_suresi_dolmus_gelen_kabul_edilmez(self):
        """F003: sıfırlanmadan sonra boşta oturum eski %95'i geçmiş resets_at ile getirir."""
        self.assertEqual(sl.merge({}, {'five_hour': w(95.0, NOW - 3000)}, NOW), {})
        out = sl.merge({'five_hour': w(2.0, W5)}, {'five_hour': w(95.0, NOW - 3000)}, NOW)
        self.assertEqual(out['five_hour']['used_percentage'], 2.0)

    def test_esit_yuzdede_yeni_gozlem_kalir(self):
        o, n = dict(w(40.0, W7), at=NOW - 900), dict(w(40.0, W7), at=NOW - 5)
        self.assertEqual(sl.merge({'seven_day': o}, {'seven_day': n}, NOW)['seven_day']['at'], NOW - 5)
        self.assertEqual(sl.merge({'seven_day': n}, {'seven_day': o}, NOW)['seven_day']['at'], NOW - 5)


class GozlemAniTest(unittest.TestCase):
    """F004: kayıt zamanı yazım anı değil, verinin gözlendiği an."""

    def test_bosta_oturum_eski_zaman_getirir(self):
        import os, tempfile
        with tempfile.NamedTemporaryFile() as f:
            os.utime(f.name, (NOW - 3600, NOW - 3600))
            self.assertEqual(sl.gozlem_ani({'transcript_path': f.name}, NOW), NOW - 3600)

    def test_transcript_yoksa_simdi(self):
        self.assertEqual(sl.gozlem_ani({}, NOW), NOW)
        self.assertEqual(sl.gozlem_ani({'transcript_path': '/yok/boyle/bir/dosya'}, NOW), NOW)

    def test_uctan_uca_bosta_oturum_kaydi_bayat_kalir(self):
        import io, json, os, tempfile
        from unittest import mock
        from usage import live
        with tempfile.TemporaryDirectory() as td, tempfile.NamedTemporaryFile() as tp:
            eski = time_now() - 3600
            os.utime(tp.name, (eski, eski))
            payload = {'transcript_path': tp.name, 'rate_limits': {
                'five_hour': {'used_percentage': 40, 'resets_at': time_now() + 9000}}}
            with mock.patch('usage.platform.state_dir', return_value=Path(td)), \
                    mock.patch('sys.stdin', io.StringIO(json.dumps(payload))), \
                    mock.patch('sys.stdout', io.StringIO()):
                sl.main()
                at, _ = live._load_statusline()
            self.assertAlmostEqual(at, eski, delta=1)
            self.assertGreater(time_now() - at, live.LIVE_FRESHNESS_SEC)


class CompactHedefTest(unittest.TestCase):
    def test_override_yoksa_tampon(self):
        """F006: override'sız compact %95'te değil, pencere - ~33k'da."""
        from unittest import mock
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(sl.compact_hedef(200_000), 167_000)
            self.assertEqual(sl.compact_hedef(1_000_000), 967_000)

    def test_override(self):
        from unittest import mock
        with mock.patch.dict('os.environ', {'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE': '55'}):
            self.assertEqual(sl.compact_hedef(1_000_000), 550_000)
        for bozuk in ('nan', 'inf', '0', '150', 'x'):
            with mock.patch.dict('os.environ', {'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE': bozuk}):
                self.assertEqual(sl.compact_hedef(200_000), 167_000, bozuk)


class BudaTest(unittest.TestCase):
    def test_eski_gecmis_dosyalari_silinir(self):
        """F009: oturum başı dosyalar sınırsız birikiyordu."""
        import os, tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for ad, yas in (('statusline-ctx-eski.json', 8 * 86400), ('statusline-ctx-yeni.json', 60),
                            ('statusline-limits.json', 30 * 86400)):
                (d / ad).write_text('[]', encoding='utf-8')
                os.utime(d / ad, (NOW - yas, NOW - yas))
            sl.buda(d, NOW)
            self.assertEqual(sorted(p.name for p in d.iterdir()),
                             ['statusline-ctx-yeni.json', 'statusline-limits.json'])


def time_now():
    import time
    return time.time()

if __name__ == '__main__':
    unittest.main()

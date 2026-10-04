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


if __name__ == '__main__':
    unittest.main()

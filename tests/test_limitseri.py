"""usage/limitseri.py — limit yüzdesinin zaman serisi (v0.6 FAZ 1).

Kapılar: masaüstü geçmişinden `org` hiçbir çıktıya girmez · aynı değer ikinci kez yazılmaz ·
kancalar ağ isteği üretmez (yeni ağ isteği 0, 429 sayısı artmaz).
"""
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from usage import limitseri as L
from usage import live

FIX = Path(__file__).resolve().parent / 'fixtures' / 'limit'
KANARYA = 'org-kanarya-7f3e9c1d'
R = 1_791_166_000                                   # sabit sıfırlanma anı (epoch sn)

_spec = importlib.util.spec_from_file_location(
    'statusline_limitseri', Path(__file__).resolve().parent.parent / 'surface' / 'statusline.py')
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


def w(p, r=R):
    return {'used_percentage': p, 'resets_at': r}


def satirlar(dosya):
    try:
        return [json.loads(s) for s in Path(dosya).read_text(encoding='utf-8').splitlines() if s]
    except FileNotFoundError:
        return []


class AgYasak:
    """Kanca yolunda herhangi bir ağ denemesi testi düşürür."""

    def __enter__(self):
        self.cagri = []
        hata = lambda *a, **k: self.cagri.append(a) or (_ for _ in ()).throw(AssertionError('ağ!'))
        self._p = [mock.patch('urllib.request.urlopen', side_effect=hata),
                   mock.patch('socket.create_connection', side_effect=hata),
                   mock.patch('http.client.HTTPConnection.connect', side_effect=hata)]
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self._p:
            p.stop()


class GeriAlTest(unittest.TestCase):
    def test_465_ornek_okunur_org_hicbir_ciktida_yok(self):
        g = L.geri_al(FIX / 'masaustu-gecmis.json')
        self.assertEqual(g['durum'], 'ok')
        self.assertEqual(len({o['t'] for o in g['ornekler']}), 465)
        self.assertEqual(len(g['ornekler']), 930)                    # fh + sd
        dokum = json.dumps(g, ensure_ascii=False) + repr(g)
        self.assertNotIn(KANARYA, dokum)
        self.assertNotIn('org', dokum)
        for o in g['ornekler']:
            self.assertEqual(set(o), {'t', 'w', 'p', 'r', 'k'})

    def test_org_ayristirma_aninda_atilir(self):
        """`org` belleğe bile alınmaz: json'un kurduğu hiçbir sözlükte yok."""
        gorulen = []
        asil = L._yalniz_izinli
        with mock.patch.object(L, '_yalniz_izinli', side_effect=lambda c: gorulen.append(asil(c)) or gorulen[-1]):
            L.geri_al(FIX / 'masaustu-gecmis.json')
        self.assertTrue(gorulen)
        self.assertFalse(any('org' in d for d in gorulen))

    def test_degerler_ve_zaman_birimi(self):
        g = L.geri_al(FIX / 'masaustu-gecmis.json')['ornekler']
        ilk = [o for o in g if o['t'] == g[0]['t']]
        self.assertEqual({o['w']: o['p'] for o in ilk}, {'five_hour': 0.0, 'seven_day': 10.0})
        self.assertAlmostEqual(g[0]['t'], 1_790_105_765.502)         # ms → sn
        self.assertEqual(g, sorted(g, key=lambda o: o['t']))

    def test_bicim_degisirse_okunamadi_cokmez(self):
        g = L.geri_al(FIX / 'masaustu-gecmis-v3.json')
        self.assertEqual((g['durum'], g['ornekler']), ('okunamadi', []))
        self.assertNotIn(KANARYA, json.dumps(g))

    def test_dosya_yok_ve_bozuk_json(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(L.geri_al(Path(td) / 'yok.json')['durum'], 'yok')
            bozuk = Path(td) / 'b.json'
            bozuk.write_text('{"version": 2, "samples": [{"org": "%s", ' % KANARYA, encoding='utf-8')
            g = L.geri_al(bozuk)
            self.assertEqual(g['durum'], 'okunamadi')
            self.assertNotIn(KANARYA, json.dumps(g))

    def test_varsayilan_yol_xdg_config(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': td}), \
                mock.patch('usage.platform.SYS_PLATFORM', 'linux'):
            self.assertEqual(L.masaustu_yolu(), Path(td) / 'Claude' / 'plan-usage-history.json')

    def test_kaynak_dosya_degismez(self):
        with tempfile.TemporaryDirectory() as td:
            kopya = Path(td) / 'h.json'
            kopya.write_bytes((FIX / 'masaustu-gecmis.json').read_bytes())
            once = (kopya.read_bytes(), kopya.stat().st_mtime_ns)
            L.geri_al(kopya)
            self.assertEqual((kopya.read_bytes(), kopya.stat().st_mtime_ns), once)
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()), ['h.json'])

    @unittest.skipUnless(os.environ.get('UT_GERCEK_VERI') == '1',
                         'gerçek masaüstü geçmişi: UT_GERCEK_VERI=1 ile koş (hermetik değil)')
    def test_gercek_masaustu_gecmisi(self):
        g = L.geri_al()
        self.assertEqual(g['durum'], 'ok')
        self.assertGreaterEqual(len({o['t'] for o in g['ornekler']}), 465)
        self.assertNotIn('org', json.dumps(g))


class EkleTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dosya = Path(td.name) / 'limit-seri.jsonl'

    def test_ayni_deger_ikinci_kez_yazilmaz(self):
        self.assertTrue(L.ekle({'five_hour': w(41.0)}, 1000.0, 'statusline', self.dosya))
        self.assertFalse(L.ekle({'five_hour': w(41.0)}, 1060.0, 'statusline', self.dosya))
        self.assertFalse(L.ekle({'five_hour': w(41.0, R + 30)}, 1090.0, 'live', self.dosya))  # ±60 sn aynı pencere
        self.assertEqual(len(satirlar(self.dosya)), 1)
        self.assertTrue(L.ekle({'five_hour': w(42.0)}, 1200.0, 'statusline', self.dosya))
        self.assertEqual([s['w']['five_hour']['p'] for s in satirlar(self.dosya)], [41.0, 42.0])

    def test_cagri_basina_en_cok_bir_satir_yalniz_degisen_pencere(self):
        L.ekle({'five_hour': w(10.0), 'seven_day': w(50.0, R + 500_000)}, 1000.0, 'statusline', self.dosya)
        L.ekle({'five_hour': w(11.0), 'seven_day': w(50.0, R + 500_000)}, 1100.0, 'statusline', self.dosya)
        s = satirlar(self.dosya)
        self.assertEqual(len(s), 2)
        self.assertEqual(set(s[0]['w']), {'five_hour', 'seven_day'})
        self.assertEqual(set(s[1]['w']), {'five_hour'})

    def test_sifirlanmada_ayni_yuzde_yeni_pencere_yazilir(self):
        L.ekle({'five_hour': w(0.0, R)}, 1000.0, 'statusline', self.dosya)
        self.assertTrue(L.ekle({'five_hour': w(0.0, R + 18_000)}, 2000.0, 'statusline', self.dosya))

    def test_bozuk_yuzde_yazilmaz(self):
        """#52326: yüzde alanına sıfırlanma anı gelebiliyor."""
        for p in (R, -3, 100.5, float('nan'), float('inf'), '41', None, True):
            self.assertFalse(L.ekle({'five_hour': w(p)}, 1000.0, 'statusline', self.dosya), p)
        self.assertEqual(satirlar(self.dosya), [])

    def test_reset_bicimleri_epoch_sn(self):
        L.ekle({'five_hour': {'utilization': 7.04, 'resets_at': '2026-10-04T21:00:00.123+00:00'},
                'seven_day': {'utilization': 51.0, 'resets_at': 1_791_166_000_000}}, 1.0, 'live', self.dosya)
        v = satirlar(self.dosya)[0]['w']
        self.assertEqual(v['five_hour'], {'p': 7.0, 'r': 1_791_147_600})
        self.assertEqual(v['seven_day'], {'p': 51.0, 'r': 1_791_166_000})

    def test_hicbir_kosulda_istisna_atmaz(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(L.ekle({'five_hour': w(5.0)}, 1.0, 'x', Path(td)))     # dizin
        self.assertFalse(L.ekle('çöp', 1.0, 'x', self.dosya))
        self.assertFalse(L.ekle({'five_hour': w(5.0)}, None, 'x', self.dosya))

    def test_dosya_sahibine_ozel(self):
        L.ekle({'five_hour': w(5.0)}, 1.0, 'x', self.dosya)
        if os.name == 'posix':
            self.assertEqual(self.dosya.stat().st_mode & 0o777, 0o600)

    def test_son_deger_t_ye_gore_dosya_sirasina_gore_degil(self):
        self.dosya.write_text(
            '{"t":2000,"k":"x","w":{"five_hour":{"p":30,"r":%d}}}\n'
            '{"t":1000,"k":"x","w":{"five_hour":{"p":20,"r":%d}}}\n' % (R, R), encoding='utf-8')
        self.assertEqual(L.son_degerler(self.dosya)['five_hour']['p'], 30.0)
        self.assertFalse(L.ekle({'five_hour': w(30.0)}, 3000.0, 'x', self.dosya))


class OkuTest(unittest.TestCase):
    def test_senaryo_sirali_ve_suzgecli(self):
        o = L.oku(dosya=FIX / 'seri-senaryo.jsonl')
        self.assertEqual(len(o), 42)
        self.assertEqual(o, sorted(o, key=lambda x: x['t']))
        self.assertEqual(L.oku(pencere='seven_day', dosya=FIX / 'seri-senaryo.jsonl'), [])
        ilk = o[0]['t']
        self.assertEqual(len(L.oku(since=ilk + 1, dosya=FIX / 'seri-senaryo.jsonl')), 41)
        # sıfırlanma: yüzde düşer, r ileri atlar
        d = next(i for i in range(1, len(o)) if o[i]['p'] < o[i - 1]['p'])
        self.assertEqual((o[d - 1]['p'], o[d]['p']), (20.0, 0.0))
        self.assertGreater(o[d]['r'], o[d - 1]['r'])

    def test_bozuk_satirlar_atlanir(self):
        self.assertEqual(L.oku(dosya=FIX / 'seri-bozuk.jsonl'), [])
        self.assertEqual(L.oku(dosya=FIX / 'yok.jsonl'), [])


class BudaTest(unittest.TestCase):
    def test_sinir_asilinca_en_yeniler_kalir(self):
        with tempfile.TemporaryDirectory() as td:
            dosya = Path(td) / 'limit-seri.jsonl'
            dosya.write_text(''.join('{"t":%d,"k":"x","w":{"five_hour":{"p":%d,"r":null}}}\n' % (i, i % 100)
                                     for i in range(300)) + 'çöp\n', encoding='utf-8')
            self.assertFalse(L.buda(dosya, azami_bayt=10**9, kalan=50))
            self.assertTrue(L.buda(dosya, azami_bayt=1000, kalan=50))
            o = L.oku(dosya=dosya)
            self.assertEqual([x['t'] for x in o], [float(i) for i in range(250, 300)])
            self.assertNotIn('çöp', dosya.read_text(encoding='utf-8'))


class StatuslineKancaTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dizin = Path(td.name)
        p = mock.patch('usage.platform.state_dir', return_value=self.dizin)
        p.start()
        self.addCleanup(p.stop)

    def cagir(self, payload):
        with mock.patch('sys.stdin', io.StringIO(json.dumps(payload))), \
                mock.patch('sys.stdout', io.StringIO()):
            sl.main()

    def test_ayni_deger_tekrar_yazilmaz_degisince_bir_satir(self):
        r = time.time() + 9000
        with AgYasak() as ag:
            for _ in range(3):
                self.cagir({'rate_limits': {'five_hour': w(40, r), 'seven_day': w(60, r + 400_000)}})
            self.cagir({'rate_limits': {'five_hour': w(41, r), 'seven_day': w(60, r + 400_000)}})
        s = satirlar(self.dizin / L.DOSYA)
        self.assertEqual(len(s), 2)
        self.assertEqual(s[1]['w'], {'five_hour': {'p': 41.0, 'r': int(r)}})
        self.assertEqual({x['k'] for x in s}, {'statusline'})
        self.assertEqual(ag.cagri, [])

    def test_bosta_oturumun_bayat_degeri_seriyi_bozmaz(self):
        """Birleşik kayıt yazılır: bayat %59 taze %69'u ezmiyorsa seriye de girmez."""
        r = time.time() + 400_000
        self.cagir({'rate_limits': {'seven_day': w(69, r)}})
        self.cagir({'rate_limits': {'seven_day': w(59, r)}})
        self.assertEqual([x['p'] for x in L.oku(dosya=self.dizin / L.DOSYA)], [69.0])

    def test_bozuk_yuzde_52326(self):
        payload = json.loads((FIX / 'statusline-bozuk.json').read_text(encoding='utf-8'))
        payload['rate_limits']['seven_day']['resets_at'] = time.time() + 400_000
        self.cagir(payload)
        self.assertEqual([x['w'] for x in L.oku(dosya=self.dizin / L.DOSYA)], ['seven_day'])

    def test_seri_yazilamazsa_durum_satiri_yine_basar(self):
        out = io.StringIO()
        with mock.patch.object(L, 'ekle', side_effect=RuntimeError('disk')), \
                mock.patch('sys.stdin', io.StringIO(json.dumps({'rate_limits': {'five_hour': w(40, time.time() + 9000)}}))), \
                mock.patch('sys.stdout', out):
            sl.main()                                  # kanca hatası durum satırını düşürmez
        self.assertTrue((self.dizin / 'statusline-limits.json').exists())


class LiveKancaTest(unittest.TestCase):
    def setUp(self):
        self.eski = (live._CACHE, live._RETRY_AT)
        live._CACHE, live._RETRY_AT = None, 0.0
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dizin = Path(td.name)
        for p in (mock.patch('usage.platform.state_dir', return_value=self.dizin),
                  mock.patch.object(live, '_read_token', return_value=('token', 0, None))):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        live._CACHE, live._RETRY_AT = self.eski

    def yanit(self, p5, p7=51.0):
        return {'ok': True, 'error': None, 'raw': {}, 'windows': {
            'five_hour': {'utilization': p5, 'resets_at': '2026-10-04T21:00:00+00:00'},
            'seven_day': {'utilization': p7, 'resets_at': '2026-10-09T07:00:00+00:00'}}}

    def test_basarili_yanit_bir_satir_ayni_deger_tekrar_yazilmaz(self):
        raw = mock.Mock(side_effect=[self.yanit(7.0), self.yanit(7.0), self.yanit(8.0)])
        with AgYasak() as ag, mock.patch.object(live, '_fetch_raw', raw):
            for _ in range(3):
                live.fetch(force=True)
        self.assertEqual(raw.call_count, 3)                           # kanca ek istek üretmedi
        self.assertEqual(ag.cagri, [])
        s = satirlar(self.dizin / L.DOSYA)
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0]['k'], 'live')
        self.assertEqual(s[1]['w'], {'five_hour': {'p': 8.0, 'r': 1_791_147_600}})

    def test_basarisiz_ve_429_yazmaz(self):
        for cevap in ({'ok': False, 'error': 'offline', 'raw': None},
                      {'ok': False, 'error': 'HTTP 429', 'raw': None, 'rateLimited': True, 'retryAfterSec': 60}):
            live._CACHE, live._RETRY_AT = None, 0.0
            with mock.patch.object(live, '_fetch_raw', return_value=cevap):
                live.fetch(force=True)
        self.assertFalse((self.dizin / L.DOSYA).exists())

    def test_taze_durum_satiri_varken_ag_da_seri_de_yok(self):
        """Taze kayıt yolunda ağa çıkılmaz; seriyi zaten statusline kancası yazdı."""
        (self.dizin / 'statusline-limits.json').write_text(json.dumps(
            {'at': time.time(), 'windows': {'five_hour': w(12.0, time.time() + 9000)}}), encoding='utf-8')
        raw = mock.Mock(side_effect=AssertionError('ağ'))
        with AgYasak() as ag, mock.patch.object(live, '_fetch_raw', raw):
            live.fetch(force=True)
        self.assertEqual((raw.call_count, ag.cagri), (0, []))
        self.assertFalse((self.dizin / L.DOSYA).exists())


class AgsizlikTest(unittest.TestCase):
    def test_modul_ag_kutuphanesi_kullanmaz(self):
        kaynak = Path(L.__file__).read_text(encoding='utf-8')
        for yasak in ('urllib', 'socket', 'http.client', 'requests'):
            self.assertNotIn(yasak, kaynak)


if __name__ == '__main__':
    unittest.main()

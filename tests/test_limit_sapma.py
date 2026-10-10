"""Limit Kara Kutusu FAZ 3 — aralıklar, kayan temel çizgi, "2× hızlı" bayrağı, geriye dönük sınav.

Kapılar: "Mart 23" hızlı tüketimi yakalanır, sakin aralık bayraklanmaz · sıfırlanma, yüzde
düşüşü, < 5 puanlık aralık ve bozuk yüzde ölçüme girmez · yerel turn'ü olmayan aralık "hızlı"
sayılmaz · sınav çıktısında org ve oturum kimliği yok, ağ isteği yok.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usage import engine
from usage import limitseri as L

FIX = Path(__file__).resolve().parent / 'fixtures' / 'limit'
KANARYA = 'org-kanarya-7f3e9c1d'
T0 = 1774256400                 # 2026-03-23 09:00 UTC
SIFIR = T0 + 5 * 3600           # 14:00 — sıfırlanma
GUN = 86400


def senaryo(kayma=0):
    """seri-senaryo.jsonl → five_hour gözlemleri (isteğe bağlı gün kaydırmalı)."""
    out = []
    for s in (FIX / 'seri-senaryo.jsonl').read_text(encoding='utf-8').splitlines():
        d = json.loads(s)
        w = d['w']['five_hour']
        out.append({'t': d['t'] + kayma, 'w': 'five_hour', 'p': w['p'],
                    'r': w['r'] + kayma, 'k': d['k']})
    return out


def sabit_is(bas, bit, birim=1000.0, sid='s'):
    """[bas, bit) arasında dakikada bir turn, her biri `birim` — iş hızı sabit."""
    out, t = [], bas + 30
    while t < bit:
        out.append(engine.Turn(int(t * 1000), 'claude-opus-5-5', birim, int(birim), 0, 0, 0, 0,
                               f'm{t}', sid))
        t += 60
    return out


def gunun_isi(kayma=0):
    # Sakin pencere 09:00-13:00 (%0→%20) ve hızlı pencere 14:00-16:00 (%0→%20): AYNI iş hızı.
    return (sabit_is(T0 + kayma, T0 + 4 * 3600 + kayma)
            + sabit_is(SIFIR + kayma, SIFIR + 2 * 3600 + kayma))


class Mart23Test(unittest.TestCase):
    def test_dilimli_fikstur_hizli_pencere_bayraklanir_sakin_bayraklanmaz(self):
        ar = L.olc(senaryo(), gunun_isi(), 'five_hour', dilim=True)
        sakin = [a for a in ar if a['t1'] <= SIFIR - 3600]
        hizli = [a for a in ar if a['t0'] >= SIFIR]
        self.assertEqual(len(ar), 8)
        self.assertEqual((len(sakin), len(hizli)), (4, 4))
        self.assertFalse(any(a['bayrak'] for a in sakin))
        self.assertTrue(all(a['bayrak'] for a in hizli))
        # Fikstür örnekleri birkaç saniye kaymalı → turn sayısı ±1-2.
        for a in sakin:
            self.assertAlmostEqual(a['oran'], 12000, delta=500)        # ~60 turn × 1000 / 5
        for a in hizli:
            self.assertAlmostEqual(a['oran'], 6000, delta=500)
            self.assertLessEqual(a['kat'], 0.5)

    def test_kesintisiz_kip_onceki_gunlerle_temel_kurar(self):
        g, turns = [], []
        for gun in (3, 2, 1, 0):
            g += senaryo(-gun * GUN)
            turns += gunun_isi(-gun * GUN)
        ar = L.olc(g, turns, 'five_hour')
        self.assertEqual(len(ar), 8)                     # gün başına 2 kesintisiz parça
        bugun = [a for a in ar if a['t0'] >= T0]
        self.assertEqual([a['bayrak'] for a in bugun], [False, True])
        self.assertAlmostEqual(bugun[0]['oran'], 12000, delta=200)   # ~240 turn × 1000 / 20
        self.assertAlmostEqual(bugun[1]['oran'], 6000, delta=200)

    def test_hiz_yarim_degilse_bayrak_yok(self):
        # Hızlı pencerede iş %10 fazla → oran temelin 0,55'i → eşik altı değil.
        turns = sabit_is(T0, T0 + 4 * 3600) + sabit_is(SIFIR, SIFIR + 2 * 3600, birim=1100.0)
        ar = L.olc(senaryo(), turns, 'five_hour', dilim=True)
        self.assertFalse(any(a['bayrak'] for a in ar))


class KopusTest(unittest.TestCase):
    def g(self, t, p, r=None):
        return {'t': t, 'w': 'five_hour', 'p': p, 'r': r, 'k': 'x'}

    def test_sifirlanma_araliga_girmez(self):
        ar = L.olc(senaryo(), gunun_isi(), 'five_hour', dilim=True, temel_asgari=0)
        for a in ar:
            self.assertGreater(a['p1'], a['p0'])
            self.assertFalse(a['t0'] < SIFIR < a['t1'], a)

    def test_r_degisimi_yuzde_dusmese_de_kopus(self):
        # Pencere döndü ama yeni pencerede yüzde eskisinden yüksek: r farkı kopuş sayılır.
        g = [self.g(0, 0, 18000), self.g(600, 3, 18000), self.g(18600, 6, 36000),
             self.g(19200, 9, 36000)]
        self.assertEqual(len(L._kesintisiz(g, 'five_hour')), 2)

    def test_r_ileri_kaydiysa_gecilmeden_de_kopus(self):
        # Sıfırlanma anı pencere dolmadan değişti (erken sıfırlanma / plan değişimi).
        g = [self.g(0, 0, 18000), self.g(600, 3, 18000), self.g(1200, 6, 30000)]
        self.assertEqual(len(L._kesintisiz(g, 'five_hour')), 2)

    def test_r_bilinmiyorsa_yuzde_dususu_kopus(self):
        # Masaüstü nabzı (r yok): boşluk kısa, pencere süresi aşılmadı; tek kopuş nedeni düşüş.
        g = [self.g(0, 10), self.g(300, 12), self.g(600, 1), self.g(900, 7)]
        parcalar = L._kesintisiz(g, 'five_hour')
        self.assertEqual([[x['p'] for x in p] for p in parcalar], [[10, 12], [1, 7]])

    def test_r_gecildiyse_kopus(self):
        g = [self.g(0, 0, 3600), self.g(4000, 8, 3600)]           # r=3600 geçildi
        self.assertEqual(len(L._kesintisiz(g, 'five_hour')), 2)

    def test_r_bilinmiyorsa_on_dakikadan_uzun_bosluk_kopus(self):
        g = [self.g(0, 0), self.g(300, 2), self.g(300 + 601, 8)]
        self.assertEqual(len(L._kesintisiz(g, 'five_hour')), 2)
        g = [self.g(0, 0), self.g(300, 2), self.g(900, 8)]
        self.assertEqual(len(L._kesintisiz(g, 'five_hour')), 1)

    def test_r_bilinmiyorsa_pencereden_uzun_parca_bolunur(self):
        # 5 dk nabızla 6 saat %0'da bekleme: gizli sıfırlanma olabilir.
        g = [self.g(i * 300, 0 if i < 70 else i - 69) for i in range(80)]
        parcalar = L._kesintisiz(g, 'five_hour')
        self.assertGreater(len(parcalar), 1)
        for p in parcalar:
            self.assertLessEqual(p[-1]['t'] - p[0]['t'], 5 * 3600)

    def test_bes_puan_alti_olculmez(self):
        g = [self.g(0, 0, 18000), self.g(600, 4, 18000)]
        self.assertEqual(L.olc(g, sabit_is(0, 600), 'five_hour'), [])
        g.append(self.g(900, 5, 18000))
        self.assertEqual(len(L.olc(g, sabit_is(0, 900), 'five_hour')), 1)

    def test_cift_yazim_tek_sayilir(self):
        g = [self.g(0, 0, 18000), self.g(10, 0, 18000), self.g(600, 5, 18000),
             self.g(601, 5, 18000), self.g(1200, 10, 18000)]
        ar = L.olc(g, sabit_is(0, 1200), 'five_hour', dilim=True, temel_asgari=0)
        self.assertEqual([(a['p0'], a['p1']) for a in ar], [(0, 5), (5, 10)])
        self.assertEqual(ar[0]['t0'], 0)

    def test_bozuk_yuzde_seriye_girmez(self):
        self.assertEqual(L.oku(dosya=FIX / 'seri-bozuk.jsonl'), [])
        g = L.gozlemler('five_hour', seri_dosya=FIX / 'seri-bozuk.jsonl', masaustu=False)
        self.assertEqual(g, [])

    def test_yerel_kayit_yoksa_hizli_sayilmaz_temele_girmez(self):
        ar = L.olc(senaryo(), sabit_is(T0, T0 + 4 * 3600), 'five_hour', dilim=True)
        hizli = [a for a in ar if a['t0'] >= SIFIR]
        self.assertTrue(hizli)
        self.assertTrue(all(a['durum'] == 'yerel-kayit-yok' and not a['bayrak'] and
                            a['oran'] is None for a in hizli))
        self.assertEqual(L.ozet(ar)['yerel_kayit_yok'], 4)
        self.assertEqual(L.ozet(ar)['olculen'], 4)

    def test_temel_yetersizse_bayrak_yok(self):
        ar = L.olc(senaryo(), gunun_isi(), 'five_hour')          # yalnız 2 parça
        self.assertEqual([a['durum'] for a in ar], ['temel-yok', 'temel-yok'])
        self.assertFalse(any(a['bayrak'] for a in ar))


class OzetTest(unittest.TestCase):
    def test_cv(self):
        ar = [{'oran': 100.0, 'durum': 'tamam', 'bayrak': False},
              {'oran': 300.0, 'durum': 'tamam', 'bayrak': True},
              {'oran': None, 'durum': 'yerel-kayit-yok', 'bayrak': False}]
        o = L.ozet(ar)
        self.assertEqual((o['aralik'], o['olculen'], o['bayrak'], o['yerel_kayit_yok']),
                         (3, 2, 1, 1))
        self.assertEqual(o['medyan'], 200.0)
        self.assertEqual(o['cv'], 0.5)                           # pstdev 100 / ortalama 200


class SinavTest(unittest.TestCase):
    """Uçtan uca: fikstür masaüstü geçmişi + seri + transcript'ler, gerçek ev dizini yok."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        kok = Path(self._td.name) / 'projects' / '-sentetik'
        kok.mkdir(parents=True)
        for ad in ('oturum-a', 'oturum-b', 'oturum-c'):
            shutil.copy(FIX / 'transcripts' / f'{ad}.jsonl', kok / f'{ad}.jsonl')
        self._p = mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', kok.parent)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._td.cleanup()

    def test_cikti_gizli_alan_tasimaz_ve_ag_yok(self):
        from tests.test_limitseri import AgYasak
        with AgYasak() as ag:
            s = L.sinav(seri_dosya=FIX / 'seri-senaryo.jsonl',
                        masaustu=FIX / 'masaustu-gecmis.json')
        self.assertEqual(ag.cagri, [])
        metin = json.dumps(s, ensure_ascii=False)
        for yasak in (KANARYA, 'org', 'sentetik-a', 'sentetik-b', 'sentetik-c', 'msg_', 'req_',
                      self._td.name, os.path.expanduser('~')):
            self.assertNotIn(yasak, metin)
        self.assertEqual(s['turn'], 35)
        self.assertIn('five_hour', s['pencere'])
        a = [x for x in s['pencere']['five_hour']['araliklar'] if x['turn']]
        self.assertTrue(a)
        self.assertTrue(all(0 < x['tepe_oturum'] <= 1 for x in a))

    def test_seri_basladiktan_sonra_masaustu_ornekleri_atilir(self):
        seri = L.oku(pencere='five_hour', dosya=FIX / 'seri-senaryo.jsonl')
        g = L.gozlemler('five_hour', FIX / 'seri-senaryo.jsonl', FIX / 'masaustu-gecmis.json')
        mas = [x for x in g if x['k'] == 'masaustu']
        self.assertTrue(all(x['t'] < seri[0]['t'] for x in mas))
        self.assertEqual(g[len(mas):], seri)


@unittest.skipUnless(os.environ.get('UT_GERCEK_VERI') == '1',
                     'gerçek masaüstü geçmişi + transcript (yalnız UT_GERCEK_VERI=1)')
class GercekVeriSinavTest(unittest.TestCase):
    def test_bes_saat_cv_oldurme_olcutunun_altinda(self):
        s = L.sinav(seri_dosya=Path(tempfile.gettempdir()) / 'olmayan-limit-seri.jsonl')
        o = s['pencere']['five_hour']['ozet']
        self.assertGreaterEqual(o['olculen'], 10)
        self.assertLess(o['cv'], 0.5)
        self.assertNotIn('"org"', json.dumps(s))


if __name__ == '__main__':
    unittest.main()

"""Limit Kara Kutusu FAZ 2 — aralık dökümü + tekilleştirme.

Kapılar: iki yüzde gözlemi arasındaki turn'ler oturum / model / alt ajan / önbellek-okuma
paylarına doğru bölünür (sentetik 3 oturum, ±%1) · aynı yanıtın tekrar satırı bir kez sayılır ·
Turn'e eklenen alanlar eski konumsal kullanımı bozmaz.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usage import engine
from usage import limitseri as L

FIX = Path(__file__).resolve().parent / 'fixtures' / 'limit'
T0 = 1774256400                     # 2026-03-23 09:00 UTC (seri-senaryo.jsonl ile aynı gün)


def _satir(ts, mid=None, req=None, sid='s', side=None, model='claude-opus-5-5', out=50):
    o = {'type': 'assistant', 'sessionId': sid, 'timestamp': ts,
         'message': {'model': model, 'role': 'assistant',
                     'usage': {'input_tokens': 100, 'output_tokens': out,
                               'cache_creation_input_tokens': 0,
                               'cache_read_input_tokens': 0}}}
    if mid is not None:
        o['message']['id'] = mid
    if req is not None:
        o['requestId'] = req
    if side is not None:
        o['isSidechain'] = side
    return json.dumps(o)


class _Projeler(unittest.TestCase):
    """Geçici ~/.claude/projects; gerçek ev dizini okunmaz."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.kok = Path(self._td.name) / 'projects'
        self.kok.mkdir()
        self._p = mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', self.kok)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._td.cleanup()

    def yaz(self, rel, satirlar):
        f = self.kok / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('\n'.join(satirlar) + '\n', encoding='utf-8')
        return f


class UcOturumDokumTest(_Projeler):
    """Fikstür: a opus 10 turn (+1 tekrar) · b sonnet 20 · c opus 5 alt ajan.
    Turn başına birim = ağırlık × (50×4 + 100 + 1000 + 5000×0,1) = ağırlık × 1800.
    a = 18000 · b = 20 × 450 = 9000 · c = 9000 → toplam 36000."""

    def setUp(self):
        super().setUp()
        d = self.kok / '-sentetik'
        d.mkdir()
        for ad in ('oturum-a', 'oturum-b', 'oturum-c'):
            shutil.copy(FIX / 'transcripts' / f'{ad}.jsonl', d / f'{ad}.jsonl')
        self.turns = engine._scan_turns(0)

    def test_tekrar_satiri_bir_kez_sayilir(self):
        self.assertEqual(len(self.turns), 35)

    def test_paylar(self):
        d = L.aralik_dokumu(self.turns)
        self.assertEqual(d['turn'], 35)
        self.assertAlmostEqual(d['birim'], 36000, delta=1)
        self.assertEqual(d['ham'], 35 * 6150)
        self.assertAlmostEqual(d['oturum']['sentetik-a'], 0.50, delta=0.01)
        self.assertAlmostEqual(d['oturum']['sentetik-b'], 0.25, delta=0.01)
        self.assertAlmostEqual(d['oturum']['sentetik-c'], 0.25, delta=0.01)
        self.assertAlmostEqual(d['model']['opus'], 0.75, delta=0.01)
        self.assertAlmostEqual(d['model']['sonnet'], 0.25, delta=0.01)
        self.assertAlmostEqual(d['alt_ajan'], 0.25, delta=0.01)
        # önbellek-okuma birimi: 15 opus × 500 + 20 sonnet × 125 = 10000 → 10000/36000
        self.assertAlmostEqual(d['onbellek_okuma'], 10000 / 36000, delta=0.01)
        self.assertAlmostEqual(sum(d['oturum'].values()), 1.0, delta=0.001)
        self.assertAlmostEqual(sum(d['model'].values()), 1.0, delta=0.001)

    def test_yeni_alanlar_dolu(self):
        c = [t for t in self.turns if t.sid == 'sentetik-c']
        self.assertEqual(len(c), 5)
        self.assertTrue(all(t.side for t in c))
        self.assertFalse(any(t.side for t in self.turns if t.sid != 'sentetik-c'))
        self.assertTrue(all(t.req.startswith('req_') for t in self.turns))
        self.assertTrue(all(t.ver == '2.1.197' for t in self.turns))

    def test_aralik_sinirlari_t0_haric_t1_dahil(self):
        # a'nın turn'leri 09:01, 09:11 … 10:31; (09:01, 09:21] → a001, a002 (+ b, c'nin o arası)
        r = T0 + 5 * 3600
        g0 = {'t': T0 + 60, 'p': 0.0, 'r': r, 'k': 'x', 'w': 'five_hour'}
        g1 = {'t': T0 + 21 * 60, 'p': 5.0, 'r': r, 'k': 'x', 'w': 'five_hour'}
        ar = L.olc([g0, g1], self.turns, 'five_hour', temel_asgari=0)
        self.assertEqual(len(ar), 1)
        a = [s for s in ar[0]['dokum']['oturum']]
        self.assertIn('sentetik-a', a)
        # a: 09:11, 09:21 (2) · b: 09:01:07, 09:11:07 (2) · c: 09:01:13, 09:11:13 (2)
        self.assertEqual(ar[0]['dokum']['turn'], 6)

    def test_bos_aralik(self):
        d = L.aralik_dokumu([])
        self.assertEqual((d['turn'], d['birim'], d['alt_ajan'], d['onbellek_okuma']),
                         (0, 0, 0.0, 0.0))
        self.assertEqual(d['oturum'], {})


class AltAjanYoluTest(_Projeler):
    def test_subagents_yolu_isSidechain_olmadan_da_alt_ajan(self):
        self.yaz('-p/ana.jsonl', [_satir('2026-03-23T09:01:00Z', 'm1', 'r1')])
        self.yaz('-p/oturum/subagents/agent-x.jsonl', [_satir('2026-03-23T09:02:00Z', 'm2', 'r2')])
        t = {x.mid: x for x in engine._scan_turns(0)}
        self.assertFalse(t['m1'].side)
        self.assertTrue(t['m2'].side)

    def test_kok_adi_subagents_ise_ana_oturum_alt_ajan_sayilmaz(self):
        # Yalnız projects/ altındaki yol bölümlerine bakılır; kök dizinin kendi adı sayılmaz.
        kok = self.kok.parent / 'subagents' / 'projects'
        kok.mkdir(parents=True)
        f = kok / '-p' / 'ana.jsonl'
        f.parent.mkdir()
        f.write_text(_satir('2026-03-23T09:01:00Z', 'm1', 'r1') + '\n', encoding='utf-8')
        with mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', kok):
            self.assertFalse(engine._scan_turns(0)[0].side)


class TekillestirmeTest(_Projeler):
    def say(self, satirlar):
        self.yaz('-p/x.jsonl', satirlar)
        return len(engine._scan_turns(0))

    def test_ayni_mesaj_ayni_istek_bir(self):
        self.assertEqual(self.say([_satir('2026-03-23T09:01:00Z', 'm1', 'r1')] * 2), 1)

    def test_ayni_mesaj_farkli_istek_bir(self):
        # message.id yanıt başına tekil → aynı yanıt; istek kimliği farkı ikinci kez saydırmaz.
        self.assertEqual(self.say([_satir('2026-03-23T09:01:00Z', 'm1', 'r1'),
                                   _satir('2026-03-23T09:01:00Z', 'm1', 'r2')]), 1)

    def test_mesaj_kimligi_yoksa_istek_kimligi_tekillestirir(self):
        self.assertEqual(self.say([_satir('2026-03-23T09:01:00Z', None, 'r1')] * 2), 1)
        self.assertEqual(self.say([_satir('2026-03-23T09:01:00Z', None, 'r1'),
                                   _satir('2026-03-23T09:02:00Z', None, 'r2')]), 2)

    def test_iki_kimlik_de_yoksa_tekillestirilemez(self):
        self.assertEqual(self.say([_satir('2026-03-23T09:01:00Z')] * 2), 2)

    def test_mesaj_ve_istek_anahtarlari_carpismaz(self):
        # Bir satırın mid'i başka bir satırın req'iyle aynı metin olsa da ikisi ayrı yanıttır.
        self.assertEqual(self.say([_satir('2026-03-23T09:01:00Z', 'x1', 'r9'),
                                   _satir('2026-03-23T09:02:00Z', None, 'x1')]), 2)

    def test_dosyalar_arasi_tekrar(self):
        # Devam ettirilen oturum geçmişi yeni dosyaya kopyalar: aynı satır iki dosyada.
        s = _satir('2026-03-23T09:01:00Z', 'm1', 'r1')
        self.yaz('-p/eski.jsonl', [s])
        self.assertEqual(self.say([s]), 1)


class TurnUyumTest(unittest.TestCase):
    def test_eski_dokuz_alanli_kurulus_calisir(self):
        t = engine.Turn(1, 'claude-opus-5-5', 1.0, 1, 1, 0, 0, 0, 'm')
        self.assertEqual((t.sid, t.req, t.side, t.ver), ('', '', False, ''))


if __name__ == '__main__':
    unittest.main()

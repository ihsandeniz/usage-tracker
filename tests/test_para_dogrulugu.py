"""Para doğruluğu — 2026-11-05 taramasının R1, R2, Y1, Y2, Y3 bulguları.

Panel 30 günü 5.079 $ gösteriyordu, resmî fiyatla doğrusu ~5.774 $ (yanında "tam fiyat"
yazarken). Temiz kurulum ise aynı kullanımı 9.666 $ gösteriyordu. 590 testin hiçbiri
yakalamadı, çünkü hiçbir fikstür gerçek günlük biçimini taşımıyordu:
  - 1 saatlik önbellek yazımı `usage.cache_creation.ephemeral_1h_input_tokens` altında,
  - bir yanıt birkaç satıra yazılıyor ve `output_tokens` son satırda tamamlanıyor,
  - Haiku 5.5'in 100.000 üstü fiyatı katalogda `tiers` altında.

Fiyatlar: https://platform.claude.com/docs/en/about-claude/pricing (2026-11-05).
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from usage import catalog, engine, pricing

from tests.test_pricing import _IsolatedCatalog

OPUS55 = {'input': 4, 'output': 20, 'cache_read': 0.2, 'cache_write': 5}
HAIKU55 = {'input': 0.1, 'output': 0.5, 'cache_read': 0.01, 'cache_write': 0.125,
           'tiers': [{'input': 0.5, 'output': 2.5, 'cache_read': 0.05, 'cache_write': 0.625,
                      'tier': {'type': 'context', 'size': 100000}}]}


def _satir(mid, out=10, ts='2026-11-05T09:00:00Z', model='claude-opus-5-5', inp=1, cc=0, cc1h=None,
           cr=0, sid='s'):
    usage = {'input_tokens': inp, 'output_tokens': out,
             'cache_creation_input_tokens': cc, 'cache_read_input_tokens': cr}
    if cc1h is not None:
        usage['cache_creation'] = {'ephemeral_5m_input_tokens': cc - cc1h,
                                   'ephemeral_1h_input_tokens': cc1h}
    return json.dumps({'type': 'assistant', 'sessionId': sid, 'timestamp': ts, 'requestId': 'r-' + mid,
                       'message': {'id': mid, 'model': model, 'role': 'assistant', 'usage': usage}})


class _Projeler(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.kok = Path(self._td.name) / 'projects'
        self.kok.mkdir()
        p = mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', self.kok)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)
        engine._CACHE.clear()
        self.addCleanup(engine._CACHE.clear)

    def yaz(self, ad, satirlar):
        f = self.kok / ad
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('\n'.join(satirlar) + '\n', encoding='utf-8')


class BirSaatlikOnbellek(_IsolatedCatalog):
    """R1 — 1 saatlik yazım tabanın 2 katı, 5 dakikalık 1,25 katı. Claude Code yazımlarının
    çoğu 1 saatlik: hepsini 1,25 kat saymak 30 günde yaklaşık 630 $ eksik gösteriyordu."""

    def setUp(self):
        super().setUp()
        self.write_bundle({'anthropic': {'claude-opus-5-5': OPUS55, 'claude-haiku-5-5': HAIKU55}})
        pricing.invalidate()

    def test_bir_saatlik_yazim_tabanin_iki_kati(self):
        usd, kaynak = pricing.turn_cost_usd(0, 0, 1_000_000, 0, 'claude-opus-5-5', cc1h=1_000_000)
        self.assertAlmostEqual(usd, 8.0)
        self.assertEqual(kaynak, 'catalog')

    def test_karisik_yazim_ikiye_bolunur(self):
        usd, _ = pricing.turn_cost_usd(0, 0, 1_000_000, 0, 'claude-opus-5-5', cc1h=250_000)
        self.assertAlmostEqual(usd, 0.75 * 5 + 0.25 * 8)

    def test_eski_cagri_bicimi_bozulmaz(self):
        # sef.py ve dış betikler beş argümanla çağırıyor.
        usd, _ = pricing.turn_cost_usd(0, 0, 1_000_000, 0, 'claude-opus-5-5')
        self.assertAlmostEqual(usd, 5.0)

    def test_saglam_olmayan_alt_alan_toplami_asamaz(self):
        usd, _ = pricing.turn_cost_usd(0, 0, 1_000, 0, 'claude-opus-5-5', cc1h=999_999_999)
        self.assertAlmostEqual(usd, 1_000 * 8 / 1e6)


class SonSatirVeBirSaat(_Projeler):
    """Y1 + R1 — günlükten okuma tarafı."""

    def test_motor_bir_saatlik_alani_okur(self):
        self.yaz('-p/a.jsonl', [_satir('m1', cc=1000, cc1h=800)])
        t = engine._scan_turns(0)[0]
        self.assertEqual((t.cc, t.cc1h), (1000, 800))

    def test_alan_yoksa_sifir(self):
        self.yaz('-p/a.jsonl', [_satir('m1', cc=1000)])
        self.assertEqual(engine._scan_turns(0)[0].cc1h, 0)

    def test_bozuk_alan_tarayiciyi_dusurmez(self):
        o = json.loads(_satir('m1', cc=1000))
        o['message']['usage']['cache_creation'] = 'bozuk'
        self.yaz('-p/a.jsonl', [json.dumps(o)])
        self.assertEqual(engine._scan_turns(0)[0].cc1h, 0)

    def test_son_satirin_ciktisi_sayilir(self):
        self.yaz('-p/a.jsonl', [_satir('m1', out=8), _satir('m1', out=8), _satir('m1', out=900)])
        turns = engine._scan_turns(0)
        self.assertEqual(len(turns), 1, 'aynı yanıt tek sayılmalı')
        self.assertEqual(turns[0].out, 900)

    def test_limit_birimi_de_son_satirdan(self):
        self.yaz('-p/a.jsonl', [_satir('m1', out=8, inp=0), _satir('m1', out=900, inp=0)])
        self.assertAlmostEqual(engine._scan_turns(0)[0].units, 900 * 4 * 1.0)

    def test_zaman_ilk_satirda_kalir(self):
        self.yaz('-p/a.jsonl', [_satir('m1', ts='2026-11-05T09:00:00Z'),
                                _satir('m1', out=99, ts='2026-11-05T09:30:00Z')])
        t = engine._scan_turns(0)[0]
        self.assertEqual(t.ms, engine._iso_to_ms('2026-11-05T09:00:00Z'))

    def test_dosyalar_arasi_kopyada_tam_olan_kazanir(self):
        # Yarıda kesilmiş kopya önce taransa da tam satır sayılır; dosya sırası fark etmez.
        self.yaz('-p/a.jsonl', [_satir('m1', out=8)])
        self.yaz('-p/b.jsonl', [_satir('m1', out=900)])
        turns = engine._scan_turns(0)
        self.assertEqual([t.out for t in turns], [900])

    def test_kimliksiz_satirlar_birlesmez(self):
        ham = json.loads(_satir('m1'))
        del ham['message']['id'], ham['requestId']
        self.yaz('-p/a.jsonl', [json.dumps(ham), json.dumps(ham)])
        self.assertEqual(len(engine._scan_turns(0)), 2)


class HarcamaUcuUca(_IsolatedCatalog):
    """Bütün zincir: JSONL → compute_spend. Kırılan tek halka bunu düşürür."""

    def test_toplam_bir_saatlik_ve_son_satiri_gorur(self):
        self.write_bundle({'anthropic': {'claude-opus-5-5': OPUS55}})
        pricing.invalidate()
        kok = self.tmp / 'projects' / '-p'
        kok.mkdir(parents=True)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        (kok / 'a.jsonl').write_text('\n'.join([
            _satir('m1', out=10, ts=ts, inp=0, cc=1_000_000, cc1h=1_000_000),
            _satir('m1', out=1_000_000, ts=ts, inp=0, cc=1_000_000, cc1h=1_000_000),
        ]) + '\n', encoding='utf-8')
        engine._CACHE.clear()
        self.addCleanup(engine._CACHE.clear)
        with mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', self.tmp / 'projects'):
            s = engine.compute_spend(30)
        self.assertAlmostEqual(s['total'], 8.0 + 20.0)
        self.assertEqual(s['tokensTotal']['cache_write'], 1_000_000)


class HaikuFiyatKatmani(_IsolatedCatalog):
    """Y2 — prompt 100.000'i aşınca her kalem 5 kat. Resmî tanım: prompt uzunluğu girdi +
    önbellek okuma + önbellek yazma; her istek kendi başına fiyatlanır."""

    def setUp(self):
        super().setUp()
        self.write_bundle({'anthropic': {'claude-haiku-5-5': HAIKU55, 'claude-opus-5-5': OPUS55}})
        pricing.invalidate()

    def test_esikte_alt_fiyat(self):
        usd, _ = pricing.turn_cost_usd(0, 1_000_000, 0, 100_000, 'claude-haiku-5-5')
        self.assertAlmostEqual(usd, 0.5 + 100_000 * 0.01 / 1e6)

    def test_esigin_ustunde_her_kalem_ust_fiyat(self):
        usd, _ = pricing.turn_cost_usd(1, 1_000_000, 0, 100_000, 'claude-haiku-5-5')
        self.assertAlmostEqual(usd, 2.5 + 100_000 * 0.05 / 1e6 + 1 * 0.5 / 1e6)

    def test_esigi_onbellek_yazimi_da_doldurur(self):
        usd, _ = pricing.turn_cost_usd(0, 0, 100_001, 0, 'claude-haiku-5-5')
        self.assertAlmostEqual(usd, 100_001 * 0.625 / 1e6)

    def test_ust_katmanda_bir_saatlik_yazim_o_katmanin_iki_kati(self):
        usd, _ = pricing.turn_cost_usd(0, 0, 200_000, 0, 'claude-haiku-5-5', cc1h=200_000)
        self.assertAlmostEqual(usd, 200_000 * 1.0 / 1e6)

    def test_katmanli_olmayan_model_etkilenmez(self):
        usd, _ = pricing.turn_cost_usd(0, 0, 0, 5_000_000, 'claude-opus-5-5')
        self.assertAlmostEqual(usd, 1.0)

    def test_gomulu_katalog_kaydi_katmani_korur(self):
        # Katalog yolu ikisi de: kırpılmış biçim (gömülü/kullanıcı) ve ham models.dev (Hermes).
        providers, _ = catalog._from_own_shape({'providers': {'anthropic': {'h': HAIKU55}}})
        self.assertEqual(providers['anthropic']['h']['tiers'][0]['tier']['size'], 100000)
        ham = catalog._from_models_dev_shape({'anthropic': {'models': {'h': {'cost': HAIKU55}}}})
        self.assertEqual(ham['anthropic']['h']['tiers'][0]['input'], 0.5)

    def test_bozuk_katman_taban_fiyata_duser(self):
        bozuk = dict(HAIKU55, tiers=[{'input': 'x'}, 'y', {'tier': {'size': 'z'}, 'input': 9}])
        self.write_bundle({'anthropic': {'claude-haiku-5-5': bozuk}})
        pricing.invalidate()
        usd, _ = pricing.turn_cost_usd(0, 1_000_000, 0, 500_000, 'claude-haiku-5-5')
        self.assertAlmostEqual(usd, 0.5 + 500_000 * 0.01 / 1e6)


class SonnetBesFiyati(unittest.TestCase):
    """Y3 — 1 Eylül zammı iptal edildi, 2/10 $ kalıcı. Yalıtılmamış: depodaki gerçek dosya."""

    def test_zam_tarihinden_sonra_da_iki_on(self):
        pricing.invalidate()
        fiyat, kaynak = pricing.resolve_price('claude-sonnet-5', today=date(2026, 11, 5))
        self.assertEqual((fiyat['input'], fiyat['output']), (2, 10))
        self.assertEqual(fiyat['cache_read'], 0.2)
        self.assertEqual(kaynak, 'official')


class GomuluKatalogKapisi(_IsolatedCatalog):
    """R2 — 86 günlük katalog Claude 5.5 ailesini tanımıyordu ve temiz kurulum 9.666 $
    gösteriyordu. Sürüm kapısı: eski ya da güncel Claude modeli eksik katalog yayımlanamaz."""

    def setUp(self):
        super().setUp()
        self.TAM = {m: OPUS55 for m in catalog.GEREKLI_MODELLER}

    def test_taze_ve_tam_katalog_gecer(self):
        self.write_bundle({'anthropic': self.TAM}, generated_at='2026-11-01')
        self.assertEqual(catalog.gomulu_sorunlari(bugun=date(2026, 11, 5)), [])

    def test_eski_katalog_kalir(self):
        self.write_bundle({'anthropic': self.TAM}, generated_at='2026-08-11')
        sorun = catalog.gomulu_sorunlari(bugun=date(2026, 11, 5))
        self.assertEqual(len(sorun), 1)
        self.assertIn('86', sorun[0])

    def test_eksik_model_kalir(self):
        eksik = {k: v for k, v in self.TAM.items() if k != 'claude-opus-5-5'}
        self.write_bundle({'anthropic': eksik}, generated_at='2026-11-01')
        sorun = catalog.gomulu_sorunlari(bugun=date(2026, 11, 5))
        self.assertEqual(len(sorun), 1)
        self.assertIn('claude-opus-5-5', sorun[0])

    def test_tarihsiz_katalog_kalir(self):
        with mock.patch.object(catalog, '_read_bundled', return_value=({'anthropic': self.TAM}, {})):
            self.assertTrue(catalog.gomulu_sorunlari(bugun=date(2026, 11, 5)))

    def test_komut_cikis_kodu(self):
        self.write_bundle({'anthropic': self.TAM}, generated_at='2020-01-01')
        with mock.patch('builtins.print'):
            self.assertEqual(catalog._main(['--check-bundle']), 1)
        self.write_bundle({'anthropic': self.TAM}, generated_at=date.today().isoformat())
        with mock.patch('builtins.print'):
            self.assertEqual(catalog._main(['--check-bundle']), 0)


class DepodakiKatalogGuncel(unittest.TestCase):
    """Yalıtılmamış: depoya işlenmiş dosya. Yaşı CI'daki `--check-bundle` tutar; burada yaş
    bakılmaz, yoksa zaman geçince ilgisiz her test koşusu kırmızıya düşerdi."""

    def test_guncel_claude_modelleri_ve_haiku_katmani_gomulu(self):
        providers, meta = catalog._read_bundled()
        anthropic = providers['anthropic']
        for m in catalog.GEREKLI_MODELLER:
            self.assertIn(m, anthropic, f'{m} gömülü katalogda yok')
        self.assertEqual(anthropic['claude-haiku-5-5']['tiers'][0]['tier']['size'], 100000)
        self.assertEqual(anthropic['claude-opus-5-5']['input'], 4)


if __name__ == '__main__':
    unittest.main()

"""Limit Kara Kutusu FAZ 5 — `usage kanit`: paylaşılabilir kanıt + Codex (deneysel).

Kapılar: kanıtta istem, yol, proje adı, oturum kimliği, git dalı, hesap (org) YOK — transcript'e
kanarya değerler gömülür ve çıktıda aranır · sızıntı taraması bir alan kaçarsa basmayı durdurur ·
model adı ve istemci sürümü beyaz listeden geçer · Codex CV > %50 ise oran/bayrak verilmez ·
ağ isteği 0.
"""
import contextlib
import io
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from unittest import mock

from usage import cli, engine
from usage import limitseri as L
from usage.providers import codex
from tests.test_limit_sapma import senaryo, T0, SIFIR, GUN, FIX, KANARYA
from tests.test_limitseri import AgYasak

UUID = '3f2b9c1e-7a4d-4e2f-9b1c-5d6e7f8a9b0c'
KANARYALAR = (KANARYA, UUID, '/home/kanarya-kisi', 'kanarya-kisi', 'gizli-proje-adi',
              'gizli-dal-xyz', 'GIZLI-ISTEM-123', 'msg_kanarya', 'req_kanarya', 'org')


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def seri_yaz(dosya, gozlemler):
    with open(dosya, 'w', encoding='utf-8') as f:
        for g in gozlemler:
            f.write(json.dumps({'t': g['t'], 'k': g['k'],
                                'w': {'five_hour': {'p': g['p'], 'r': g['r']}}}) + '\n')


def gunler():
    g = []
    for gun in (3, 2, 1, 0):
        g += senaryo(-gun * GUN)
    return g


def is_zamanlari():
    """Her gün: sakin pencere 09-13 ve hızlı pencere 14-16, dakikada bir turn (sabit iş hızı)."""
    out = []
    for gun in (3, 2, 1, 0):
        k = -gun * GUN
        for bas, bit in ((T0 + k, T0 + 4 * 3600 + k), (SIFIR + k, SIFIR + 2 * 3600 + k)):
            t = bas + 30
            while t < bit:
                out.append(t)
                t += 60
    return out


class _Ev(unittest.TestCase):
    """Geçici ev: kanaryalı transcript'ler + seri + kanaryalı masaüstü geçmişi + Codex yok."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        kok = Path(self._td.name)
        self.seri = kok / 'limit-seri.jsonl'
        seri_yaz(self.seri, gunler())
        proje = kok / 'projects' / '-home-kanarya-kisi-gizli-proje-adi'
        (proje / UUID / 'subagents').mkdir(parents=True)
        satirlar = []
        for i, t in enumerate(is_zamanlari()):
            satirlar.append(json.dumps({
                'type': 'user', 'sessionId': UUID, 'cwd': '/home/kanarya-kisi/gizli-proje-adi',
                'gitBranch': 'gizli-dal-xyz', 'timestamp': _iso(t - 5),
                'message': {'role': 'user', 'content': 'GIZLI-ISTEM-123 lütfen dosyayı düzelt'}}))
            satirlar.append(json.dumps({
                'type': 'assistant', 'sessionId': UUID, 'cwd': '/home/kanarya-kisi/gizli-proje-adi',
                'gitBranch': 'gizli-dal-xyz', 'requestId': f'req_kanarya{i:06d}',
                'version': '2.1.197', 'isSidechain': False, 'timestamp': _iso(t),
                'message': {'id': f'msg_kanarya{i:06d}',
                            'model': '<synthetic>' if i % 50 == 0 else 'claude-opus-5-5',
                            'role': 'assistant',
                            'content': [{'type': 'text', 'text': 'GIZLI-ISTEM-123 yanıtı'}],
                            'usage': {'input_tokens': 200, 'output_tokens': 200,
                                      'cache_creation_input_tokens': 0,
                                      'cache_read_input_tokens': 0}}}))
        (proje / f'{UUID}.jsonl').write_text('\n'.join(satirlar) + '\n', encoding='utf-8')
        for p in (mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', kok / 'projects'),
                  mock.patch.object(L, 'yol', return_value=self.seri),
                  mock.patch.object(L, 'masaustu_yolu', return_value=FIX / 'masaustu-gecmis.json'),
                  mock.patch.object(codex, 'SESSIONS_DIR', kok / 'codex-yok'),
                  mock.patch('usage.limitseri._paths.home', return_value=Path('/home/kanarya-kisi'))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def kanit(self, **kw):
        return L.kanit(**kw)


class KanitIcerikTest(_Ev):
    def test_araliklar_ve_bayraklar(self):
        k = self.kanit()
        c = k['claude']
        self.assertEqual(c['summary']['measured'], 8)            # 4 gün × 2 pencere
        self.assertEqual(c['summary']['flagged'], 3)             # ilk hızlı pencerede temel yok
        self.assertEqual([a['flagged_2x_faster'] for a in c['intervals']],
                         [False, False, False, True, False, True, False, True])
        a = c['intervals'][3]
        self.assertRegex(a['start'], r'^\d{4}-\d\d-\d\dT\d\d:\d\dZ$')
        self.assertLessEqual(a['ratio_to_baseline'], 0.5)
        self.assertEqual(a['client_version'], '2.1.197')
        self.assertEqual(set(a['models']), {'opus', 'other'})    # '<synthetic>' → other
        self.assertEqual(set(a), {'start', 'end', 'points', 'minutes', 'units_per_pct', 'baseline',
                                  'ratio_to_baseline', 'flagged_2x_faster', 'models',
                                  'subagent_share', 'cache_read_share', 'client_version'})
        self.assertEqual(k['codex'], {'experimental': True, 'status': 'no-data'})

    def test_yarim_yuzdeden_kucuk_pay_basilmaz(self):
        ar = [{'oran': 1000.0, 'temel': None, 'kat': None, 'bayrak': False, 'puan': 5.0,
               't0': T0, 't1': T0 + 600, 'sure_sn': 600,
               'dokum': {'model': {'opus': 0.996, 'haiku': 0.004}, 'alt_ajan': 0.0,
                         'onbellek_okuma': 0.0, 'surum': {'2.1.197': 1.0}}}]
        self.assertEqual(L._kanit_araliklari(ar)[0]['models'], {'opus': 1.0})

    def test_utc_dakikaya_asagi_yuvarlanir(self):
        self.assertEqual(L._utc_dk(T0 + 59.9), '2026-03-23T09:00Z')
        self.assertEqual(L._utc_dk(T0 + 60), '2026-03-23T09:01Z')

    def test_yedi_gun_penceresi_calisir(self):
        k = self.kanit(pencere='7d')
        self.assertEqual(k['window'], '7d')
        self.assertEqual(k['claude']['summary']['measured'], 0)  # fikstürde haftalık yok

    def test_ag_yok(self):
        with AgYasak() as ag:
            self.kanit()
        self.assertEqual(ag.cagri, [])


class KanitGizlilikTest(_Ev):
    def ciktilar(self):
        k = self.kanit(arac_surumu='9.9.9')
        return json.dumps(k, ensure_ascii=False, indent=1), L.kanit_metni(k)

    def test_kanarya_degerler_ciktida_yok(self):
        for metin in self.ciktilar():
            for yasak in KANARYALAR:
                self.assertNotIn(yasak, metin)
            self.assertNotRegex(metin, r'[0-9a-f]{8}-[0-9a-f]{4}-')
            self.assertNotIn('/home', metin)

    def test_sizinti_taramasi_temiz(self):
        for metin in self.ciktilar():
            self.assertEqual(L.sizinti_bul(metin), [])

    def test_surum_ve_model_beyaz_listesi(self):
        self.assertEqual(L._surum_adi('2.1.197'), '2.1.197')
        for kotu in ('2.1.197 /home/x', 'dev-build', '', None, '1'):
            self.assertIsNone(L._surum_adi(kotu))
        self.assertEqual(L._claude_aile('<synthetic>'), 'other')
        self.assertEqual(L._claude_aile('opus'), 'opus')
        self.assertEqual(L._codex_aile('gpt-5.6-sol'), 'gpt-5.6-sol')
        self.assertEqual(L._codex_aile('benim-sirket/gizli-vekil-modeli'), 'other')


class BosVeriTest(unittest.TestCase):
    def test_bos_veride_none_basilmaz(self):
        with tempfile.TemporaryDirectory() as td:
            k = L.kanit(seri_dosya=Path(td) / 'yok', masaustu=False, turns=[], codex=False)
        metin = L.kanit_metni(k)
        self.assertIn('no measured stretch yet', metin)
        self.assertNotIn('None', metin)


class SizintiBulTest(unittest.TestCase):
    def test_her_desen_yakalanir(self):
        with mock.patch('usage.limitseri._paths.home', return_value=PurePosixPath('/home/ornek-kisi')), \
                mock.patch('getpass.getuser', return_value='ornekkisi'):
            for metin, beklenen in (
                    ('x /home/ornek-kisi/a', 'home-dir'), ('/Users/a/b', 'unix-path'),
                    ('/srv/veri/x', 'unix-path'), ('~/proje', 'home-path'),
                    ('C:\\\\Users\\\\a', 'windows-path'), ('yol C:\\Users\\a', 'windows-path'),
                    ('D:\\iş', 'windows-path'), (UUID, 'uuid'),
                    ('a.b@ornek.com', 'email'), ('{"org": 1}', 'org-key'),
                    ('org-abc12345', 'org-id'), ('sk-ant-0123456789abc', 'api-key'),
                    ('msg_0123456789', 'message-id'), ('{"cwd": "x"}', 'session-key'),
                    ('{"sessionId": "x"}', 'session-key'), ('by OrnekKisi', 'username')):
                self.assertIn(beklenen, L.sizinti_bul(metin), metin)

    def test_temiz_metin(self):
        with mock.patch('getpass.getuser', return_value='ornekkisi'):
            self.assertEqual(L.sizinti_bul('2026-03-23T09:00Z opus 92% units 2.1.197'), [])
            # yöntem metnindeki "opus/fable", "units/1%" ve tarih yol sayılmaz
            self.assertEqual(L.sizinti_bul('opus/fable 1 x (out*4) units/1% 2026-10-08T22:02Z'), [])


class CliTest(_Ev):
    def calistir(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            kod = cli.main(['kanit', *argv])
        return kod, out.getvalue(), err.getvalue()

    def test_metin_ve_json(self):
        kod, out, _ = self.calistir()
        self.assertEqual(kod, 0)
        self.assertIn('limit evidence · window 5h', out)
        self.assertIn('flagged 2x-faster: 3', out)
        kod, out, _ = self.calistir('--json', '--no-codex')
        self.assertEqual(kod, 0)
        d = json.loads(out)
        self.assertNotIn('codex', d)
        self.assertEqual(d['format'], L.KANIT_BICIM)

    def test_sizinti_varsa_hicbir_sey_basmaz(self):
        sahte = L.kanit()
        sahte['claude']['intervals'][0]['client_version'] = '/home/kanarya-kisi/x'
        with mock.patch.object(L, 'kanit', return_value=sahte):
            for argv in ((), ('--json',)):
                kod, out, err = self.calistir(*argv)
                self.assertEqual(kod, 1)
                self.assertEqual(out, '')
                self.assertIn('refusing to print', err)

    def test_sunucu_dagiticisi_komutu_tanir(self):
        import server
        self.assertIn('kanit', server.CLI_COMMANDS)


def rollout_yaz(dizin, gozlemler, olaylar, limit_id='codex', model='gpt-5.6-sol'):
    dizin.mkdir(parents=True, exist_ok=True)
    satirlar = [json.dumps({'timestamp': _iso(T0 - 4 * GUN), 'type': 'turn_context',
                            'payload': {'model': model}})]
    for g in gozlemler:
        satirlar.append(json.dumps({'timestamp': _iso(g['t']), 'type': 'event_msg', 'payload': {
            'type': 'token_count', 'info': None,
            'rate_limits': {'limit_id': limit_id, 'plan_type': 'plus',
                            'primary': {'used_percent': g['p'], 'window_minutes': 300,
                                        'resets_at': g['r']}}}}))
    for t, tok in olaylar:
        satirlar.append(json.dumps({'timestamp': _iso(t), 'type': 'event_msg', 'payload': {
            'type': 'token_count',
            'info': {'last_token_usage': {'input_tokens': tok + 500, 'cached_input_tokens': 500,
                                          'output_tokens': 0}}}}))
    (dizin / f'rollout-{limit_id}.jsonl').write_text('\n'.join(satirlar) + '\n', encoding='utf-8')


class CodexTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dizin = Path(self._td.name) / 'sessions' / '2026' / '03' / '23'
        p = mock.patch.object(codex, 'SESSIONS_DIR', Path(self._td.name) / 'sessions')
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def test_tutarli_veri_oran_verir(self):
        rollout_yaz(self.dizin, gunler(), [(t, 1000) for t in is_zamanlari()])
        r = L._codex_kanit('five_hour')
        self.assertEqual(r['status'], 'ok')
        self.assertTrue(r['experimental'])
        self.assertLess(r['cv'], 0.5)
        self.assertEqual(r['flagged'], 3)
        self.assertEqual({a['models']['gpt-5.6-sol'] for a in r['flagged_intervals']}, {1.0})

    def test_gurultulu_veride_oran_ve_bayrak_yok(self):
        olay = [(t, 1000 * (1 + 40 * (i % 97 == 0))) for i, t in enumerate(is_zamanlari())]
        olay = [(t, tok * (30 if T0 - 2 * GUN < t < T0 - GUN else 1)) for t, tok in olay]
        rollout_yaz(self.dizin, gunler(), olay)
        r = L._codex_kanit('five_hour')
        self.assertEqual(r['status'], 'noisy')
        self.assertGreater(r['cv'], 0.5)
        for alan in ('median_units_per_pct', 'flagged', 'flagged_intervals'):
            self.assertNotIn(alan, r)

    def test_baska_kota_karismaz(self):
        rollout_yaz(self.dizin, gunler(), [(t, 1000) for t in is_zamanlari()], limit_id='premium')
        g, olaylar = codex.limit_gecmisi('five_hour')
        self.assertEqual(g, [])
        self.assertEqual(len(olaylar), len(is_zamanlari()))
        self.assertEqual(L._codex_kanit('five_hour')['status'], 'no-data')

    def test_dizin_yoksa_veri_yok(self):
        with mock.patch.object(codex, 'SESSIONS_DIR', Path(self._td.name) / 'yok'):
            self.assertIsNone(codex.limit_gecmisi())
            self.assertEqual(L._codex_kanit('five_hour'), {'experimental': True,
                                                           'status': 'no-data'})

    def test_onbelleksiz_token_birimi(self):
        rollout_yaz(self.dizin, [], [(T0, 1000)])
        _, olaylar = codex.limit_gecmisi()
        self.assertEqual(olaylar, [(T0 * 1000, 'gpt-5.6-sol', 1000, 0)])


if __name__ == '__main__':
    unittest.main()

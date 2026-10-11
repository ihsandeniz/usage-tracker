"""Limit Kara Kutusu FAZ 4 — `/api/limit-kutu` ve panel kartı.

Kapılar: kart bu pencerenin dökümünü ve oranını doğru taşır, "2× hızlı" bayrağını yalnız
gerçekten hızlı pencerede kaldırır · uç yan etkisiz, ağsız, oturum kimliği ve org taşımaz ·
kart (web/app.js) gerçek fonksiyonla çalıştırılır: her durumda metin basar, NaN/undefined yok,
model adı kaçışlanır · rozet yüzeyine yeni alan girmez.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest import mock

from usage import engine
from usage import limitseri as L
from tests.test_limit_sapma import senaryo, gunun_isi, sabit_is, T0, SIFIR, GUN, FIX, KANARYA
from tests.test_limitseri import AgYasak

REPO = Path(__file__).resolve().parent.parent
APP_JS = REPO / 'web' / 'app.js'
NODE = shutil.which('node')


def seri_yaz(dosya, gozlemler):
    with open(dosya, 'w', encoding='utf-8') as f:
        for g in gozlemler:
            f.write(json.dumps({'t': g['t'], 'k': g['k'],
                                'w': {'five_hour': {'p': g['p'], 'r': g['r']}}}) + '\n')


class _Seri(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dosya = Path(self._td.name) / 'limit-seri.jsonl'

    def tearDown(self):
        self._td.cleanup()

    def kur(self, gunler=(3, 2, 1, 0), bugun_kes=None):
        """Önceki günler: sakin + hızlı pencere. Bugün: `bugun_kes` anına kadar."""
        g, turns = [], []
        for gun in gunler:
            gg, tt = senaryo(-gun * GUN), gunun_isi(-gun * GUN)
            if gun == 0 and bugun_kes is not None:
                gg = [x for x in gg if x['t'] <= bugun_kes]
                tt = [x for x in tt if x.ms <= bugun_kes * 1000]
            g += gg
            turns += tt
        seri_yaz(self.dosya, g)
        return turns

    def kutu(self, now, turns):
        return L.kutu(now=now, seri_dosya=self.dosya, masaustu=False, turns=turns)


class KutuDurumTest(_Seri):
    def test_hizli_pencere_bayrakli(self):
        turns = self.kur()
        now = SIFIR + 2 * 3600 + 120                           # 16:02, pencere 19:00'a kadar
        k = self.kutu(now, turns)
        self.assertEqual(k['durum'], 'tamam')
        self.assertTrue(k['bayrak'])
        self.assertLessEqual(k['kat'], 0.5)
        self.assertAlmostEqual(k['oran'], 6000, delta=200)
        self.assertAlmostEqual(k['temel'], 12000, delta=200)
        self.assertEqual(k['pencereBasi'], SIFIR)              # r - 5 saat
        self.assertEqual(k['dokum']['turn'], 120)              # 14:00-16:00, dakikada bir
        self.assertEqual(k['dokum']['model'], {'opus': 1.0})
        self.assertEqual(k['dokum']['oturumSayisi'], 1)
        self.assertEqual(k['dokum']['oturumlar'], [1.0])
        self.assertEqual(k['son']['p'], 20.0)
        self.assertEqual(k['son']['yasSn'], 120 + int(SIFIR + 2 * 3600 - L.oku(dosya=self.dosya)[-1]['t']))
        self.assertEqual(k['olculen'], 8)

    def test_sakin_pencere_bayraksiz(self):
        turns = self.kur(bugun_kes=T0 + 4 * 3600 + 60)
        k = self.kutu(T0 + 4 * 3600 + 120, turns)
        self.assertEqual(k['durum'], 'tamam')
        self.assertFalse(k['bayrak'])
        self.assertGreater(k['kat'], 1.0)
        self.assertIsNotNone(k['sonBayrak'])                    # dünkü hızlı pencere
        # dün 16:00 → bugün 13:02 = 21 sa 2 dk
        self.assertAlmostEqual(k['sonBayrak']['yasSn'], GUN - 3 * 3600 + 120, delta=600)

    def test_bes_puan_altinda_kisa(self):
        turns = self.kur(bugun_kes=T0 + 1800)                  # bugün %0→%2
        k = self.kutu(T0 + 1900, turns)
        self.assertEqual(k['durum'], 'kisa')
        self.assertIsNone(k['oran'])
        self.assertFalse(k['bayrak'])
        self.assertGreater(k['dokum']['turn'], 0)
        self.assertIsNotNone(k['temel'])                       # geçmişten

    def test_pencere_dondu_bayat(self):
        turns = self.kur()
        k = self.kutu(SIFIR + 5 * 3600 + 1, turns)            # r geçildi, yeni gözlem yok
        self.assertEqual(k['durum'], 'bayat')
        self.assertIsNone(k['dokum'])
        self.assertIsNone(k['oran'])
        self.assertIsNotNone(k['temel'])

    def test_gozlem_yok(self):
        k = self.kutu(T0, [])
        self.assertEqual(k['durum'], 'gozlem-yok')
        self.assertEqual((k['son'], k['dokum'], k['oran'], k['bayrak']), (None, None, None, False))

    def test_r_bilinmiyorsa_on_dakika_icinde_guncel(self):
        g = [{'t': T0 + i * 300, 'k': 'masaustu', 'p': float(i), 'r': None} for i in range(8)]
        seri_yaz(self.dosya, g)
        turns = sabit_is(T0, T0 + 2100)
        k = self.kutu(T0 + 2100 + 500, turns)
        self.assertNotEqual(k['durum'], 'bayat')
        self.assertEqual(k['pencereBasi'], round(T0 + 2600 - 5 * 3600))
        k = self.kutu(T0 + 2100 + 700, turns)
        self.assertEqual(k['durum'], 'bayat')

    def test_oturum_kimligi_org_ve_yol_tasimaz(self):
        turns = [t._replace(sid='gizli-oturum-uuid') for t in self.kur()]
        k = L.kutu(now=SIFIR + 2 * 3600 + 120, seri_dosya=self.dosya,
                   masaustu=FIX / 'masaustu-gecmis.json', turns=turns)
        metin = json.dumps(k)
        for yasak in ('gizli-oturum-uuid', KANARYA, '"org"', self._td.name):
            self.assertNotIn(yasak, metin)

    def test_en_cok_bes_oturum_payi_buyukten_kucuge(self):
        turns = self.kur()
        ek = []
        for i, x in enumerate(t for t in turns if t.ms > SIFIR * 1000):
            ek.append(x._replace(sid=f's{i % 7}', units=x.units * (1 + i % 7)))
        k = self.kutu(SIFIR + 2 * 3600 + 120, [t for t in turns if t.ms <= SIFIR * 1000] + ek)
        o = k['dokum']['oturumlar']
        self.assertEqual(k['dokum']['oturumSayisi'], 7)
        self.assertEqual(len(o), 5)
        self.assertEqual(o, sorted(o, reverse=True))

    def test_ag_yok(self):
        turns = self.kur()
        with AgYasak() as ag:
            self.kutu(SIFIR + 2 * 3600 + 120, turns)
        self.assertEqual(ag.cagri, [])


class OnbellekTest(unittest.TestCase):
    def setUp(self):
        L._kutu_onbellek.update(t=0.0, v=None)
        self.addCleanup(L._kutu_onbellek.update, t=0.0, v=None)

    def test_ttl_icinde_tek_hesap(self):
        with mock.patch.object(L, 'kutu', return_value={'durum': 'tamam'}) as k:
            L.kutu_onbellekli()
            L.kutu_onbellekli()
        self.assertEqual(k.call_count, 1)

    def test_ttl_dolunca_yeniden(self):
        with mock.patch.object(L, 'kutu', return_value={'durum': 'tamam'}) as k:
            L.kutu_onbellekli()
            L._kutu_onbellek['t'] -= L.KUTU_TTL + 1
            L.kutu_onbellekli()
        self.assertEqual(k.call_count, 2)

    def test_hata_karti_dusurur_paneli_degil(self):
        with mock.patch.object(L, 'kutu', side_effect=RuntimeError('x')):
            self.assertEqual(L.kutu_onbellekli(), {'pencere': 'five_hour', 'durum': 'hata',
                                                   'hata': 'RuntimeError'})


class UcTest(unittest.TestCase):
    """Gerçek sunucu, geçici portta; veri yolları geçici dizinde."""

    def setUp(self):
        import server as server_mod
        self._td = tempfile.TemporaryDirectory()
        kok = Path(self._td.name)
        self.seri = kok / 'limit-seri.jsonl'
        L._kutu_onbellek.update(t=0.0, v=None)
        self.addCleanup(L._kutu_onbellek.update, t=0.0, v=None)
        for p in (mock.patch.object(L, 'yol', return_value=self.seri),
                  mock.patch.object(L, 'masaustu_yolu', return_value=kok / 'yok.json'),
                  mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', kok / 'projects'),
                  mock.patch.object(server_mod, 'ALLOWED_HOSTS', set())):
            p.start()
            self.addCleanup(p.stop)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server_mod.Handler)
        self.port = self.httpd.server_address[1]
        server_mod.ALLOWED_HOSTS.add(f'127.0.0.1:{self.port}')
        Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self._td.cleanup)

    def get(self, path, method='GET'):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', method=method)
        req.add_header('Host', f'127.0.0.1:{self.port}')
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_bos_veride_gozlem_yok_200(self):
        st, d = self.get('/api/limit-kutu')
        self.assertEqual(st, 200)
        self.assertEqual(d['durum'], 'gozlem-yok')

    def test_veriyle_ve_yan_etkisiz(self):
        seri_yaz(self.seri, senaryo())
        once = self.seri.read_bytes()
        st, d = self.get('/api/limit-kutu')
        self.assertEqual(st, 200)
        self.assertIn(d['durum'], ('bayat', 'kisa', 'tamam'))
        self.assertEqual(d['olculen'], 0)                      # transcript yok → yerel kayıt yok
        self.assertEqual(self.seri.read_bytes(), once)

    def test_post_kabul_edilmez(self):
        st, _ = self.get('/api/limit-kutu', method='POST')
        self.assertIn(st, (403, 404, 405))


class RozetSizintiTest(unittest.TestCase):
    def test_kart_wire_ve_rozete_girmez(self):
        # Kart kendi ucundan beslenir; /v1/usage wire'ı ve rozet sunucusu bu alanları bilmez.
        for f in ('usage/engine.py', 'surface/rozet-sunucu.py', 'surface/waybar-usage.sh'):
            src = (REPO / f).read_text(encoding='utf-8')
            for alan in ('limit-kutu', 'limitKutu', 'kutu_onbellekli'):
                self.assertNotIn(alan, src, f'{f} → {alan}')


KART_HARNESS = r'''
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function lift(re, name) {
  const m = src.match(re);
  if (!m) { console.error('MISSING:' + name); process.exit(2); }
  return m[0];
}
eval(lift(/^const I18N = \{[\s\S]*?\n\};/m, 'I18N').replace(/^const /, 'var '));
eval(lift(/^const roundHalfUp = [\s\S]*?\n\};/m, 'roundHalfUp').replace(/^const /, 'var '));
eval(lift(/^const fmtTok = [\s\S]*?;\r?\n/m, 'fmtTok').replace(/^const /, 'var '));
eval(lift(/^const LK_COLORS = .*;$/m, 'LK_COLORS').replace(/^const /, 'var '));
var LANG = process.argv[4];
var t = (k) => I18N[LANG]?.[k] ?? I18N.en?.[k] ?? k;
var EL = { innerHTML: '' };
var $ = (id) => id === 'limit-kutu' ? EL : null;
for (const n of ['esc', 'countdown', 'lkFill', 'lkPct', 'lkRound', 'renderLimitKutu']) {
  eval(lift(new RegExp('function ' + n + '\\([\\s\\S]*?\\n}', 'm'), n));
}
const out = JSON.parse(process.argv[3]).map(d => { renderLimitKutu(d); return EL.innerHTML; });
console.log(JSON.stringify(out));
'''


@unittest.skipUnless(NODE, 'node not installed — panel behaviour cannot be executed here')
class KartTest(_Seri):
    @classmethod
    def setUpClass(cls):
        cls._h = tempfile.TemporaryDirectory()
        cls.harness = Path(cls._h.name) / 'kart.js'
        cls.harness.write_text(KART_HARNESS, encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls._h.cleanup()

    def ciz(self, kutular, dil='tr'):
        out = subprocess.run([NODE, str(self.harness), str(APP_JS), json.dumps(kutular), dil],
                             capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def gercek_kutular(self):
        turns = self.kur()
        return [self.kutu(SIFIR + 2 * 3600 + 120, turns),            # hızlı
                self.kutu(SIFIR + 5 * 3600 + 1, turns),              # bayat
                L.kutu(now=T0, seri_dosya=Path(self._td.name) / 'yok', masaustu=False, turns=[]),
                {'pencere': 'five_hour', 'durum': 'hata', 'hata': 'RuntimeError'}]

    def test_her_durum_metin_basar_nan_undefined_yok(self):
        for dil in ('tr', 'en'):
            for html in self.ciz(self.gercek_kutular(), dil):
                self.assertTrue(html.strip())
                for kotu in ('NaN', 'undefined', 'null', '{u}', '{n}', '{a}', '{k}', '{p}', '{cv}'):
                    self.assertNotIn(kotu, html, f'{dil}: {kotu}')

    def test_hizli_kart(self):
        tr, bayat, yok, hata = self.ciz(self.gercek_kutular())
        self.assertIn('⚑ 2× hızlı', tr)
        self.assertIn('lk-alarm', tr)
        self.assertIn('%1 ≈ 6k birim', tr)
        self.assertIn('olağan 12k', tr)
        self.assertIn('≈ 20 puan', tr)                    # opus payı 1.0 × %20
        k = self.gercek_kutular()[0]
        k['dokum']['model'] = {'opus': 0.69, 'sonnet': 0.31}
        self.assertIn('≈ 13,8 puan', self.ciz([k])[0])          # TR ondalık virgül
        self.assertIn('≈ 13.8 pts', self.ciz([k], 'en')[0])
        self.assertIn('güncel ölçüm yok', bayat)
        self.assertNotIn('lk-alarm', bayat)
        self.assertIn('Henüz limit yüzdesi geçmişi yok', yok)
        self.assertIn('alınamadı', hata)
        en = self.ciz(self.gercek_kutular()[:1], 'en')[0]
        self.assertIn('⚑ 2× faster', en)
        self.assertIn('1% ≈ 6k units', en)

    def test_lejant_yuzdeleri_100_tr_isaret_onde(self):
        import re
        k = self.gercek_kutular()[0]
        k['dokum']['model'] = {'opus': 0.913, 'sonnet': 0.082, 'haiku': 0.005}
        tr = self.ciz([k])[0]
        yuz = [int(x) for x in re.findall(r'<span>%(\d+)</span>', tr)]
        self.assertEqual(yuz, [91, 8, 1])                      # 91+8+0 değil, toplam 100
        self.assertNotRegex(re.sub(r'<[^>]+>', '', tr), r'\d%')   # görünen metinde "92%" yok
        en = self.ciz([k], 'en')[0]
        self.assertEqual([int(x) for x in re.findall(r'<span>(\d+)%</span>', en)], [91, 8, 1])

    def test_sifira_yuvarlanan_pay_kucuktur_der(self):
        k = self.gercek_kutular()[0]
        k['dokum']['model'] = {'opus': 0.996, 'haiku': 0.004}
        self.assertIn('<span>&lt;%1</span>', self.ciz([k])[0])
        self.assertIn('<span>&lt;1%</span>', self.ciz([k], 'en')[0])

    def test_carpan_pill_ile_celismez(self):
        tr = self.ciz(self.gercek_kutular()[:1])[0]
        self.assertNotRegex(tr, r'0[.,]\d+×')                 # "0.44×" yan yana "2× hızlı" yok
        self.assertIn('olağan işin yalnız %', tr)

    def test_model_adi_kacislanir(self):
        k = self.gercek_kutular()[0]
        k['dokum']['model'] = {'<img src=x onerror=alert(1)>': 1.0}
        html = self.ciz([k])[0]
        self.assertNotIn('<img', html)
        self.assertIn('&lt;img', html)

    def test_iki_dilde_ayni_anahtarlar(self):
        src = APP_JS.read_text(encoding='utf-8')
        for anahtar in ('lkTitle', 'lkFast', 'lkNote', 'lkStaleMsg', 'lkFastMsg', 'lkBaseMsg', 'lkLastFlag'):
            self.assertEqual(src.count(f'{anahtar}: '), 2, anahtar)


if __name__ == '__main__':
    unittest.main()

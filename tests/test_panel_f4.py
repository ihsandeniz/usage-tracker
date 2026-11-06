"""F4 panel — 2026-11-05 taramasının Y9, Y12 ve app.js:664 bulguları (Y8, Y10 tarayıcıda ölçülür)."""
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from usage.providers import codex

REPO = Path(__file__).resolve().parent.parent
APP = (REPO / 'web' / 'app.js').read_text(encoding='utf-8')
TR_HARF = re.compile('[çğışİĞŞ]')


def _node(fonksiyonlar, ifade, lang='en'):
    gov = []
    for ad in fonksiyonlar:
        m = re.search(r'^function ' + ad + r'\(.*?^\}', APP, re.M | re.S)
        assert m, ad
        gov.append(m.group(0))
    i18n = re.search(r'^const I18N = \{.*?^\};', APP, re.M | re.S).group(0).replace('const I18N', 'var I18N')
    js = i18n + f"\nvar LANG='{lang}';\nvar t=(k)=>I18N[LANG]?.[k]??I18N.en?.[k]??k;\n" + '\n'.join(gov) \
        + f'\nconsole.log(JSON.stringify({ifade}));'
    out = subprocess.run(['node', '-e', js], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which('node'), 'node yok')
class IngilizceTurkceKalmaz(unittest.TestCase):
    """Y9 — "canlı · gerçek", "Oturum (5s)" İngilizce modda Türkçe kalıyordu."""
    DURUM = ("[{source:'live',live:{}},{live:{stale:true,ageSec:3600}},{live:{rateLimited:true}},"
             "{live:{error:'token okunamadı'}},{live:{}}]")

    def test_kaynak_rozeti(self):
        for f in _node(['fmtAge', 'liveFlag'], f'{self.DURUM}.map(liveFlag)'):
            self.assertIsNone(TR_HARF.search(f['text']), f['text'])

    def test_turkce_kipte_rozet_ayni(self):
        f = _node(['fmtAge', 'liveFlag'], "liveFlag({source:'live',live:{}})", lang='tr')
        self.assertEqual(f['text'], '🟢 canlı · gerçek')

    def test_bar_adlari_sozlukten(self):
        self.assertNotIn("lbar('Oturum (5s)'", APP)
        self.assertNotIn("lbar('Haftalık (tüm)'", APP)

    def test_sabit_turkce_kalmadi(self):
        self.assertNotIn("val = 'kapalı'", APP)
        self.assertNotIn('sağlayıcı veri döndürmedi`', APP)
        # 2026-11-05: goz yakaladı, Türkçe harf taşımadıkları için ölçüm betiği görmemişti.
        for metin in ("= '✓ tam fiyat'", "aktif` :", "? 'yerel'", '`abonelik ·', '≈ maliyet <span',
                      '>Sıfırlanma<', '>Kalan<', '<span>Aylık '):
            self.assertNotIn(metin, APP)

    def test_uyari_kodlari_ceviriye_baglanir(self):
        kart = {'warnings': ['Türkçe metin'], 'warningCodes': [{'code': 'filesCap', 'n': 40}]}
        metin = _node(['kartUyarilari'], f'kartUyarilari({json.dumps(kart)})')
        self.assertEqual(len(metin), 1)
        self.assertIn('40', metin[0])
        self.assertIsNone(TR_HARF.search(metin[0]), metin[0])

    def test_kodsuz_uyari_kaybolmaz(self):
        self.assertEqual(_node(['kartUyarilari'], "kartUyarilari({warnings:['x']})"), ['x'])


class KodluUyari(unittest.TestCase):
    def test_codex_dosya_tavani_kod_tasir(self):
        with tempfile.TemporaryDirectory() as td:
            kok = Path(td) / 'sessions'
            kok.mkdir()
            for i in range(4):
                (kok / f'rollout-{i}.jsonl').write_text(json.dumps({
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'type': 'event_msg',
                    'payload': {'type': 'token_count', 'info': {'last_token_usage': {
                        'input_tokens': 1, 'output_tokens': 1}}}}) + '\n', encoding='utf-8')
            with mock.patch.object(codex, 'SESSIONS_DIR', kok), mock.patch.object(codex, 'MAX_FILES', 2):
                k = codex.collect(30)
        self.assertEqual(k['warningCodes'], [{'code': 'filesCap', 'n': 2}])


@unittest.skipUnless(shutil.which('node'), 'node yok')
class BosSatirYok(unittest.TestCase):
    def test_olculmeyen_model_satiri_gizlenir(self):
        # Y12: yüzde yok, altında "kalibre değil". Canlı uç model başına yüzde vermiyor.
        self.assertEqual(_node(['modelSatiriVar'], "[modelSatiriVar({pct:null,name:'opus'}),"
                               "modelSatiriVar({pct:12}),modelSatiriVar(null)]"), [False, True, False])


@unittest.skipUnless(shutil.which('node'), 'node yok')
class KirilimOlcum(unittest.TestCase):
    def test_kirilim_kapaliyken_olculuyor_yazmaz(self):
        # app.js:664 — ayar kapalıyken sonsuza dek "ölçülüyor" yazıyordu.
        self.assertEqual(_node(['ctxKirilimMetni'], "ctxKirilimMetni({total:5,breakdown_off:true})"), '')
        self.assertNotEqual(_node(['ctxKirilimMetni'], "ctxKirilimMetni({total:5})"), '')


if __name__ == '__main__':
    unittest.main()

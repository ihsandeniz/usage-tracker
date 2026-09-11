"""Rozet yüzeyi (surface/rozet-sunucu.py) — telefon/saat.

⚠️ `unittest` kullanılıyor, `pytest` DEĞİL: CI `unittest` koşuyor ve
`import pytest` yapan bir test dosyası 2026-08-29'da CI'ı kırmızı yaktı.

Bu yüzey tailnet'e — gerekirse public HTTPS'e — açılıyor. En kritik test
harcama sızıntısı testidir: yüzeyin ne gösterdiği değil, **ne göstermediği**
sözleşmenin parçası.
"""
import importlib.util
import json
import unittest
from pathlib import Path

_KAYNAK = Path(__file__).resolve().parent.parent / 'surface' / 'rozet-sunucu.py'
_spec = importlib.util.spec_from_file_location('rozet_sunucu', _KAYNAK)
rozet_sunucu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rozet_sunucu)


def _wire(claude=None, codex=None, warn=75, crit=90):
    """Üretimin şekline sadık asgari wire."""
    saglayicilar = []
    if claude is not None:
        limits = dict(claude)
        limits['thresholds'] = {'warn': warn, 'crit': crit}
        saglayicilar.append({'id': 'claude', 'limits': limits})
    if codex is not None:
        saglayicilar.append({'id': 'codex', 'limits': dict(codex)})
    return {'providers': saglayicilar}


def _bar(pct, reset=3600, stale=False):
    return {'pct': pct, 'resetInSec': reset, 'stale': stale}


class RozetUretimi(unittest.TestCase):
    def _uret(self, wire):
        """rozet() ağa çıkmadan ölçülsün — urlopen yerine sahte cevap."""
        class _Sahte:
            def __enter__(_s): return _s
            def __exit__(*_): return False
            def read(_s): return json.dumps(wire).encode()
        gercek = rozet_sunucu.urllib.request.urlopen
        rozet_sunucu.urllib.request.urlopen = lambda *a, **k: _Sahte()
        try:
            # json.load(fp) fp.read() çağırır — sahte nesne bunu karşılıyor.
            return rozet_sunucu.rozet()
        finally:
            rozet_sunucu.urllib.request.urlopen = gercek

    def test_harcama_alanlari_asla_sizmaz(self):
        """🔴 Bu yüzey funnel ile public'e çıkabilir — $ oraya düşmemeli.

        Wire'a harcama koyup rozette ARANIYOR: biri ileride `spend`i
        taşımaya kalkarsa bu test kırmızı yanar.
        """
        wire = _wire(claude={'session': _bar(40.0), 'weekly': _bar(10.0)})
        wire['providers'][0]['spend'] = {'today': 104.02, 'days30': 6559.38}
        wire['spend'] = {'catalog': 'hermes'}

        cikti = self._uret(wire)
        duz = json.dumps(cikti).lower()
        for yasak in ('spend', 'usd', 'cost', '104.02', '6559'):
            self.assertNotIn(yasak, duz,
                             f'rozet harcama sızdırdı: {yasak!r} → {cikti}')

    def test_manset_en_yuksek_yuzde(self):
        """waybar-usage.sh ile AYNI tanım — yeni yüzey kendi tanımını icat etmez."""
        cikti = self._uret(_wire(
            claude={'session': _bar(40.0), 'weekly': _bar(12.0)},
            codex={'session': _bar(67.0), 'weekly': _bar(38.0)}))
        self.assertEqual(cikti['pct'], 67.0)
        self.assertEqual(cikti['text'], '◐ 67%')

    def test_bayat_bar_mansete_girmez(self):
        """Ölçülmüş ama eskimiş sayıyı 'şu an %X' diye yayınlamak yalandır."""
        cikti = self._uret(_wire(
            claude={'session': _bar(95.0, stale=True), 'weekly': _bar(20.0)}))
        self.assertEqual(cikti['pct'], 20.0, 'bayat bar manşete girdi')

    def test_olculemeyen_yuzde_ok_degil_unknown(self):
        """pct yoksa 'ok' demek, engellenmek istenen pahalı işi başlatır.

        FAZ 7'nin kararı: bilinmeyen 0 değil, bilinmiyor.
        """
        cikti = self._uret(_wire(claude={'session': _bar(None), 'weekly': _bar(None)}))
        self.assertIsNone(cikti['pct'])
        self.assertEqual(cikti['class'], 'unknown')
        self.assertEqual(cikti['text'], '○ —')

    def test_esik_sunucudan_gelir(self):
        """Eşiği yüzey icat etmiyor; wire'daki thresholds'u okuyor."""
        dusuk = self._uret(_wire(claude={'session': _bar(50.0)}, warn=40, crit=45))
        self.assertEqual(dusuk['class'], 'crit')
        yuksek = self._uret(_wire(claude={'session': _bar(50.0)}, warn=80, crit=95))
        self.assertEqual(yuksek['class'], 'ok')

    def test_reset_alan_adi_wire_ile_ayni(self):
        """🐞 `resetsInSec` yazılıp sessizce null alınmıştı (2026-09-11).

        Alan adı TEKİL: resetInSec. Bu test o hatanın geri gelmesini engeller.
        """
        cikti = self._uret(_wire(claude={'session': _bar(40.0, reset=2507)}))
        self.assertEqual(cikti['claude_session_reset'], 2507,
                         'reset alanı okunamadı — wire adı resetInSec mi?')

    def test_kart_yoksa_alan_uydurulmaz(self):
        """Codex kurulu değilse codex_* alanları hiç olmamalı, 0 değil."""
        cikti = self._uret(_wire(claude={'session': _bar(40.0)}))
        self.assertNotIn('codex_session', cikti)


class HazirMetinAlanlari(unittest.TestCase):
    """🔑 KWGT aynı ifadede İKİNCİ wg()'yi null döndürüyor ve `mu(round, null)`
    "invalid numeric argument" veriyor — canlı ölçüldü. Bu yüzden birleştirme
    sunucuda yapılıyor; widget tek alan okur.
    """

    def _uret(self, wire):
        return RozetUretimi._uret(self, wire)

    def test_olculemeyen_yuzde_tire_olur_sifir_degil(self):
        """'0%' yazmak 'ölçemedim'i 'hiç kullanmadın' gibi gösterirdi."""
        cikti = self._uret(_wire(
            claude={'session': _bar(15.0), 'weekly': _bar(19.0)},
            codex={'session': _bar(None), 'weekly': _bar(41.0)}))
        self.assertEqual(cikti['codex_text'], 'Codex — · hafta 41%')
        self.assertNotIn('0%', cikti['codex_text'])

    def test_kart_yoksa_satirdan_duser_ama_null_ise_durur(self):
        """'Codex kurulu değil' ile 'penceresi sıfırlandı' aynı görünmemeli."""
        yok = self._uret(_wire(claude={'session': _bar(15.0)}))
        self.assertEqual(yok['kirilim'], 'Claude 15%')

        olculemedi = self._uret(_wire(claude={'session': _bar(15.0)},
                                      codex={'session': _bar(None)}))
        self.assertEqual(olculemedi['kirilim'], 'Claude 15% · Codex —')

    def test_sure_gun_kademesi_var(self):
        """FAZ 7'de haftalık reset '85s 41dk' diye basılmıştı — aynı kusur
        burada '274 dk' olurdu."""
        self.assertEqual(rozet_sunucu._sure(1800), '30 dk')
        self.assertEqual(rozet_sunucu._sure(9158), '2 sa 32 dk')
        self.assertEqual(rozet_sunucu._sure(487958), '5 gün 15 sa')
        self.assertEqual(rozet_sunucu._sure(None), '—')
        self.assertEqual(rozet_sunucu._sure(-5), '—')

    def test_metin_alanlari_formul_tasimaz(self):
        """Alanlar KWGT'ye düz metin gitmeli; '$' widget'ta formül sanılır."""
        cikti = self._uret(_wire(claude={'session': _bar(15.0)}))
        for alan in ('text', 'kirilim', 'claude_text', 'reset_text'):
            self.assertNotIn('$', cikti[alan], f'{alan} formül karakteri taşıyor')


class YolKapisi(unittest.TestCase):
    def test_yalniz_tek_yol_sunulur(self):
        """Yüzey tek yol: /rozet.json. Geri kalan her şey 404."""
        self.assertEqual(rozet_sunucu.YOL, '/rozet.json')
        kaynak = _KAYNAK.read_text(encoding='utf-8')
        self.assertIn('send_error(404)', kaynak,
                      'bilinmeyen yol 404 dönmüyor')

    def test_loopback_bind(self):
        """Dışarı açılış tailscale serve'ün işi — süreç 0.0.0.0'a bağlanmamalı."""
        kaynak = _KAYNAK.read_text(encoding='utf-8')
        self.assertIn("('127.0.0.1', PORT)", kaynak)
        self.assertNotIn("'0.0.0.0'", kaynak)


if __name__ == '__main__':
    unittest.main()

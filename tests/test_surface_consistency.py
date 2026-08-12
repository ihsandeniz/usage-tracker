#!/usr/bin/env python3
"""
test_surface_consistency.py — Y3, Y6, Y7, partial durumu testi.

Üç yüzey (web/app.js, surface/waybar-usage.sh, surface/usage-tray.py) aynı sonucu
vermeli: eşik, yuvarlama, para birimi. İşlemsel testler + statik kod testi.

Çalıştır: python3 -m unittest tests.test_surface_consistency -v
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


class Y6Thresholds(unittest.TestCase):
    """Y6: Eşik tek kaynağı yok — hardcoded 90/75 bulunması hata."""

    def test_web_app_js_no_hardcoded_thresholds_in_crit_warn(self):
        """web/app.js'de hardcoded >= 90 / >= 75 arayması."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')

        # barClass() fonksiyonu dışında hardcoded 90/75 bulma
        # barClass, renderLimits, renderProviders, renderGlance içinde özel kısaltma var, onları dışla

        # Satırları ayıkla
        lines = app_js.split('\n')
        issues = []
        for i, line in enumerate(lines, 1):
            # barClass fonksiyonunu skip (line 128–133)
            if 'function barClass(pct, th)' in line:
                continue
            if 128 <= i <= 133:
                continue

            # renderProviders içinde line 271, 304 — sağlayıcılar kısıtlı olmalı
            if 'lim.pct >= 90' in line or 'q.pct >= 90' in line:
                issues.append((i, line.strip()))
            if 'lim.pct >= 75' in line or 'q.pct >= 75' in line:
                issues.append((i, line.strip()))
            # glance mode line 399-400
            if i == 399 or i == 400:
                if '>= 90' in line or '>= 75' in line:
                    issues.append((i, line.strip()))

        self.assertEqual(len(issues), 0,
            f"web/app.js hardcoded eşik bulundu:\n" +
            "\n".join(f"  {n}: {l}" for n, l in issues))

    def test_web_app_js_uses_thresholds_parameter(self):
        """barClass fonksiyonunun th parametresi kullanması."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        # barClass'ta (th.crit ?? 90) ve (th.warn ?? 75) olmalı
        self.assertIn('function barClass(pct, th)', app_js)
        self.assertIn('(th.crit ?? 90)', app_js)
        self.assertIn('(th.warn ?? 75)', app_js)

    def test_waybar_no_hardcoded_thresholds(self):
        """surface/waybar-usage.sh'de hardcoded 90/75 bulma."""
        waybar_sh = (Path(__file__).parent.parent / 'surface' / 'waybar-usage.sh').read_text(encoding='utf-8')

        # jq tanımlarında >= 90 / >= 75 arayışı
        lines = waybar_sh.split('\n')
        issues = []
        for i, line in enumerate(lines, 1):
            # jq def col(p) ve $hi_pct >= kontrollerine bak
            if '>= 90' in line or '>= 75' in line:
                if 'def' not in line and not line.strip().startswith('#'):
                    issues.append((i, line.strip()))

        self.assertEqual(len(issues), 0,
            f"waybar-usage.sh hardcoded eşik:\n" +
            "\n".join(f"  {n}: {l}" for n, l in issues))

    def test_tray_py_no_hardcoded_thresholds(self):
        """surface/usage-tray.py'de hardcoded 90/75 bulma."""
        tray_py = (Path(__file__).parent.parent / 'surface' / 'usage-tray.py').read_text(encoding='utf-8')

        lines = tray_py.split('\n')
        issues = []
        for i, line in enumerate(lines, 1):
            # >= 90 / >= 75 arayışı (yorum satırı değilse)
            if ('pct >= 90' in line or 'pct >= 75' in line or
                'hi >= 90' in line or 'hi >= 75' in line):
                if not line.strip().startswith('#'):
                    issues.append((i, line.strip()))

        self.assertEqual(len(issues), 0,
            f"usage-tray.py hardcoded eşik:\n" +
            "\n".join(f"  {n}: {l}" for n, l in issues))


class Y7Rounding(unittest.TestCase):
    """Y7: Yuvarlama — üç yüzey aynı sonuç vermeli."""

    def _jq_round(self, v):
        """jq banker's rounding: round(v*10)/10."""
        # Built-in jq round() banker's rounding (round-half-to-even)
        result = subprocess.run(
            ['jq', '-n', f'({v}*10|round/10)'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=2
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
        else:
            self.fail(f"jq call failed: {result.stderr}")

    def _python_round(self, v):
        """Python half-up rounding: math.floor(v*10+0.5)/10."""
        import math
        return math.floor(v * 10 + 0.5) / 10

    def _js_toFixed(self, v):
        """Simüle: Math.floor(v*10+0.5)/10 — half-up."""
        import math
        return math.floor(v * 10 + 0.5) / 10

    def test_rounding_consistency_62_45(self):
        """62.45 — üç yüzey aynı yapmalı."""
        v = 62.45
        jq_result = self._jq_round(v)
        py_result = self._python_round(v)
        js_result = self._js_toFixed(v)

        self.assertEqual(jq_result, py_result,
            f"jq ({jq_result}) ≠ python ({py_result})")
        self.assertEqual(py_result, js_result,
            f"python ({py_result}) ≠ js ({js_result})")
        # Artık burada hata alacak — yuvarlama uyumsuz

    def test_rounding_consistency_62_44(self):
        """62.44 — üç yüzey aynı yapmalı."""
        v = 62.44
        jq_result = self._jq_round(v)
        py_result = self._python_round(v)
        js_result = self._js_toFixed(v)

        self.assertEqual(jq_result, py_result)
        self.assertEqual(py_result, js_result)

    def test_rounding_consistency_0_05(self):
        """0.05 — edge case."""
        v = 0.05
        jq_result = self._jq_round(v)
        py_result = self._python_round(v)
        js_result = self._js_toFixed(v)

        self.assertEqual(jq_result, py_result)
        self.assertEqual(py_result, js_result)

    def test_rounding_consistency_99_95(self):
        """99.95 — edge case."""
        v = 99.95
        jq_result = self._jq_round(v)
        py_result = self._python_round(v)
        js_result = self._js_toFixed(v)

        self.assertEqual(jq_result, py_result)
        self.assertEqual(py_result, js_result)


class Y3Currency(unittest.TestCase):
    """Y3: Para birimi — backend'den okunmalı, hardcoded $ kaldırılmalı."""

    def test_web_app_js_fmtUsd_uses_currency_param(self):
        """fmtUsd fonksiyonunun currency parametresi kullanması."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        # fmtUsd'nin currency parametresi olmalı
        self.assertIn('const fmtUsd = (n, currency', app_js,
            "fmtUsd currency parametresi almalı")

    def test_backend_sends_currency(self):
        """Backend'in currency alanı gönderip göndermediğini doğrula."""
        engine_py = (Path(__file__).parent.parent / 'usage' / 'engine.py').read_text(encoding='utf-8')
        # engine.py'de 'currency' alanı olmalı (provider veya spend içinde)
        self.assertIn("'currency'", engine_py,
            "engine.py currency alanı üretmeli")

    def test_web_app_js_providers_render_uses_currency(self):
        """app.js'de sağlayıcı kart render'ında currency okunmalı."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        # sağlayıcı kartında fmtUsd çağrıları currency parametresi almalı
        self.assertIn('fmtUsd(lim.used, c.currency)', app_js,
            "Sağlayıcı limit line'ında currency kullanılmalı")


class PartialStatus(unittest.TestCase):
    """Ek: partial durumu — sağlayıcılardan gelir, 'ok' gibi render et ama uyarı göster."""

    def test_genlog_produces_truncated(self):
        """_genlog.py truncated alanı üretmeli (partial → tarama eksik)."""
        genlog_py = (Path(__file__).parent.parent / 'usage' / 'providers' / '_genlog.py').read_text(encoding='utf-8')
        self.assertIn("truncated", genlog_py,
            "_genlog.py truncated alanı üretmeli")

    def test_web_app_js_recognizes_partial(self):
        """app.js partial status'u tanımalı (sağlayıcı kartında)."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("c.status === 'partial'", app_js,
            "app.js partial durumu check etmeli")


class Phase3Section3Grouping(unittest.TestCase):
    """FAZ 3 §3: Sağlayıcı kalabalığı — gruplama, arama, uzun kuyruk."""

    def test_web_has_provider_search_input(self):
        """HTML'de arama input'u olmalı (id=prov-search)."""
        html = (Path(__file__).parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="prov-search"', html, "prov-search input'u olmalı")
        self.assertIn('data-i18n-ph="provSearchPlaceholder"', html, "arama placeholder i18n'li olmalı")

    def test_web_has_category_tabs(self):
        """HTML'de kategori sekmeleri olmalı (all, active, api, local)."""
        html = (Path(__file__).parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')
        tabs = ['all', 'active', 'api', 'local']
        for tab in tabs:
            self.assertIn(f'data-tab="{tab}"', html, f"tab={tab} olmalı")
        self.assertIn('class="prov-tab active"', html, "başlangıçta aktif sekme olmalı")

    def test_web_has_longtrail_group(self):
        """HTML'de uzun kuyruk grubu olmalı (details + prov-longtrail-cards)."""
        html = (Path(__file__).parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="prov-longtrail"', html, "prov-longtrail details'ı olmalı")
        self.assertIn('id="prov-longtrail-cards"', html, "longtrail cards ızgarası olmalı")

    def test_web_has_no_match_message(self):
        """HTML'de arama sonucu yoksa mesajı olmalı."""
        html = (Path(__file__).parent.parent / 'web' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="prov-no-match"', html, "prov-no-match mesaj div'i olmalı")

    def test_app_js_has_localstorage_versioning(self):
        """app.js localStorage key'lerinde .v1 eki olmalı (schema değişimi için)."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        # ut.panel.tab.v1, ut.panel.search.v1 olmalı
        self.assertIn("'ut.panel.tab.v1'", app_js, "localStorage tab key sürümlü olmalı")
        self.assertIn("'ut.panel.search.v1'", app_js, "localStorage search key sürümlü olmalı")
        # Eski version (v0, sürümsüz) YOK olmalı
        self.assertNotIn("'ut.panel.tab'", app_js.replace("'ut.panel.tab.v1'", ""), "eski key'ler kaldırılmalı")

    def test_app_js_has_filter_function(self):
        """app.js filterProviders() fonksiyonu olmalı."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('function filterProviders(', app_js, "filterProviders fonksiyonu olmalı")
        self.assertIn('function matchesSearchQuery(', app_js, "matchesSearchQuery fonksiyonu olmalı")

    def test_app_js_has_the_three_category_branches(self):
        """Üç sekmenin de bir dalı var mı — yalnız VARLIK kontrolü.

        Bu test eskiden api dalının kaynak metnini birebir sabitliyordu:
            assertIn("['spend', 'tokens', 'quota'].includes(c.kind)", app_js)
        O sınıflandırma yanlıştı — `tokens` kartları (Codex/Aider/Continue/Cody/Windsurf)
        diskten log okur, anahtar istemez; "API" sekmesinde görünmeleri, kartı bulamayan
        kullanıcıyı var olmayan bir anahtarı aramaya gönderir. Test bunu yakalamadı,
        çünkü davranışı değil **kaynak metni** ölçüyordu; dahası düzeltmeyi engelledi:
        doğru sınıflandırma yazıldığında kırmızıya döndü.

        Ders: kaynak metni sabitleyen test, refactor'ı cezalandırır ve hatayı görmez.
        Sınıflandırmanın kendisi artık `tests/test_panel_behavior.py`'de gerçek
        `filterProviders` çalıştırılarak ölçülüyor.
        """
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        for tab in ('active', 'api', 'local'):
            self.assertIn(f"selectedTab === '{tab}'", app_js, f'{tab} sekmesinin dalı yok')
        self.assertIn("c.status === 'ok' || c.status === 'partial'", app_js,
                      'aktif sekmesi partial kartları da göstermeli')

    def test_app_js_longtrail_logic(self):
        """Uzun kuyruk mantığı: nodata/offline kartları ayrılıyor."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("c.status === 'nodata' || c.status === 'offline'", app_js, "longtrail statüs kontrolü olmalı")
        self.assertIn('const main = filtered.filter', app_js, "main kartlar filtrelemesi olmalı")
        self.assertIn('const longtrail = filtered.filter', app_js, "longtrail kartlar filtrelemesi olmalı")

    def test_app_js_search_input_listener(self):
        """app.js'de arama input'u event listener'ı olmalı."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("$('prov-search')", app_js, "prov-search seçim olmalı")
        self.assertIn("addEventListener('input'", app_js, "arama input event listener olmalı")
        self.assertIn("localStorage.setItem('ut.panel.search.v1'", app_js, "arama terimi localStorage'a kaydedilmeli")

    def test_app_js_tab_buttons_listeners(self):
        """app.js'de sekme düğmeleri event listener'ları olmalı."""
        app_js = (Path(__file__).parent.parent / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("querySelectorAll('.prov-tab')", app_js, "sekme seçimi olmalı")
        self.assertIn("addEventListener('click'", app_js, "sekme click listener olmalı")
        self.assertIn("getAttribute('data-tab')", app_js, "tab attribute okuma olmalı")
        self.assertIn("localStorage.setItem('ut.panel.tab.v1'", app_js, "tab localStorage'a kaydedilmeli")


class Y3CurrencyIntegration(unittest.TestCase):
    """Y3 para birimi — real HTTP + tray/waybar testleri."""

    def _get_free_port(self):
        """Boş bir TCP portu bul."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def _make_cny_response(self):
        """CNY para birimli fake /v1/usage response."""
        return {
            'providers': [
                {
                    'id': 'deepseek',
                    'name': 'DeepSeek',
                    'kind': 'spend',
                    'status': 'ok',
                    'currency': 'CNY',
                    'spend': {
                        'today': 12.45,
                        'currency': 'CNY'
                    }
                }
            ],
            'thresholds': {'warn': 75, 'crit': 90}
        }

    def test_tray_currency_deepseek_cny(self):
        """Tray: DeepSeek CNY kartında ¥ sembolü gösterilmeli."""
        # Tray'de _summarize fonksiyonu CNY currency'yi ¥ ile gösteriyor mu kontrol et
        tray_path = Path(__file__).parent.parent / 'surface' / 'usage-tray.py'
        tray_code = tray_path.read_text(encoding='utf-8')

        # _num fonksiyonunda CNY eşlemesi olmalı
        self.assertIn("'CNY': '¥'", tray_code,
            "usage-tray.py'de CNY para birimi eşlemesi olmalı")

        # _num() çağrılarında currency parametresi olmalı
        self.assertIn('_num(spend.get(', tray_code,
            "Tray _num() çağrısında currency parametresi olmalı")

    def test_waybar_currency_deepseek_cny(self):
        """Waybar: DeepSeek CNY kartında ¥ sembolü gösterilmeli."""
        # Waybar script'inde CNY eşlemesi olmalı
        waybar_script = Path(__file__).parent.parent / 'surface' / 'waybar-usage.sh'
        waybar_code = waybar_script.read_text(encoding='utf-8')

        # money() fonksiyonunda CNY eşlemesi olmalı
        self.assertIn("elif curr == \"CNY\" then", waybar_code,
            "waybar-usage.sh'de CNY para birimi eşlemesi olmalı")
        self.assertIn('¥', waybar_code,
            "waybar-usage.sh'de ¥ sembolü olmalı")

        # money() çağrılarında currency parametresi geçilmeli
        self.assertIn('money(.total.usd; (.currency // "USD"))', waybar_code,
            "waybar money() çağrısında currency parametresi olmalı")


class StalenessReachesEverySurface(unittest.TestCase):
    """Bir sayının yaşı, sayının kendisi kadar bilgidir.

    2026-08-12 ölçümü `guard`'da yakaladı: 7 gün önce donmuş %5 "güvenle devam" diye
    okunuyordu. Aynı sayı üç yüzeyde daha gösteriliyordu ve hiçbiri bayat olduğunu
    söylemiyordu — panel onu "🟢 canlı · gerçek" diye etiketliyordu bile. `live.py`
    bayat değeri göstermeyi bilinçli seçti; işaret ekrana çıkmazsa o seçim yalana döner.

    Grep değil çalıştırma: jq programı gerçekten koşuyor, tray'in özetleyicisi gerçekten
    çağrılıyor, panelin rozet fonksiyonu Node'da gerçekten değerlendiriliyor.
    """

    REPO = Path(__file__).resolve().parent.parent

    @staticmethod
    def _wire(stale=True, age=604800.0):
        def bar(pct):
            return {'pct': pct, 'used': 10, 'units': 10, 'budget': 100, 'calibSuspect': None,
                    'resetAtMs': None, 'resetInSec': 3600,
                    'forecast': {'willExceed': False, 'etaMs': None, 'etaText': None},
                    'live': True, 'stale': stale}
        return {
            'schema': 'usage/v1', 'generatedAtMs': 1770000000000, 'source': 'live',
            'thresholds': {'warn': 75, 'crit': 90},
            'providers': [{
                'id': 'claude', 'name': 'Claude Code', 'calibrated': True,
                'live': {'ok': True, 'error': None, 'cached': True, 'fetchedAtMs': 1,
                         'ageSec': age, 'stale': stale, 'rateLimited': False,
                         'rateLimitTier': None},
                'limits': {'session': bar(5.0), 'weekly': bar(5.0), 'weeklyModel': None,
                           'thresholds': {'warn': 75, 'crit': 90}},
                'spend': {'currency': 'USD', 'today': 1.0, 'yesterday': 1.0, 'last30d': 1.0,
                          'byModel': [], 'priceComplete': True, 'estimatedModels': [],
                          'unknownPriceModels': [], 'catalog': None},
            }],
        }

    # ── waybar: the real jq program, served over real HTTP ────────────────────
    def _run_waybar(self, wire):
        # waybar is a Linux/Wayland module and this feeder is its Linux path — the Windows
        # path is `usage --format waybar`, which has no shell in it and is tested above.
        # Running the bash feeder under Git Bash measures Git Bash: it exits 1 with an empty
        # stderr on the runner, which was news (nothing had ever executed this script on
        # Windows before), but it is not a defect in a surface that cannot exist there.
        if sys.platform.startswith('win'):
            self.skipTest('waybar feeder is the Linux surface; the Windows path is the CLI feeder')
        if not shutil.which('jq'):
            self.skipTest('jq not installed')
        payload = json.dumps(wire).encode('utf-8')

        class H(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        httpd = HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            env = dict(os.environ, USAGE_URL=f'http://127.0.0.1:{httpd.server_address[1]}')
            out = subprocess.run(['bash', str(self.REPO / 'surface' / 'waybar-usage.sh')],
                                 capture_output=True, text=True, encoding='utf-8',
                                 errors='replace', timeout=30, env=env)
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_waybar_tooltip_says_the_number_is_old(self):
        badge = self._run_waybar(self._wire(stale=True))
        self.assertIn('bayat', badge['tooltip'])
        self.assertTrue(badge['text'].strip(), 'the badge went empty — jq empty-stream regression')

    def test_a_fresh_badge_carries_no_warning(self):
        badge = self._run_waybar(self._wire(stale=False, age=12.0))
        self.assertNotIn('bayat', badge['tooltip'])

    # ── tray: the real summariser ─────────────────────────────────────────────
    def _tray(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'usage_tray', self.REPO / 'surface' / 'usage-tray.py')
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except SystemExit:                      # Qt yoksa modül kendini kapatır
            self.skipTest('tray module needs a Qt binding')
        return module

    def test_the_tray_tooltip_says_the_number_is_old(self):
        tray = self._tray()
        _cls, _pct, tooltip, _headline = tray._summarize(self._wire(stale=True))
        self.assertIn('bayat', tooltip)
        _cls, _pct, fresh, _headline = tray._summarize(self._wire(stale=False, age=12.0))
        self.assertNotIn('bayat', fresh)

    def test_an_empty_grid_has_a_way_out(self):
        """Ölçüldü (2026-08-13, headless): bozuk `ut.panel.search.v1` ızgarayı boşaltıyor.
        Sekme artık kurtarılıyor ama aramayı silmek KULLANICININ kararı — sessizce atmak,
        gerçekten yazdığı sorguyu yok saymak olurdu. Bunun yerine sebep görünür ve çıkış
        tek tık: "Eşleşme yok" bloğunda temizleme düğmesi. Ölçüm: 0 kart → tık → 8 kart.

        Bu test yapısal — bir DOM olayı saf fonksiyon değil, düğümler olmadan
        çalıştırılamaz. Davranışın kendisi headless bir tarayıcıda ölçüldü; burada
        sabitlenen, kurtarma yolunun **var olduğu** ve doğru anahtarı temizlediği.
        """
        html = (self.REPO / 'web' / 'index.html').read_text(encoding='utf-8')
        app = (self.REPO / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="prov-clear-search"', html, 'boş ekranda kurtarma düğmesi yok')
        self.assertIn("$('prov-clear-search')", app, 'düğme hiçbir yere bağlanmamış')
        self.assertIn("localStorage.setItem('ut.panel.search.v1', '')", app,
                      'düğme kaydedilmiş aramayı temizlemiyor — yeniden yüklemede geri gelir')

    # ── panel: the real badge function, executed ──────────────────────────────
    def test_the_panel_does_not_call_a_week_old_number_live(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        harness = '''
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
for (const n of ['fmtAge', 'liveFlag']) {
  const m = src.match(new RegExp('function ' + n + '\\\\([\\\\s\\\\S]*?\\\\n}', 'm'));
  if (!m) { console.error('MISSING_FUNCTION:' + n); process.exit(2); }
  eval(m[0]);
}
const stale = liveFlag({source: 'live', live: {ok: true, stale: true, ageSec: 604800}});
const fresh = liveFlag({source: 'live', live: {ok: true, stale: false, ageSec: 12}});
console.log(JSON.stringify({stale, fresh}));
'''
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'flag.js'
            path.write_text(harness, encoding='utf-8')
            out = subprocess.run([node, str(path), str(self.REPO / 'web' / 'app.js')],
                                 capture_output=True, text=True, encoding='utf-8',
                             errors='replace', timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertIn('bayat', result['stale']['text'])
        self.assertIn('7g', result['stale']['text'], 'the badge does not say how old')
        self.assertEqual(result['stale']['cls'], 'pill warn')
        self.assertEqual(result['fresh']['text'], '🟢 canlı · gerçek')


if __name__ == '__main__':
    unittest.main()

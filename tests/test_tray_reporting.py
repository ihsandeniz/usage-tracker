#!/usr/bin/env python3
"""
test_tray_reporting.py — tepsi menüsü başarısızlığı bildiriyor mu?

Ölçülen kusur: `_run_surface()` stderr'i DEVNULL'a gönderiyor ve `Popen` patlamadıysa
`True` dönüyordu. Popen, program çalışıp SONRA ölünce patlamaz — yani `exit 2` ile ölen
usage-widget "açıldı" sayılıyordu. Menüden "Widget Aç/Kapa"ya basan kullanıcı hiçbir tepki
almıyordu ve hata hiçbir yere düşmüyordu: **widget'ın kapalı olması ile menünün çalışmaması
ekranda aynı şeydi.**

Neden bu dosya `test_surface_consistency.py`'ye eklenmedi: oradaki `_tray()` yardımcısı Qt
binding yoksa testi ATLAR. Tepsinin bildirim yolu CI'da hiç ölçülmezdi — kusurun kendisi de
zaten "kimse bakmıyordu" sınıfındandı. Burada Qt `sys.modules`'a saplanıyor, böylece
`usage-tray.py`'nin GERÇEK kodu Qt olmadan, atlanmadan koşuyor.

Sahte değil, gerçek olan ne: alt süreç gerçekten başlatılıyor (sahte usage-widget), stderr
gerçekten dosyaya akıyor, `xdg-open` gerçek bir PATH stub'ıyla çözülüyor. Sahtelenen tek şey
Qt çizim/tepsi katmanı — o bir GUI host'u gerektiriyor, mantık değil.

Çalıştır: python3 -m unittest tests.test_tray_reporting -v
"""
import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRAY_PATH = REPO / 'surface' / 'usage-tray.py'

# Tepsi bir Linux masaüstü yüzeyi (SNI host, Hyprland yerleştirme, hyprctl çağıran
# usage-widget). Sahte kardeş script shebang'le çalıştırılıyor — Windows'ta shebang yok.
# Ledger: test/isletim-sistemine-bagli-kurulum-testi-asilir.
if os.name == 'nt':                                      # pragma: no cover
    raise unittest.SkipTest('tray is a Linux desktop surface; the fake widget needs a shebang')


# ── Qt sahtesi — yalnız çizim/tepsi katmanı ──────────────────────────────────
class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)


class _Action:
    def __init__(self, text, callback=None):
        self.text = text
        self.callback = callback
        self.enabled = True

    def setEnabled(self, v):
        self.enabled = v

    def setText(self, v):
        self.text = v


class _Menu:
    def __init__(self):
        self.actions = []

    def addAction(self, text, callback=None):
        act = _Action(text, callback)
        self.actions.append(act)
        return act

    def addSeparator(self):
        pass

    def tikla(self, text):
        """Menü öğesine bas — kullanıcının yaptığı şeyin ta kendisi."""
        for act in self.actions:
            if act.text == text:
                if act.callback is None:
                    raise AssertionError('menü öğesi "%s" hiçbir yere bağlı değil' % text)
                return act.callback()
        raise AssertionError('menüde "%s" yok: %r' % (text, [a.text for a in self.actions]))


class _SystemTrayIcon:
    Warning = 2
    Trigger = 3
    showMessage_raises = False

    def __init__(self):
        self.icon = None
        self.tooltip = None
        self.menu = None
        self.balloons = []
        self.activated = _Signal()

    def setIcon(self, icon):
        self.icon = icon

    def setToolTip(self, t):
        self.tooltip = t

    def setContextMenu(self, m):
        self.menu = m

    def show(self):
        pass

    def showMessage(self, title, body, level=None, msec=None):
        if type(self).showMessage_raises:
            # Bazı SNI host'ları bu çağrıyı hiç uygulamaz.
            raise NotImplementedError('this SNI host has no notifications')
        self.balloons.append((title, body, level, msec))

    @staticmethod
    def isSystemTrayAvailable():
        return True


class _QTimer:
    def __init__(self):
        self.timeout = _Signal()
        self.interval = None

    def start(self, ms):
        self.interval = ms


class _Noop:
    """QPixmap/QPainter/QIcon/QColor/QPen — çağrılır, hiçbir şey yapmaz."""
    Antialiasing = 1

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _qt_stub():
    import types
    qtwidgets = types.ModuleType('PyQt5.QtWidgets')
    qtwidgets.QSystemTrayIcon = _SystemTrayIcon
    qtwidgets.QMenu = _Menu
    qtwidgets.QApplication = _Noop

    qtgui = types.ModuleType('PyQt5.QtGui')
    for ad in ('QPixmap', 'QPainter', 'QIcon', 'QColor', 'QPen'):
        setattr(qtgui, ad, _Noop)
    qtgui.QPainter = _Noop

    qtcore = types.ModuleType('PyQt5.QtCore')
    qtcore.QTimer = _QTimer
    qtcore.Qt = _Noop()

    pkg = types.ModuleType('PyQt5')
    pkg.QtWidgets, pkg.QtGui, pkg.QtCore = qtwidgets, qtgui, qtcore
    return pkg


def load_tray(path=TRAY_PATH):
    """usage-tray.py'yi Qt olmadan içe al. Modül nesnesi döner (kopya değil, gerçek dosya)."""
    stub = _qt_stub()
    korunan = {ad: sys.modules.get(ad) for ad in
               ('PyQt5', 'PyQt5.QtWidgets', 'PyQt5.QtGui', 'PyQt5.QtCore')}
    sys.modules['PyQt5'] = stub
    try:
        spec = importlib.util.spec_from_file_location('usage_tray_under_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for ad, onceki in korunan.items():
            if onceki is None:
                sys.modules.pop(ad, None)
            else:
                sys.modules[ad] = onceki
    return module


# ── ortak kurulum ────────────────────────────────────────────────────────────
class TrayCase(unittest.TestCase):
    """İzole SELF_DIR + log + kısa tavan. Gerçek alt süreç, sahte kardeş script."""

    WAIT = 0.4          # canlı tavan 1.0 sn; testte kısa tutuluyor, mantık aynı

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='ut-tray-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tray = load_tray()
        self.tray.SELF_DIR = self.tmp
        self.tray.LOG_PATH = self.tmp / 'state' / 'tray.log'
        self.tray.SURFACE_WAIT_SEC = self.WAIT
        self.tray._fetch = lambda: None          # sunucuya gerçekten gitme
        _SystemTrayIcon.showMessage_raises = False
        self.addCleanup(setattr, _SystemTrayIcon, 'showMessage_raises', False)

    # ── yardımcılar ──────────────────────────────────────────────────────────
    def widget_yaz(self, govde, calistirilabilir=True):
        """Sahte usage-widget üret. Shebang sys.executable — hangi python'la koştuğumuz belli."""
        yol = self.tmp / 'usage-widget'
        yol.write_text('#!%s\n%s' % (sys.executable, govde), encoding='utf-8')
        if calistirilabilir:
            yol.chmod(yol.stat().st_mode | stat.S_IXUSR)
        else:
            yol.chmod(stat.S_IRUSR)
        return yol

    def path_stub(self, xdg_open=True):
        """PATH'i izole et. xdg-open gerçek olsun ama TARAYICI AÇMASIN."""
        kutu = self.tmp / 'bin'
        kutu.mkdir(exist_ok=True)
        if xdg_open:
            sahte = kutu / 'xdg-open'
            sahte.write_text('#!%s\nimport sys\nopen(%r, "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
                             % (sys.executable, str(self.tmp / 'xdg-open.calls')), encoding='utf-8')
            sahte.chmod(sahte.stat().st_mode | stat.S_IXUSR)
        onceki = os.environ.get('PATH', '')
        os.environ['PATH'] = str(kutu)
        self.addCleanup(os.environ.__setitem__, 'PATH', onceki)
        return kutu

    def log_metni(self):
        try:
            return self.tray.LOG_PATH.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return ''


# ── _run_surface sözleşmesi ──────────────────────────────────────────────────
class TheWidgetLauncherTellsTheTruth(TrayCase):

    def test_a_missing_widget_is_reported_not_swallowed(self):
        ok, mesaj = self.tray._run_surface('toggle')
        self.assertFalse(ok)
        self.assertIn('usage-widget', mesaj)
        self.assertIn(str(self.tmp), mesaj, 'mesaj hangi yola baktığını söylemiyor')
        self.assertIn('usage-widget', self.log_metni(), 'sebep hiçbir yere yazılmadı')

    def test_a_widget_that_dies_is_a_failure_even_though_popen_succeeded(self):
        """Kusurun ta kendisi: Popen başarılı, program ölü, eski kod True diyordu."""
        self.widget_yaz('import sys\nsys.exit(2)\n')
        ok, mesaj = self.tray._run_surface('toggle')
        self.assertFalse(ok, 'ölen widget hâlâ başarı sayılıyor')
        self.assertIn('2', mesaj, 'çıkış kodu mesajda yok')

    def test_the_dead_widgets_own_error_reaches_the_log(self):
        """stderr DEVNULL'a giderse "hiçbir şey olmadı" ile "patladı" aynı görünür."""
        self.widget_yaz('import sys\nsys.stderr.write("hyprctl: no such dispatcher\\n")\nsys.exit(3)\n')
        ok, _ = self.tray._run_surface('open')
        self.assertFalse(ok)
        self.assertIn('hyprctl: no such dispatcher', self.log_metni(),
                      'kardeş sürecin stderr\'i log dosyasına akmıyor')

    def test_the_failure_message_carries_the_reason_not_just_the_code(self):
        self.widget_yaz('import sys\nsys.stderr.write("chromium not found\\n")\nsys.exit(4)\n')
        ok, mesaj = self.tray._run_surface('open')
        self.assertFalse(ok)
        self.assertIn('chromium not found', mesaj,
                      'kullanıcıya yalnız çıkış kodu gösteriliyor, sebep gösterilmiyor')

    def test_a_clean_exit_is_success_and_does_not_wait_out_the_ceiling(self):
        """Başarı yolu menü tıklamasını tavan boyunca bekletmemeli."""
        self.widget_yaz('import sys\nsys.exit(0)\n')
        basla = time.monotonic()
        ok, mesaj = self.tray._run_surface('toggle')
        gecen = time.monotonic() - basla
        self.assertTrue(ok, mesaj)
        self.assertEqual(mesaj, '')
        self.assertLess(gecen, self.WAIT, 'çıkış kodu 0 görülmesine rağmen tavan bekleniyor')

    def test_a_widget_still_running_at_the_ceiling_counts_as_open(self):
        """Uzun ömürlü süreç = pencere açıldı. Tavan aşılmamalı — menü donar."""
        self.widget_yaz('import time\ntime.sleep(30)\n')
        basla = time.monotonic()
        ok, mesaj = self.tray._run_surface('open')
        gecen = time.monotonic() - basla
        self.assertTrue(ok, mesaj)
        self.assertGreaterEqual(gecen, self.WAIT)
        self.assertLess(gecen, self.WAIT + 1.5, 'poll döngüsü tavanda durmuyor')

    def test_a_widget_that_cannot_be_executed_is_reported(self):
        self.widget_yaz('import sys\nsys.exit(0)\n', calistirilabilir=False)
        ok, mesaj = self.tray._run_surface('toggle')
        self.assertFalse(ok)
        self.assertIn('toggle', mesaj)
        self.assertIn('usage-widget', self.log_metni())


# ── _open_panel geri düşüşü ──────────────────────────────────────────────────
class ThePanelStillOpensWhenTheWidgetCannot(TrayCase):

    def test_the_panel_falls_back_to_the_browser_without_bothering_the_user(self):
        """Widget yoksa panel yine açılır — bu bir arıza değil, geri düşüş. Balon çıkmamalı."""
        self.path_stub(xdg_open=True)
        mesaj = self.tray._open_panel()
        self.assertEqual(mesaj, '', 'çalışan geri düşüş kullanıcıyı uyarıyor')
        # xdg-open gerçekten çağrıldı mı — ve panelin adresiyle mi?
        for _ in range(40):
            if (self.tmp / 'xdg-open.calls').exists():
                break
            time.sleep(0.05)
        cagri = (self.tmp / 'xdg-open.calls').read_text(encoding='utf-8')
        self.assertIn(self.tray.BASE, cagri)

    def test_the_panel_reports_when_both_paths_fail(self):
        self.path_stub(xdg_open=False)      # PATH'te xdg-open yok
        mesaj = self.tray._open_panel()
        self.assertTrue(mesaj, 'iki yol da öldü ama kullanıcıya hiçbir şey denmiyor')
        self.assertIn('xdg-open', mesaj)
        self.assertIn('xdg-open', self.log_metni())


# ── menünün kendisi — tıklanan şey ───────────────────────────────────────────
class TheMenuEntryReportsWhatHappened(TrayCase):

    def _tepsi(self):
        return self.tray.Tray(_Noop())

    def test_the_menu_entry_warns_the_user_when_the_widget_fails(self):
        """Regresyonun tıklanabilir hâli: menü öğesi eskiden çıplak lambda'ya bağlıydı."""
        self.widget_yaz('import sys\nsys.stderr.write("no compositor\\n")\nsys.exit(2)\n')
        tepsi = self._tepsi()
        tepsi.icon.menu.tikla('Widget Aç/Kapa')
        self.assertEqual(len(tepsi.icon.balloons), 1, 'menü sessizce hiçbir şey yapmadı')
        _baslik, govde, seviye, _sure = tepsi.icon.balloons[0]
        self.assertIn('no compositor', govde)
        self.assertIn(str(self.tray.LOG_PATH), govde, 'balon ayrıntının nerede olduğunu söylemiyor')
        self.assertEqual(seviye, _SystemTrayIcon.Warning)

    def test_the_menu_entry_stays_quiet_when_the_widget_opens(self):
        self.widget_yaz('import sys\nsys.exit(0)\n')
        tepsi = self._tepsi()
        tepsi.icon.menu.tikla('Widget Aç/Kapa')
        self.assertEqual(tepsi.icon.balloons, [], 'başarılı açılışta uyarı balonu çıkıyor')

    def test_the_left_click_path_reports_too(self):
        """Sol tık da paneli açar — aynı bildirim yolundan geçmeli."""
        self.path_stub(xdg_open=False)
        tepsi = self._tepsi()
        tepsi._on_activate(_SystemTrayIcon.Trigger)
        self.assertEqual(len(tepsi.icon.balloons), 1, 'sol tıkta iki yol da öldü, kullanıcı duymadı')

    def test_the_balloon_falls_back_to_stderr_when_the_host_cannot_show_it(self):
        self.widget_yaz('import sys\nsys.exit(2)\n')
        _SystemTrayIcon.showMessage_raises = True
        tepsi = self._tepsi()
        eski, sys.stderr = sys.stderr, _Yakala()
        try:
            tepsi.icon.menu.tikla('Widget Aç/Kapa')     # showMessage patlıyor
            yazilan = sys.stderr.metin
        finally:
            sys.stderr = eski
        self.assertIn('usage-tray', yazilan, 'balon gösterilemedi ve mesaj tamamen kayboldu')


class _Yakala:
    def __init__(self):
        self.metin = ''

    def write(self, s):
        self.metin += s

    def flush(self):
        pass


# ── log yolu taşınabilirliği ─────────────────────────────────────────────────
class TheLogLandsWhereTheDesktopSaysItShould(unittest.TestCase):

    def _yukle(self, **env):
        onceki = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items() if v is not None})
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
        try:
            return load_tray()
        finally:
            for k, v in onceki.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_the_log_follows_xdg_state_home(self):
        with tempfile.TemporaryDirectory() as td:
            tray = self._yukle(XDG_STATE_HOME=td)
            self.assertEqual(tray.LOG_PATH, Path(td) / 'usage-tracker' / 'tray.log')

    def test_without_xdg_state_home_it_uses_the_conventional_path(self):
        tray = self._yukle(XDG_STATE_HOME=None)
        self.assertEqual(tray.LOG_PATH,
                         Path.home() / '.local' / 'state' / 'usage-tracker' / 'tray.log')


if __name__ == '__main__':
    unittest.main()

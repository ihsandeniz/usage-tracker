"""Portability — paths, encodings, atomic writes.

Three defects, all of which only bite off this machine:

  B2  User state was written next to the code (`Path(__file__).parent.parent`). That works
      exactly as long as the code lives somewhere writable. Under PyInstaller, in
      Program Files, in /usr/lib or in a read-only container it does not, and the failure
      is a broken install rather than a warning.
  T2  Temp files used a FIXED name (`with_suffix('.tmp')`). Two writers race and one
      wins with half the other's bytes.
  T4  Nothing in the codebase ever asked which platform it was on, so there was no branch
      to get wrong — and no branch to test either.
  T5  The startup banner could not be encoded in cp1252, so the packaged tool crashed on
      Windows before it bound the port — and the suite could not see it, because the
      harness handed the child `PYTHONIOENCODING=utf-8`.

The Windows branch is exercised here by faking the platform. That is NOT proof it works on
Windows; it is proof the *code path* is reachable and self-consistent. Only a real
windows-latest run (or a real machine) can say more, and this file cannot.
"""
import ast
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest import mock

from usage import platform as up

REPO = pathlib.Path(__file__).resolve().parent.parent


class _FakePlatform:
    """Swap in a whole environment: OS marker plus the env vars that OS actually sets."""

    def __init__(self, testcase, *, sys_platform, env):
        self._p = [mock.patch.object(up, 'SYS_PLATFORM', sys_platform),
                   mock.patch.dict(os.environ, env, clear=True)]
        for p in self._p:
            p.start()
        testcase.addCleanup(self.stop)

    def stop(self):
        for p in reversed(self._p):
            p.stop()


def linux(tc, home):
    return _FakePlatform(tc, sys_platform='linux', env={'HOME': str(home)})


def windows(tc, home):
    return _FakePlatform(tc, sys_platform='win32', env={
        'USERPROFILE': str(home),
        'APPDATA': str(pathlib.Path(home) / 'AppData' / 'Roaming'),
        'LOCALAPPDATA': str(pathlib.Path(home) / 'AppData' / 'Local'),
    })


class BothBranchesResolve(unittest.TestCase):
    """T4 — a branch only one side of which is tested is a branch that will break on the
    other side. Ledger: test/yesil-ama-yanlis-seyi-olcuyor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_linux_uses_xdg(self):
        linux(self, self.home)
        self.assertEqual(up.config_dir(), self.home / '.config' / 'usage-tracker')
        self.assertEqual(up.state_dir(),  self.home / '.local' / 'state' / 'usage-tracker')
        self.assertEqual(up.cache_dir(),  self.home / '.cache' / 'usage-tracker')

    def test_linux_honours_xdg_overrides(self):
        _FakePlatform(self, sys_platform='linux', env={
            'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / 'cfg'),
            'XDG_STATE_HOME': str(self.home / 'st'), 'XDG_CACHE_HOME': str(self.home / 'ca')})
        self.assertEqual(up.config_dir(), self.home / 'cfg' / 'usage-tracker')
        self.assertEqual(up.state_dir(),  self.home / 'st' / 'usage-tracker')
        self.assertEqual(up.cache_dir(),  self.home / 'ca' / 'usage-tracker')

    def test_windows_uses_appdata(self):
        windows(self, self.home)
        roaming = self.home / 'AppData' / 'Roaming'
        local = self.home / 'AppData' / 'Local'
        self.assertEqual(up.config_dir(), roaming / 'usage-tracker')
        self.assertEqual(up.state_dir(),  local / 'usage-tracker' / 'State')
        self.assertEqual(up.cache_dir(),  local / 'usage-tracker' / 'Cache')

    def test_windows_without_appdata_still_returns_something_writable(self):
        """A stripped service account may have no APPDATA. Falling over is not an option;
        falling back to the profile directory is."""
        _FakePlatform(self, sys_platform='win32', env={'USERPROFILE': str(self.home)})
        for d in (up.config_dir(), up.state_dir(), up.cache_dir()):
            self.assertTrue(str(d).startswith(str(self.home)), f'{d} escaped the profile')

    def test_no_path_ever_lands_inside_the_installation(self):
        """B2 — the whole point. Whatever the platform, state must not be written next to
        the code, because the code may be read-only."""
        for make in (linux, windows):
            with self.subTest(platform=make.__name__):
                make(self, self.home)
                for d in (up.config_dir(), up.state_dir(), up.cache_dir()):
                    self.assertFalse(str(d).startswith(str(REPO)),
                                     f'{d} is inside the installation directory')


class ExistingUsersKeepTheirData(unittest.TestCase):
    """Moving a path silently throws away the calibration people already did."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_legacy_file_next_to_the_code_is_still_found(self):
        legacy = self.root / 'usage_calib.json'
        legacy.write_text('{"anchor": 1}', encoding='utf-8')
        new = self.root / 'state' / 'usage_calib.json'

        self.assertEqual(up.pick_existing(new, legacy), legacy)

    def test_the_new_location_wins_once_it_exists(self):
        legacy = self.root / 'usage_calib.json'
        legacy.write_text('{"anchor": 1}', encoding='utf-8')
        new = self.root / 'state' / 'usage_calib.json'
        new.parent.mkdir()
        new.write_text('{"anchor": 2}', encoding='utf-8')

        self.assertEqual(up.pick_existing(new, legacy), new)

    def test_with_neither_present_we_write_to_the_new_one(self):
        new = self.root / 'state' / 'usage_calib.json'
        self.assertEqual(up.pick_existing(new, self.root / 'nope.json'), new)


class AtomicWrite(unittest.TestCase):
    """T2 — a fixed `.tmp` name means two writers share one scratch file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_it_writes_and_reads_back(self):
        target = self.root / 'sub' / 'x.json'
        up.atomic_write_text(target, 'merhaba ✓')
        self.assertEqual(target.read_text(encoding='utf-8'), 'merhaba ✓')

    def test_concurrent_writers_do_not_interleave(self):
        import threading
        target = self.root / 'x.json'
        payloads = [str(i) * 20_000 for i in range(8)]

        threads = [threading.Thread(target=up.atomic_write_text, args=(target, p))
                   for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Whoever won, the file must be exactly one payload — never a blend of two.
        self.assertIn(target.read_text(encoding='utf-8'), payloads)

    def test_it_leaves_no_scratch_files_behind(self):
        target = self.root / 'x.json'
        for _ in range(5):
            up.atomic_write_text(target, 'ok')
        leftovers = [p.name for p in self.root.iterdir() if p.name != 'x.json']
        self.assertEqual(leftovers, [], f'temp files left behind: {leftovers}')

    def test_a_failed_write_does_not_destroy_the_previous_file(self):
        target = self.root / 'x.json'
        up.atomic_write_text(target, 'good')
        with mock.patch.object(up.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                up.atomic_write_text(target, 'bad')
        self.assertEqual(target.read_text(encoding='utf-8'), 'good')


class EveryTextFileDeclaresItsEncoding(unittest.TestCase):
    """T1 — Python's default text encoding is the *locale's*, and on Windows that is still
    cp1252, not UTF-8. Reading a UTF-8 source file without saying so raises
    UnicodeDecodeError the moment a Turkish character or an emoji shows up.

    The product code was already clean when this was measured (2026-08-11); this test is
    what stops it drifting back, and it covers the tests too, which were not clean.
    """

    TEXT_CALLS = {'open', 'read_text', 'write_text'}

    #: `webbrowser.open(url)` ve `os.open(fd)` metin G/Ç değildir; denetçi adlara bakıyor,
    #: türlere değil. Bu iki modül dışlanmazsa doğru kod kırmızı yanar ve gerçek bulguları
    #: gürültüye boğar — bir denetçinin en pahalı kusuru, güvenilirliğini kaybetmesidir.
    NOT_FILE_IO = {'webbrowser', 'os'}

    def _offenders(self):
        out = []
        for path in sorted(REPO.rglob('*.py')):
            if any(part in ('.git', '__pycache__', '.venv') for part in path.parts):
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = (fn.attr if isinstance(fn, ast.Attribute)
                        else fn.id if isinstance(fn, ast.Name) else None)
                if name not in self.TEXT_CALLS:
                    continue
                if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                        and fn.value.id in self.NOT_FILE_IO):
                    continue
                mode = None
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                if name == 'open' and isinstance(fn, ast.Attribute) and node.args \
                        and isinstance(node.args[0], ast.Constant):
                    mode = node.args[0].value
                for kw in node.keywords:
                    if kw.arg == 'mode' and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if isinstance(mode, str) and 'b' in mode:
                    continue                       # binary: encoding would be an error
                if not any(kw.arg == 'encoding' for kw in node.keywords):
                    out.append(f'{path.relative_to(REPO)}:{node.lineno}  {name}()')
        return out

    def test_no_text_io_relies_on_the_locale(self):
        offenders = self._offenders()
        self.assertFalse(offenders,
                         'text I/O without encoding= — breaks on a cp1252 Windows:\n  ' +
                         '\n  '.join(offenders))


class NothingWritesIntoTheInstallation(unittest.TestCase):
    """B2 measured at the modules that actually write, not just at the path helper.
    A correct `platform.py` that nobody calls fixes nothing."""

    WRITERS = [
        ('usage.engine',     'CALIB_PATH',   'usage_calib.json'),
        ('usage.viewconfig', 'CONFIG_PATH',  'view_config.json'),
        ('usage.settings',   'SETTINGS_PATH', 'settings.json'),
    ]

    def test_state_files_live_outside_the_code(self):
        import importlib
        for module_name, attr, filename in self.WRITERS:
            with self.subTest(module=module_name):
                mod = importlib.import_module(module_name)
                path = pathlib.Path(getattr(mod, attr))
                self.assertFalse(
                    str(path).startswith(str(REPO / 'usage')) or path.parent == REPO,
                    f'{module_name}.{attr} still writes {filename} next to the code: {path}')

    def test_the_live_cache_is_outside_the_code(self):
        from usage import live
        self.assertFalse(str(live._disk_cache_path()).startswith(str(REPO)))

    def test_the_price_cache_is_outside_the_code(self):
        from usage import catalog
        saved = catalog._USER_CACHE_OVERRIDE
        catalog._USER_CACHE_OVERRIDE = None
        try:
            self.assertFalse(str(catalog.user_cache_path()).startswith(str(REPO)))
        finally:
            catalog._USER_CACHE_OVERRIDE = saved

    def test_read_only_data_may_stay_with_the_code(self):
        """The distinction is writability, not location: the price snapshot and the
        overrides table ship with the package and are never written to."""
        from usage import catalog, pricing
        self.assertTrue(str(catalog.BUNDLED_PATH).startswith(str(REPO)))
        self.assertTrue(str(pricing.OVERRIDES_PATH).startswith(str(REPO)))

    def test_calibration_written_by_an_old_version_is_still_read(self):
        from usage import engine
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            legacy = root / 'usage_calib.json'
            legacy.write_text('{"anchorPct": 42}', encoding='utf-8')
            with mock.patch.object(engine, 'CALIB_PATH', root / 'state' / 'usage_calib.json'), \
                    mock.patch.object(engine, 'LEGACY_CALIB_PATH', legacy):
                self.assertEqual(engine._load_calib().get('anchorPct'), 42)

    def test_saving_calibration_creates_the_new_location(self):
        from usage import engine
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            new = root / 'state' / 'usage_calib.json'
            with mock.patch.object(engine, 'CALIB_PATH', new), \
                    mock.patch.object(engine, 'LEGACY_CALIB_PATH', root / 'absent.json'):
                self.assertTrue(engine._save_calib({'anchorPct': 7}))
                self.assertTrue(new.exists(), 'save did not create the parent directory')
                self.assertEqual(engine._load_calib().get('anchorPct'), 7)


class FrozenBundle(unittest.TestCase):
    """PyInstaller unpacks a one-file build into a temp directory and points sys._MEIPASS at
    it. Anything resolved from `Path(__file__).parent.parent` lands somewhere else, so the
    web assets, the price snapshot and the overrides table go missing — in a build that
    otherwise starts up fine and reports no error. Nothing in the repo knew about this.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.meipass = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_unfrozen_resources_sit_with_the_code(self):
        self.assertFalse(up.is_frozen())
        self.assertEqual(up.resource_dir(), up.INSTALL_DIR)

    def test_frozen_resources_come_from_meipass(self):
        with mock.patch.object(up.sys, '_MEIPASS', str(self.meipass), create=True), \
                mock.patch.object(up.sys, 'frozen', True, create=True):
            self.assertTrue(up.is_frozen())
            self.assertEqual(up.resource_dir(), self.meipass)

    def test_state_still_goes_to_the_user_when_frozen(self):
        """_MEIPASS is a temp directory that disappears on exit. Writing state there would
        silently lose the user's calibration on every run."""
        with mock.patch.object(up.sys, '_MEIPASS', str(self.meipass), create=True), \
                mock.patch.object(up.sys, 'frozen', True, create=True):
            for d in (up.config_dir(), up.state_dir(), up.cache_dir()):
                self.assertFalse(str(d).startswith(str(self.meipass)),
                                 f'{d} would vanish when the bundle exits')

    def test_bundled_resources_are_resolved_through_the_helper(self):
        """A module that hardcodes __file__ will not follow _MEIPASS. Assert the wiring,
        since a frozen build cannot be exercised from here."""
        import ast
        for rel in ('usage/pricing.py', 'usage/catalog.py'):
            src = (REPO / rel).read_text(encoding='utf-8')
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                    continue
                target = node.targets[0]
                if not (isinstance(target, ast.Name)
                        and target.id in ('OVERRIDES_PATH', 'BUNDLED_PATH')):
                    continue
                self.assertNotIn('__file__', ast.dump(node.value),
                                 f'{rel}: {target.id} resolves from __file__ and will not '
                                 f'find its data inside a PyInstaller bundle')


class StdlibPlatformIsNotShadowed(unittest.TestCase):
    """usage/platform.py sits next to modules that may `import platform`. If the package
    directory ever lands on sys.path, ours would win and break them silently."""

    def test_importing_platform_still_gives_the_stdlib(self):
        import platform as stdlib_platform
        self.assertTrue(hasattr(stdlib_platform, 'python_version'))
        self.assertIsNot(stdlib_platform, up)

    def test_our_module_does_not_import_the_stdlib_one(self):
        """Defence in depth: if we never import it, we can never be confused by it."""
        src = (REPO / 'usage' / 'platform.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn('platform', [a.name for a in node.names])


class TheStaticGuardComparesLikeWithLike(unittest.TestCase):
    """T6 — the panel's files are behind a path comparison, and comparisons need one shape.

    The traversal guard resolves the *requested* path and checks it sits under the web root.
    The root itself was stored unresolved, so anything that gives the same directory two
    spellings makes every file 404 while the API keeps answering: the tool starts, the
    endpoints work, and the screen is blank.

    On Windows that is the normal case, not an edge one — `%TEMP%` is commonly an 8.3 short
    name (`C:\\Users\\RUNNER~1\\...`), a PyInstaller bundle unpacks under it, and
    `sys._MEIPASS` carries the short spelling while `resolve()` returns the long one. The
    packaging run measured exactly that on 2026-08-13: `/`, `/app.js` and `/styles.css`
    never returned 200 from a binary whose wire endpoint was fine.

    A symlink is the same defect with a spelling Linux can produce, which is why this test
    can run anywhere.
    """

    def setUp(self):
        import server as server_mod
        self.server_mod = server_mod
        self.root = pathlib.Path(tempfile.mkdtemp(prefix='ut-static-'))
        (self.root / 'real' / 'web').mkdir(parents=True)
        (self.root / 'real' / 'web' / 'app.js').write_text('ok', encoding='utf-8')
        (self.root / 'link').symlink_to(self.root / 'real')

    def test_a_file_reached_through_an_alias_is_still_served(self):
        served = self.server_mod.static_file('/app.js', root=self.root / 'link' / 'web')
        self.assertIsNotNone(served, 'the web root spelled differently served nothing')
        self.assertEqual(served.read_text(encoding='utf-8'), 'ok')

    def test_the_traversal_guard_still_says_no(self):
        """The fix must not buy the panel's files with the guard they sit behind."""
        (self.root / 'real' / 'secret.txt').write_text('no', encoding='utf-8')
        for attempt in ('/../secret.txt', '/..%2fsecret.txt', '/subdir/../../secret.txt'):
            with self.subTest(attempt=attempt):
                self.assertIsNone(
                    self.server_mod.static_file(attempt, root=self.root / 'link' / 'web'))

    def test_the_module_root_is_resolved_at_import(self):
        self.assertEqual(self.server_mod.WEBD, self.server_mod.WEBD.resolve())


class TheEntryPointSurvivesALegacyCodePage(unittest.TestCase):
    """T5 — the one the CI harness was hiding.

    On Windows, Python only reaches the console through WriteConsoleW; the moment stdout is
    a pipe or a file it encodes with the locale code page (cp1252). The startup banner
    contains `→`, which cp1252 does not have, so the packaged tool raised UnicodeEncodeError
    and exited **before binding the port** — measured on windows-latest 2026-08-13, the
    first time `package.yml` ran. `usage --format waybar` starts with `◐` and dies the same
    way; that command is the documented Windows feeder.

    No test could see it: `tests/test_cli.py:run_cli` sets `PYTHONIOENCODING=utf-8` for the
    child, which *is* the fix, handed to the code under test by its harness. So these run
    the real entry points with cp1252 forced on and nothing else changed.

    Forcing it works on any platform, which is the point — the bug is reproducible on Linux
    and does not need a Windows machine to stay fixed.
    """

    def _env(self, home):
        env = dict(os.environ)
        env.update({'PYTHONIOENCODING': 'cp1252', 'HOME': home, 'USERPROFILE': home,
                    'XDG_CONFIG_HOME': str(pathlib.Path(home) / 'config'),
                    'XDG_STATE_HOME': str(pathlib.Path(home) / 'state'),
                    'XDG_CACHE_HOME': str(pathlib.Path(home) / 'cache')})
        env.pop('USAGE_DEMO', None)
        return env

    def _run(self, args, home):
        return subprocess.run([sys.executable, str(REPO / 'server.py'), *args],
                              capture_output=True, cwd=str(REPO), env=self._env(home),
                              timeout=90)                      # bytes: decoding is our job

    def test_the_server_banner_does_not_kill_the_server(self):
        home = tempfile.mkdtemp(prefix='ut-cp1252-')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        env = self._env(home)
        env['USAGE_PORT'] = str(port)
        proc = subprocess.Popen([sys.executable, str(REPO / 'server.py')],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(REPO), env=env)
        try:
            deadline = time.time() + 20
            served = False
            while time.time() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen(
                            f'http://127.0.0.1:{port}/v1/usage', timeout=2) as r:
                        served = r.status == 200
                    break
                except Exception:
                    time.sleep(0.4)
            if proc.poll() is not None:
                out, err = proc.communicate()
                self.fail('the server died before serving on a cp1252 stdout:\n'
                          + err.decode('utf-8', 'replace')[-800:])
            self.assertTrue(served, 'the server never answered on 127.0.0.1')
        finally:
            proc.kill()
            proc.communicate()

    def test_the_waybar_feeder_still_emits_json(self):
        home = tempfile.mkdtemp(prefix='ut-cp1252-')
        p = self._run(['usage', '--format', 'waybar', '--local'], home)
        self.assertEqual(p.returncode, 0, p.stderr.decode('utf-8', 'replace')[-500:])
        payload = json.loads(p.stdout.decode('utf-8', 'replace'))
        self.assertIn('text', payload)
        self.assertIn('class', payload)

    def test_the_turkish_greeting_survives_a_legacy_code_page(self):
        """`İ` (U+0130) cp1252'de yoktur ve iki dilli karşılamanın **ilk** harfidir.

        Windows koşucusu bunu 2026-08-13'te gösterdi: v0.2.2'de kapatılan kusur sınıfı,
        çıktıya Türkçe eklenince yeni bir kapıdan geri geldi. Koruma artık karşılamayı basan
        fonksiyonun kendi içinde de çağrılıyor; bu test onu cp1252 altında koşturuyor.
        """
        import io
        import sys as _sys
        sys.path.insert(0, str(REPO))
        import server as server_mod
        from usage import i18n, wizard

        buffer = io.TextIOWrapper(io.BytesIO(), encoding='cp1252', errors='strict')
        original = _sys.stdout
        _sys.stdout = buffer
        try:
            with mock.patch.object(wizard, 'is_windows', return_value=True), \
                 mock.patch.object(wizard, 'is_first_run', return_value=False), \
                 mock.patch.dict(os.environ, {'UT_NO_BROWSER': '1', 'UT_LANG': 'tr'}):
                server_mod._greet_and_open(no_open=False)     # eskiden burada çökerdi
        finally:
            _sys.stdout = original
        self.assertIn('İlk', i18n.t('first_run', 'tr'), 'the message under test lost its İ')

    def test_a_mistyped_command_still_explains_itself(self):
        """The dispatcher's message is separated with `·` — cp1252 has it, but the reply
        travels through the same stream, so it is measured rather than assumed."""
        p = self._run(['gaurd'], tempfile.mkdtemp(prefix='ut-cp1252-'))
        self.assertEqual(p.returncode, 64)
        self.assertIn('unknown command', p.stderr.decode('utf-8', 'replace'))


if __name__ == '__main__':
    unittest.main()

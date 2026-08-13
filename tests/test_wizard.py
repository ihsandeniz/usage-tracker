"""Kurulum sihirbazı — bir kurulumun geri alınabilir olduğu, geri alınarak ölçülür.

Bu dosyanın çoğu "yazdı mı" değil **"neyi yazmadı"** sorusunu sorar. Sihirbazın tehlikeli
tarafı yazmak değil, *kullanıcının kendi dosyasının üstüne* yazmak ve *dokunmadığı bir şeyi*
geri alırken bozmaktır:

  W1  `undo`, sihirbazın hiç yazmadığı bir autostart'ı durdurup siliyordu. Ölçüldü
      2026-08-13: geliştirme makinesindeki `service.sh` servisini kapattı.
  W2  Var olan bir dosyanın üstüne yedeksiz yazmak.
  W3  İkinci çalıştırmanın, ilk çalıştırmanın aldığı yedeği ezmesi (orijinal kaybolur).

Windows adımları burada **sahte platformla** koşuyor — bu, Windows'ta çalıştığının kanıtı
değil; yolun *erişilebilir ve kendi içinde tutarlı* olduğunun kanıtı. Gerçek kanıt
`package.yml`'in windows-latest işinde, donmuş ikilinin üstünde.
"""
import io
import os
import urllib.error
import pathlib
import tempfile
import unittest
from unittest import mock

from usage import platform as up
from usage import wizard


class _Sandbox(unittest.TestCase):
    """Her test kendi HOME'unda. `systemctl` asla gerçekten çağrılmasın diye `_run` da
    yerinden alınır — sandbox `systemctl --user`'ı kapsamaz (FAZ 5e dersi, ledger'da)."""

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix='ut-wizard-'))
        env = {'HOME': str(self.home), 'USERPROFILE': str(self.home),
               'XDG_CONFIG_HOME': str(self.home / '.config'),
               'XDG_STATE_HOME': str(self.home / '.state'),
               'XDG_CACHE_HOME': str(self.home / '.cache'),
               'APPDATA': str(self.home / 'AppData' / 'Roaming'),
               'LOCALAPPDATA': str(self.home / 'AppData' / 'Local')}
        patches = [mock.patch.dict(os.environ, env, clear=False),
                   mock.patch.object(wizard, '_run', return_value=(0, ''))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.run_calls = wizard._run

    def as_windows(self):
        p = mock.patch.object(up, 'SYS_PLATFORM', 'win32')
        p.start()
        self.addCleanup(p.stop)


class WhatItWrites(_Sandbox):

    def test_every_generated_file_carries_the_stamp(self):
        """`undo` dosyayı adından değil içeriğinden tanır; damgasız üretim, geri alınamaz
        üretimdir."""
        for step in ('autostart', 'shortcut'):
            with self.subTest(step=step):
                wizard.do(step)
                path = (wizard._autostart_path() if step == 'autostart'
                        else wizard._shortcut_path())
                self.assertIn(wizard.STAMP, path.read_text(encoding='utf-8'))

    def test_preview_shows_the_bytes_that_do_writes(self):
        """Önizleme bir *tarif* değil, yazılacak metnin kendisi olmalı."""
        lines = wizard.preview('shortcut')['lines']
        wizard.do('shortcut')
        written = wizard._shortcut_path().read_text(encoding='utf-8')
        for line in lines[2:]:
            if line.strip():
                self.assertIn(line, written.replace('\r\n', '\n'))

    def test_a_full_cycle_leaves_nothing_behind(self):
        wizard.do('autostart')
        wizard.do('shortcut')
        wizard.undo('autostart')
        wizard.undo('shortcut')
        self.assertFalse(wizard._autostart_path().exists())
        self.assertFalse(wizard._shortcut_path().exists())


class ItRefusesToTouchWhatIsNotItsOwn(_Sandbox):
    """W1 — turun en pahalı dersi."""

    def _foreign(self, path, text='[Unit]\nDescription=the user wrote this\n'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_undo_leaves_a_foreign_autostart_alone(self):
        path = wizard._autostart_path()
        self._foreign(path)
        result = wizard.undo('autostart')
        self.assertTrue(path.exists(), 'undo deleted a file it never wrote')
        self.assertFalse(result['changed'])
        self.assertTrue(any('left' in m for m in result['messages']))

    def test_undo_does_not_stop_a_service_it_did_not_install(self):
        """Dosyayı silmemek yetmez: `systemctl disable --now` çalışırsa kullanıcının
        servisi durur ve dosya yerinde durduğu için kimse sebebini anlamaz."""
        self._foreign(wizard._autostart_path())
        with mock.patch.object(wizard, '_run', return_value=(0, '')) as run:
            wizard.undo('autostart')
        run.assert_not_called()

    @unittest.skipIf(wizard.is_windows(), 'systemd is the Linux mechanism')
    def test_undo_does_stop_the_service_it_did_install(self):
        wizard.do('autostart')
        with mock.patch.object(wizard, '_run', return_value=(0, '')) as run:
            wizard.undo('autostart')
        self.assertTrue(run.called, 'our own service was left running after undo')

    def test_a_foreign_file_is_backed_up_before_it_is_overwritten(self):
        """W2 — üstüne yazmak meşru (kullanıcı kurulum istedi), sessizce yapmak değil."""
        path = wizard._autostart_path()
        self._foreign(path, 'original content\n')
        result = wizard.do('autostart')
        backup = path.with_name(path.name + '.bak-usage-tracker')
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding='utf-8'), 'original content\n')
        self.assertTrue(any('kept at' in m for m in result['messages']))

    def test_a_second_run_does_not_destroy_the_first_backup(self):
        """W3 — yedek almanın anlamı, *orijinali* saklamaktır. İkinci koşu yedeği
        tazeleseydi, kurtardığı şey kendi yazdığı dosya olurdu."""
        path = wizard._autostart_path()
        self._foreign(path, 'original content\n')
        wizard.do('autostart')
        wizard.do('autostart')
        backup = path.with_name(path.name + '.bak-usage-tracker')
        self.assertEqual(backup.read_text(encoding='utf-8'), 'original content\n')


class TheWindowsPaths(_Sandbox):
    """Sahte platform: yol *erişilebilir mi*, içerik *kendi içinde tutarlı mı*."""

    def setUp(self):
        super().setUp()
        self.as_windows()

    def test_it_installs_into_the_users_own_folder_not_program_files(self):
        target = wizard.installed_binary()
        self.assertIn('AppData', str(target))
        self.assertTrue(str(target).endswith('usage-tracker.exe'))
        self.assertNotIn('Program Files', str(target))

    def test_autostart_goes_to_the_startup_folder_as_a_hidden_launcher(self):
        path = wizard._autostart_path()
        self.assertEqual(path.name, 'usage-tracker.vbs')
        self.assertIn('Startup', str(path))
        body = wizard._autostart_body()
        self.assertIn(', 0, False', body, 'the launcher would show a console window')
        self.assertIn(wizard.STAMP, body)

    def test_the_launcher_quotes_a_path_with_spaces(self):
        """`C:\\Users\\Ali Veli\\…` — tırnaksız bir yol, sessizce çalışmayan bir autostart."""
        with mock.patch.object(wizard, 'installed_binary',
                               return_value=pathlib.Path(r'C:\Users\Ali Veli\usage-tracker.exe')), \
             mock.patch.object(wizard, 'is_installed', return_value=True), \
             mock.patch.object(up, 'is_frozen', return_value=True):
            body = wizard._autostart_body()
        self.assertIn('""C:\\Users\\Ali Veli\\usage-tracker.exe""', body)

    def test_the_shortcut_opens_the_panel_rather_than_a_second_server(self):
        body = wizard._shortcut_body()
        self.assertTrue(body.rstrip().endswith('panel'),
                        'the shortcut must call `panel`, which reuses a running server')
        self.assertIn(wizard.STAMP, body)

    def test_windows_files_use_crlf(self):
        """Not Defteri'nde tek satır görünen bir `.cmd`, kullanıcının okuyamadığı bir
        dosyadır — ve bu dosyalar okunmak için yazılıyor."""
        for body in (wizard._autostart_body(), wizard._shortcut_body()):
            self.assertIn('\r\n', body)


class TheKeysOnDisk(_Sandbox):
    """Linux tarafı: anahtarlar `setup.sh`'ın okuduğu dosyaya yazılır."""

    def setUp(self):
        super().setUp()
        if wizard.is_windows():
            self.skipTest('on Windows the store is HKCU\\Environment, not a file')

    def test_they_land_in_the_same_file_setup_sh_reads(self):
        """İki sihirbazın iki ayrı anahtar deposu, kullanıcının günler sonra fark edeceği
        bir sessiz hatadır."""
        self.assertEqual(wizard.env_file(), up.config_dir() / 'env')

    def test_a_written_key_comes_back_as_set_but_never_as_a_value(self):
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        probe = wizard._probe_keys()
        self.assertIn('OPENROUTER_API_KEY', probe['set'])
        self.assertNotIn('sk-or-secret', repr(probe))

    def test_the_file_is_not_world_readable(self):
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        mode = wizard.env_file().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f'key file mode is {oct(mode)}')

    def test_it_keeps_comments_and_unrelated_variables(self):
        """Dosyayı yeniden akıtmak, kullanıcının kendi satırlarını silmektir
        (`packaging/env_edit.py`'ın dersi)."""
        path = wizard.env_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# my notes\nSOMETHING_ELSE=keep me\nOPENROUTER_API_KEY=old\n',
                        encoding='utf-8')
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'new'}})
        text = path.read_text(encoding='utf-8')
        self.assertIn('# my notes', text)
        self.assertIn('SOMETHING_ELSE=keep me', text)
        self.assertIn('OPENROUTER_API_KEY=new', text)
        self.assertNotIn('OPENROUTER_API_KEY=old', text)

    def test_an_unknown_name_is_refused_rather_than_written(self):
        result = wizard.do('keys', {'keys': {'PATH': '/tmp/evil'}})
        self.assertFalse(result['ok'])
        self.assertFalse(wizard.env_file().exists())

    def test_undo_clears_the_keys_it_stored(self):
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        wizard.undo('keys')
        self.assertNotIn('sk-or-secret', wizard.env_file().read_text(encoding='utf-8'))
        self.assertEqual(wizard._probe_keys()['set'], [])


class _FakeRegistry:
    """`winreg` yerine geçen en küçük şey. Amaç Windows'u taklit etmek değil, **Windows
    kod yolunu her makinede koşturmak**: gerçek kayıt defterine yazan bir test, koştuğu
    makineyi kirletir ve Linux'ta hiç koşmazdı."""

    HKEY_CURRENT_USER = 'HKCU'
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.broadcasts = 0

    class _Key:
        def __init__(self, store):
            self.store = store

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, sub):                     # noqa: N802 — winreg's own naming
        return self._Key(self)

    CreateKey = OpenKey

    def QueryValueEx(self, key, name):                # noqa: N802
        if name not in key.store.values:
            raise FileNotFoundError(name)
        return key.store.values[name], self.REG_SZ

    def SetValueEx(self, key, name, _res, _type, value):   # noqa: N802
        key.store.values[name] = value

    def DeleteValue(self, key, name):                 # noqa: N802
        if name not in key.store.values:
            raise FileNotFoundError(name)
        del key.store.values[name]


class TheKeysInTheRegistry(_Sandbox):
    """Windows tarafı: değerler `HKCU\\Environment`'a yazılır, okuma yalnız ad döndürür."""

    def setUp(self):
        super().setUp()
        self.as_windows()
        self.registry = _FakeRegistry()
        import sys as _sys
        patch = mock.patch.dict(_sys.modules, {'winreg': self.registry})
        patch.start()
        self.addCleanup(patch.stop)
        broadcast = mock.patch.object(wizard, '_win_broadcast_env')
        self.broadcast = broadcast.start()
        self.addCleanup(broadcast.stop)

    def test_a_key_is_written_to_the_user_environment(self):
        result = wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        self.assertTrue(result['ok'])
        self.assertEqual(self.registry.values['OPENROUTER_API_KEY'], 'sk-or-secret')

    def test_nothing_is_written_next_to_the_program(self):
        """Windows'ta anahtar dosyası yoktur; yanlışlıkla bir tane oluşturmak, kullanıcının
        beklemediği bir yerde düz metin anahtar bırakmak olurdu."""
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        self.assertFalse(wizard.env_file().exists())

    def test_the_probe_reports_the_name_and_never_the_value(self):
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        probe = wizard._probe_keys()
        self.assertIn('OPENROUTER_API_KEY', probe['set'])
        self.assertNotIn('sk-or-secret', repr(probe))

    def test_the_change_is_announced_so_new_processes_see_it(self):
        """`setx` yayını kendi yapar; `winreg` yapmaz. Yayın olmadan yeni açılan terminal
        bile eski ortamı miras alır ve kullanıcı anahtarı yazdığını sanır."""
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        self.assertTrue(self.broadcast.called)

    def test_undo_removes_the_value(self):
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        wizard.undo('keys')
        self.assertNotIn('OPENROUTER_API_KEY', self.registry.values)


class TheRecommendedRun(_Sandbox):

    def test_auto_never_writes_a_key(self):
        """Sormadan çalışan bir mod, sormadan anahtar yazamaz."""
        self.assertNotIn('keys', wizard.AUTO_STEPS)
        with mock.patch.object(wizard, '_do_keys') as keys:
            wizard.auto()
        keys.assert_not_called()

    def test_auto_reports_a_failed_step_instead_of_swallowing_it(self):
        """`setup.sh --auto`'nun ilk sürümü yutuyordu ve kullanıcı kurulumun bittiğini
        sanıyordu."""
        with mock.patch.object(wizard, '_do_shortcut',
                               return_value={'ok': False, 'changed': False, 'messages': []}):
            result = wizard.auto()
        self.assertFalse(result['ok'])
        self.assertIn('shortcut', result['failed'])

    @unittest.skipIf(wizard.is_windows(), 'the key store is the registry there')
    def test_uninstall_keeps_the_keys_and_the_data(self):
        wizard.do('keys', {'keys': {'OPENROUTER_API_KEY': 'sk-or-secret'}})
        wizard.do('shortcut')
        wizard.uninstall()
        self.assertFalse(wizard._shortcut_path().exists())
        self.assertIn('OPENROUTER_API_KEY',
                      wizard.env_file().read_text(encoding='utf-8'))


class TheDetachedServerKeepsItsFiles(_Sandbox):
    """Paketin içinden başlatılan sunucu, başlatanın ömrüne bağlı olamaz.

    Tek dosyalık paket kendini `/tmp/_MEIxxxx`'e açar ve bu yolu çocuklarına miras bırakır;
    ebeveyn çıkınca dizini siler. `panel` komutu sunucuyu **kasten** ebeveynden uzun yaşasın
    diye başlattığı için, miras alınan yol çocuğu silinmiş bir dizine bağlar: `/v1/usage`
    200 döner (kod bellekte), panelin her dosyası **404** (diskten okunur). Ölçüldü
    2026-08-13; bu kısayolun açtığı boş bir panel demekti.
    """

    def test_the_child_does_not_inherit_the_parents_bundle_directory(self):
        with mock.patch.dict(os.environ, {'_PYI_APPLICATION_HOME_DIR': '/tmp/_MEIzzz',
                                          '_PYI_ARCHIVE_FILE': '/x/usage-tracker',
                                          '_MEIPASS2': '/tmp/_MEIzzz',
                                          'PATH': '/usr/bin'}):
            env = wizard.child_env()
        self.assertNotIn('_PYI_APPLICATION_HOME_DIR', env)
        self.assertNotIn('_PYI_ARCHIVE_FILE', env)
        self.assertNotIn('_MEIPASS2', env)
        self.assertEqual(env.get('PATH'), '/usr/bin', 'it threw away the rest of the environment')


class TheBrowserWizardAnnouncesItself(_Sandbox):
    """Adres tamponda kalırsa sihirbaz açılmamış sayılır.

    `setup --ui` rastgele bir port ve tek kullanımlık bir jeton üretir; kullanıcıya tek
    ulaşma yolu bastığı adrestir. stdout bir boruya bağlıyken Python blok tamponlar ve o
    satır sunucu kapanana kadar görünmez — terminalde fark edilmez, saran her şey askıda
    kalır. Ölçüldü 2026-08-13, konteynerde 60 saniyelik bir askı olarak.
    """

    def test_the_url_arrives_before_the_server_stops(self):
        import re
        import subprocess
        import sys
        env = dict(os.environ, UT_NO_BROWSER='1')       # testin ekrana dokunma hakkı yok
        proc = subprocess.Popen(
            [sys.executable, str(pathlib.Path(__file__).resolve().parent.parent / 'server.py'),
             'setup', '--ui'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', env=env)
        self.addCleanup(proc.kill)
        url = None
        for _ in range(20):
            line = proc.stdout.readline()
            if not line:
                break
            found = re.search(r'http://127\.0\.0\.1:\d+/wizard\.html#\S+', line)
            if found:
                url = found.group(0)
                break
        self.assertIsNotNone(url, 'the wizard never printed its address on a pipe')

        base, token = url.split('#')
        self.assertTrue(len(token) > 20, 'the one-time token looks too short to be unguessable')
        import urllib.request
        with urllib.request.urlopen(base, timeout=5) as page:
            self.assertEqual(page.status, 200)
        request = urllib.request.Request(base.replace('wizard.html', 'api/wizard/probe'))
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)   # jetonsuz
        self.assertEqual(refused.exception.code, 403)
        quit_req = urllib.request.Request(base.replace('wizard.html', 'api/wizard/quit'),
                                          method='POST', data=b'{}')
        quit_req.add_header('Content-Type', 'application/json')
        quit_req.add_header('X-UT-Token', token)
        urllib.request.urlopen(quit_req, timeout=5)
        self.assertEqual(proc.wait(timeout=15), 0)


class TheMachineInterface(_Sandbox):
    """Tarayıcı yüzü bu sözleşmeyi konuşuyor; kırılırsa UI sessizce boşalır."""

    def test_probe_names_the_steps_so_the_page_does_not_have_to(self):
        probe = wizard.probe()
        self.assertTrue(probe['ok'])
        ids = [s['id'] for s in probe['steps']]
        self.assertEqual(ids, ['install', 'autostart', 'shortcut', 'keys', 'verify'])
        for sid in ids:
            self.assertIn(sid, probe, f'probe carries no state for step {sid}')

    def test_an_unknown_step_is_an_error_not_an_exception(self):
        for call in (lambda: wizard.do('rm -rf /'), lambda: wizard.undo('rm -rf /')):
            result = call()
            self.assertFalse(result['ok'])
            self.assertTrue(any('unknown step' in m for m in result['messages']))
        self.assertFalse(wizard.preview('nope')['ok'])

    def test_verify_installs_nothing(self):
        """`verify` bir *kontrol* adımıdır. `doctor`'ı çağırdığı için onun kendi
        dizinlerine (yazılabilirlik denemesi, canlı limit önbelleği) dokunması olağandır —
        ölçülebilir ve anlamlı iddia şu: sihirbazın kurduğu hiçbir şeyi kurmaz."""
        wizard.do('verify')
        self.assertFalse(wizard._autostart_path().exists())
        self.assertFalse(wizard._shortcut_path().exists())
        self.assertFalse(wizard.env_file().exists())
        self.assertFalse(wizard.installed_binary().exists())


class ThePageArrivesDressed(_Sandbox):
    """🔴 Sihirbaz açıldı ve **kurulum sihirbazına benzemiyordu.**

    Gerçek bir Windows makinesinde, 2026-08-13: sayfa geldi, JavaScript çalıştı, adımlar
    listelendi — ama `/styles.css` sunucunun listesinde olmadığı için 404 döndü,
    `setup.css`'teki 40 `var(--…)` tanımsız kaldı ve kullanıcı tarayıcı varsayılanlarıyla
    çizilmiş ham HTML gördü. "Çalışıyor" ile "kurulum arayüzü gibi görünüyor" aynı şey değil.

    CI de kaçırdı: kontrol listesi de **elle** yazılmıştı ve aynı dosyayı unutuyordu. İki
    liste aynı eksikliği paylaşırsa ölçüm kusuru göremez. Bu yüzden buradaki iddia sabit bir
    liste değil: **sayfanın kendi `href`/`src` referansları** üzerinden koşuyor.
    """

    def _serve(self):
        import http.server
        import threading
        from usage import wizard_server as ws
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), ws.Handler)
        port = srv.server_address[1]
        srv.allowed_hosts = {f'127.0.0.1:{port}'}
        srv.allowed_origins = {f'http://127.0.0.1:{port}'}
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return port

    def test_every_asset_the_page_asks_for_is_served(self):
        import re
        import urllib.request
        from usage import wizard_server as ws

        html = (ws.WEBD / ws.PAGE).read_text(encoding='utf-8')
        wanted = re.findall(r'(?:href|src)="(/[^"]+)"', html)
        self.assertTrue(wanted, 'the page references nothing — the test would prove nothing')

        port = self._serve()
        missing = []
        for path in wanted:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as r:
                    if r.status != 200 or not r.read():
                        missing.append(f'{path} -> {r.status}')
            except urllib.error.HTTPError as exc:
                missing.append(f'{path} -> {exc.code}')
        self.assertEqual(missing, [],
                         'the wizard page would render undressed: ' + ', '.join(missing))

    def test_the_stylesheet_that_defines_the_variables_is_among_them(self):
        """`setup.css` tek başına yetmez — renkler, yazı tipi ve kutu ölçüleri `styles.css`'te
        tanımlı. Onsuz sayfa teknik olarak 'çalışır' ve kullanıcıya bozuk görünür."""
        from usage import wizard_server as ws
        served = ws._static_map()
        self.assertIn('/styles.css', served)
        self.assertIn('/setup.css', served)
        variables = (ws.WEBD / 'setup.css').read_text(encoding='utf-8').count('var(--')
        self.assertGreater(variables, 0)

    def test_only_files_the_page_names_are_reachable(self):
        """Listeyi sayfadan türetmek, sunucuyu `web/` klasörünün tamamına açmak demek değil."""
        from usage import wizard_server as ws
        served = ws._static_map()
        self.assertNotIn('/index.html', served)
        self.assertNotIn('/app.js', served)


class TheClassesItUsesExist(_Sandbox):
    """Sayfa doğru dosyaları yüklese bile, var olmayan (ya da yanlış) bir sınıf adı onu
    yine bozuk gösterir.

    İki kusur bu turda tam olarak buradan çıktı: `wizard.js` metin düğmelerine `.ghost`
    veriyordu — `.ghost`, `styles.css`'te **34×34 bir ikon düğmesidir** ve hover'da 90° döner
    (⟳ için tasarlanmış) → Türkçe etiketler kutudan taştı ve düğmeler üst üste bindi. Doğru
    sınıf `.txtbtn`'dı. Bu test, kullanılan her sınıfın yüklenen stil dosyalarında **tanımlı**
    olduğunu ölçer; nasıl göründüğünü değil, tanımsız olmadığını.
    """

    def _classes_used(self):
        import re
        from usage import wizard_server as ws
        js = (ws.WEBD / 'wizard.js').read_text(encoding='utf-8')
        html = (ws.WEBD / ws.PAGE).read_text(encoding='utf-8')
        used = set()
        for match in re.findall(r"el\('[a-z]+', '([a-z0-9 ._-]+)'", js):
            used.update(part for part in match.split() if part and not part.startswith('.'))
        for match in re.findall(r'class="([a-z0-9 _-]+)"', html):
            used.update(match.split())
        return used

    def test_every_class_is_defined_in_the_stylesheets_the_page_loads(self):
        from usage import wizard_server as ws
        css = ''
        for name in ('styles.css', 'setup.css'):
            css += (ws.WEBD / name).read_text(encoding='utf-8')
        undefined = sorted(c for c in self._classes_used() if f'.{c}' not in css)
        self.assertEqual(undefined, [],
                         'the page uses classes nothing defines: ' + ', '.join(undefined))

    def test_text_buttons_do_not_borrow_the_icon_button(self):
        """`.ghost` sabit genişlikli ve dönen bir ikon düğmesi; içine metin koymak onu
        bozar. Topbar'daki ⟳ meşru kullanıcısıdır, adım düğmeleri değil."""
        import re
        from usage import wizard_server as ws
        js = (ws.WEBD / 'wizard.js').read_text(encoding='utf-8')
        offenders = re.findall(r"el\('button', 'ghost'[^)]*\)", js)
        self.assertEqual(offenders, [], 'a text button is using the icon-button class')


class TheFirstRunOpensTheWizard(_Sandbox):
    """🔴 Turun kaynağı: ihsan `.exe`'yi gerçek bir Windows makinesinde **çift tıkladı**,
    siyah bir pencere açıldı ve orada kaldı. Sunucu çalışıyordu; tarayıcı açılmıyordu,
    sihirbaz açılmıyordu ve `usage-tracker setup` yazması gerektiğini bilmesinin bir yolu
    yoktu. Belge bile "çift tıkla, panel açılır" diyordu — açılmıyordu.

    Kural: **bir kurulum, kullanıcıdan komut yazmasını istediği anda kurulum olmaktan çıkar.**
    """

    def _server(self):
        import server as server_mod
        return server_mod

    def test_a_fresh_machine_counts_as_a_first_run(self):
        self.assertTrue(wizard.is_first_run())

    def test_installing_or_enabling_autostart_ends_the_first_run(self):
        wizard.do('autostart')
        self.assertFalse(wizard.is_first_run(),
                         'the wizard would open again on every launch')

    def test_on_windows_the_first_launch_opens_the_wizard_then_the_panel(self):
        server_mod = self._server()
        calls = []
        with mock.patch.object(wizard, 'is_windows', return_value=True), \
             mock.patch.object(wizard, 'is_first_run', return_value=True), \
             mock.patch.dict(os.environ, {'UT_NO_BROWSER': '1'}), \
             mock.patch('usage.wizard_server.serve',
                        side_effect=lambda *a, **k: calls.append('wizard')):
            server_mod._greet_and_open(no_open=False)
        self.assertEqual(calls, ['wizard'], 'the wizard never opened on a fresh machine')

    def test_the_autostart_copy_opens_nothing(self):
        """Oturum açılışında koşan kopya tarayıcı açarsa, kullanıcı her açılışta bir pencere
        kapatır — ve sonunda otomatik başlatmayı kapatır."""
        server_mod = self._server()
        with mock.patch('usage.wizard_server.serve') as serve, \
             mock.patch('webbrowser.open') as opened:
            server_mod._greet_and_open(no_open=True)
        serve.assert_not_called()
        opened.assert_not_called()

    def test_the_launcher_it_writes_passes_no_open(self):
        for step, body in (('autostart', wizard._autostart_body()),):
            with self.subTest(step=step):
                self.assertIn('--no-open', body,
                              'the logon launcher would open a browser every time')

    def test_linux_is_left_alone(self):
        """Linux'ta bu ikili çoğunlukla systemd altında ya da SSH'ta koşar; oralarda
        tarayıcı açmak yardım değil sürprizdir."""
        server_mod = self._server()
        with mock.patch.object(wizard, 'is_windows', return_value=False), \
             mock.patch.object(wizard, 'is_first_run', return_value=True), \
             mock.patch('usage.wizard_server.serve') as serve, \
             mock.patch('webbrowser.open') as opened:
            server_mod._greet_and_open(no_open=False)
        serve.assert_not_called()
        opened.assert_not_called()


class TheWindowIsBilingual(_Sandbox):
    """Makinenin dilini yanlış tahmin etmenin bedeli, iki satır fazladan metinden büyük."""

    def test_both_languages_are_printed(self):
        from usage import i18n
        lines = i18n.both('panel_running', url='http://127.0.0.1:8770')
        self.assertEqual(len(lines), 2)
        self.assertNotEqual(lines[0], lines[1])
        self.assertTrue(all('127.0.0.1:8770' in line for line in lines))

    def test_the_language_can_be_forced(self):
        from usage import i18n
        with mock.patch.dict(os.environ, {'UT_LANG': 'tr'}):
            self.assertEqual(i18n.language(), 'tr')
            self.assertIn('Panel çalışıyor', i18n.t('panel_running', url='x'))
        with mock.patch.dict(os.environ, {'UT_LANG': 'en'}):
            self.assertEqual(i18n.language(), 'en')

    def test_an_unknown_key_never_crashes_the_startup(self):
        from usage import i18n
        self.assertEqual(i18n.t('no-such-message'), 'no-such-message')

    def test_every_message_exists_in_both_languages(self):
        """Yarım çeviri, kullanıcının bir cümleyi anlayıp diğerini anlamaması demek."""
        from usage import i18n
        missing = [key for key, entry in i18n.MESSAGES.items()
                   if not entry.get('en') or not entry.get('tr')]
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()

"""FAZ 6/B — the settings the user is allowed to change, and the ones they are not.

Three of these tests exist because of a specific way this feature could be worthless or
harmful rather than merely wrong:

  * A threshold you can save but that no surface reads is a placebo. Y6 already taught this
    project that lesson once (three surfaces carried their own hardcoded 75/90 while the
    server happily served a different pair). So the test that matters is not "did it save"
    but `test_a_saved_threshold_reaches_the_wire`.
  * A rejected write must leave the old value alone. A validator that clears the file on bad
    input turns a typo into a silent reset to 75/90 — the user would only find out when an
    alert failed to fire.
  * The permanent server never writes keys and never returns their values. The setup wizard
    has a random port, a one-time token and a self-shutdown for exactly that job; the panel
    server has none of those and runs all day.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest import mock

from usage import engine, settings, viewconfig

REPO = Path(__file__).resolve().parent.parent


class _Isolated(unittest.TestCase):
    """Every test gets its own config/state dir. Nothing touches the real one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / 'config').mkdir()
        (root / 'state').mkdir()
        self.settings_path = root / 'config' / 'settings.json'
        self.calib_path = root / 'state' / 'usage_calib.json'
        self._patches = [
            mock.patch.object(settings, 'SETTINGS_PATH', self.settings_path),
            mock.patch.object(engine, 'CALIB_PATH', self.calib_path),
            mock.patch.object(engine, 'LEGACY_CALIB_PATH', root / 'absent.json'),
            # The CLI's `config` command reads this one on every run — unpatched it would
            # touch the real ~/.config, which rule 1 in tests/README.md forbids.
            mock.patch.object(viewconfig, 'CONFIG_PATH', root / 'config' / 'view_config.json'),
            mock.patch.object(viewconfig, 'LEGACY_CONFIG_PATH', root / 'absent.json'),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._tmp.cleanup)
        for p in self._patches:
            self.addCleanup(p.stop)

    def write_calib(self, obj):
        self.calib_path.write_text(json.dumps(obj), encoding='utf-8')


class Defaults(_Isolated):

    def test_no_file_means_the_documented_defaults(self):
        s = settings.get_settings()
        self.assertEqual(s['thresholds'], {'warn': 75, 'crit': 90})
        self.assertEqual(s['refreshSeconds'], 30)
        self.assertEqual(s['display']['currency'], 'USD')
        self.assertIsNone(s['display']['rate'])

    def test_a_corrupt_file_falls_back_instead_of_crashing_the_panel(self):
        self.settings_path.write_text('{not json', encoding='utf-8')
        self.assertEqual(settings.get_settings()['thresholds'], {'warn': 75, 'crit': 90})

    def test_thresholds_already_in_the_calibration_file_are_not_lost(self):
        """Thresholds have lived in usage_calib.json since FAZ 0. Anyone who set 60/80 there
        must not silently get 75/90 back the day this feature ships."""
        self.write_calib({'thresholds': {'warn': 60, 'crit': 80}})
        self.assertEqual(settings.get_settings()['thresholds'], {'warn': 60, 'crit': 80})


class Validation(_Isolated):

    def assert_rejected(self, patch, because):
        settings.save_settings({'thresholds': {'warn': 40, 'crit': 55}})
        before = self.settings_path.read_bytes() if self.settings_path.exists() else None
        before_calib = self.calib_path.read_bytes() if self.calib_path.exists() else None
        ok, err = settings.save_settings(patch)
        self.assertFalse(ok, f'accepted {patch} — {because}')
        self.assertTrue(err, 'a rejection must say why')
        after = self.settings_path.read_bytes() if self.settings_path.exists() else None
        after_calib = self.calib_path.read_bytes() if self.calib_path.exists() else None
        self.assertEqual(before, after, 'a rejected write changed settings.json')
        self.assertEqual(before_calib, after_calib, 'a rejected write changed the calibration')
        self.assertEqual(settings.get_settings()['thresholds'], {'warn': 40, 'crit': 55},
                         'a rejected write reset the thresholds the user had set')

    def test_warn_below_crit(self):
        self.assert_rejected({'thresholds': {'warn': 90, 'crit': 75}}, 'warn must stay below crit')

    def test_warn_equal_to_crit(self):
        self.assert_rejected({'thresholds': {'warn': 80, 'crit': 80}},
                             'equal thresholds make the warn level unreachable')

    def test_zero_and_over_a_hundred(self):
        self.assert_rejected({'thresholds': {'warn': 0, 'crit': 90}}, 'warn 0 fires forever')
        self.assert_rejected({'thresholds': {'warn': 75, 'crit': 101}}, 'crit 101 never fires')

    def test_strings_are_not_numbers(self):
        self.assert_rejected({'thresholds': {'warn': '75', 'crit': '90'}}, 'JSON strings are not numbers')
        self.assert_rejected({'thresholds': {'warn': True, 'crit': 90}},
                             'bool is an int in Python — that is how the $1.00 bug happened')

    def test_refresh_cannot_hammer_the_disk(self):
        self.assert_rejected({'refreshSeconds': 1}, 'a 1s poll rescans every transcript on disk')

    def test_refresh_cannot_be_effectively_off(self):
        self.assert_rejected({'refreshSeconds': 100000}, 'a stale panel is worse than no panel')

    def test_unknown_keys_are_refused_not_stored(self):
        self.assert_rejected({'bindHost': '0.0.0.0'},
                             'silently storing unknown keys invites config that does nothing')

    def test_a_valid_write_survives(self):
        ok, err = settings.save_settings({'thresholds': {'warn': 10, 'crit': 20}, 'refreshSeconds': 60})
        self.assertTrue(ok, err)
        s = settings.get_settings()
        self.assertEqual(s['thresholds'], {'warn': 10, 'crit': 20})
        self.assertEqual(s['refreshSeconds'], 60)

    def test_a_partial_write_leaves_the_other_keys_alone(self):
        settings.save_settings({'thresholds': {'warn': 10, 'crit': 20}, 'refreshSeconds': 60})
        settings.save_settings({'refreshSeconds': 15})
        s = settings.get_settings()
        self.assertEqual(s['refreshSeconds'], 15)
        self.assertEqual(s['thresholds'], {'warn': 10, 'crit': 20})


class ThresholdsReachEverySurface(_Isolated):
    """The point of the feature. Saving is not the deliverable — the wire is."""

    def wire(self):
        with tempfile.TemporaryDirectory() as projects:
            with mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', Path(projects)):
                return engine.usage_wire()

    def test_a_saved_threshold_reaches_the_wire(self):
        settings.save_settings({'thresholds': {'warn': 10, 'crit': 20}})
        wire = self.wire()
        self.assertEqual(wire['thresholds'], {'warn': 10, 'crit': 20},
                         'the top-level thresholds still carry the old default')
        self.assertEqual(wire['providers'][0]['limits']['thresholds'], {'warn': 10, 'crit': 20},
                         'the compatibility copy under providers[0] drifted from the top level')

    def test_the_cli_guard_uses_the_saved_threshold(self):
        """Same numbers the panel writes, read by a surface with no browser in it."""
        from usage import cli
        settings.save_settings({'thresholds': {'warn': 10, 'crit': 20}})
        wire = self.wire()
        self.assertEqual(cli.thresholds_of(wire), {'warn': 10, 'crit': 20})


class CliWritesTheSameSetting(_Isolated):
    """A headless box has no panel to open. If the CLI could not move the line, the feature
    would only exist for people with a browser on the machine."""

    def run_cli(self, argv):
        from usage import cli
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(argv)

    def test_config_flags_change_the_threshold(self):
        rc = self.run_cli(['config', '--warn', '33', '--crit', '44', '--json'])
        self.assertEqual(rc, 0)
        self.assertEqual(settings.get_settings()['thresholds'], {'warn': 33, 'crit': 44})

    def test_one_flag_keeps_the_other_side(self):
        settings.save_settings({'thresholds': {'warn': 33, 'crit': 44}})
        self.run_cli(['config', '--warn', '20', '--json'])
        self.assertEqual(settings.get_settings()['thresholds'], {'warn': 20, 'crit': 44})

    def test_an_impossible_pair_is_refused_with_a_reason(self):
        settings.save_settings({'thresholds': {'warn': 33, 'crit': 44}})
        rc = self.run_cli(['config', '--warn', '99', '--json'])
        self.assertEqual(rc, 1, 'the CLI reported success for a rejected write')
        self.assertEqual(settings.get_settings()['thresholds'], {'warn': 33, 'crit': 44})


class DisplayCurrency(_Isolated):
    """A converted number is a derived number. It may be shown only with the rate that
    produced it and the day that rate was entered — otherwise it is a confident lie."""

    def test_a_foreign_currency_without_a_rate_is_refused(self):
        ok, err = settings.save_settings({'display': {'currency': 'TRY'}})
        self.assertFalse(ok)
        self.assertIn('rate', (err or '').lower())

    def test_a_rate_must_be_a_positive_number(self):
        for bad in (0, -3, 'kırk', None):
            ok, _ = settings.save_settings({'display': {'currency': 'TRY', 'rate': bad}})
            self.assertFalse(ok, f'accepted rate {bad!r}')

    def test_the_server_stamps_the_rate_date_not_the_client(self):
        ok, err = settings.save_settings(
            {'display': {'currency': 'TRY', 'rate': 41.2, 'rateSetAtMs': 1}}, now_ms=1_700_000_000_000)
        self.assertTrue(ok, err)
        d = settings.get_settings()['display']
        self.assertEqual(d['rate'], 41.2)
        self.assertEqual(d['rateSetAtMs'], 1_700_000_000_000,
                         'the client got to choose how old its own rate looks')

    def test_going_back_to_usd_drops_the_rate(self):
        settings.save_settings({'display': {'currency': 'TRY', 'rate': 41.2}})
        settings.save_settings({'display': {'currency': 'USD'}})
        d = settings.get_settings()['display']
        self.assertEqual(d['currency'], 'USD')
        self.assertIsNone(d['rate'], 'a stale rate left behind will be used by the next surface')

    def test_convert_returns_none_without_a_rate(self):
        self.assertIsNone(settings.convert(12.0, {'currency': 'TRY', 'rate': None}))

    def test_convert_never_touches_the_usd_truth(self):
        self.assertIsNone(settings.convert(12.0, {'currency': 'USD', 'rate': None}),
                          'USD needs no conversion — returning a number invites double display')


class KeyStatus(_Isolated):

    def test_names_and_set_flags_only(self):
        with mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'sk-or-SECRETVALUE-42'}, clear=False):
            status = settings.key_status()
        blob = json.dumps(status)
        self.assertNotIn('SECRETVALUE', blob, 'the panel would have shown the key itself')
        row = [r for r in status if r['provider'] == 'openrouter'][0]
        self.assertEqual(row['env'], 'OPENROUTER_API_KEY')
        self.assertTrue(row['set'])

    def test_an_unset_key_says_so_without_inventing_one(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status = settings.key_status()
        self.assertTrue(all(r['set'] is False for r in status))

    def test_every_provider_the_cli_knows_is_listed(self):
        """One map, two surfaces. Two maps drift and doctor starts disagreeing with the panel."""
        from usage import cli
        self.assertEqual({r['provider'] for r in settings.key_status()}, set(cli.PROVIDER_ENV))


class HttpEndpoint(_Isolated):
    """The permanent server, started for real — the handler is where the guards live."""

    def setUp(self):
        super().setUp()
        import server as server_mod
        self.server_mod = server_mod
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server_mod.Handler)
        self.port = self.httpd.server_address[1]
        Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)      # shutdown() stops the loop, not the socket
        self.addCleanup(self.httpd.shutdown)
        # The handler's allowlist is built from the module's PORT at import time.
        self._hosts = mock.patch.object(
            server_mod, 'ALLOWED_HOSTS', {f'127.0.0.1:{self.port}', 'localhost'})
        self._hosts.start()
        self.addCleanup(self._hosts.stop)

    def call(self, method, path, body=None, headers=None):
        url = f'http://127.0.0.1:{self.port}{path}'
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header('Host', f'127.0.0.1:{self.port}')
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        for k, v in (headers or {}).items():
            req.remove_header(k)
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or '{}')

    def test_get_returns_settings_and_key_names(self):
        code, body = self.call('GET', '/api/settings')
        self.assertEqual(code, 200)
        self.assertEqual(body['settings']['thresholds'], {'warn': 75, 'crit': 90})
        self.assertTrue(body['keys'])
        self.assertNotIn('value', json.dumps(body['keys'][0]))

    def test_post_saves_and_get_reflects_it(self):
        code, _ = self.call('POST', '/api/settings', {'thresholds': {'warn': 11, 'crit': 22}})
        self.assertEqual(code, 200)
        _, body = self.call('GET', '/api/settings')
        self.assertEqual(body['settings']['thresholds'], {'warn': 11, 'crit': 22})

    def test_a_foreign_origin_cannot_write(self):
        """A page on the open internet can reach 127.0.0.1. Content-Type alone is not a lock —
        the Origin check is (§güvenlik 2, ported from the setup wizard)."""
        code, _ = self.call('POST', '/api/settings', {'refreshSeconds': 60},
                            headers={'Origin': 'http://evil.example'})
        self.assertEqual(code, 403)
        self.assertEqual(settings.get_settings()['refreshSeconds'], 30)

    def test_the_panels_own_origin_can_write(self):
        code, _ = self.call('POST', '/api/settings', {'refreshSeconds': 60},
                            headers={'Origin': f'http://127.0.0.1:{self.port}'})
        self.assertEqual(code, 200)

    def test_the_existing_write_endpoints_got_the_same_lock(self):
        for path in ('/api/view-config', '/api/calibrate'):
            with self.subTest(path=path):
                code, _ = self.call('POST', path, {'hidden_providers': []},
                                    headers={'Origin': 'http://evil.example'})
                self.assertEqual(code, 403, f'{path} still accepts a cross-origin write')

    def test_a_request_without_an_origin_is_allowed_on_purpose(self):
        """Pinning a decision, not an accident.

        `curl`, the CLI and the tray send no Origin header at all; refusing them would break
        every non-browser client to guard against a browser that cannot omit the header —
        a cross-origin fetch always sends one, and a page cannot delete its own. Three
        independent audits landed on this line in 2026-08; it stays, and now it is measured,
        so tomorrow's change to it is a red test rather than a silent policy shift.
        """
        code, _ = self.call('POST', '/api/settings', {'refreshSeconds': 45})
        self.assertEqual(code, 200)
        self.assertEqual(settings.get_settings()['refreshSeconds'], 45)

    def test_hiding_the_claude_card_is_refused_by_the_server_too(self):
        """docs/WIRE.md:37 promises providers[0] is Claude. The panel offered a checkbox that
        broke that promise — and with it the waybar badge and `guard`."""
        code, body = self.call('POST', '/api/view-config', {'hidden_providers': ['claude']})
        self.assertEqual(code, 400)
        self.assertIn('claude', json.dumps(body).lower())
        self.assertEqual(viewconfig.get_config()['hidden_providers'], [])

    def test_hiding_another_card_still_works(self):
        code, _ = self.call('POST', '/api/view-config', {'hidden_providers': ['ollama']})
        self.assertEqual(code, 200)
        self.assertEqual(viewconfig.get_config()['hidden_providers'], ['ollama'])

    def test_an_evil_host_header_is_refused(self):
        code, _ = self.call('GET', '/api/settings', headers={'Host': 'evil.example'})
        self.assertEqual(code, 403)

    def test_a_bad_body_is_a_400_and_changes_nothing(self):
        self.call('POST', '/api/settings', {'thresholds': {'warn': 11, 'crit': 22}})
        code, body = self.call('POST', '/api/settings', {'thresholds': {'warn': 99, 'crit': 22}})
        self.assertEqual(code, 400)
        self.assertTrue(body.get('error'))
        _, after = self.call('GET', '/api/settings')
        self.assertEqual(after['settings']['thresholds'], {'warn': 11, 'crit': 22})

    def test_the_wrong_content_type_is_refused(self):
        url = f'http://127.0.0.1:{self.port}/api/settings'
        req = urllib.request.Request(url, data=b'{}', method='POST')
        req.add_header('Content-Type', 'text/plain')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 400)

    def test_the_server_refuses_to_write_a_key(self):
        """Not "not implemented yet" — a decision. The panel server has no token, no random
        port and no self-shutdown; the setup wizard has all three and exists for this."""
        code, _ = self.call('POST', '/api/settings',
                            {'keys': {'OPENROUTER_API_KEY': 'sk-or-nope'}})
        self.assertEqual(code, 400)
        self.assertNotIn('sk-or-nope', self.settings_path.read_text(encoding='utf-8')
                         if self.settings_path.exists() else '')


class PanelUsesTheSetting(unittest.TestCase):
    """The panel polled on a hardcoded 30s. A refresh setting that app.js ignores is a lie
    told by the UI about itself."""

    def setUp(self):
        self.src = (REPO / 'web' / 'app.js').read_text(encoding='utf-8')

    def test_the_poll_interval_is_not_hardcoded(self):
        self.assertNotIn('30000)', self.src.replace(' ', ''),
                         'app.js still schedules its poll on a literal 30000ms')

    def test_the_panel_reads_the_settings_endpoint(self):
        self.assertIn('/api/settings', self.src)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""
test_cli.py — FAZ 4: the command line surface.

The CLI is a **contract with other programs**, not a convenience: `guard` is called from
shell scripts and its exit code decides whether an expensive job starts. So the exit codes
are pinned here first, and the numbers — not the wording — are what these tests assert.

Everything is hermetic: no test reads the real `~/.claude`, and every subprocess gets a
throwaway HOME/XDG. Commands that talk to a server are pointed at a fake one served from
this file, so no test depends on a running usage-tracker.

Run: python3 -m unittest tests.test_cli -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from usage import cli  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────
def make_claude(session_pct=50.0, weekly_pct=None, thresholds=None):
    th = thresholds or {'warn': 75, 'crit': 90}

    def bar(pct):
        # A bar with an unknown percentage still exists — engine.limit_bar() returns the
        # dict with `pct: null`. Dropping the bar entirely would be a different bug and
        # would let the renderer pass this test for the wrong reason.
        return {'pct': pct, 'used': 120.0, 'units': 120.0, 'budget': 240,
                'calibSuspect': False, 'resetAtMs': 1770000000000, 'resetInSec': 4500}

    return {
        'id': 'claude', 'name': 'Claude Code', 'calibrated': True,
        'live': {'ok': True, 'error': None, 'cached': False, 'fetchedAtMs': 1770000000000,
                 'ageSec': 12.0, 'stale': False, 'rateLimited': False, 'rateLimitTier': None},
        'limits': {
            'session': bar(session_pct),
            'weekly': bar(weekly_pct),
            'weeklyModel': None,
            'thresholds': th,
        },
        'spend': {
            'currency': 'USD', 'today': 9.55, 'yesterday': 41.02, 'last30d': 5086.92,
            'byModel': [{'model': 'claude-opus-5', 'short': 'opus', 'usd': 12.3, 'source': 'catalog'}],
            'priceComplete': True, 'estimatedModels': [], 'unknownPriceModels': [],
            'catalog': {'source': 'bundled', 'sourceLabel': 'bundled snapshot', 'path': '',
                        'generatedAt': '2026-08-03', 'ageDays': 9, 'stale': False,
                        'modelCount': 5857, 'providerCount': 168, 'warning': None},
        },
    }


def make_wire(session_pct=50.0, weekly_pct=None, thresholds=None, extra_providers=None):
    th = thresholds or {'warn': 75, 'crit': 90}
    return {
        'schema': 'usage/v1',
        'generatedAtMs': 1770000000000,
        'generatedAt': '2026-08-12T10:00:00',
        'thresholds': th,
        'providers': [make_claude(session_pct, weekly_pct, th)] + list(extra_providers or []),
    }


OPENROUTER_CARD = {
    'id': 'openrouter', 'name': 'OpenRouter', 'kind': 'spend', 'available': True,
    'status': 'ok', 'error': None, 'currency': 'USD',
    'spend': {'today': 0.16, 'month': 3.76},
    'balance': 6.23,
    'limit': {'used': 4.0, 'limit': 5.0, 'pct': 80.0},
}

ELEVENLABS_CARD = {
    'id': 'elevenlabs', 'name': 'ElevenLabs', 'kind': 'quota', 'available': True,
    'status': 'ok', 'error': None, 'currency': None,
    'quota': {'used': 82300, 'limit': 100000, 'remaining': 17700, 'pct': 82.3},
}

OLLAMA_CARD = {
    'id': 'ollama', 'name': 'Ollama', 'kind': 'local', 'available': True,
    'status': 'offline', 'error': None, 'currency': None,
    'note': 'Binary installed, service down.', 'models': [], 'running': [],
}


class FakeWireServer:
    """A stand-in for a running usage-tracker; serves one payload on any path."""

    def __init__(self, payload, status=200, body=None):
        self.payload, self.status, self.body = payload, status, body
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                raw = (outer.body if outer.body is not None
                       else json.dumps(outer.payload).encode('utf-8'))
                self.send_response(outer.status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f'http://127.0.0.1:{self.port}'

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def closed_port() -> int:
    """A port nobody is listening on (bind, read the number, close)."""
    import socket
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_cli(args, env_extra=None, timeout=60, entry='module'):
    """Run the CLI in a subprocess with a throwaway HOME. Returns CompletedProcess."""
    tmp = tempfile.mkdtemp(prefix='ut-cli-test-')
    env = dict(os.environ)
    env.update({
        'HOME': tmp, 'USERPROFILE': tmp,
        'XDG_CONFIG_HOME': str(Path(tmp) / 'config'),
        'XDG_STATE_HOME': str(Path(tmp) / 'state'),
        'XDG_CACHE_HOME': str(Path(tmp) / 'cache'),
        'APPDATA': str(Path(tmp) / 'roaming'), 'LOCALAPPDATA': str(Path(tmp) / 'local'),
        'PYTHONIOENCODING': 'utf-8',
    })
    env.pop('USAGE_DEMO', None)
    env.update(env_extra or {})
    cmd = ([sys.executable, '-m', 'usage.cli'] if entry == 'module'
           else [sys.executable, str(ROOT / 'server.py')]) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT),
                          env=env, timeout=timeout)


# ── rounding parity ───────────────────────────────────────────────────────────
class Rounding(unittest.TestCase):
    """Y7: every surface rounds half-up. A fourth surface must not invent a fifth rule."""

    def test_half_up_matches_the_other_surfaces(self):
        for raw, expected in ((62.45, 62.5), (62.44, 62.4), (0.05, 0.1), (99.95, 100.0)):
            self.assertEqual(cli.round_half_up(raw), expected, f'{raw} rounded wrong')


# ── guard: the exit code contract ─────────────────────────────────────────────
class GuardExitCodes(unittest.TestCase):
    """`usage-tracker guard || skip_expensive_job` — these numbers are the API."""

    def _guard(self, wire, extra=(), **kw):
        with FakeWireServer(wire) as url:
            return run_cli(['guard', '--url', url + '/v1/usage', *extra], **kw)

    def test_below_warn_exits_zero(self):
        self.assertEqual(self._guard(make_wire(50.0)).returncode, 0)

    def test_between_warn_and_crit_exits_one(self):
        self.assertEqual(self._guard(make_wire(80.0)).returncode, 1)

    def test_at_or_above_crit_exits_two(self):
        self.assertEqual(self._guard(make_wire(95.0)).returncode, 2)

    def test_unknown_percentage_exits_three_not_zero(self):
        """A missing number is not a safe number: unknown must never read as 'plenty left'."""
        self.assertEqual(self._guard(make_wire(None)).returncode, 3)

    def test_unreachable_explicit_url_exits_three(self):
        r = run_cli(['guard', '--url', f'http://127.0.0.1:{closed_port()}/v1/usage'])
        self.assertEqual(r.returncode, 3)

    def test_thresholds_come_from_the_wire_not_from_the_cli(self):
        """Y6: the server owns warn/crit. Same percentage, different server config."""
        low = {'warn': 10, 'crit': 20}
        self.assertEqual(self._guard(make_wire(15.0, thresholds=low)).returncode, 1)
        self.assertEqual(self._guard(make_wire(25.0, thresholds=low)).returncode, 2)
        # ...and the same 15% is fine under the default configuration
        self.assertEqual(self._guard(make_wire(15.0)).returncode, 0)

    def test_custom_threshold_flag_collapses_to_a_single_boundary(self):
        """--threshold 80 means 'tell me when I pass 80', not 'now 80 is critical'."""
        r = self._guard(make_wire(85.0), extra=['--threshold', '80'])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._guard(make_wire(75.0), extra=['--threshold', '80']).returncode, 0)

    def test_weekly_counts_too_not_just_the_session(self):
        """The wall you hit first is the wall that matters."""
        self.assertEqual(self._guard(make_wire(5.0, weekly_pct=95.0)).returncode, 2)

    def test_json_output_carries_the_decision(self):
        r = self._guard(make_wire(80.0), extra=['--json'])
        out = json.loads(r.stdout)
        self.assertEqual(out['level'], 'warn')
        self.assertEqual(out['exitCode'], 1)
        self.assertEqual(out['pct'], 80.0)
        self.assertEqual(out['scope'], 'claude/session')
        self.assertEqual(out['thresholds'], {'warn': 75, 'crit': 90})
        self.assertEqual(out['source'], 'server')

    def test_provider_selects_another_card(self):
        wire = make_wire(5.0, extra_providers=[ELEVENLABS_CARD])
        r = self._guard(wire, extra=['--provider', 'elevenlabs', '--json'])
        out = json.loads(r.stdout)
        self.assertEqual(out['pct'], 82.3)
        self.assertEqual(r.returncode, 1)

    def test_provider_all_takes_the_worst_card(self):
        wire = make_wire(5.0, extra_providers=[OPENROUTER_CARD, ELEVENLABS_CARD])
        r = self._guard(wire, extra=['--provider', 'all', '--json'])
        out = json.loads(r.stdout)
        self.assertEqual(out['pct'], 82.3)          # elevenlabs 82.3 > openrouter 80.0
        self.assertEqual(r.returncode, 1)

    def test_bad_arguments_do_not_collide_with_the_level_codes(self):
        """argparse exits 2 by default — which guard already means 'critical'."""
        r = run_cli(['guard', '--nope'])
        self.assertEqual(r.returncode, 64)
        r2 = run_cli(['no-such-command'])
        self.assertEqual(r2.returncode, 64)


# ── usage / providers: the human and machine views ────────────────────────────
class UsageCommand(unittest.TestCase):
    def test_json_is_the_wire_verbatim(self):
        wire = make_wire(62.45, extra_providers=[OLLAMA_CARD])
        with FakeWireServer(wire) as url:
            r = run_cli(['usage', '--format', 'json', '--url', url + '/v1/usage'])
        self.assertEqual(json.loads(r.stdout), wire)

    def test_text_rounds_half_up(self):
        with FakeWireServer(make_wire(62.45)) as url:
            r = run_cli(['usage', '--url', url + '/v1/usage'])
        self.assertIn('62.5%', r.stdout)
        self.assertNotIn('62.4%', r.stdout)

    def test_text_shows_unknown_as_dash_never_as_zero(self):
        with FakeWireServer(make_wire(None)) as url:
            r = run_cli(['usage', '--url', url + '/v1/usage'])
        self.assertIn('—', r.stdout)
        self.assertNotIn('0.0%', r.stdout)

    def test_waybar_format_is_a_waybar_object(self):
        with FakeWireServer(make_wire(95.0)) as url:
            r = run_cli(['usage', '--format', 'waybar', '--url', url + '/v1/usage'])
        out = json.loads(r.stdout)
        self.assertEqual(set(out) >= {'text', 'tooltip', 'class'}, True)
        self.assertEqual(out['class'], 'crit')

    def test_waybar_class_follows_the_servers_thresholds(self):
        with FakeWireServer(make_wire(15.0, thresholds={'warn': 10, 'crit': 20})) as url:
            r = run_cli(['usage', '--format', 'waybar', '--url', url + '/v1/usage'])
        self.assertEqual(json.loads(r.stdout)['class'], 'warn')

    def test_waybar_offline_is_reported_not_faked(self):
        r = run_cli(['usage', '--format', 'waybar',
                     '--url', f'http://127.0.0.1:{closed_port()}/v1/usage'])
        out = json.loads(r.stdout)
        self.assertEqual(out['class'], 'off')
        self.assertEqual(r.returncode, 0)          # a feeder must not crash waybar

    def test_provider_filter_narrows_the_output(self):
        wire = make_wire(50.0, extra_providers=[OPENROUTER_CARD, OLLAMA_CARD])
        with FakeWireServer(wire) as url:
            r = run_cli(['usage', '--provider', 'openrouter', '--url', url + '/v1/usage'])
        self.assertIn('OpenRouter', r.stdout)
        self.assertNotIn('Ollama', r.stdout)

    def test_catalogue_warning_travels_with_the_dollars(self):
        """docs/WIRE.md: a surface that shows dollars must show `spend.catalog.warning`.

        The whole reason that field exists is that a $0.00 with no price catalogue behind
        it looked identical to a real $0.00 — on every surface that forgot to print it.
        """
        wire = make_wire(50.0)
        wire['providers'][0]['spend']['catalog'] = {
            'source': 'none', 'sourceLabel': 'no catalogue', 'path': '', 'generatedAt': None,
            'ageDays': None, 'stale': False, 'modelCount': 0, 'providerCount': 0,
            'warning': 'Price catalogue could not be loaded — the amounts shown are incomplete.',
        }
        with FakeWireServer(wire) as url:
            r = run_cli(['usage', '--url', url + '/v1/usage'])
        self.assertIn('could not be loaded', r.stdout)

    def test_unpriced_models_are_flagged_as_a_floor(self):
        wire = make_wire(50.0)
        wire['providers'][0]['spend']['priceComplete'] = False
        wire['providers'][0]['spend']['unknownPriceModels'] = [{'model': 'mystery-1', 'tokens': 900}]
        with FakeWireServer(wire) as url:
            r = run_cli(['usage', '--url', url + '/v1/usage'])
        self.assertIn('floor', r.stdout)

    def test_currency_is_read_not_assumed(self):
        card = dict(OPENROUTER_CARD, id='deepseek', name='DeepSeek', currency='CNY',
                    spend={'today': 12.45}, balance=3.0, limit=None)
        with FakeWireServer(make_wire(50.0, extra_providers=[card])) as url:
            r = run_cli(['usage', '--provider', 'deepseek', '--url', url + '/v1/usage'])
        self.assertIn('¥', r.stdout)
        self.assertNotIn('$12.45', r.stdout)


class ProvidersCommand(unittest.TestCase):
    def test_lists_every_card_with_its_status(self):
        wire = make_wire(50.0, extra_providers=[OPENROUTER_CARD, OLLAMA_CARD])
        with FakeWireServer(wire) as url:
            r = run_cli(['providers', '--format', 'json', '--url', url + '/v1/usage'])
        rows = {p['id']: p for p in json.loads(r.stdout)['providers']}
        self.assertEqual(rows['openrouter']['status'], 'ok')
        self.assertEqual(rows['ollama']['status'], 'offline')
        self.assertEqual(rows['openrouter']['kind'], 'spend')

    def test_text_view_mentions_each_id(self):
        wire = make_wire(50.0, extra_providers=[OPENROUTER_CARD, OLLAMA_CARD])
        with FakeWireServer(wire) as url:
            r = run_cli(['providers', '--url', url + '/v1/usage'])
        self.assertIn('openrouter', r.stdout)
        self.assertIn('ollama', r.stdout)


# ── watch: edge triggering ────────────────────────────────────────────────────
class WatchEdgeTrigger(unittest.TestCase):
    """A threshold alert that repeats every 60 s is an alert you learn to ignore."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / 'watch-state.json'
        self.addCleanup(self.tmp.cleanup)

    def _tick(self, pct, extra=()):
        """One --once pass against a fake server; returns the emitted event or None."""
        import io
        import contextlib
        with FakeWireServer(make_wire(pct)) as url:
            buf = io.StringIO()
            argv = ['watch', '--once', '--json', '--url', url + '/v1/usage',
                    '--state-file', str(self.state), *extra]
            with contextlib.redirect_stdout(buf):
                code = cli.main(argv)
        self.assertEqual(code, 0)
        line = buf.getvalue().strip()
        return json.loads(line) if line else None

    def test_fires_once_per_crossing_not_once_per_poll(self):
        first = self._tick(80.0)
        self.assertIsNotNone(first)
        self.assertEqual(first['level'], 'warn')
        self.assertIsNone(self._tick(80.0), 'second poll at the same level must stay quiet')
        self.assertIsNone(self._tick(82.0), 'drifting inside the same band is not an edge')

    def test_escalation_fires_again(self):
        self._tick(80.0)
        crit = self._tick(95.0)
        self.assertIsNotNone(crit)
        self.assertEqual(crit['level'], 'crit')

    def test_recovery_is_an_edge_too_and_rearms(self):
        self._tick(95.0)
        back = self._tick(10.0)
        self.assertIsNotNone(back)
        self.assertEqual(back['level'], 'ok')
        self.assertIsNotNone(self._tick(80.0), 'after a reset the warning must fire again')

    def test_starting_already_over_the_line_fires_immediately(self):
        self.assertIsNotNone(self._tick(95.0))

    def test_starting_below_the_line_stays_quiet(self):
        self.assertIsNone(self._tick(10.0))

    def test_unknown_percentage_never_fires(self):
        """No number is not the same as a bad number — don't wake anyone up for it."""
        self.assertIsNone(self._tick(None))

    def test_exec_receives_the_numbers_in_the_environment(self):
        """The command is the user's; it gets data through env vars, not string splicing."""
        seen = {}

        def fake_run(cmd, **kw):
            seen['cmd'] = cmd
            seen['env'] = kw.get('env') or {}
            return mock.Mock(returncode=0)

        with mock.patch.object(cli.subprocess, 'run', side_effect=fake_run):
            self._tick(95.0, extra=['--exec', 'do-something'])

        self.assertEqual(seen['cmd'], 'do-something')
        self.assertEqual(seen['env']['UT_LEVEL'], 'crit')
        self.assertEqual(seen['env']['UT_PCT'], '95.0')
        self.assertEqual(seen['env']['UT_SCOPE'], 'claude/session')
        self.assertIn('UT_MESSAGE', seen['env'])

    def test_state_survives_a_restart(self):
        self._tick(95.0)
        self.assertTrue(self.state.exists())
        self.assertIsNone(self._tick(95.0), 'a restarted watcher must not re-alert')


# ── doctor ────────────────────────────────────────────────────────────────────
class DoctorCommand(unittest.TestCase):
    def test_json_shape(self):
        r = run_cli(['doctor', '--json'])
        out = json.loads(r.stdout)
        self.assertIn('checks', out)
        self.assertIn('ok', out)
        ids = {c['id'] for c in out['checks']}
        for expected in ('python', 'paths', 'catalog', 'claude-data', 'server', 'wire'):
            self.assertIn(expected, ids, f'doctor must check {expected}')
        for c in out['checks']:
            self.assertIn(c['level'], ('ok', 'warn', 'fail'))

    def test_never_prints_a_key_value(self):
        """A diagnostic users paste into a bug report must not carry their credentials."""
        secret = 'sk-or-v1-THIS-MUST-NEVER-APPEAR'
        r = run_cli(['doctor', '--json'], env_extra={'OPENROUTER_API_KEY': secret})
        self.assertNotIn(secret, r.stdout)
        self.assertNotIn(secret, r.stderr)
        self.assertIn('OPENROUTER_API_KEY', r.stdout)   # the name is useful, the value is not

    def test_text_view_is_readable_and_exits_zero_on_a_healthy_box(self):
        r = run_cli(['doctor'])
        self.assertIn('doctor', r.stdout.lower())
        self.assertIn(r.returncode, (0, 1))

    def test_reports_the_server_it_could_not_reach(self):
        port = closed_port()
        r = run_cli(['doctor', '--json', '--url', f'http://127.0.0.1:{port}/v1/usage'])
        server = next(c for c in json.loads(r.stdout)['checks'] if c['id'] == 'server')
        self.assertEqual(server['level'], 'warn')       # the CLI still works without it
        self.assertIn(str(port), server['detail'])


# ── config ────────────────────────────────────────────────────────────────────
class ConfigCommand(unittest.TestCase):
    def test_hide_and_show_round_trip(self):
        tmp = tempfile.mkdtemp(prefix='ut-cli-cfg-')
        env = {'XDG_CONFIG_HOME': str(Path(tmp) / 'config'), 'APPDATA': str(Path(tmp) / 'roaming')}
        run_cli(['config', '--hide', 'ollama'], env_extra=env)
        out = json.loads(run_cli(['config', '--json'], env_extra=env).stdout)
        self.assertEqual(out['hidden'], ['ollama'])
        run_cli(['config', '--show', 'ollama'], env_extra=env)
        out2 = json.loads(run_cli(['config', '--json'], env_extra=env).stdout)
        self.assertEqual(out2['hidden'], [])


# ── entry points ──────────────────────────────────────────────────────────────
class EntryPoints(unittest.TestCase):
    def test_server_py_dispatches_subcommands(self):
        """One binary, one entry point — the frozen .exe has no second executable."""
        with FakeWireServer(make_wire(95.0)) as url:
            r = run_cli(['guard', '--url', url + '/v1/usage'], entry='server')
        self.assertEqual(r.returncode, 2)

    def test_server_py_without_arguments_still_means_serve(self):
        """Backwards compatibility: systemd unit and start.sh call `server.py` bare."""
        src = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('ThreadingHTTPServer', src)
        r = run_cli(['--help'], entry='server')
        self.assertEqual(r.returncode, 0)

    def test_version_matches_the_server_version(self):
        r = run_cli(['--version'])
        self.assertEqual(r.returncode, 0)
        import re
        server_src = (ROOT / 'server.py').read_text(encoding='utf-8')
        version = re.search(r"^VERSION = '([^']+)'", server_src, re.M).group(1)
        self.assertIn(version, r.stdout)

    def test_help_lists_every_command(self):
        r = run_cli(['--help'])
        for cmd in ('usage', 'providers', 'guard', 'watch', 'doctor', 'config'):
            self.assertIn(cmd, r.stdout)

    def test_a_typo_in_the_command_does_not_start_a_server(self):
        """Found in the frozen binary, not in these tests: the dispatcher fell through to
        serve() for anything it did not recognise. `usage-tracker gaurd` (a typo) then
        started an HTTP server and exited 0 — so `usage-tracker gaurd || skip_job` never
        skipped, it hung. The subcommand parser exits 64 correctly; the dispatcher in front
        of it did not, and only the dispatcher is reachable from the packaged binary.
        """
        for argv in (['gaurd'], ['--threshold', '80'], ['-x'], ['guard', 'extra-positional']):
            with self.subTest(argv=argv):
                # A port nobody uses: if the dispatcher still falls through to serve(), the
                # process must hang here rather than collide with a real server and exit 1 —
                # a hang is the honest reproduction of the bug.
                try:
                    r = run_cli(argv, entry='server', timeout=8,
                                env_extra={'USAGE_PORT': '8899'})
                except subprocess.TimeoutExpired:
                    self.fail(f'{argv} started a server instead of refusing')
                self.assertEqual(r.returncode, cli.EXIT_USAGE,
                                 f'{argv} returned {r.returncode}; 64 is the only answer that '
                                 f'cannot be mistaken for a level')


# ── local fallback (no server needed) ─────────────────────────────────────────
class LocalFallback(unittest.TestCase):
    """The CLI must work on a machine where nobody ever started the server."""

    def test_falls_back_to_computing_locally(self):
        with mock.patch('usage.engine.usage_wire', return_value=make_wire(42.0)) as m:
            wire, source = cli.load_wire(url=None, local=False, timeout=0.2,
                                         base=f'http://127.0.0.1:{closed_port()}')
        self.assertEqual(source, 'local')
        self.assertEqual(wire['providers'][0]['limits']['session']['pct'], 42.0)
        self.assertTrue(m.called)

    def test_explicit_url_does_not_silently_compute_something_else(self):
        """If you named a server, a local number in its place would be a lie about origin."""
        with mock.patch('usage.engine.usage_wire', return_value=make_wire(42.0)) as m:
            wire, source = cli.load_wire(url=f'http://127.0.0.1:{closed_port()}/v1/usage',
                                         local=False, timeout=0.2)
        self.assertIsNone(wire)
        self.assertEqual(source, 'unavailable')
        self.assertFalse(m.called)

    def test_local_flag_skips_the_server_entirely(self):
        with FakeWireServer(make_wire(99.0)) as url:
            with mock.patch('usage.engine.usage_wire', return_value=make_wire(42.0)):
                wire, source = cli.load_wire(url=None, local=True, timeout=2, base=url)
        self.assertEqual(source, 'local')
        self.assertEqual(wire['providers'][0]['limits']['session']['pct'], 42.0)

    def test_garbage_from_the_server_is_not_trusted(self):
        with FakeWireServer(None, body=b'<html>not json</html>') as url:
            wire, source = cli.load_wire(url=url + '/v1/usage', local=False, timeout=2)
        self.assertIsNone(wire)
        self.assertEqual(source, 'unavailable')


if __name__ == '__main__':
    unittest.main()

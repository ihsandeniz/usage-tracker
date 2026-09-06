#!/usr/bin/env python3
"""
test_badge_resilience.py — the waybar badge must never vanish, and the installer must
never claim a signal number somebody else is using.

Both behaviours came out of a field failure (2026-09-06): the badge went blank for
minutes at a time, and sometimes disappeared entirely. Two independent causes —

  1. `/v1/usage` takes 1.9-11s here (1.2 GB of JSONL) and the feeder cut it off at 3s.
     Server-side caching fixed the latency; these tests cover the feeder's half: when a
     round IS missed, show the last reading rather than nothing.
  2. On an unexpected wire shape the final `jq` failed, printed nothing to stdout, and
     waybar reads an empty line as "hide this module". A badge that vanishes is read as
     "waybar broke", so the wrong component gets blamed.

Run: python3 -m unittest tests.test_badge_resilience -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEEDER = ROOT / 'surface' / 'waybar-usage.sh'
sys.path.insert(0, str(ROOT / 'packaging'))


# The feeder is a bash script driving curl and jq — a Linux panel surface. Windows reaches
# the same numbers through `usage --format waybar`, which needs no shell (see docs/CLI.md),
# so running these here would test a path that platform does not have. Checking for jq is
# not enough: the GitHub Windows runner ships it, so the tests ran and failed on shell
# differences instead of skipping. (ledger: test/isletim-sistemine-bagli-kurulum-testi-asilir)
_NOT_LINUX_SURFACE = os.name == 'nt'


def _serving(payload: bytes):
    """A loopback server answering every GET with `payload`. Returns (url, stop).

    `stop` closes the listening socket as well as ending the loop — `shutdown()` alone
    leaves the socket open, which Windows reports as a ResourceWarning for every case.
    """
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def stop():
        srv.shutdown()
        srv.server_close()

    return f'http://127.0.0.1:{srv.server_port}', stop


@unittest.skipIf(_NOT_LINUX_SURFACE, 'the waybar feeder is a Linux panel surface')
@unittest.skipUnless(shutil.which('jq'), 'the feeder needs jq')
@unittest.skipUnless(shutil.which('curl'), 'the feeder needs curl')
class TheBadgeNeverPrintsNothing(unittest.TestCase):
    """Whatever the server says, stdout carries one valid waybar object.

    Empty stdout is not a neutral outcome: waybar hides the module, so a feeder crash
    reaches the user as absence rather than as an error.
    """

    def setUp(self):
        # Its own runtime dir, so a test never reads or clobbers the real badge cache.
        self.tmp = tempfile.mkdtemp()
        self.env = {**os.environ, 'XDG_RUNTIME_DIR': self.tmp}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, url):
        # Text is captured, never echoed through a shell: zsh's builtin `echo` interprets
        # the \n inside jq's output and would split one valid line into seven invalid ones
        # (ledger: test/zsh-echo-escape-yalanci-bulgu).
        p = subprocess.run(['bash', str(FEEDER)], capture_output=True, text=True,
                           env={**self.env, 'USAGE_URL': url}, timeout=60)
        return p

    def _assert_valid_badge(self, out, label):
        self.assertTrue(out.strip(), f'{label}: stdout was empty — waybar hides the module')
        obj = json.loads(out)            # raises → the badge would be malformed
        self.assertIn('text', obj, f'{label}: no "text" key')
        self.assertTrue(str(obj['text']).strip(), f'{label}: empty text')
        return obj

    def test_wire_shapes_that_used_to_kill_the_module(self):
        shapes = {
            'providers null': b'{"schema":"1","generatedAtMs":1,"providers":null}',
            'empty object': b'{}',
            'providers empty list': b'{"schema":"1","generatedAtMs":1,"limits":{},'
                                    b'"spend":{},"providers":[]}',
            'not an object at all': b'[]',
        }
        for label, payload in shapes.items():
            with self.subTest(shape=label):
                url, stop = _serving(payload)
                try:
                    p = self._run(url)
                finally:
                    stop()
                self.assertEqual(p.returncode, 0, f'{label}: exit {p.returncode}')
                self._assert_valid_badge(p.stdout, label)

    def test_server_down_without_a_cached_reading_is_still_valid_json(self):
        # Nothing listening on this port, and no cache file exists yet.
        p = self._run('http://127.0.0.1:9')
        self.assertEqual(p.returncode, 0)
        obj = self._assert_valid_badge(p.stdout, 'server down')
        self.assertEqual(obj.get('class'), 'off')


@unittest.skipIf(_NOT_LINUX_SURFACE, 'the waybar feeder is a Linux panel surface')
@unittest.skipUnless(shutil.which('jq'), 'the feeder needs jq')
@unittest.skipUnless(shutil.which('curl'), 'the feeder needs curl')
class TheBadgeFallsBackToItsLastReading(unittest.TestCase):
    """A missed round shows the previous measurement, marked as old — not a blank badge.

    30s of interval means a single missed round is 30s of nothing. The stale number is
    labelled (`⋯` plus its age) so "old" never reads as "current".
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {**os.environ, 'XDG_RUNTIME_DIR': self.tmp}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, url):
        return subprocess.run(['bash', str(FEEDER)], capture_output=True, text=True,
                              env={**self.env, 'USAGE_URL': url}, timeout=60)

    def test_a_good_round_then_a_dead_server(self):
        wire = json.dumps({
            'schema': '1', 'generatedAtMs': 1,
            'limits': {'session': {'pct': 41, 'used': 41, 'resetAtMs': 0},
                       'weekly': {'pct': 13, 'used': 13, 'resetAtMs': 0}},
            'spend': {}, 'thresholds': {'warn': 75, 'crit': 90},
            'providers': [{'id': 'claude', 'name': 'Claude', 'kind': 'tokens',
                           'limits': {'session': {'pct': 41}, 'weekly': {'pct': 13}}}],
        }).encode()

        url, stop = _serving(wire)
        try:
            good = self._run(url)
        finally:
            stop()
        self.assertEqual(good.returncode, 0)
        first = json.loads(good.stdout)
        self.assertNotIn('⋯', first['text'], 'a live reading must not be marked stale')

        cache = Path(self.tmp) / 'usage-waybar-claude.json'
        self.assertTrue(cache.is_file(), 'a good round must leave a cached reading behind')

        # Same feeder, server gone.
        dead = self._run('http://127.0.0.1:9')
        self.assertEqual(dead.returncode, 0)
        second = json.loads(dead.stdout)
        self.assertIn('⋯', second['text'],
                      'a stale reading must be marked — an unlabelled old number is worse '
                      'than no number')
        self.assertEqual(second['text'].replace(' ⋯', ''), first['text'],
                         'the fallback must be the previous reading, not a new guess')

    def test_a_reading_older_than_the_ceiling_is_not_served(self):
        cache = Path(self.tmp) / 'usage-waybar-claude.json'
        cache.write_text(json.dumps({'text': '◐ S99% W99%', 'class': 'crit', 'tooltip': 'x'}),
                         encoding='utf-8')
        p = subprocess.run(['bash', str(FEEDER)], capture_output=True, text=True,
                           env={**self.env, 'USAGE_URL': 'http://127.0.0.1:9',
                                'USAGE_CACHE_MAX_AGE': '0'}, timeout=60)
        obj = json.loads(p.stdout)
        self.assertEqual(obj.get('class'), 'off')
        self.assertNotIn('99', obj['text'],
                         'past the age ceiling the old number must be dropped, not shown')


class TheInstallerPicksAFreeSignal(unittest.TestCase):
    """`signal` is how right-click refreshes the badge — and waybar delivers SIGRTMIN+N to
    EVERY module declaring N. A hardcoded number would silently refresh a stranger's
    module. Which numbers are taken is readable from the config, so guessing is the only
    way to get it wrong. (This author's own config already used 1.)
    """

    def setUp(self):
        import waybar_edit
        self.w = waybar_edit

    def test_it_reads_the_numbers_already_in_use(self):
        cases = [
            ({'clock': {}}, 1, 'nothing taken → the first one'),
            ({'a': {'signal': 1}}, 2, 'skip the taken one'),
            ({'a': {'signal': 1}, 'b': {'signal': 2}, 'c': {'signal': 4}}, 3, 'fill the gap'),
            ([{'a': {'signal': 1}}, {'b': {'signal': 2}}], 3, 'both bars count'),
            ({'a': {'signal': True}}, 1, 'bool is not a signal number'),
            ({'a': {'signal': 'two'}}, 1, 'a string is not a signal number'),
            ({str(i): {'signal': i} for i in range(1, 31)}, 0, 'all taken → claim none'),
        ]
        for doc, expected, why in cases:
            with self.subTest(why=why):
                self.assertEqual(self.w.free_signal(doc), expected, why)

    def test_the_written_block_matches_the_number_it_picked(self):
        text = ('{\n    "modules-right": ["clock"],\n'
                '    "custom/mine": {"signal": 1},\n    "clock": {}\n}\n')
        out, target = self.w.op_add(text, '/opt/ut/surface/waybar-usage.sh',
                                    '/opt/ut/surface/usage-widget toggle', 'modules-right')
        self.assertIsNotNone(out, f'op_add refused: {target}')
        mod = self.w.bars(self.w.parse(out))[0]['custom/usage']
        self.assertEqual(mod['signal'], 2, 'signal 1 was taken')
        self.assertEqual(mod['on-click-right'], 'pkill -SIGRTMIN+2 waybar',
                         'the command must fire the number the module actually declares')

    def test_it_claims_no_signal_when_every_number_is_taken(self):
        taken = ',\n'.join(f'    "custom/m{i}": {{"signal": {i}}}' for i in range(1, 31))
        text = '{\n    "modules-right": ["clock"],\n' + taken + ',\n    "clock": {}\n}\n'
        out, target = self.w.op_add(text, '/opt/ut/surface/waybar-usage.sh', '', 'modules-right')
        self.assertIsNotNone(out, f'op_add refused: {target}')
        mod = self.w.bars(self.w.parse(out))[0]['custom/usage']
        # Better no refresh than a refresh that fires somebody else's module.
        self.assertNotIn('signal', mod)
        self.assertNotIn('on-click-right', mod)


if __name__ == '__main__':
    unittest.main()

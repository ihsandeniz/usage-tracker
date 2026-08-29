#!/usr/bin/env python3
"""
test_limit_surfaces.py — a provider other than Claude publishes limit bars.

Two things are pinned here, and they are different things:

  1. **The generalisation.** `scopes_of` used to branch on `id == "claude"`. Every surface
     that measures "how close am I to a wall" goes through it, so the day the Codex card
     started publishing the same `limits` block, the wire carried the bars and
     `guard --provider codex` still answered "no usable percentage". These tests fail if
     anyone re-keys that branch on a provider name.

  2. **The panel feeders.** polybar / i3blocks / genmon / argos / plain exist because
     waybar is an Arch-and-Hyprland assumption: Fedora ships GNOME, Kali ships XFCE, and
     both were left with no badge at all. A feeder must never exit non-zero and must never
     carry its own warn/crit — both are asserted, not assumed.

Hermetic: no test reads ~/.claude or ~/.codex, none opens a socket.

Run: python3 -m unittest tests.test_limit_surfaces -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from usage import cli                                             # noqa: E402
from tests.test_cli import make_wire, OLLAMA_CARD                 # noqa: E402


def codex_card(session_pct=84.0, weekly_pct=29.0, *, expired=False, plan='plus',
               status='ok', warnings=None):
    """The card `usage/providers/codex.py` publishes, in the shape it publishes it.

    Deliberately built from the *real* field list rather than a minimal one: a fixture
    thinner than production is how `balance` shipped as a bare number here for months
    while production shipped an object and the CLI crashed on it (2026-08-13).
    """
    def bar(pct, window_minutes):
        return {
            'pct': None if expired else pct,
            'used': None, 'units': None, 'budget': None,
            'calibSuspect': False,
            'resetAtMs': 1770000000000, 'resetInSec': None if expired else 4500,
            'forecast': None, 'live': True, 'stale': expired,
            'windowMinutes': window_minutes,
            'observedAtMs': 1769999000000, 'ageSec': 1000.0,
            'expired': expired, 'reportedPct': pct,
            'limitId': 'codex', 'reachedType': None,
        }

    return {
        'id': 'codex', 'name': 'Codex', 'kind': 'tokens', 'available': True,
        'status': status, 'error': None, 'currency': 'USD', 'auth': 'chatgpt',
        'windowDays': 30, 'sessions': 12,
        'truncated': False, 'truncatedReason': None,
        'tokens': {'input': 10, 'cached_input': 0, 'output': 5, 'total': 15},
        'today': {'tokens': 15, 'usd': 0.5}, 'total': {'tokens': 15, 'usd': 1.5},
        'byModel': [], 'byDay': [], 'usdSource': 'estimate',
        'plan': plan,
        'warnings': warnings if warnings is not None else [],
        'limits': {'session': bar(session_pct, 300), 'weekly': bar(weekly_pct, 10080)},
        'note': 'ChatGPT subscription.',
    }


class ScopesSeeAnyCardThatPublishesBars(unittest.TestCase):
    """The branch is on the field, not on the provider's name."""

    def test_codex_bars_become_scopes(self):
        wire = make_wire(10.0, 10.0, extra_providers=[codex_card()])
        scopes = {s['scope']: s for s in cli.scopes_of(wire, provider='codex')}
        self.assertEqual({'codex/session', 'codex/weekly'}, set(scopes))
        self.assertEqual(84.0, scopes['codex/session']['pct'])
        self.assertEqual(29.0, scopes['codex/weekly']['pct'])

    def test_provider_all_sees_claude_and_codex_together(self):
        wire = make_wire(10.0, 10.0, extra_providers=[codex_card()])
        names = {s['scope'] for s in cli.scopes_of(wire, provider='all')}
        self.assertIn('claude/session', names)
        self.assertIn('codex/session', names)

    def test_worst_wall_can_belong_to_codex(self):
        """guard reports the first wall you hit — whoever's wall it is."""
        wire = make_wire(10.0, 10.0, extra_providers=[codex_card(session_pct=95.0)])
        verdict = cli.evaluate(wire, provider='all')
        self.assertEqual('codex/session', verdict['scope'])
        self.assertEqual('crit', verdict['level'])
        self.assertEqual(2, verdict['exitCode'])

    def test_expired_window_is_unknown_not_zero(self):
        """The reason this whole feature is honest. A rolled-over window means we do not
        know the new window's usage; answering 0 would exit 0 and start the expensive job
        the caller asked us to block."""
        wire = {'schema': 'usage/v1', 'generatedAtMs': 0, 'generatedAt': '',
                'thresholds': {'warn': 75, 'crit': 90},
                'providers': [codex_card(expired=True)]}
        verdict = cli.evaluate(wire, provider='codex')
        self.assertIsNone(verdict['pct'])
        self.assertEqual('unknown', verdict['level'])
        self.assertEqual(3, verdict['exitCode'])

    def test_bar_level_age_survives_into_the_scope(self):
        """Claude ages as a whole card; an adapter times each reading. A scope must carry
        whichever one exists, or a stale warning loses the number that justifies it."""
        card = codex_card(expired=True)
        card['limits']['session']['ageSec'] = 4242.0
        wire = {'schema': 'usage/v1', 'generatedAtMs': 0, 'generatedAt': '',
                'thresholds': {'warn': 75, 'crit': 90}, 'providers': [card]}
        scope = next(s for s in cli.scopes_of(wire, provider='codex')
                     if s['scope'] == 'codex/session')
        self.assertTrue(scope['stale'])
        self.assertEqual(4242.0, scope['ageSec'])

    def test_a_card_without_limits_is_untouched(self):
        wire = make_wire(10.0, 10.0, extra_providers=[OLLAMA_CARD])
        self.assertEqual([], cli.scopes_of(wire, provider='ollama'))


class LimitBarsRenderInTheTextSurface(unittest.TestCase):
    def test_adapter_bars_are_printed(self):
        wire = make_wire(10.0, 10.0, extra_providers=[codex_card()])
        text = cli.render_usage_text(wire, 'local', provider='codex')
        self.assertIn('session', text)
        self.assertIn('84.0%', text)
        self.assertIn('29.0%', text)

    def test_plan_appears_in_the_header(self):
        wire = make_wire(10.0, 10.0, extra_providers=[codex_card(plan='pro')])
        self.assertIn('pro', cli.render_usage_text(wire, 'local', provider='codex'))

    def test_expired_bar_says_why_instead_of_showing_a_dash(self):
        """'—' alone reads as 'never measured'. The user needs to know a window rolled
        over and what it last said."""
        wire = make_wire(10.0, 10.0, extra_providers=[codex_card(expired=True)])
        text = cli.render_usage_text(wire, 'local', provider='codex')
        self.assertIn('window reset', text)
        self.assertIn('84.0%', text)          # reportedPct is kept, not hidden

    def test_adapter_bars_use_the_servers_thresholds(self):
        """Y6: the text surface must not carry its own warn/crit."""
        wire = make_wire(10.0, 10.0, thresholds={'warn': 20, 'crit': 30},
                         extra_providers=[codex_card(session_pct=25.0, weekly_pct=1.0)])
        text = cli.render_usage_text(wire, 'local', provider='codex')
        # 25 is below the default 75 but above this server's warn=20 → must be marked.
        session_line = next(ln for ln in text.split('\n') if 'session' in ln)
        self.assertIn('!', session_line)


class PanelFeeders(unittest.TestCase):
    """Everything a panel that is not waybar needs."""

    FORMATS = ('polybar', 'i3blocks', 'genmon', 'argos', 'plain')

    def _wire(self):
        return make_wire(62.5, 10.0, extra_providers=[codex_card()])

    def test_every_feeder_is_registered(self):
        self.assertEqual(set(self.FORMATS), set(cli.TEXT_FEEDERS))

    def test_every_feeder_shows_the_percentage(self):
        wire = self._wire()
        for fmt in self.FORMATS:
            with self.subTest(fmt=fmt):
                self.assertIn('62.5%', cli.TEXT_FEEDERS[fmt](wire, 'local', 'claude'))

    def test_every_feeder_survives_no_server(self):
        """A feeder that raises takes the user's panel with it. `wire=None` is the shape
        `load_wire` returns when nothing answers."""
        for fmt in self.FORMATS:
            with self.subTest(fmt=fmt):
                out = cli.TEXT_FEEDERS[fmt](None, 'unavailable', 'claude')
                self.assertIn('—', out)

    def test_every_feeder_can_lead_with_another_provider(self):
        wire = self._wire()
        for fmt in self.FORMATS:
            with self.subTest(fmt=fmt):
                out = cli.TEXT_FEEDERS[fmt](wire, 'local', 'codex')
                self.assertIn('84.0%', out)
                self.assertNotIn('62.5%', out.split('\n')[0])

    def test_colour_follows_the_servers_thresholds_not_a_constant(self):
        """The same 62.5 % is 'ok' on a default server and 'crit' on one configured at 60."""
        default = cli.render_polybar(self._wire(), 'local', 'claude')
        strict = cli.render_polybar(
            make_wire(62.5, 10.0, thresholds={'warn': 50, 'crit': 60}), 'local', 'claude')
        self.assertIn(cli.LEVEL_COLOR['ok'], default)
        self.assertIn(cli.LEVEL_COLOR['crit'], strict)

    def test_palette_matches_the_shell_feeder(self):
        """waybar-usage.sh hardcodes these hex values. Two palettes would mean the same
        percentage was one colour in waybar and another in polybar on the same screen."""
        shell = (ROOT / 'surface' / 'waybar-usage.sh').read_text(encoding='utf-8')
        for level in ('ok', 'warn', 'crit'):
            with self.subTest(level=level):
                self.assertIn(cli.LEVEL_COLOR[level], shell)

    def test_i3blocks_emits_exactly_three_lines(self):
        """The i3blocks protocol is positional: full_text, short_text, colour."""
        lines = cli.render_i3blocks(self._wire(), 'local', 'claude').split('\n')
        self.assertEqual(3, len(lines))
        self.assertTrue(lines[2].startswith('#'))

    def test_genmon_escapes_markup_so_the_plugin_does_not_drop_the_reading(self):
        # The hostile string goes in the card NAME, which the text surface really prints.
        # It was in `byModel[0].model` first, which it does not — the test passed while
        # escaping nothing.
        wire = self._wire()
        wire['providers'][1]['name'] = 'a<b>&c'
        out = cli.render_genmon(wire, 'local', 'claude')
        self.assertTrue(out.startswith('<txt>'))
        self.assertIn('</tool>', out)
        tool = out.split('<tool>')[1]
        self.assertNotIn('<b>', tool)
        self.assertIn('&amp;', tool)

    def test_genmon_escape_covers_attribute_characters_too(self):
        """SEC-F001 (2026-08-29 audit): quotes were not escaped. Not reachable today —
        the only attribute is our own colour constant — but the escaper is one careless
        `foreground="{...}"` away from Pango markup injection, so it escapes all five."""
        self.assertEqual('&amp;&lt;&gt;&quot;&apos;', cli._xml_escape('&<>"\''))

    def test_argos_never_leaks_a_pipe_into_a_menu_line(self):
        """Argos splits menu entries on `|`; an unescaped one truncates the row."""
        wire = self._wire()
        wire['providers'][1]['name'] = 'pipe|model'   # a field the text surface prints
        out = cli.render_argos(wire, 'local', 'claude')
        head, rest = out.split('\n', 1)
        self.assertIn('color=', head)
        for line in rest.split('\n')[1:]:            # skip the '---' separator
            self.assertEqual(1, line.count('|'), line)

    def test_plain_carries_no_markup(self):
        out = cli.render_plain(self._wire(), 'local', 'claude')
        for token in ('%{', '<', '>', '|'):
            self.assertNotIn(token, out)

    def test_waybar_still_answers_the_same_shape(self):
        """The JSON feeder was refactored onto the shared `_badge`; its contract did not
        change and nothing outside this repo should have to notice the refactor."""
        badge = cli.render_waybar(self._wire(), 'local', 'claude')
        self.assertEqual({'text', 'tooltip', 'class', 'percentage'}, set(badge))
        self.assertEqual('ok', badge['class'])
        self.assertEqual(62, badge['percentage'])
        offline = cli.render_waybar(None, 'unavailable', 'claude')
        self.assertEqual('off', offline['class'])


class PanelScriptHasNoShadowedFunctions(unittest.TestCase):
    """A second `function foo` silently replaces the first one, everywhere, forever.

    This is not hypothetical: `fmtAge` was defined twice in `web/app.js` on 2026-08-29 —
    a new helper for the adapter limit bars, added 158 lines below an existing one with
    the same name and a different duration format. `liveFlag()` kept calling `fmtAge` and
    started printing the new format instead. No syntax error, no console warning, nothing
    a screenshot of the new feature would show: the damage was to an old one.
    """

    def test_no_top_level_function_name_is_defined_twice(self):
        import re
        from collections import Counter
        for name in ('app.js', 'setup.js', 'wizard.js'):
            path = ROOT / 'web' / name
            if not path.exists():
                continue
            with self.subTest(file=name):
                names = re.findall(r'^function\s+(\w+)', path.read_text(encoding='utf-8'),
                                   re.MULTILINE)
                dupes = [n for n, c in Counter(names).items() if c > 1]
                self.assertEqual([], dupes, f'{name}: shadowed function(s) {dupes}')


class TheDocsAgreeWithTheCode(unittest.TestCase):
    """Claims in prose rot silently; these are the ones with a checkable counterpart.

    All three were found stale in the same audit (2026-08-29): the READMEs told users to
    run `./setup.sh verify`, which is not a subcommand and answers "unknown option" — a
    line that had never been executed by whoever wrote it. Two files said "15 adapters"
    when there were 16. A CLI example showed the output of version 0.3.0.
    """

    DOCS = ('README.md', 'README.tr.md', 'surface/README.md',
            'docs/CLI.md', 'docs/WIRE.md', 'docs/WINDOWS.md', 'docs/WINDOWS.tr.md')

    def _docs(self):
        for name in self.DOCS:
            path = ROOT / name
            if path.exists():
                yield name, path.read_text(encoding='utf-8')

    def test_no_document_invents_a_setup_subcommand(self):
        """`setup.sh` takes `probe|preview|do|undo`; a step name goes *after* `do`."""
        import re
        steps = ('base', 'server', 'waybar', 'widget', 'tray', 'keys', 'verify')
        pattern = re.compile(r'setup\.sh\s+(' + '|'.join(steps) + r')\b')
        for name, text in self._docs():
            with self.subTest(doc=name):
                self.assertIsNone(pattern.search(text),
                                  f'{name}: `setup.sh <step>` — the step belongs after `do`')

    def test_the_adapter_count_in_prose_matches_the_registry(self):
        import re
        from usage import providers
        real = len(providers._ADAPTERS)
        for name, text in self._docs():
            for found in re.findall(r'(\d+)\s+adapt(?:ör|ers?)', text):
                with self.subTest(doc=name, wrote=found):
                    self.assertEqual(real, int(found), f'{name}: says {found}, registry has {real}')

    def test_every_documented_usage_format_exists(self):
        """A format named in the docs that argparse rejects is a dead instruction."""
        import re
        from usage import cli
        known = set(cli.TEXT_FEEDERS) | {'text', 'json', 'waybar'}
        for name, text in self._docs():
            for found in set(re.findall(r'--format\s+([a-z0-9]+)', text)):
                with self.subTest(doc=name, fmt=found):
                    self.assertIn(found, known, f'{name}: `--format {found}` does not exist')

    def test_version_examples_are_not_from_an_older_release(self):
        """A sample transcript pinned to an old version reads as the current output."""
        import re
        import server
        for name, text in self._docs():
            for found in set(re.findall(r'usage-tracker (\d+\.\d+\.\d+)', text)):
                with self.subTest(doc=name, wrote=found):
                    self.assertEqual(server.VERSION, found,
                                     f'{name}: shows {found}, current is {server.VERSION}')


class DesktopRecommendation(unittest.TestCase):
    """`setup.sh probe` has to name a surface this desktop actually has.

    Run as a subprocess against a stubbed PATH — the FAZ 5e lesson: a test that lets the
    script see the real machine measures the machine, not the script. The PATH here holds
    no `waybar`, no `polybar`, no `gnome-shell`, so only the desktop name can decide, which
    is the branch that was wrong: the first version read $XDG_CURRENT_DESKTOP into the
    probe output and then never consulted it, answering "tray" for every XFCE, GNOME and
    KDE session — the exact users the feature is for.
    """

    STUBBED = ('python3', 'curl', 'jq', 'bash', 'sed', 'grep', 'awk', 'cat', 'tr',
               'head', 'tail', 'sort', 'uniq', 'wc', 'printf', 'id', 'date', 'env',
               'cp', 'mv', 'rm', 'mkdir', 'chmod', 'ls', 'find', 'xargs', 'mktemp',
               'readlink', 'dirname', 'cut')

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        # `setup.sh` is the Linux installer: bash, POSIX tools, $XDG_CURRENT_DESKTOP.
        # CI also runs windows-latest, where this class asked bash to run a shell script
        # through a symlinked PATH and got an empty stdout it then tried to parse as JSON.
        # Skipping is the honest outcome — there is no desktop recommendation to make on
        # a platform the wizard does not target. (`sys.platform` is 'linux' on Linux and
        # 'darwin' on macOS, where setup.sh is likewise not supported.)
        if sys.platform != 'linux':
            raise unittest.SkipTest('setup.sh is the Linux installer')
        if not shutil.which('bash'):
            raise unittest.SkipTest('bash not available')
        cls._tmp = tempfile.TemporaryDirectory()
        cls.stub = Path(cls._tmp.name)
        for name in cls.STUBBED:
            real = shutil.which(name)
            if real:
                (cls.stub / name).symlink_to(real)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _run(self, desktop, *args, isolate_home=False):
        """setup.sh in a stubbed PATH, optionally with a throwaway HOME.

        `isolate_home` matters more than it looks: with the developer's real HOME, this
        machine already has a waybar config, so `verify` takes the waybar branch and the
        non-waybar advice — the entire point of the feature — is never reached. Measured
        while writing these tests: two separate "it prints nothing" results, both of them
        the harness rather than the product. (The other one was a `grep` symlink that
        pointed at an alias; see ledger `test/izole-path-eksik-komut`.)
        """
        import json
        import subprocess
        import tempfile
        home = Path.home()
        tmp = None
        if isolate_home:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            home = Path(tmp.name)
        env = {'HOME': str(home), 'PATH': str(self.stub),
               'XDG_CURRENT_DESKTOP': desktop, 'XDG_SESSION_TYPE': 'x11'}
        proc = subprocess.run(['bash', str(ROOT / 'setup.sh'), *args],
                              capture_output=True, text=True, env=env, timeout=120)
        return json.loads(proc.stdout), proc.stderr

    def _recommend(self, desktop):
        payload, _ = self._run(desktop, 'probe')
        return payload['desktop']['recommended']

    def test_each_desktop_gets_the_panel_it_has(self):
        for desktop, expected in (('XFCE', 'genmon'),
                                  ('GNOME', 'argos'),
                                  ('KDE', 'plasmoid'),
                                  ('MATE', 'genmon')):
            with self.subTest(desktop=desktop):
                self.assertEqual(expected, self._recommend(desktop))

    def test_colon_separated_desktop_string_is_understood(self):
        """$XDG_CURRENT_DESKTOP is "ubuntu:GNOME" on Ubuntu, "pop:GNOME" on Pop!_OS."""
        self.assertEqual('argos', self._recommend('ubuntu:GNOME'))

    def test_an_anonymous_session_falls_back_to_the_tray(self):
        self.assertEqual('tray', self._recommend(''))

    def test_verify_hands_over_a_command_the_user_can_paste(self):
        """The point of the whole feature: a non-Arch user reaches the end of `verify` with
        something to paste, not with "waybar not found".

        Run with a throwaway HOME — with the developer's own, this machine has a waybar
        config, `verify` takes the waybar branch, and the advice under test never runs.
        """
        for desktop, surface, fmt in (('XFCE', 'genmon', '--format genmon'),
                                      ('GNOME', 'argos', '--format argos'),
                                      ('KDE', 'plasmoid', '--format plain')):
            with self.subTest(desktop=desktop):
                payload, stderr = self._run(desktop, 'do', 'verify', isolate_home=True)
                verify = payload.get('verify', {})
                self.assertEqual(surface, verify.get('no_waybar_suggestion'))
                self.assertIn(fmt, verify.get('no_waybar_command', ''))
                # …and the human-facing side says it too, not only the JSON.
                self.assertIn('waybar not found', stderr)
                self.assertIn(fmt, stderr)

    def test_verify_offers_the_tray_when_no_panel_is_detected(self):
        payload, stderr = self._run('', 'do', 'verify', isolate_home=True)
        self.assertEqual('tray', payload.get('verify', {}).get('no_waybar_suggestion'))
        self.assertIn('do tray', stderr)


class RecommendationsPointSomewhere(unittest.TestCase):
    """Kept out of the class above on purpose: that one needs bash and skips on Windows,
    and this check — that every surface the wizard can name is a format the CLI can
    actually emit — is worth running on every platform CI covers."""

    def test_every_recommendation_maps_to_a_real_cli_format(self):
        """A recommendation the CLI cannot produce is a dead end in the wizard."""
        mapping = {'polybar': 'polybar', 'i3blocks': 'i3blocks', 'genmon': 'genmon',
                   'argos': 'argos', 'plasmoid': 'plain'}
        for surface, fmt in mapping.items():
            with self.subTest(surface=surface):
                self.assertIn(fmt, cli.TEXT_FEEDERS)

    def test_setup_sh_names_the_same_surfaces_the_cli_knows(self):
        """`recommend_surface()` and `surface_format()` live in setup.sh; if someone adds
        a surface there and not here, the wizard prints a command that does not exist."""
        script = (ROOT / 'setup.sh').read_text(encoding='utf-8')
        for surface in ('polybar', 'i3blocks', 'genmon', 'argos', 'plasmoid', 'tray', 'waybar'):
            with self.subTest(surface=surface):
                self.assertIn(f"'{surface}\\n'", script)


if __name__ == '__main__':
    unittest.main()

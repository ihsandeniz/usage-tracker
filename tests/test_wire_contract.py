"""`/v1/usage` — the shape four surfaces depend on.

These tests run against the REAL `engine.usage_wire()`, not a hand-written fixture. That
distinction is the whole point of the file. On 2026-08-11 all three surfaces were changed to
stop hardcoding the 75/90 thresholds and read them from the server instead — and every one of
them read `.thresholds` at the top level, where the field did not exist. Their tests passed,
because those tests fed in synthetic JSON that *did* have the field. Three surfaces silently
fell back to the hardcoded default and the suite was green.

A fixture proves you can parse what you invented. Only the real assembler proves the field
is there. See ledger: test/yesil-ama-yanlis-seyi-olcuyor.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usage import demo, engine, viewconfig

# Every path a surface in this repo actually reads out of /v1/usage.
# (dotted path, which surface, file:line at the time of writing)
SURFACE_READS = [
    ('thresholds',                        'waybar', 'surface/waybar-usage.sh:69'),
    ('thresholds',                        'tray',   'surface/usage-tray.py:136'),
    ('schema',                            'panel',  'PUBLISH.md release check'),
    ('providers',                         'all',    '—'),
    ('providers.0.limits.session',        'waybar', 'surface/waybar-usage.sh:71'),
    ('providers.0.limits.weekly',         'tray',   'surface/usage-tray.py:141'),
    ('providers.0.limits.thresholds',     'compat', 'pre-2026-08-11 consumers'),
    ('providers.0.spend.today',           'panel',  'web/app.js'),
    ('providers.0.spend.currency',        'all',    'Y3 — no hardcoded $'),
    # Three surfaces read a forecast the production assembler never published: the field was
    # computed in compute_usage() and then dropped by usage_wire()'s limit_bar(). It existed
    # only in the demo, which is where the golden schema was generated from — so the whole
    # suite agreed the field was there. "Will exceed" warnings were dead for every real user.
    ('providers.0.limits.session.forecast', 'panel',  'web/app.js:189'),
    ('providers.0.limits.weekly.forecast',  'waybar', 'surface/waybar-usage.sh:95'),
    ('providers.0.limits.weekly.forecast',  'tray',   'surface/usage-tray.py:158'),
]


def _missing_fields(prod, fake, path='$'):
    """Field paths present in the production value and absent from the demo one.

    Production drives the walk, so a demo that is *richer* is fine — the failure mode being
    guarded against is the opposite: a production field the demo never emits is a field the
    golden snapshot (generated from the demo) cannot pin.
    """
    out = []
    if isinstance(prod, dict):
        if not isinstance(fake, dict):
            return [f'{path}  production has an object, demo has {type(fake).__name__}']
        for key, value in sorted(prod.items()):
            if key not in fake:
                out.append(f'{path}.{key}')
            else:
                out += _missing_fields(value, fake[key], f'{path}.{key}')
    elif isinstance(prod, list) and prod and isinstance(fake, list) and fake:
        out += _missing_fields(prod[0], fake[0], f'{path}[]')
    return out


def _dig(obj, dotted):
    cur = obj
    for part in dotted.split('.'):
        if part.isdigit():
            if not isinstance(cur, list) or len(cur) <= int(part):
                return None, f'{dotted}: list too short at .{part}'
            cur = cur[int(part)]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return None, f'{dotted}: missing key .{part}'
            cur = cur[part]
    return cur, None


class _QuietWire(unittest.TestCase):
    """No real transcripts, no network — but the real assembler."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = engine.CLAUDE_PROJECTS_DIR
        engine.CLAUDE_PROJECTS_DIR = Path(self._tmp.name) / 'no-projects'
        self._live = mock.patch.object(
            engine.live, 'fetch',
            return_value={'ok': False, 'error': 'test', 'cached': False,
                          'fetchedAtMs': None, 'ageSec': None, 'stale': False})
        self._live.start()
        self.addCleanup(self._restore)

    def _restore(self):
        self._live.stop()
        engine.CLAUDE_PROJECTS_DIR = self._saved
        self._tmp.cleanup()


class EverySurfacePathExists(_QuietWire):

    def test_paths_the_surfaces_read_are_present(self):
        wire = engine.usage_wire()
        missing = []
        for dotted, surface, where in SURFACE_READS:
            _, err = _dig(wire, dotted)
            if err:
                missing.append(f'{err}  ← read by {surface} ({where})')
        self.assertFalse(missing, 'the wire is missing paths its own surfaces read:\n  ' +
                                  '\n  '.join(missing))

    def test_thresholds_carry_both_keys_and_sane_values(self):
        th = engine.usage_wire()['thresholds']
        self.assertIn('warn', th)
        self.assertIn('crit', th)
        self.assertLess(th['warn'], th['crit'], 'warn must trip before crit')
        self.assertLessEqual(th['crit'], 100)

    def test_top_level_and_claude_thresholds_agree(self):
        """Two copies of the same number is exactly the bug we just fixed. While both
        exist for compatibility they must never disagree."""
        wire = engine.usage_wire()
        self.assertEqual(wire['thresholds'], wire['providers'][0]['limits']['thresholds'])

    def test_the_user_filter_does_not_eat_top_level_fields(self):
        """`/v1/usage` without ?all=1 goes through viewconfig.filter_wire. If that drops
        thresholds, waybar quietly falls back again and nothing notices."""
        wire = engine.usage_wire()
        filtered = viewconfig.filter_wire(wire)

        for key in ('schema', 'thresholds', 'generatedAtMs', 'providers'):
            self.assertIn(key, filtered, f'filter_wire dropped top-level {key}')
        self.assertEqual(filtered['thresholds'], wire['thresholds'])


class ClaudeIsAlwaysTheFirstCard(_QuietWire):
    """docs/WIRE.md:37 — "providers[0] is always the Claude card". Measured 2026-08-12:
    `config --hide claude` was accepted and the default endpoint led with `ollama`, so every
    consumer that trusts the documented position read another provider's numbers as Claude's.

    Two locks, because a config file written by an older build is already out there:
    the writer refuses, and the filter refuses to act on it if it was written anyway.
    """

    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory()
        root = Path(self._tmp2.name)
        for patch in (mock.patch.object(viewconfig, 'CONFIG_PATH', root / 'view_config.json'),
                      mock.patch.object(viewconfig, 'LEGACY_CONFIG_PATH', root / 'absent.json')):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self._tmp2.cleanup)

    def test_the_writer_refuses_to_hide_claude(self):
        self.assertFalse(viewconfig.save_config({'hidden_providers': ['claude']}))
        ok, err = viewconfig.validate_config({'hidden_providers': ['claude']})
        self.assertFalse(ok)
        self.assertIn('claude', err.lower())

    def test_hiding_any_other_card_still_works(self):
        self.assertTrue(viewconfig.save_config({'hidden_providers': ['ollama']}))
        self.assertEqual(viewconfig.get_config()['hidden_providers'], ['ollama'])

    def test_a_config_file_that_already_hides_claude_cannot_break_the_wire(self):
        viewconfig.CONFIG_PATH.write_text('{"hidden_providers": ["claude", "ollama"]}',
                                          encoding='utf-8')
        filtered = viewconfig.filter_wire(engine.usage_wire())
        self.assertTrue(filtered['providers'], 'the filter emptied the provider list')
        self.assertEqual(filtered['providers'][0]['id'], 'claude')

    def test_claude_is_visible_even_when_the_file_says_otherwise(self):
        viewconfig.CONFIG_PATH.write_text('{"hidden_providers": ["claude"]}', encoding='utf-8')
        self.assertTrue(viewconfig.is_visible('claude'))


class StaleLiveNumbersAreLabelledInTheWire(unittest.TestCase):
    """The overlay writes a real percentage from a cached response. Whether that number is
    minutes or a week old is the difference between a fact and a guess, and only the wire can
    carry it to the four surfaces that decide on it."""

    def _wire(self, *, stale, age):
        limits = {
            'calibrated': True,
            'session': {'pct': 5.0, 'units': 1, 'budget': 10, 'calibSuspect': None,
                        'resetAtMs': None, 'resetInSec': None, 'forecast': None},
            'weeklyAll': None, 'weeklyModel': None,
            'thresholds': {'warn': 75, 'crit': 90},
        }
        live_meta = {'ok': True, 'error': None, 'cached': True, 'fetchedAtMs': 1_000_000,
                     'ageSec': age, 'stale': stale, 'rateLimited': None, 'rateLimitTier': None}
        windows = {'five_hour': {'utilization': 5.0, 'resets_at': None, 'remaining': None}}
        with mock.patch.object(engine.live, 'fetch',
                               return_value={**live_meta, 'windows': windows}):
            engine._overlay_live(limits, 1_000_000)
        spend = {'currency': 'USD', 'today': 0.0, 'yesterday': 0.0, 'total': 0.0, 'byModel': [],
                 'priceComplete': True, 'estimatedModels': [], 'unknownPriceModels': []}
        with mock.patch.object(engine, 'compute_usage', return_value=limits), \
                mock.patch.object(engine, 'compute_spend', return_value=spend), \
                mock.patch.object(engine, 'compute_providers', return_value=[]):
            return engine.usage_wire()

    def test_a_stale_overlay_marks_the_bar_it_wrote(self):
        bar = self._wire(stale=True, age=604800.0)['providers'][0]['limits']['session']
        self.assertIs(bar['live'], True)
        self.assertIs(bar['stale'], True)

    def test_a_fresh_overlay_is_not_marked_stale(self):
        bar = self._wire(stale=False, age=12.0)['providers'][0]['limits']['session']
        self.assertIs(bar['live'], True)
        self.assertIs(bar['stale'], False)


class DemoLooksLikeProduction(_QuietWire):
    """USAGE_DEMO=1 swaps in a whole separate assembler (usage/demo.py). Anything the demo
    does not produce is a field the demo cannot exercise — including in screenshots and in
    a first-run onboarding, which is precisely when a user judges the product."""

    def test_demo_has_every_top_level_field_production_has(self):
        prod = set(engine.usage_wire())
        fake = set(demo.usage_wire())

        self.assertFalse(prod - fake,
                         f'demo wire is missing production fields: {sorted(prod - fake)}')

    def test_demo_claude_card_has_every_production_field(self):
        """Every field, at every depth — not just the section names.

        This comparison used to stop at `limits` and `spend`, one level above where it
        mattered. `forecast` lived inside a limit bar, production stopped publishing it, and
        because the golden snapshot in test_wire_schema.py is generated from the demo, the
        field stayed pinned and green in a wire no real user ever received. Anything the
        demo cannot show is a field no test can see.
        """
        missing = _missing_fields(engine.usage_wire()['providers'][0],
                                  demo.usage_wire()['providers'][0])
        self.assertFalse(missing, 'the demo card cannot exercise these production fields:\n  '
                                  + '\n  '.join(missing))


if __name__ == '__main__':
    unittest.main()

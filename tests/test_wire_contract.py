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
]


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
        prod = engine.usage_wire()['providers'][0]
        fake = demo.usage_wire()['providers'][0]

        self.assertFalse(set(prod) - set(fake),
                         f'demo claude card is missing: {sorted(set(prod) - set(fake))}')
        for section in ('limits', 'spend'):
            missing = set(prod.get(section, {})) - set(fake.get(section, {}))
            self.assertFalse(missing, f'demo claude.{section} is missing: {sorted(missing)}')


if __name__ == '__main__':
    unittest.main()

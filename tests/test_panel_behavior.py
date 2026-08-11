"""The panel's provider filter — run, not grepped.

`test_surface_consistency.py` asserts that the search box, the tabs and the long-tail
group exist in the source. That is a useful lock but it cannot answer the only question
that matters: does a card end up in the right place?

It did not. When these tests were first written the "API" tab included Codex and Aider,
which read token counts from local log files and need no key at all. A user whose Codex
card was missing would have gone looking for an API key that does not exist. The static
tests were green throughout.

There is no browser here and no test framework for the panel, but `filterProviders` is a
pure function and Node is on every GitHub runner. So the real function is extracted from
web/app.js and executed against the real demo wire — no hand-written fixture, because a
fixture only proves we can parse what we invented (see tests/test_wire_contract.py for
the same lesson learned the hard way).

Skipped when Node is absent; the suite itself still has zero dependencies.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_JS = REPO / 'web' / 'app.js'
NODE = shutil.which('node')

# Extract the pure functions and answer one question per call, so a failure names itself.
HARNESS = r'''
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
for (const n of ['categorizeProvider', 'matchesSearchQuery', 'filterProviders']) {
  const m = src.match(new RegExp('function ' + n + '\\([\\s\\S]*?\\n}', 'm'));
  if (!m) { console.error('MISSING_FUNCTION:' + n); process.exit(2); }
  eval(m[0]);
}
const cards = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const r = filterProviders(cards, process.argv[4], process.argv[5] || '');
console.log(JSON.stringify({
  main: r.main.map(c => c.id).sort(),
  longtrail: r.longtrail.map(c => c.id).sort(),
}));
'''


@unittest.skipUnless(NODE, 'node not installed — panel behaviour cannot be executed here')
class ProviderFilter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from usage import demo
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.harness = tmp / 'harness.js'
        cls.harness.write_text(HARNESS, encoding='utf-8')
        # The real demo assembler, not a fixture: it produces every card kind on every
        # machine, so this asserts against output the product actually emits.
        cards = [p for p in demo.usage_wire()['providers'] if p.get('id') != 'claude']
        cls.cards_path = tmp / 'cards.json'
        cls.cards_path.write_text(json.dumps(cards), encoding='utf-8')
        cls.by_kind = {}
        for c in cards:
            cls.by_kind.setdefault(c.get('kind'), []).append(c['id'])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def filter(self, tab, query=''):
        out = subprocess.run(
            [NODE, str(self.harness), str(APP_JS), str(self.cards_path), tab, query],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0,
                         f'harness failed: {out.stderr.strip()}')
        return json.loads(out.stdout)

    # ── the tab answers "why is my card not here" ────────────────────────────
    def test_api_tab_holds_only_providers_that_need_a_key(self):
        shown = set(self.filter('api')['main']) | set(self.filter('api')['longtrail'])
        expected = set(self.by_kind.get('spend', []) + self.by_kind.get('quota', []))
        self.assertEqual(shown, expected)

    def test_local_tab_holds_everything_read_from_disk(self):
        """'tokens' cards (Codex, Aider, Continue, Cody, Windsurf) parse local log files.
        Filing them under API sends a user hunting for a key that does not exist."""
        shown = set(self.filter('local')['main']) | set(self.filter('local')['longtrail'])
        expected = set(self.by_kind.get('local', []) + self.by_kind.get('tokens', []))
        self.assertEqual(shown, expected)

    def test_the_two_tabs_partition_everything(self):
        api = set(self.filter('api')['main']) | set(self.filter('api')['longtrail'])
        local = set(self.filter('local')['main']) | set(self.filter('local')['longtrail'])
        every = set(self.filter('all')['main']) | set(self.filter('all')['longtrail'])

        self.assertEqual(api & local, set(), 'a card appears under both tabs')
        self.assertEqual(api | local, every, 'a card is reachable from neither tab')

    # ── the long tail ────────────────────────────────────────────────────────
    def test_nodata_and_offline_are_collapsed_away(self):
        r = self.filter('all')
        for card_id in r['longtrail']:
            self.assertIn(card_id, [c['id'] for c in self._cards()
                                    if c.get('status') in ('nodata', 'offline')])
        for card_id in r['main']:
            self.assertNotIn(card_id, [c['id'] for c in self._cards()
                                       if c.get('status') in ('nodata', 'offline')])

    def test_a_truncated_card_is_not_buried(self):
        """'partial' means the numbers are short, not that the provider is idle. Hiding it
        in a collapsed group is how a warning goes unread."""
        cards = self._cards() + [{'id': 'trunc', 'name': 'Trunc', 'kind': 'tokens',
                                  'status': 'partial'}]
        path = Path(self._tmp.name) / 'with_partial.json'
        path.write_text(json.dumps(cards), encoding='utf-8')
        out = subprocess.run([NODE, str(self.harness), str(APP_JS), str(path), 'all', ''],
                             capture_output=True, text=True, timeout=30)
        r = json.loads(out.stdout)
        self.assertIn('trunc', r['main'])
        self.assertNotIn('trunc', r['longtrail'])

    def test_active_tab_keeps_partial_and_drops_the_rest(self):
        r = self.filter('active')
        live = {c['id'] for c in self._cards() if c.get('status') in ('ok', 'partial')}
        self.assertEqual(set(r['main']), live)
        self.assertEqual(r['longtrail'], [])

    # ── search ───────────────────────────────────────────────────────────────
    def test_search_matches_on_id_and_name(self):
        r = self.filter('all', 'open')
        found = set(r['main']) | set(r['longtrail'])
        self.assertTrue(found, 'searching "open" found nothing at all')
        for card_id in found:
            self.assertIn('open', card_id.lower())

    def test_a_search_with_no_hits_returns_nothing_rather_than_everything(self):
        r = self.filter('all', 'zzzz-no-such-provider')
        self.assertEqual(r['main'], [])
        self.assertEqual(r['longtrail'], [])

    def test_search_and_tab_apply_together(self):
        r = self.filter('local', 'open')
        found = set(r['main']) | set(r['longtrail'])
        on_disk = set(self.by_kind.get('local', []) + self.by_kind.get('tokens', []))
        self.assertTrue(found <= on_disk, 'the tab was ignored once a search was typed')

    def _cards(self):
        return json.loads(self.cards_path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

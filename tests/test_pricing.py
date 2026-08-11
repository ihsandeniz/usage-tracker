"""Pricing — where the dollar figures come from.

Covers B1, found on 2026-08-11: the price catalogue was read from
~/.hermes/models_dev_cache.json, a file produced by a *different* tool (Hermes CLI).
Measured on this machine: with that file present the catalogue held 2731 models across
178 providers; with it absent, 0 and 0 — and the failure was swallowed by a bare
`except Exception`. On any machine without Hermes, which is every user's machine, the
product's core promise (show me what I spent) quietly returned $0.00 and looked fine.

Also covers the dated override: an intro price with a known end date used to live in a
free-text `_note` that a human had to remember to act on.
"""
import gzip
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from usage import catalog, pricing


class _IsolatedCatalog(unittest.TestCase):
    """Point every catalogue source at a temp dir so the suite never depends on
    whether this machine happens to have Hermes installed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved = (catalog.HERMES_CACHE, catalog.BUNDLED_PATH,
                       catalog._USER_CACHE_OVERRIDE, pricing.OVERRIDES_PATH)
        catalog.HERMES_CACHE = self.tmp / 'no-hermes-here.json'
        catalog.BUNDLED_PATH = self.tmp / 'bundle.json.gz'
        catalog._USER_CACHE_OVERRIDE = self.tmp / 'no-user-cache.json'
        pricing.OVERRIDES_PATH = self.tmp / 'price_overrides.json'
        catalog.invalidate()
        pricing.invalidate()
        self.addCleanup(self._restore)

    def _restore(self):
        (catalog.HERMES_CACHE, catalog.BUNDLED_PATH,
         catalog._USER_CACHE_OVERRIDE, pricing.OVERRIDES_PATH) = self._saved
        catalog.invalidate()
        pricing.invalidate()
        self._tmp.cleanup()

    def write_bundle(self, prices, generated_at='2026-08-11'):
        payload = {'_meta': {'generatedAt': generated_at, 'source': 'https://models.dev/api.json'},
                   'providers': prices}
        with gzip.open(catalog.BUNDLED_PATH, 'wt', encoding='utf-8') as fh:
            json.dump(payload, fh)

    def write_hermes(self, prices):
        raw = {prov: {'models': {mid: {'cost': cost} for mid, cost in models.items()}}
               for prov, models in prices.items()}
        catalog.HERMES_CACHE.write_text(json.dumps(raw), encoding='utf-8')

    def write_overrides(self, obj):
        pricing.OVERRIDES_PATH.write_text(json.dumps(obj), encoding='utf-8')


class WorksWithoutHermes(_IsolatedCatalog):
    """B1 — the tool must price a model on a machine that has never heard of Hermes."""

    def test_prices_resolve_from_the_bundled_snapshot(self):
        self.write_bundle({'anthropic': {'claude-opus-4-8': {
            'input': 5.0, 'output': 25.0, 'cache_read': 0.5, 'cache_write': 6.25}}})

        price, source = pricing.resolve_price('claude-opus-4-8')

        self.assertEqual(source, 'catalog',
                         'no Hermes on this machine and the price came back unpriced')
        self.assertEqual(price['input'], 5.0)
        self.assertEqual(price['output'], 25.0)

    def test_a_real_turn_costs_more_than_zero_without_hermes(self):
        self.write_bundle({'anthropic': {'claude-opus-4-8': {
            'input': 5.0, 'output': 25.0, 'cache_read': 0.5, 'cache_write': 6.25}}})

        usd, source = pricing.turn_cost_usd(1_000_000, 1_000_000, 0, 0, 'claude-opus-4-8')

        self.assertEqual(source, 'catalog')
        self.assertAlmostEqual(usd, 30.0, places=6,
                               msg='a million in and a million out billed as $0 — B1 is back')

    def test_hermes_still_wins_when_it_is_there(self):
        """Hermes refreshes daily; the bundle is a frozen snapshot. Live data must win."""
        self.write_bundle({'anthropic': {'claude-opus-4-8': {'input': 5.0, 'output': 25.0}}})
        self.write_hermes({'anthropic': {'claude-opus-4-8': {'input': 7.0, 'output': 35.0}}})

        price, _ = pricing.resolve_price('claude-opus-4-8')
        self.assertEqual(price['input'], 7.0, 'the stale bundle shadowed live Hermes data')


class CatalogStatusIsVisible(_IsolatedCatalog):
    """B1 — 'sessiz $0 yasak'. If the catalogue is degraded, it has to say so out loud."""

    def test_status_names_the_source_actually_used(self):
        self.write_bundle({'anthropic': {'claude-opus-4-8': {'input': 5.0, 'output': 25.0}}})

        st = catalog.status()
        self.assertEqual(st['source'], 'bundled')
        self.assertEqual(st['modelCount'], 1)
        self.assertGreaterEqual(st['providerCount'], 1)

    def test_an_empty_catalog_is_reported_not_swallowed(self):
        # nothing written anywhere: no hermes, no user cache, no bundle
        st = catalog.status()

        self.assertEqual(st['source'], 'none')
        self.assertEqual(st['modelCount'], 0)
        self.assertTrue(st['warning'], 'the catalogue is empty and nothing warns about it')

    def test_an_old_bundle_is_flagged_stale(self):
        self.write_bundle({'anthropic': {'x': {'input': 1.0, 'output': 1.0}}},
                          generated_at='2020-01-01')

        st = catalog.status()
        self.assertTrue(st['stale'])
        self.assertGreater(st['ageDays'], 365)
        self.assertTrue(st['warning'])

    def test_a_fresh_hermes_catalog_raises_no_warning(self):
        self.write_hermes({'anthropic': {'claude-opus-4-8': {'input': 5.0, 'output': 25.0}}})

        st = catalog.status()
        self.assertEqual(st['source'], 'hermes')
        self.assertFalse(st['stale'])
        self.assertFalse(st['warning'])


class DatedOverrides(_IsolatedCatalog):
    """The Sonnet 5 intro price ends 2026-08-31. That date belonged in the data, not in a
    free-text note that a human has to remember to act on."""

    def setUp(self):
        super().setUp()
        self.write_bundle({})
        self.write_overrides({
            'claude-sonnet-5': {
                'input': 2, 'output': 10, 'cache_read': 0.2, 'cache_write': 2.5,
                'source': 'official',
                'until': '2026-08-31',
                'then': {'input': 3, 'output': 15, 'cache_read': 0.3, 'cache_write': 3.75,
                         'source': 'official'},
            }
        })

    def test_intro_price_applies_before_the_end_date(self):
        price, source = pricing.resolve_price('claude-sonnet-5', today=date(2026, 8, 11))
        self.assertEqual(price['input'], 2)
        self.assertEqual(source, 'official')

    def test_intro_price_applies_on_the_last_day(self):
        price, _ = pricing.resolve_price('claude-sonnet-5', today=date(2026, 8, 31))
        self.assertEqual(price['input'], 2, 'the end date must be inclusive')

    def test_the_scheduled_price_takes_over_by_itself(self):
        price, source = pricing.resolve_price('claude-sonnet-5', today=date(2026, 9, 1))
        self.assertEqual(price['input'], 3,
                         'the intro price outlived its end date — nobody edited the file')
        self.assertEqual(price['output'], 15)
        self.assertEqual(source, 'official')

    def test_an_override_without_a_schedule_is_untouched(self):
        self.write_overrides({'claude-fable-5': {'input': 10, 'output': 50, 'source': 'official'}})
        pricing.invalidate()

        price, source = pricing.resolve_price('claude-fable-5', today=date(2099, 1, 1))
        self.assertEqual(price['input'], 10)
        self.assertEqual(source, 'official')


class TheShippedBundleIsReal(unittest.TestCase):
    """Not isolated on purpose: this asserts the artefact actually committed to the repo."""

    def test_bundle_exists_and_holds_a_usable_catalog(self):
        self.assertTrue(catalog.BUNDLED_PATH.exists(),
                        f'{catalog.BUNDLED_PATH} is missing — a fresh clone cannot price anything')
        providers, meta = catalog._read_bundled()
        self.assertGreater(sum(len(m) for m in providers.values()), 1000)
        self.assertIn('generatedAt', meta)

    def test_the_anthropic_models_we_bill_are_in_it(self):
        providers, _ = catalog._read_bundled()
        anthropic = providers.get('anthropic', {})
        self.assertTrue(anthropic, 'no anthropic prices in the bundle')
        for m in ('claude-opus-4-5', 'claude-haiku-4-5'):
            self.assertIn(m, anthropic, f'{m} missing from the shipped catalogue')


class CatalogStatusReachesTheWire(unittest.TestCase):
    """B1 — 'sessiz $0 yasak'. The warning is useless if it stops at the module boundary;
    it has to travel out to whatever the user is actually looking at."""

    def setUp(self):
        from usage import engine
        self.engine = engine
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_dir = engine.CLAUDE_PROJECTS_DIR
        engine.CLAUDE_PROJECTS_DIR = Path(self._tmp.name) / 'no-projects'
        self.addCleanup(self._restore)

    def _restore(self):
        self.engine.CLAUDE_PROJECTS_DIR = self._saved_dir
        self._tmp.cleanup()

    def test_spend_carries_where_its_prices_came_from(self):
        spend = self.engine.compute_spend(1)

        self.assertIn('catalog', spend, 'the $ figures do not say where their prices came from')
        st = spend['catalog']
        self.assertIn(st['source'], ('user', 'hermes', 'bundled', 'none'))
        self.assertIn('modelCount', st)
        self.assertIn('warning', st)


if __name__ == '__main__':
    unittest.main()

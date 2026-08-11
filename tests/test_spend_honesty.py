"""Regression tests for honest spend totals and live-quota freshness metadata."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from usage import engine, live, pricing


def _write_turns(projects_dir: Path, turns):
    path = projects_dir / 'project' / 'session.jsonl'
    path.parent.mkdir(parents=True)
    rows = []
    for index, (model, usage) in enumerate(turns):
        rows.append({
            'timestamp': datetime.now().astimezone().isoformat(),
            'message': {'id': f'msg-{index}', 'model': model, 'usage': usage},
        })
    path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')


def _limits_with_live(live_meta):
    bar = {
        'pct': 10.0, 'units': 100, 'budget': 1000, 'calibSuspect': None,
        'resetAtMs': 2_000_000, 'resetInSec': 100,
    }
    return {
        'calibrated': True,
        'session': dict(bar),
        'weeklyAll': dict(bar),
        'weeklyModel': None,
        'thresholds': {'warn': 75, 'crit': 90},
        'live': live_meta,
    }


class UnknownPricesAreExplicit(unittest.TestCase):
    def setUp(self):
        engine._CACHE.clear()

    def tearDown(self):
        engine._CACHE.clear()

    def test_unknown_tokens_are_a_reported_floor_not_real_spend(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            projects = root / 'projects'
            _write_turns(projects, [
                ('priced-model', {'input_tokens': 1_000_000}),
                ('mystery-model', {'input_tokens': 300, 'output_tokens': 200}),
            ])

            def fake_cost(_inp, _out, _cc, _cr, model):
                # A non-zero sentinel proves engine excludes an unknown source
                # instead of relying on pricing.py's current zero placeholder.
                return (2.0, 'official') if model == 'priced-model' else (9.0, 'unknown')

            with mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', projects), \
                    mock.patch.object(pricing, 'turn_cost_usd', side_effect=fake_cost):
                spend = engine.compute_spend(30)

            self.assertEqual(spend['total'], 2.0)
            self.assertFalse(spend['priceComplete'])
            self.assertEqual(spend['estimatedModels'], [])
            self.assertEqual(
                spend['unknownPriceModels'],
                [{'model': 'mystery-model', 'tokens': 500}],
            )

            with mock.patch.object(engine, 'compute_usage', return_value=_limits_with_live({})), \
                    mock.patch.object(engine, 'compute_spend', return_value=spend), \
                    mock.patch.object(engine, 'compute_providers', return_value=[]):
                wire_spend = engine.usage_wire()['providers'][0]['spend']

            self.assertEqual(
                wire_spend['unknownPriceModels'],
                [{'model': 'mystery-model', 'tokens': 500}],
            )


class LiveFreshnessIsExplicit(unittest.TestCase):
    def setUp(self):
        self.old_cache = live._CACHE
        live._CACHE = None

    def tearDown(self):
        live._CACHE = self.old_cache

    def test_success_records_when_the_request_finished(self):
        response = {'ok': True, 'error': None, 'windows': {}}
        with mock.patch.object(live, '_read_token', return_value=('token', 0, None)), \
                mock.patch.object(live, '_fetch_raw', return_value=response), \
                mock.patch.object(live, '_save_disk_cache'), \
                mock.patch.object(live.time, 'time', side_effect=[1000.0, 1002.0]):
            result = live.fetch(force=True)

        self.assertEqual(result['fetchedAtMs'], 1_002_000)
        self.assertEqual(result['ageSec'], 0.0)
        self.assertIs(result['stale'], False)

    def test_cache_older_than_freshness_window_is_stale(self):
        fetched_at = 1000.0
        now = fetched_at + live.LIVE_FRESHNESS_SEC + 1.0
        live._CACHE = (fetched_at, {'ok': True, 'error': None, 'windows': {}})
        with mock.patch.object(live, '_read_token', return_value=('token', 0, None)), \
                mock.patch.object(live, '_fetch_raw', return_value={'ok': False, 'error': 'offline'}), \
                mock.patch.object(live.time, 'time', return_value=now):
            result = live.fetch(force=True)

        self.assertEqual(result['fetchedAtMs'], 1_000_000)
        self.assertEqual(result['ageSec'], live.LIVE_FRESHNESS_SEC + 1.0)
        self.assertIs(result['stale'], True)

    def test_v1_wire_forwards_live_age_metadata(self):
        live_meta = {
            'ok': True,
            'fetchedAtMs': 1_000_000,
            'ageSec': 601.0,
            'stale': True,
        }
        spend = {
            'currency': 'USD', 'today': 0.0, 'yesterday': 0.0, 'total': 0.0,
            'byModel': [], 'priceComplete': True, 'estimatedModels': [],
            'unknownPriceModels': [],
        }
        with mock.patch.object(engine, 'compute_usage', return_value=_limits_with_live(live_meta)), \
                mock.patch.object(engine, 'compute_spend', return_value=spend), \
                mock.patch.object(engine, 'compute_providers', return_value=[]):
            provider = engine.usage_wire()['providers'][0]

        self.assertEqual(provider['live']['fetchedAtMs'], 1_000_000)
        self.assertEqual(provider['live']['ageSec'], 601.0)
        self.assertIs(provider['live']['stale'], True)


if __name__ == '__main__':
    unittest.main()

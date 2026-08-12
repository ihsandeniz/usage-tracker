"""Regression tests for honest spend totals and live-quota freshness metadata."""
import json
import os
import tempfile
import time
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


class DayWindowsFollowTheCalendar(unittest.TestCase):
    """"Yesterday" is a date, not 86 400 000 milliseconds ago.

    The windows were built by subtracting a fixed 24 hours from local midnight. In a DST
    region the local day before a transition is 23 or 25 hours long, so "yesterday" started
    an hour off, one hour of spend landed in the wrong bucket, and the 30-day list either
    repeated a date or skipped one. Türkiye has been on a fixed UTC+3 since 2016 — this is a
    defect for everyone else who runs the published tool.
    """

    TZ = 'Europe/Berlin'          # DST ends 2026-10-25; that local day is 25 hours long

    def setUp(self):
        if not hasattr(time, 'tzset'):
            self.skipTest('no tzset on this platform')
        self._old_tz = os.environ.get('TZ')
        os.environ['TZ'] = self.TZ
        time.tzset()
        engine._CACHE.clear()

    def tearDown(self):
        if self._old_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = self._old_tz
        time.tzset()
        engine._CACHE.clear()

    def test_the_day_the_clocks_went_back_is_twenty_five_hours_long(self):
        now = datetime(2026, 10, 26, 12, 0)
        hours = (engine._day_start_ms(now) - engine._days_back_start_ms(now, 1)) / 3_600_000
        self.assertEqual(hours, 25.0, 'yesterday was measured as a fixed 24 hours')

    def test_a_thirty_day_window_still_begins_at_a_local_midnight(self):
        now = datetime(2026, 10, 26, 12, 0)
        start = engine._days_back_start_ms(now, 29)
        self.assertEqual(datetime.fromtimestamp(start / 1000).strftime('%Y-%m-%d %H:%M'),
                         '2026-09-27 00:00')

    def test_spend_from_the_hour_before_the_transition_is_not_counted_as_yesterday(self):
        """The test that actually discriminates: which bucket a turn falls into.

        With `today_start - 86 400 000`, "yesterday" began at 23:00 on 24 October — an hour
        that belongs to the 24th. A turn made in that hour was reported as yesterday's
        spend, and the 24th quietly lost it. The date list alone does not catch this: it
        stays plausible while every boundary sits an hour early.
        """
        frozen = datetime(2026, 10, 26, 12, 0)

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        turns = [
            # 25 Ekim'in İLK saati. Sabit 24 saatlik çıkarma "dün"ü 25 Ekim 01:00'de
            # başlatıyor — bu tur o pencerenin dışında kalıyor ve dünün toplamından
            # sessizce düşüyor (toplamda görünmeye devam ettiği için de fark edilmiyor).
            ('2026-10-25T00:30:00+02:00', 'first-hour-of-yesterday'),
            ('2026-10-25T12:00:00+01:00', 'yesterday'),   # 25 Ekim (CET, geçişten sonra)
            ('2026-10-26T09:00:00+01:00', 'today'),
        ]
        with tempfile.TemporaryDirectory() as td:
            projects = Path(td) / 'projects'
            path = projects / 'p' / 's.jsonl'
            path.parent.mkdir(parents=True)
            path.write_text('\n'.join(json.dumps({
                'timestamp': ts,
                'message': {'id': f'msg-{name}', 'model': 'm', 'usage': {'input_tokens': 1000}},
            }) for ts, name in turns) + '\n', encoding='utf-8')
            # `_scan_turns` skips files older than the window (a real optimisation). The
            # clock here is frozen into the future, so the file has to claim that age too —
            # otherwise this test measures the mtime filter instead of the day boundary.
            stamp = frozen.timestamp()
            os.utime(path, (stamp, stamp))

            with mock.patch.object(engine, 'CLAUDE_PROJECTS_DIR', projects), \
                    mock.patch.object(engine, 'datetime', _Frozen), \
                    mock.patch.object(pricing, 'turn_cost_usd',
                                      side_effect=lambda *a, **k: (1.0, 'official')):
                spend = engine.compute_spend(30)

        self.assertEqual(spend['today'], 1.0, 'today picked up a neighbouring day')
        self.assertEqual(spend['yesterday'], 2.0,
                         "the first hour of 25 October fell outside yesterday's window")
        self.assertEqual(spend['total'], 3.0)
        days = [row['day'] for row in spend['byDay']]
        self.assertEqual(len(set(days)), 30, f'a date appears twice: {days}')
        self.assertEqual((days[0], days[-1]), ('2026-09-27', '2026-10-26'))


if __name__ == '__main__':
    unittest.main()

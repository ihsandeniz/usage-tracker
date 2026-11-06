"""Codex adapter — the rollout-log scanner.

Covers Y4, found on 2026-08-11: the scan stops at 40 files and 20 000 lines per file and
said nothing about it. Two separate problems hide there:

  1. silence — a total that stopped early was presented as the user's usage
  2. arbitrariness — `rglob` yields files in filesystem order, so the 40 that survived
     were not the 40 most recent. Two runs on the same disk could disagree.
"""
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from usage import pricing
from usage.providers import codex


def _rollout(path: Path, model: str, events, mtime=None):
    """Write a rollout jsonl the parser understands, then set its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({'model': model})]
    for ts_ms, total_in, cached_in, total_out in events:
        lines.append(json.dumps({
            'timestamp': datetime.fromtimestamp(ts_ms / 1000).isoformat(),
            'type': 'event_msg',
            'payload': {'type': 'token_count', 'info': {'last_token_usage': {
                'input_tokens': total_in, 'cached_input_tokens': cached_in,
                'output_tokens': total_out}}},
        }))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))


class _IsolatedSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sessions = Path(self._tmp.name) / 'sessions'
        self.sessions.mkdir(parents=True)
        self._saved = (codex.SESSIONS_DIR, codex.AUTH_PATH, dict(codex._FILE_CACHE))
        codex.SESSIONS_DIR = self.sessions
        codex.AUTH_PATH = Path(self._tmp.name) / 'auth.json'
        codex._FILE_CACHE.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        codex.SESSIONS_DIR, codex.AUTH_PATH, cache = self._saved
        codex._FILE_CACHE.clear()
        codex._FILE_CACHE.update(cache)
        self._tmp.cleanup()


class TruncationIsReported(_IsolatedSessions):

    def test_file_ceiling_is_announced(self):
        now_ms = int(time.time() * 1000)
        for i in range(codex.MAX_FILES + 5):
            _rollout(self.sessions / f'rollout-{i}.jsonl', 'gpt-5.2-codex',
                     [(now_ms, 100, 0, 50)])

        card = codex.collect(30)

        self.assertTrue(card.get('truncated'),
                        'the scan stopped at the file ceiling and the card did not say so')
        self.assertEqual(card.get('truncatedReason'), 'files')
        self.assertEqual(card['status'], 'partial',
                         'an incomplete total must not be labelled ok')
        self.assertIn('⚠', card['note'])

    def test_a_normal_scan_is_not_flagged(self):
        now_ms = int(time.time() * 1000)
        _rollout(self.sessions / 'rollout-a.jsonl', 'gpt-5.2-codex', [(now_ms, 100, 0, 50)])

        card = codex.collect(30)

        self.assertFalse(card.get('truncated'))
        self.assertEqual(card['status'], 'ok')
        self.assertNotIn('⚠', card['note'])


class TruncationKeepsTheNewest(_IsolatedSessions):
    """If we can only read 40 files, they must be the 40 most recent — not whichever 40
    the filesystem happened to hand us first."""

    def test_the_newest_files_are_the_ones_kept(self):
        now = time.time()
        now_ms = int(now * 1000)
        # MAX_FILES old files worth 1 output token each, then 3 new ones worth 1000 each.
        # Written oldest-last on purpose so filesystem order cannot accidentally be right.
        for i in range(codex.MAX_FILES):
            _rollout(self.sessions / f'rollout-old-{i}.jsonl', 'gpt-5.2-codex',
                     [(now_ms - 3_600_000, 1, 0, 1)], mtime=now - 86_400 - i)
        for i in range(3):
            _rollout(self.sessions / f'rollout-new-{i}.jsonl', 'gpt-5.2-codex',
                     [(now_ms, 1, 0, 1000)], mtime=now - i)

        card = codex.collect(30)

        self.assertTrue(card.get('truncated'))
        self.assertGreaterEqual(
            card['tokens']['output'], 3000,
            'the three newest sessions were dropped in favour of arbitrary older ones')

    def test_ordering_is_stable_across_runs(self):
        now = time.time()
        now_ms = int(now * 1000)
        for i in range(codex.MAX_FILES + 6):
            _rollout(self.sessions / f'rollout-{i}.jsonl', 'gpt-5.2-codex',
                     [(now_ms, 1, 0, i + 1)], mtime=now - i)

        codex._FILE_CACHE.clear()
        first = codex.collect(30)['total']['tokens']
        codex._FILE_CACHE.clear()
        second = codex.collect(30)['total']['tokens']

        self.assertEqual(first, second, 'two runs over the same directory disagreed')


def _satir(ts_ms, inp, cached, out, total=None):
    info = {'last_token_usage': {'input_tokens': inp, 'cached_input_tokens': cached,
                                 'output_tokens': out}}
    if total is not None:
        info['total_token_usage'] = {'total_tokens': total}
    return json.dumps({'timestamp': datetime.fromtimestamp(ts_ms / 1000).isoformat(),
                       'type': 'event_msg',
                       'payload': {'type': 'token_count', 'info': info,
                                   'rate_limits': {'limit_id': 'codex', 'primary': {
                                       'used_percent': 10.0, 'window_minutes': 300,
                                       'resets_at': int(ts_ms / 1000) + 9000}}}})


def _yaz(path: Path, *satirlar, model='gpt-5.2-codex'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join([json.dumps({'model': model}), *satirlar]) + '\n', encoding='utf-8')


class ArchivedSessionsCount(_IsolatedSessions):
    """Measured 2026-11-05: 30-day Codex tokens were 47 % short because the archive
    directory was never read. Every test here pins one way of getting that wrong again."""

    def setUp(self):
        super().setUp()
        self.archive = self.sessions.parent / 'archived_sessions'
        self.now_ms = int(time.time() * 1000)

    def test_archived_tokens_are_counted(self):
        _yaz(self.sessions / 'rollout-a.jsonl', _satir(self.now_ms, 100, 0, 10, 110))
        _yaz(self.archive / 'rollout-b.jsonl', _satir(self.now_ms, 200, 0, 20, 220))
        card = codex.collect(30)
        self.assertEqual(card['total']['tokens'], 330)
        self.assertEqual(card['sessions'], 2)

    def test_same_session_in_both_directories_counts_once(self):
        for kok in (self.sessions, self.archive):
            _yaz(kok / 'rollout-a.jsonl', _satir(self.now_ms, 100, 0, 10, 110))
        card = codex.collect(30)
        self.assertEqual(card['total']['tokens'], 110)
        self.assertEqual(card['sessions'], 1)

    def test_same_session_without_totals_counts_once(self):
        for kok in (self.sessions, self.archive):
            _yaz(kok / 'rollout-a.jsonl', _satir(self.now_ms, 100, 0, 10))
        self.assertEqual(codex.collect(30)['total']['tokens'], 110)

    def test_distinct_sessions_without_totals_are_not_merged(self):
        """Identical usage at the same instant is two requests when no total proves otherwise."""
        _yaz(self.sessions / 'rollout-a.jsonl', _satir(self.now_ms, 100, 0, 10))
        _yaz(self.archive / 'rollout-b.jsonl', _satir(self.now_ms, 100, 0, 10))
        self.assertEqual(codex.collect(30)['total']['tokens'], 220)

    def test_fork_replay_with_a_new_timestamp_counts_once(self):
        _yaz(self.sessions / 'rollout-1-ust.jsonl', _satir(self.now_ms - 60_000, 100, 0, 10, 110))
        _yaz(self.archive / 'rollout-2-fork.jsonl',
             _satir(self.now_ms, 100, 0, 10, 110), _satir(self.now_ms, 50, 0, 5, 165))
        self.assertEqual(codex.collect(30)['total']['tokens'], 165)

    def test_replay_of_an_event_before_the_window_stays_out(self):
        """The original sits outside the window; only its copy is inside. Still not new usage."""
        eski = self.now_ms - 40 * 86_400_000
        _yaz(self.sessions / 'rollout-1-ust.jsonl', _satir(eski, 100, 0, 10, 110))
        _yaz(self.archive / 'rollout-2-fork.jsonl', _satir(self.now_ms, 100, 0, 10, 110))
        self.assertEqual(codex.collect(30)['total']['tokens'], 0)

    def test_repeated_event_inside_a_file_counts_once(self):
        _yaz(self.sessions / 'rollout-a.jsonl',
             _satir(self.now_ms - 2000, 100, 0, 10, 110),
             _satir(self.now_ms - 1000, 100, 0, 10, 110),
             _satir(self.now_ms, 50, 0, 5, 165))
        self.assertEqual(codex.collect(30)['total']['tokens'], 165)

    def test_unchanged_total_with_a_different_last_usage_is_not_new_spend(self):
        _yaz(self.sessions / 'rollout-a.jsonl',
             _satir(self.now_ms - 1000, 100, 0, 10, 110),
             _satir(self.now_ms, 999, 0, 99, 110))
        self.assertEqual(codex.collect(30)['total']['tokens'], 110)

    def test_a_month_of_files_fits_under_the_default_ceiling(self):
        self.assertGreaterEqual(codex.MAX_FILES, 66,
                                '66 active + archived files measured in 30 days (2026-11-06)')

    def test_archive_alone_does_not_open_a_card_without_sessions(self):
        self.sessions.rmdir()
        _yaz(self.archive / 'rollout-a.jsonl', _satir(self.now_ms, 100, 0, 10, 110))
        self.assertIsNone(codex.collect(30))

    def test_limit_history_reads_the_archive_once(self):
        _yaz(self.sessions / 'rollout-1-ust.jsonl', _satir(self.now_ms - 60_000, 100, 0, 10, 110))
        _yaz(self.archive / 'rollout-2-fork.jsonl',
             _satir(self.now_ms, 100, 0, 10, 110), _satir(self.now_ms, 50, 0, 5, 165))
        gozlem, olaylar = codex.limit_gecmisi('five_hour')
        self.assertEqual(sorted(o[2] for o in olaylar), [50, 100])
        asil = next(o for o in olaylar if o[2] == 100)
        self.assertAlmostEqual(asil[0], self.now_ms - 60_000, delta=2,
                               msg='the copy survived instead of the original')
        self.assertEqual(len(gozlem), 2, 'a replayed event carries a stale percent under a new timestamp')


class LongContextPrice(unittest.TestCase):
    """OpenAI bills the whole request at the long-context rate above 272K input tokens."""

    def _fiyat(self):
        return mock.patch.object(pricing, 'resolve_provider_price', return_value=({
            'input': 4.0, 'output': 20.0, 'cache_read': 0.4, 'cache_write': 5.0,
            'tiers': [{'input': 8.0, 'output': 30.0, 'cache_read': 0.8, 'cache_write': 10.0,
                       'over': 272000}]}, 'catalog'))

    def test_at_the_threshold_short_rate(self):
        with self._fiyat():
            usd, _ = pricing.codex_cost_usd(272_000, 0, 1_000_000, 'gpt-5.6-sol')
        self.assertAlmostEqual(usd, 272_000 * 4 / 1e6 + 20)

    def test_above_the_threshold_whole_request_long_rate(self):
        with self._fiyat():
            usd, _ = pricing.codex_cost_usd(300_000, 100_000, 1_000_000, 'gpt-5.6-sol')
        self.assertAlmostEqual(usd, (200_000 * 8 + 100_000 * 0.8) / 1e6 + 30)


if __name__ == '__main__':
    unittest.main()

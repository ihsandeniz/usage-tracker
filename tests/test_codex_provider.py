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


if __name__ == '__main__':
    unittest.main()

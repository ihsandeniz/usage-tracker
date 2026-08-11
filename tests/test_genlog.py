"""_genlog — the shared local-log scanner used by the detection-based adapters.

Covers two defects found on 2026-08-11:
  Y1  nested directories were scanned twice, doubling every token count
  Y4  hitting the file/line ceiling was silent, so a truncated total looked complete
"""
import json
import tempfile
import unittest
from pathlib import Path

from usage.providers import _genlog


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')


class NestedDirectories(unittest.TestCase):
    """Y1 — ~/.codeium and ~/.codeium/windsurf are both in windsurf._DIRS, and the inner
    one is *inside* the outer one. rglob over both visits every file twice."""

    def test_a_file_under_two_scanned_dirs_is_counted_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inner = root / 'windsurf'
            _write_jsonl(inner / 'log.jsonl',
                         [{'promptTokens': 100, 'generatedTokens': 50}])

            # exactly how windsurf.py calls it: inner dir first, then its parent
            scanned = _genlog.scan([inner, root])

            self.assertEqual(scanned['input'], 100,
                             'the same file was counted twice — nested dirs are not deduped')
            self.assertEqual(scanned['output'], 50)
            self.assertEqual(scanned['total'], 150)
            self.assertEqual(scanned['files'], 1, 'one file on disk must count as one file')

    def test_the_same_dir_passed_twice_is_scanned_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_jsonl(root / 'a.jsonl', [{'inputTokens': 7, 'outputTokens': 3}])

            self.assertEqual(_genlog.scan([root, root])['total'], 10)

    def test_distinct_trees_still_both_counted(self):
        """The dedup must not swing the other way and drop a genuinely separate dir."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_jsonl(root / 'one' / 'a.jsonl', [{'inputTokens': 5, 'outputTokens': 0}])
            _write_jsonl(root / 'two' / 'b.jsonl', [{'inputTokens': 6, 'outputTokens': 0}])

            scanned = _genlog.scan([root / 'one', root / 'two'])
            self.assertEqual(scanned['input'], 11)
            self.assertEqual(scanned['files'], 2)


class TruncationIsReported(unittest.TestCase):
    """Y4 — MAX_FILES / MAX_LINES cut the scan short and said nothing. A number that stopped
    early is not the user's usage; it has to announce itself."""

    def test_file_ceiling_sets_the_truncated_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(_genlog.MAX_FILES + 3):
                _write_jsonl(root / f'log{i}.jsonl', [{'inputTokens': 1, 'outputTokens': 0}])

            scanned = _genlog.scan([root])

            self.assertTrue(scanned.get('truncated'),
                            'scan stopped at the file ceiling without reporting it')
            self.assertEqual(scanned.get('truncatedReason'), 'files')

    def test_line_ceiling_sets_the_truncated_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_jsonl(root / 'big.jsonl',
                         [{'inputTokens': 1, 'outputTokens': 0}] * (_genlog.MAX_LINES + 5))

            scanned = _genlog.scan([root])

            self.assertTrue(scanned.get('truncated'),
                            'scan stopped at the line ceiling without reporting it')
            self.assertEqual(scanned.get('truncatedReason'), 'lines')

    def test_a_small_scan_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_jsonl(root / 'small.jsonl', [{'inputTokens': 1, 'outputTokens': 1}])

            self.assertFalse(_genlog.scan([root]).get('truncated'))

    def test_truncation_reaches_the_card_the_user_sees(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(_genlog.MAX_FILES + 3):
                _write_jsonl(root / f'log{i}.jsonl', [{'inputTokens': 1, 'outputTokens': 0}])

            card = _genlog.tokens_card('x', 'X', _genlog.scan([root]), 'note')

            self.assertTrue(card.get('truncated'), 'the card hides the truncation from the UI')
            self.assertIn('partial', (card.get('status') or ''),
                          'a truncated total must not be presented as a plain ok')


if __name__ == '__main__':
    unittest.main()

# tests

Stdlib `unittest`. **No test dependencies** — the project promises zero dependencies and the
test suite keeps that promise, so CI needs no install step and behaves identically on Linux
and Windows runners.

```bash
python3 -m unittest discover -s tests -t .      # from the repo root
python3 -m unittest tests.test_genlog -v        # one module
```

## Rules

1. **Hermetic.** A test must never read your real `~/.claude`, `~/.codeium`, `~/.hermes` or
   write outside a temp dir. Use `tempfile.TemporaryDirectory` and patch the module constant.
2. **Write the failing test first.** Every fix in this suite started as a test that reproduced
   the wrong number. A test added after the fix proves nothing about the bug.
3. **Assert the number, not the shape.** `assertEqual(total, 300)` catches a double count;
   `assertIn('total', card)` does not.

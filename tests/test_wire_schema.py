"""`/v1/usage` — the structural snapshot.

`test_wire_contract.py` asserts that the paths the surfaces read exist. This file is the
other half: it pins the *whole* shape, so a field that disappears or changes type fails here
even though nothing in this repo happened to read it. Outside consumers read this wire too,
and they are not represented in our test suite by anything else.

The snapshot records field names and types, never values — a fixture full of dollar amounts
and timestamps would fail on every run, get regenerated reflexively, and stop meaning
anything. Types are stable; numbers are not.

When a change here is intentional:
    python3 -m tests.test_wire_schema --update
and put the reason in the commit message. Deleting or retyping a field is a major-version
change; see docs/WIRE.md.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usage import demo, engine

GOLDEN = Path(__file__).resolve().parent / 'golden' / 'v1_usage.schema.json'


def shape_of(value):
    """A value's structure, with the values thrown away.

    Lists collapse to the merged shape of their items: two provider cards of the same kind
    must agree, and a list that is empty in this environment simply records nothing rather
    than pretending the element type is unknowable.
    """
    if isinstance(value, dict):
        return {k: shape_of(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        merged = {}
        scalars = set()
        for item in value:
            s = shape_of(item)
            if isinstance(s, dict):
                for k, v in s.items():
                    merged[k] = v if k not in merged else _widen(merged[k], v)
            else:
                scalars.add(s)
        if merged:
            return [dict(sorted(merged.items()))]
        return [sorted(scalars)[0]] if scalars else []
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, int):
        return 'number'
    if isinstance(value, float):
        return 'number'
    return 'string'


def _widen(a, b):
    """Merge two observed shapes for the same key. `null` never wins against a real type —
    a field that is null on this machine may be populated on another."""
    if a == b:
        return a
    if a == 'null':
        return b
    if b == 'null':
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = v if k not in out else _widen(out[k], v)
        return dict(sorted(out.items()))
    if isinstance(a, list) and isinstance(b, list):
        if not a:
            return b
        if not b:
            return a
        return [_widen(a[0], b[0])]
    if isinstance(a, list) or isinstance(b, list):
        # one side saw items and the other saw a scalar/null — keep the richer side
        return a if isinstance(a, list) else b
    return f'{a}|{b}'


def _diff(expected, actual, path='$'):
    """Human-readable differences, deepest-first, so the message names the field."""
    out = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in sorted(set(expected) - set(actual)):
            out.append(f'{path}.{k}  REMOVED (was {expected[k]!r})')
        for k in sorted(set(actual) - set(expected)):
            out.append(f'{path}.{k}  ADDED ({actual[k]!r}) — run --update if intended')
        for k in sorted(set(expected) & set(actual)):
            out += _diff(expected[k], actual[k], f'{path}.{k}')
    elif isinstance(expected, list) and isinstance(actual, list):
        if expected and actual:
            out += _diff(expected[0], actual[0], f'{path}[]')
        elif expected and not actual:
            out.append(f'{path}[]  became empty — the snapshot can no longer see this shape')
    elif expected != actual:
        out.append(f'{path}  {expected!r} → {actual!r}')
    return out


def _quiet_wire(fn):
    """The real assembler, with no transcripts and no network."""
    with tempfile.TemporaryDirectory() as td:
        saved = engine.CLAUDE_PROJECTS_DIR
        engine.CLAUDE_PROJECTS_DIR = Path(td) / 'no-projects'
        patch = mock.patch.object(
            engine.live, 'fetch',
            return_value={'ok': False, 'error': 'snapshot', 'cached': False,
                          'fetchedAtMs': None, 'ageSec': None, 'stale': False,
                          'rateLimited': False, 'rateLimitTier': None})
        patch.start()
        try:
            return fn()
        finally:
            patch.stop()
            engine.CLAUDE_PROJECTS_DIR = saved


def _split(wire):
    """Claude is pinned on its own; the adapter cards are pinned as one merged shape.

    Merging everything into a single `providers[]` shape would let a field vanish from the
    Claude card unnoticed, as long as some other card still carried a field by that name.
    Which cards exist depends on the machine — codex here, none on a CI runner — so the
    adapter shape has to stay a union.
    """
    providers = wire.get('providers') or []
    skeleton = {k: v for k, v in wire.items() if k != 'providers'}
    return {
        'wire': shape_of(skeleton),
        'claude': shape_of(providers[0]) if providers else {},
        'anyAdapterCard': shape_of(providers[1:]),
    }


def current_snapshot():
    """Taken from demo mode, deliberately.

    Production output depends on the machine: this laptop contributes codex and ollama
    cards, a CI runner contributes none, and a snapshot that changes with the hardware is a
    snapshot nobody can trust or reproduce. Demo mode synthesises every card kind on every
    machine, so it pins the same shape everywhere.

    That leaves one gap — a field production emits and demo does not would go unpinned. It
    is closed from the other side: `test_wire_contract.DemoLooksLikeProduction` fails if
    production grows a field demo lacks.
    """
    return _quiet_wire(lambda: _split(demo.usage_wire()))


class WireShapeIsPinned(unittest.TestCase):

    def test_the_wire_still_matches_the_recorded_shape(self):
        self.assertTrue(GOLDEN.exists(),
                        f'{GOLDEN} missing — run: python3 -m tests.test_wire_schema --update')
        expected = json.loads(GOLDEN.read_text(encoding='utf-8'))
        differences = _diff(expected, current_snapshot())
        self.assertFalse(differences, 'the wire format drifted:\n  ' + '\n  '.join(differences))

    def test_the_snapshot_would_notice_a_removal(self):
        """A snapshot test that cannot fail is decoration. This proves the comparison bites."""
        expected = json.loads(GOLDEN.read_text(encoding='utf-8'))
        mutilated = dict(expected)
        mutilated['wire'] = {k: v for k, v in expected['wire'].items() if k != 'thresholds'}
        self.assertTrue(_diff(expected, mutilated), 'removing a top-level field went unnoticed')

    def test_the_snapshot_would_notice_a_retype(self):
        expected = json.loads(GOLDEN.read_text(encoding='utf-8'))
        mutilated = dict(expected, wire=dict(expected['wire'], schema='number'))
        self.assertTrue(_diff(expected, mutilated), 'changing a field type went unnoticed')


def _update():
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(current_snapshot(), indent=2, sort_keys=True) + '\n',
                      encoding='utf-8')
    print(f'wrote {GOLDEN}')
    return 0


if __name__ == '__main__':
    if '--update' in sys.argv:
        raise SystemExit(_update())
    unittest.main()

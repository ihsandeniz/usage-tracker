#!/usr/bin/env python3
"""Editor for the provider-keys env file (~/.config/usage-tracker/env).

    env_edit.py set    --file PATH          NAME=VALUE lines on stdin
    env_edit.py delete --file PATH --name NAME

Why a file and not a heredoc inside setup.sh: `python3 - <<'PY'` feeds the
*program* through stdin, which leaves nothing for the program to read. Keys
have to arrive on stdin — never argv, which is world-readable in /proc and
lands in shell history — so the program has to live somewhere else.

`set` rewrites an existing NAME= line in place (so the commented template keeps
its shape and its ordering) and appends only genuinely new names. An empty
value comments the line out rather than deleting it, so the template still
tells you the name exists. The file is written with mode 600, always.

Prints the number of keys it acted on. Exit 0 even for zero — "nothing matched"
is an outcome the caller reports, not a crash.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

NAME_RE = re.compile(r'[A-Z][A-Z0-9_]{2,63}')


def read_pairs(stream) -> list[tuple[str, str]]:
    """Parse NAME=VALUE lines, dropping anything that isn't an env assignment."""
    pairs = []
    for line in stream:
        line = line.rstrip('\n').rstrip('\r')
        if not line.strip():
            continue
        name, sep, value = line.partition('=')
        if not sep or not NAME_RE.fullmatch(name.strip()):
            continue
        pairs.append((name.strip(), value))
    return pairs


def load(path: str) -> list[str]:
    """Existing lines, or [] if there is no file yet.

    Only a missing file is treated as empty. Any other read error propagates:
    swallowing it would mean rewriting the file from an empty list, which
    silently deletes every key the user already had.
    """
    try:
        return open(path, encoding='utf-8').read().splitlines()
    except FileNotFoundError:
        return []


def save(path: str, lines: list[str]) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    old = os.umask(0o077)               # never let it exist world-readable, even briefly
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines).rstrip('\n') + '\n')
    finally:
        os.umask(old)
    os.chmod(path, 0o600)


def apply_pairs(lines: list[str], pairs: list[tuple[str, str]]) -> tuple[int, int]:
    """Returns (handled, changed).

    They differ, and the difference matters: the template ships every known key
    as a commented `# NAME=` line, so deleting a key that was never set still
    *matches* a line. Counting matches made the wizard say "removed" about a key
    the user never had. `changed` counts lines whose text actually moved.
    """
    handled = changed = 0
    for name, value in pairs:
        pat = re.compile(rf'^\s*(#\s*)?{re.escape(name)}=')
        new = f'{name}={value}' if value else f'# {name}='
        for i, line in enumerate(lines):
            if pat.match(line):
                handled += 1
                if lines[i] != new:
                    lines[i] = new
                    changed += 1
                break
        else:
            if value:
                lines.append(new)
                handled += 1
                changed += 1
    return handled, changed


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('action', choices=['set', 'delete'])
    ap.add_argument('--file', required=True)
    ap.add_argument('--name', default='')
    a = ap.parse_args()

    if a.action == 'set':
        pairs = read_pairs(sys.stdin)
    else:
        if not NAME_RE.fullmatch(a.name):
            print('0')
            return 0
        pairs = [(a.name, '')]          # empty value → commented out, not gone

    if not pairs:
        print('0')
        return 2                    # nothing matched — different from "it broke"

    try:
        lines = load(a.file)
        handled, changed = apply_pairs(lines, pairs)
        if a.action == 'delete' and changed == 0:
            print('0')             # it was already absent — don't rewrite the file
            return 2
        save(a.file, lines)
        n = handled if a.action == 'set' else changed
    except OSError as e:
        # The caller must be able to tell "no valid names" from "the disk said
        # no" — reporting both as 0 sent users chasing the wrong problem.
        print(f'{e.strerror or e}: {a.file}', file=sys.stderr)
        print('0')
        return 1
    print(n)
    return 0


if __name__ == '__main__':
    sys.exit(main())

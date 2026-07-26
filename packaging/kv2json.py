#!/usr/bin/env python3
"""kv2json.py — turn `path<TAB>type<TAB>value` lines on stdin into one JSON object.

Why this exists: setup.sh has to emit machine-readable state for the graphical
wizard, and hand-rolling JSON in bash is how quoting bugs get shipped. The shell
collects flat key/value pairs; this does the escaping and the nesting. One
process per emit, not one per string.

    deps.python3    s   3.13
    deps.curl       b   true
    server.port     n   8770
    deps.browser    z
    keys.set        a   OPENROUTER_API_KEY      <- repeat the path to append

Types: s string · b bool · n number · z null · a append-string-to-array.
An unknown type is treated as a string, and a malformed number falls back to a
string too — the wizard showing "3.x" beats the wizard crashing.

Values are single-line by contract (callers collapse newlines); a value may
contain tabs, since only the first two tabs are treated as separators.
"""
import json
import sys


def set_path(root: dict, path: str, value, append: bool = False) -> None:
    """Walk dotted `path`, creating dicts, and place `value` at the leaf."""
    parts = path.split('.')
    node = root
    for p in parts[:-1]:
        nxt = node.get(p)
        if not isinstance(nxt, dict):       # last writer wins: a scalar in the
            nxt = {}                        # way of a deeper path is replaced
            node[p] = nxt
        node = nxt
    leaf = parts[-1]
    if append:
        arr = node.get(leaf)
        if not isinstance(arr, list):
            arr = []
            node[leaf] = arr
        arr.append(value)
    else:
        node[leaf] = value


def main() -> int:
    out: dict = {}
    for raw in sys.stdin:
        line = raw.rstrip('\n')
        if not line.strip():
            continue
        parts = line.split('\t', 2)
        if len(parts) < 2:
            continue
        path, kind = parts[0].strip(), parts[1].strip()
        val = parts[2] if len(parts) > 2 else ''
        if not path:
            continue
        if kind == 'b':
            set_path(out, path, val.strip().lower() in ('1', 'true', 'yes', 'y'))
        elif kind == 'n':
            try:
                num = float(val)
                set_path(out, path, int(num) if num.is_integer() else num)
            except ValueError:
                set_path(out, path, val)
        elif kind == 'z':
            set_path(out, path, None)
        elif kind == 'a':
            set_path(out, path, val, append=True)
        else:
            set_path(out, path, val)
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())

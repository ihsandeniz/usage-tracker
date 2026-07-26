#!/usr/bin/env python3
"""Surgical editor for waybar's JSONC config — adds/removes the usage badge.

Why not json.load() + json.dump()? Because that silently deletes every comment
and reflows the whole file. That file is the user's config, not ours. So we edit
the raw *text* (comments and formatting survive untouched) and only use parsing
to prove we didn't break anything: parse before, parse after, and refuse to
write unless the result is valid AND contains exactly what we intended.

Exit codes (the wizard branches on these):
  0  done — file changed
  1  unexpected error
  2  nothing to do (already installed / already absent) — file untouched
  3  cannot edit safely (unparseable, unusual layout, modules list elsewhere)
     → caller should fall back to "here's the snippet, paste it yourself"

Usage:
  waybar_edit.py add    --config PATH --exec PATH [--click PATH] [--list modules-right]
  waybar_edit.py remove --config PATH
  waybar_edit.py check  --config PATH
"""
from __future__ import annotations

import argparse
import json
import re
import sys

MODULE = "custom/usage"


# ── tokenizer ───────────────────────────────────────────────────────────────
# Just enough of a JSONC scanner to locate things by position. Depth is counted
# *after* an opening bracket, so the keys of a top-level object sit at depth 1.
def tokenize(text: str):
    toks = []
    i, n, depth = 0, len(text), 0
    while i < n:
        c = text[i]
        if c == "/" and text[i + 1 : i + 2] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and text[i + 1 : i + 2] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == "\\":
                    buf.append(text[j + 1 : j + 2])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            toks.append(("str", i, j + 1, "".join(buf), depth))
            i = j + 1
            continue
        if c in "{[":
            depth += 1
            toks.append(("open", i, i + 1, c, depth))
        elif c in "}]":
            toks.append(("close", i, i + 1, c, depth))
            depth -= 1
        elif c in ":,":
            toks.append((c, i, i + 1, c, depth))
        i += 1
    return toks


def strip_for_parse(text: str) -> str:
    """Comments → spaces (offsets preserved), trailing commas removed."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text[i + 1 : i + 2] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and text[i + 1 : i + 2] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = "\n" if text[k] == "\n" else " "
            i = j
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            i = j + 1
            continue
        i += 1
    return re.sub(r",(\s*[}\]])", r" \1", "".join(out))


def parse(text: str):
    return json.loads(strip_for_parse(text))


def bars(doc):
    """waybar allows a single bar object or an array of them."""
    if isinstance(doc, dict):
        return [doc]
    if isinstance(doc, list):
        return [b for b in doc if isinstance(b, dict)]
    return []


# ── locators ────────────────────────────────────────────────────────────────
def bar_open(toks):
    """Opening brace of the first bar object + the depth its keys live at."""
    root = next((t for t in toks if t[0] == "open"), None)
    if root is None:
        return None, None
    if root[3] == "{":
        return root, 1
    first = next((t for t in toks if t[0] == "open" and t[3] == "{" and t[4] == 2), None)
    return (first, 2) if first else (None, None)


def find_key(toks, key, depth):
    """A string token used as a key (followed by ':') at the given depth."""
    for idx, t in enumerate(toks):
        if t[0] == "str" and t[3] == key and t[4] == depth:
            nxt = next((x for x in toks[idx + 1 :] if x[0] in (":", ",", "open", "close", "str")), None)
            if nxt and nxt[0] == ":":
                return idx, t
    return None, None


def value_open(toks, key_idx):
    """The opening bracket of the value that follows a key token."""
    for t in toks[key_idx + 1 :]:
        if t[0] == "open":
            return t
        if t[0] in (",", "close"):
            return None
    return None


def match_close(toks, open_tok):
    for t in toks:
        if t[0] == "close" and t[1] > open_tok[1] and t[4] == open_tok[4]:
            return t
    return None


def detect_indent(text: str) -> str:
    for line in text.splitlines():
        m = re.match(r"^([ \t]+)\S", line)
        if m:
            return m.group(1)
    return "  "


# ── operations ──────────────────────────────────────────────────────────────
def op_add(text: str, exec_path: str, click_path: str, list_name: str):
    doc = parse(text)  # raises on garbage → caller maps to exit 3
    bar = bars(doc)
    if not bar:
        return None, "config has no bar object"
    if MODULE in bar[0]:
        return None, "already-present"

    toks = tokenize(text)
    b_open, key_depth = bar_open(toks)
    if b_open is None:
        return None, "cannot locate the bar object"

    # which modules-* list to join: caller's choice, else whichever exists
    candidates = [list_name] + [c for c in ("modules-right", "modules-left", "modules-center") if c != list_name]
    target = next((c for c in candidates if isinstance(bar[0].get(c), list)), None)
    if target is None:
        return None, "no modules-left/center/right list in this file (an include?)"

    k_idx, _ = find_key(toks, target, key_depth)
    if k_idx is None:
        return None, f'"{target}" lives in another file (include) — cannot edit here'
    arr = value_open(toks, k_idx)
    if arr is None or arr[3] != "[":
        return None, f'"{target}" is not an inline array here'

    ind = detect_indent(text)
    base = ind * key_depth
    block = (
        f'\n{base}"{MODULE}": {{\n'
        f'{base}{ind}"exec": {json.dumps(exec_path, ensure_ascii=False)},\n'
        f'{base}{ind}"return-type": "json",\n'
        f'{base}{ind}"interval": 30'
        + (f',\n{base}{ind}"on-click": {json.dumps(click_path, ensure_ascii=False)}'
           if click_path else "")
        + f"\n{base}}},"
    )

    # highest offset first, so earlier offsets stay valid
    ins = sorted(
        [(arr[2], f'"{MODULE}"' + (", " if bar[0][target] else "")), (b_open[2], block)],
        key=lambda x: -x[0],
    )
    out = text
    for pos, frag in ins:
        out = out[:pos] + frag + out[pos:]

    newdoc = parse(out)  # must still be valid JSONC
    nb = bars(newdoc)[0]
    if MODULE not in nb or MODULE not in nb.get(target, []):
        return None, "post-edit check failed"
    return out, target


def op_remove(text: str):
    doc = parse(text)
    bar = bars(doc)
    if not bar or MODULE not in bar[0]:
        return None, "already-absent"

    toks = tokenize(text)
    _, key_depth = bar_open(toks)
    cuts = []

    k_idx, k_tok = find_key(toks, MODULE, key_depth)
    if k_idx is None:
        return None, "cannot locate the module definition"
    v = value_open(toks, k_idx)
    if v is None:
        return None, "cannot locate the module body"
    end = match_close(toks, v)
    if end is None:
        return None, "unbalanced brackets"
    stop = end[2]
    tail = next((t for t in toks if t[0] == "," and t[1] >= stop), None)
    if tail and text[stop : tail[1]].strip() == "":
        stop = tail[2]
    start = k_tok[1]
    while start > 0 and text[start - 1] in " \t":
        start -= 1
    if start > 0 and text[start - 1] == "\n":
        start -= 1
    cuts.append((start, stop))

    # the "custom/usage" entry inside every modules-* list
    for idx, t in enumerate(toks):
        if t[0] == "str" and t[3] == MODULE and t[4] == key_depth + 1:
            s, e = t[1], t[2]
            comma = next((x for x in toks[idx + 1 :] if x[0] in (",", "close")), None)
            if comma and comma[0] == "," and text[e : comma[1]].strip() == "":
                e = comma[2]
            else:  # last element → eat the comma before it instead
                prev = [x for x in toks[:idx] if x[0] == "," and x[1] < s]
                if prev and text[prev[-1][2] : s].strip() == "":
                    s = prev[-1][1]
            while e < len(text) and text[e] in " \t":
                e += 1
            cuts.append((s, e))

    out = text
    for s, e in sorted(cuts, key=lambda x: -x[0]):
        out = out[:s] + out[e:]

    newdoc = parse(out)
    nb = bars(newdoc)
    if nb and MODULE in nb[0]:
        return None, "post-edit check failed"
    return out, "removed"


# ── cli ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("action", choices=["add", "remove", "check"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--exec", dest="exec_path", default="")
    ap.add_argument("--click", dest="click_path", default="")
    ap.add_argument("--list", dest="list_name", default="modules-right")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        text = open(a.config, encoding="utf-8").read()
    except OSError as e:
        print(f"cannot read {a.config}: {e}", file=sys.stderr)
        return 3

    try:
        doc = parse(text)
    except Exception as e:
        print(f"config is not valid JSONC ({e}) — refusing to touch it", file=sys.stderr)
        return 3

    if a.action == "check":
        b = bars(doc)
        print("present" if b and MODULE in b[0] else "absent")
        return 0 if (b and MODULE in b[0]) else 2

    try:
        if a.action == "add":
            if not a.exec_path:
                print("--exec is required", file=sys.stderr)
                return 1
            out, info = op_add(text, a.exec_path, a.click_path, a.list_name)
        else:
            out, info = op_remove(text)
    except Exception as e:
        print(f"edit failed ({e}) — file untouched", file=sys.stderr)
        return 3

    if out is None:
        if info in ("already-present", "already-absent"):
            print(info)
            return 2
        print(info, file=sys.stderr)
        return 3

    if a.dry_run:
        sys.stdout.write(out)
        return 0

    with open(a.config, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(info)
    return 0


if __name__ == "__main__":
    sys.exit(main())

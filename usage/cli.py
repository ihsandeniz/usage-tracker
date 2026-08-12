#!/usr/bin/env python3
"""
usage-tracker CLI — the command line surface (FAZ 4).

Why this exists, in one line: **a number you cannot act on is decoration.**

Until now every surface was something you look at — a panel, a bar badge, a tray icon. None
of them could be asked a question by another program. `guard` can: it answers with an exit
code, so a shell script can refuse to start an expensive job when the quota is nearly gone.

    usage-tracker guard --threshold 80 || echo "not now"

The second reason is portability. The feeders that keep the badge alive are Bash + jq +
curl, which is a Linux-shaped assumption sitting in the middle of a tool we want to ship on
Windows. Every one of those scripts is doing what this module does — fetch `/v1/usage`,
pick a number, colour it. `usage --format waybar` is the same feeder without the shell.

Rules it inherits from the rest of the codebase:

* **Thresholds come from the server** (`thresholds` at the top of the wire). A hardcoded
  75/90 that matches today diverges silently the day the config changes → docs/WIRE.md.
* **Half-up rounding**, like every other surface. 62.45 is 62.5 here too.
* **Unknown is not zero.** A missing percentage prints `—` and exits 3; it never reads as
  "plenty left", because that is the one wrong answer that costs the user something.
* **Currency is read, never assumed** — a CNY balance with a `$` in front of it is a lie.

Works with or without a running server: if `127.0.0.1:8770` does not answer, the numbers
are computed in-process. Naming a `--url` explicitly turns that fallback off — if you asked
a specific server, a locally computed number in its place would misreport where it came from.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

if __package__ in (None, ''):                     # `python3 usage/cli.py` de çalışsın
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from usage import engine, platform as _paths, viewconfig
else:
    from . import engine
    from . import platform as _paths
    from . import viewconfig


# ── contracts ─────────────────────────────────────────────────────────────────
# guard's exit codes. Scripts branch on these; they are as public as the wire format.
LEVEL_EXIT = {'ok': 0, 'warn': 1, 'crit': 2, 'unknown': 3}

# Wrong usage must NOT land inside that range — argparse's default of 2 would be read as
# "critical" by a script that only knows the guard contract. 64 = EX_USAGE (sysexits.h).
EXIT_USAGE = 64

# The single fallback allowed by docs/WIRE.md § Thresholds: used only when no server and no
# wire could be reached at all.
FALLBACK_THRESHOLDS = {'warn': 75, 'crit': 90}

DEFAULT_PORT = int(os.environ.get('USAGE_PORT', '8770'))
DEFAULT_BASE = os.environ.get('USAGE_URL', f'http://127.0.0.1:{DEFAULT_PORT}')

CURRENCY_SYMBOLS = {'USD': '$', 'EUR': '€', 'CNY': '¥', 'GBP': '£', 'TRY': '₺'}

# Provider → environment variable. Names only; doctor prints whether they are set and never
# what they contain (people paste diagnostics into public bug reports).
PROVIDER_ENV = {
    'openrouter': ('OPENROUTER_API_KEY',),
    'openai': ('OPENAI_ADMIN_KEY',),
    'deepseek': ('DEEPSEEK_API_KEY',),
    'elevenlabs': ('ELEVENLABS_API_KEY',),
    'together': ('TOGETHER_API_KEY',),
    'novita': ('NOVITA_API_KEY',),
    'deepinfra': ('DEEPINFRA_API_KEY',),
    'huggingface': ('HUGGINGFACE_API_KEY', 'HF_TOKEN'),
}


def version() -> str:
    """The one VERSION lives in server.py (CI checks the git tag against it)."""
    main_mod = sys.modules.get('__main__')
    v = getattr(main_mod, 'VERSION', None)        # frozen binary / `python server.py guard`
    if isinstance(v, str):
        return v
    try:
        import server                              # noqa: WPS433 — repo root on sys.path
        return server.VERSION
    except Exception:
        return 'unknown'


# ── formatting (shared with the other surfaces on purpose) ────────────────────
def round_half_up(value, decimals: int = 1):
    """floor(x * 10 + 0.5) / 10 — the rule every surface uses.

    Python's round() and jq's round() are banker's rounding and disagree with JavaScript at
    the midpoint: 62.45 rendered as 62.4 in waybar and 62.5 in the panel, for the same number.
    """
    if value is None:
        return None
    factor = 10 ** decimals
    return math.floor(value * factor + 0.5) / factor


def fmt_pct(pct) -> str:
    return '—' if pct is None else f'{round_half_up(pct)}%'


def fmt_money(value, currency='USD') -> str:
    if value is None:
        return '—'
    symbol = CURRENCY_SYMBOLS.get((currency or 'USD').upper())
    body = f'{value:,.2f}'
    return f'{symbol}{body}' if symbol else f'{(currency or "").upper()} {body}'.strip()


def fmt_duration(seconds) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ''
    if total <= 0:
        return ''
    days, hours, minutes = total // 86400, (total % 86400) // 3600, (total % 3600) // 60
    if days:
        return f'{days}d{hours}h'
    if hours:
        return f'{hours}h{minutes}m'
    return f'{minutes}m'


def fmt_count(value) -> str:
    try:
        return f'{int(value):,}'
    except (TypeError, ValueError):
        return '—'


# ── data ──────────────────────────────────────────────────────────────────────
def fetch_http(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get('providers'), list):
        raise ValueError('response is not a usage wire document')
    return payload


def load_wire(url=None, local=False, timeout=3.0, base=None, all_providers=False):
    """→ (wire | None, source) where source is 'server' | 'local' | 'unavailable'.

    Three ways in, in order of trust:
      * `--url`  → that server, and only that server. No silent substitution.
      * `--local`→ compute here; never touch the network for the wire.
      * neither  → try the default server, fall back to computing locally.
    """
    if local:
        return _local_wire(all_providers), 'local'

    if url:
        try:
            return fetch_http(url, timeout), 'server'
        except Exception:
            return None, 'unavailable'

    target = (base or DEFAULT_BASE).rstrip('/') + '/v1/usage' + ('?all=1' if all_providers else '')
    try:
        return fetch_http(target, timeout), 'server'
    except Exception:
        pass
    try:
        return _local_wire(all_providers), 'local'
    except Exception:
        return None, 'unavailable'


def _local_wire(all_providers=False) -> dict:
    wire = engine.usage_wire()
    return wire if all_providers else viewconfig.filter_wire(wire)


def thresholds_of(wire) -> dict:
    """docs/WIRE.md: the server owns warn/crit; the constant below is the last resort."""
    if isinstance(wire, dict):
        top = wire.get('thresholds')
        if isinstance(top, dict) and top.get('warn') is not None:
            return top
        for card in wire.get('providers') or []:
            nested = ((card or {}).get('limits') or {}).get('thresholds')
            if isinstance(nested, dict) and nested.get('warn') is not None:
                return nested
    return dict(FALLBACK_THRESHOLDS)


def card_of(wire, provider_id):
    for card in (wire or {}).get('providers') or []:
        if isinstance(card, dict) and card.get('id') == provider_id:
            return card
    return None


def scopes_of(wire, provider='claude') -> list:
    """Every measurable percentage in the wire, flattened.

    A "scope" is one wall you can hit: `claude/session`, `claude/weekly`,
    `openrouter/limit`, `elevenlabs/quota`. The worst of them is what guard reports —
    the first wall you hit is the one that stops you.
    """
    out = []
    for card in (wire or {}).get('providers') or []:
        if not isinstance(card, dict):
            continue
        cid = card.get('id')
        if provider not in ('all', None) and cid != provider:
            continue
        name = card.get('name') or cid

        if cid == 'claude':
            limits = card.get('limits') or {}
            for key in ('session', 'weekly', 'weeklyModel'):
                bar = limits.get(key)
                if isinstance(bar, dict):
                    out.append({'provider': cid, 'providerName': name, 'scope': f'{cid}/{key}',
                                'pct': bar.get('pct'), 'resetInSec': bar.get('resetInSec'),
                                'calibSuspect': bar.get('calibSuspect')})
            continue

        for key in ('limit', 'quota'):
            bar = card.get(key)
            if isinstance(bar, dict) and bar.get('pct') is not None:
                out.append({'provider': cid, 'providerName': name, 'scope': f'{cid}/{key}',
                            'pct': bar.get('pct'), 'resetInSec': bar.get('resetInSec'),
                            'calibSuspect': False})
    return out


def level_for(pct, warn, crit) -> str:
    if pct is None:
        return 'unknown'
    if crit is not None and pct >= crit:
        return 'crit'
    if warn is not None and pct >= warn:
        return 'warn'
    return 'ok'


def evaluate(wire, provider='claude', threshold=None, warn=None, crit=None) -> dict:
    """Pick the worst scope and say what it means. The heart of guard and watch."""
    th = thresholds_of(wire)
    if threshold is not None:
        # "tell me when I pass 80" — one boundary, no second tier. Turning 80 into `crit`
        # instead would answer a question the user did not ask (exit 2 vs exit 1).
        eff_warn, eff_crit = float(threshold), None
    else:
        eff_warn = float(warn) if warn is not None else th.get('warn')
        eff_crit = float(crit) if crit is not None else th.get('crit')

    scopes = scopes_of(wire, provider)
    known = [s for s in scopes if s.get('pct') is not None]
    worst = max(known, key=lambda s: s['pct']) if known else None

    pct = worst['pct'] if worst else None
    level = level_for(pct, eff_warn, eff_crit)
    crossed = eff_crit if level == 'crit' else eff_warn if level == 'warn' else None

    if worst is None:
        message = (f'no usable percentage for "{provider}"' if scopes or provider != 'claude'
                   else 'no usable percentage (server unreachable or nothing measured yet)')
    else:
        message = f'{worst["scope"]} at {fmt_pct(pct)}'
        if crossed is not None:
            message += f' (over {round_half_up(crossed)})'

    return {
        'level': level,
        'exitCode': LEVEL_EXIT[level],
        'pct': pct,
        'scope': worst['scope'] if worst else None,
        'provider': provider,
        'resetInSec': worst.get('resetInSec') if worst else None,
        'thresholds': ({'warn': eff_warn, 'crit': eff_crit} if threshold is not None else th),
        'crossed': crossed,
        'message': message,
        'scopes': scopes,
    }


# ── command: usage ────────────────────────────────────────────────────────────
def render_usage_text(wire, source, provider=None) -> str:
    lines = [f'usage-tracker {version()} · source: {source}']
    th = thresholds_of(wire)
    cards = [c for c in (wire.get('providers') or []) if isinstance(c, dict)]
    if provider and provider != 'all':
        cards = [c for c in cards if c.get('id') == provider]
    if not cards:
        lines.append(f'  no card for "{provider}"')
        return '\n'.join(lines)

    for card in cards:
        lines.append('')
        if card.get('id') == 'claude':
            lines.extend(_render_claude(card, th))
        else:
            lines.extend(_render_adapter(card))
    return '\n'.join(lines)


def _render_claude(card, thresholds) -> list:
    limits = card.get('limits') or {}
    live = card.get('live') or {}
    freshness = 'live' if live.get('ok') and not live.get('stale') else (
        'stale' if live.get('ok') else 'calibrated' if card.get('calibrated') else 'estimated')
    out = [f'{card.get("name", "Claude Code")}  [{freshness}]']

    labels = {'session': 'session', 'weekly': 'weekly', 'weeklyModel': 'weekly model'}
    for key, label in labels.items():
        bar = limits.get(key)
        if not isinstance(bar, dict):
            continue
        pct = bar.get('pct')
        mark = {'crit': '!!', 'warn': ' !', 'ok': '  ', 'unknown': ' ?'}[
            level_for(pct, thresholds.get('warn'), thresholds.get('crit'))]
        reset = fmt_duration(bar.get('resetInSec'))
        suffix = f'   resets in {reset}' if reset else ''
        name = bar.get('name')
        out.append(f'  {mark} {label:<13}{fmt_pct(pct):>7}{suffix}'
                   + (f'   [{name}]' if name else ''))

    spend = card.get('spend') or {}
    currency = spend.get('currency') or 'USD'
    out.append(f'     spend        today {fmt_money(spend.get("today"), currency)}'
               f' · yesterday {fmt_money(spend.get("yesterday"), currency)}'
               f' · 30d {fmt_money(spend.get("last30d"), currency)}')
    catalog = spend.get('catalog') or {}
    if catalog.get('sourceLabel'):
        out.append(f'     prices       {catalog.get("sourceLabel")}'
                   f' · {fmt_count(catalog.get("modelCount"))} models')
    # docs/WIRE.md: a surface that shows dollars MUST show this warning when it is set.
    if catalog.get('warning'):
        out.append(f'     ⚠ {catalog["warning"]}')
    unknown = spend.get('unknownPriceModels') or []
    if unknown:
        out.append(f'     ⚠ {len(unknown)} model(s) have no price — the totals above are a floor')
    return out


def _render_adapter(card) -> list:
    cid = card.get('id')
    kind = card.get('kind') or '—'
    status = card.get('status') or 'nodata'
    currency = card.get('currency')
    head = f'{card.get("name", cid)}  [{kind} · {status}]'
    out = [head]

    spend = card.get('spend') or {}
    if spend:
        parts = []
        if spend.get('today') is not None:
            parts.append(f'today {fmt_money(spend.get("today"), currency)}')
        if spend.get('month') is not None:
            parts.append(f'month {fmt_money(spend.get("month"), currency)}')
        if card.get('balance') is not None:
            parts.append(f'balance {fmt_money(card.get("balance"), currency)}')
        if parts:
            out.append('     ' + ' · '.join(parts))

    limit = card.get('limit')
    if isinstance(limit, dict) and limit.get('pct') is not None:
        out.append(f'     limit        {fmt_pct(limit.get("pct"))}'
                   f' of {fmt_money(limit.get("limit"), currency)}')

    quota = card.get('quota')
    if isinstance(quota, dict) and quota.get('pct') is not None:
        out.append(f'     quota        {fmt_pct(quota.get("pct"))}'
                   f' of {fmt_count(quota.get("limit"))}'
                   f' ({fmt_count(quota.get("remaining"))} left)')

    tokens = card.get('tokens') or {}
    total = card.get('total') or {}
    if tokens.get('total') is not None:
        line = f'     tokens       {fmt_count(tokens.get("total"))}'
        if total.get('usd') is not None:
            line += f' ≈ {fmt_money(total.get("usd"), currency)}'
        out.append(line)

    if card.get('error'):
        out.append(f'     ⚠ {card["error"]}')
    if card.get('note'):
        out.append(f'     {card["note"]}')
    return out


def render_waybar(wire, source, provider='claude') -> dict:
    """The Bash+jq feeder, without Bash and without jq — so Windows can have a badge too."""
    if wire is None:
        return {'text': '○ —', 'tooltip': f'usage-tracker offline ({DEFAULT_BASE})', 'class': 'off'}

    verdict = evaluate(wire, provider=provider)
    css = {'ok': 'ok', 'warn': 'warn', 'crit': 'crit', 'unknown': 'off'}[verdict['level']]
    tooltip = [render_usage_text(wire, source).replace('\n\n', '\n')]
    return {
        'text': f'◐ {fmt_pct(verdict["pct"])}',
        'tooltip': '\n'.join(tooltip),
        'class': css,
        'percentage': int(verdict['pct']) if verdict['pct'] is not None else 0,
    }


def cmd_usage(args) -> int:
    wire, source = _wire_for(args)
    if args.format == 'waybar':
        print(json.dumps(render_waybar(wire, source, args.provider or 'claude'),
                         ensure_ascii=False))
        return 0                                   # a feeder must never crash the bar
    if wire is None:
        _fail(f'no data (source: {source})')
        return LEVEL_EXIT['unknown']
    if args.format == 'json':
        print(json.dumps(wire, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    print(render_usage_text(wire, source, args.provider))
    return 0


# ── command: providers ────────────────────────────────────────────────────────
def cmd_providers(args) -> int:
    wire, source = _wire_for(args, all_providers=args.all)
    if wire is None:
        _fail(f'no data (source: {source})')
        return LEVEL_EXIT['unknown']

    rows = []
    for card in wire.get('providers') or []:
        if not isinstance(card, dict):
            continue
        rows.append({
            'id': card.get('id'),
            'name': card.get('name'),
            'kind': card.get('kind') or ('claude' if card.get('id') == 'claude' else None),
            'status': card.get('status') or ('ok' if card.get('id') == 'claude' else 'nodata'),
            'currency': card.get('currency'),
            'error': card.get('error'),
            'note': card.get('note'),
        })

    if args.format == 'json':
        print(json.dumps({'source': source, 'providers': rows}, ensure_ascii=False,
                         indent=2 if args.pretty else None))
        return 0

    print(f'usage-tracker {version()} · source: {source} · {len(rows)} card(s)')
    for row in rows:
        detail = row.get('error') or row.get('note') or ''
        print(f'  {row["id"]:<14}{str(row["kind"] or "—"):<9}{str(row["status"]):<9}{detail}')
    hidden = viewconfig.get_config().get('hidden_providers') or []
    if hidden and not args.all:
        print(f'  ({len(hidden)} hidden by config: {", ".join(hidden)} — see `config --show`)')
    return 0


# ── command: guard ────────────────────────────────────────────────────────────
def cmd_guard(args) -> int:
    wire, source = _wire_for(args)
    if wire is None:
        verdict = {'level': 'unknown', 'exitCode': LEVEL_EXIT['unknown'], 'pct': None,
                   'scope': None, 'provider': args.provider or 'claude', 'resetInSec': None,
                   'thresholds': dict(FALLBACK_THRESHOLDS), 'crossed': None,
                   'message': f'no data (source: {source})', 'scopes': []}
    else:
        verdict = evaluate(wire, provider=args.provider or 'claude', threshold=args.threshold,
                           warn=args.warn, crit=args.crit)

    payload = {k: v for k, v in verdict.items() if k != 'scopes'}
    payload['source'] = source

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    elif not args.quiet:
        stream = sys.stdout if verdict['level'] == 'ok' else sys.stderr
        print(f'{verdict["level"]}: {verdict["message"]}', file=stream)
    return verdict['exitCode']


# ── command: watch ────────────────────────────────────────────────────────────
def _watch_state_path(args) -> Path:
    return Path(args.state_file) if args.state_file else _paths.state_dir() / 'watch-state.json'


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        # A corrupt state file must not stop the watcher; the worst case is one extra alert.
        return {}


def _write_state(path: Path, state: dict) -> None:
    try:
        _paths.atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))
    except OSError:
        pass                                        # unwritable state = noisier alerts, not a crash


def _notify(event) -> None:
    title = f'usage-tracker · {event["level"]}'
    body = event['message']
    sender = shutil.which('notify-send')
    if sender:
        urgency = 'critical' if event['level'] == 'crit' else 'normal'
        try:
            subprocess.run([sender, f'--urgency={urgency}', title, body], check=False)
            return
        except OSError:
            pass
    print(f'{title}: {body}')


def watch_tick(args, state_path: Path):
    """One poll. Returns the event when the level *changed*, else None.

    Edge-triggered on purpose: an alert that repeats every 60 s is an alert you learn to
    ignore, and the bash notifier this replaces already made that mistake once.
    """
    wire, source = _wire_for(args)
    if wire is None:
        return None                                  # unreachable ≠ recovered: leave state alone

    verdict = evaluate(wire, provider=args.provider or 'claude', threshold=args.threshold,
                       warn=args.warn, crit=args.crit)
    if verdict['level'] == 'unknown':
        return None                                  # no number is not a reason to wake anyone

    key = args.provider or 'claude'
    state = _read_state(state_path)
    levels = state.get('levels') if isinstance(state.get('levels'), dict) else {}
    previous = levels.get(key, 'ok')
    if verdict['level'] == previous:
        return None

    levels[key] = verdict['level']
    state['levels'] = levels
    state['updatedAt'] = datetime.now().isoformat(timespec='seconds')
    _write_state(state_path, state)

    return {
        'ts': state['updatedAt'],
        'level': verdict['level'],
        'previous': previous,
        'pct': verdict['pct'],
        'scope': verdict['scope'],
        'provider': key,
        'thresholds': verdict['thresholds'],
        'crossed': verdict['crossed'],
        'source': source,
        'message': verdict['message'],
    }


def _run_exec(command: str, event) -> None:
    """The user's own command. Data goes through the environment, never through string
    splicing — a percentage pasted into a shell line is an injection waiting to happen."""
    env = dict(os.environ)
    env.update({
        'UT_LEVEL': str(event['level']),
        'UT_PREVIOUS': str(event['previous']),
        'UT_PCT': '' if event['pct'] is None else str(event['pct']),
        'UT_SCOPE': str(event['scope'] or ''),
        'UT_PROVIDER': str(event['provider']),
        'UT_THRESHOLD': '' if event['crossed'] is None else str(event['crossed']),
        'UT_SOURCE': str(event['source']),
        'UT_MESSAGE': str(event['message']),
    })
    try:
        subprocess.run(command, shell=True, env=env, check=False)
    except OSError as exc:
        _fail(f'--exec failed: {exc}')


def cmd_watch(args) -> int:
    state_path = _watch_state_path(args)
    interval = max(5, int(args.interval))
    if not args.once and not args.json:
        print(f'watching every {interval}s (provider: {args.provider or "claude"}) — Ctrl+C to stop',
              file=sys.stderr)
    try:
        while True:
            event = watch_tick(args, state_path)
            if event:
                if args.json:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                else:
                    print(f'{event["ts"]}  {event["previous"]} → {event["level"]}  '
                          f'{event["message"]}', flush=True)
                if args.exec:
                    _run_exec(args.exec, event)
                if args.notify:
                    _notify(event)
            if args.once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


# ── command: doctor ───────────────────────────────────────────────────────────
def _check(checks, cid, level, title, detail, hint=None):
    checks.append({'id': cid, 'level': level, 'title': title, 'detail': detail, 'hint': hint})


def collect_doctor(args) -> dict:
    checks = []

    # 1. interpreter
    py = sys.version_info
    _check(checks, 'python', 'ok' if py >= (3, 9) else 'fail', 'Python',
           f'{py.major}.{py.minor}.{py.micro}' + (' · frozen build' if _paths.is_frozen() else ''),
           None if py >= (3, 9) else 'Python 3.9 or newer is required.')

    # 2. where the user's data lives, and whether we may write there
    paths = _paths.describe()
    unwritable = []
    for label in ('configDir', 'stateDir'):
        target = Path(paths[label])
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / '.doctor-write-test'
            probe.write_text('ok', encoding='utf-8')
            probe.unlink()
        except OSError as exc:
            unwritable.append(f'{label}: {exc.__class__.__name__}')
    _check(checks, 'paths', 'fail' if unwritable else 'ok', 'Paths',
           f'{paths["system"]} · config {paths["configDir"]} · state {paths["stateDir"]}'
           + (f' · NOT WRITABLE ({"; ".join(unwritable)})' if unwritable else ''),
           'Check the permissions on the directories above.' if unwritable else None)

    # 3. prices — a $0.00 with no catalogue behind it looks exactly like a real $0.00
    try:
        from . import catalog
        status = catalog.status()
        level = 'fail' if (status['source'] == 'none' or not status['modelCount']) else (
            'warn' if status['stale'] else 'ok')
        _check(checks, 'catalog', level, 'Price catalogue',
               f'{status["sourceLabel"]} · {fmt_count(status["modelCount"])} models'
               + (f' · {status["ageDays"]} days old' if status.get('ageDays') is not None else ''),
               status.get('warning'))
    except Exception as exc:
        _check(checks, 'catalog', 'fail', 'Price catalogue', f'{type(exc).__name__}: {exc}',
               'Run `python3 -m usage.catalog --update`.')

    # 4. the transcripts everything is measured from
    claude_dir = Path(getattr(engine, 'CLAUDE_PROJECTS_DIR', Path.home() / '.claude' / 'projects'))
    if claude_dir.exists():
        found = 0
        for _ in claude_dir.rglob('*.jsonl'):
            found += 1
            if found >= 500:
                break
        label = f'{claude_dir} · {found}{"+" if found >= 500 else ""} transcript file(s)'
        _check(checks, 'claude-data', 'ok' if found else 'warn', 'Claude data', label,
               None if found else 'No transcripts yet — every number will read as zero.')
    else:
        _check(checks, 'claude-data', 'warn', 'Claude data', f'{claude_dir} does not exist',
               'Claude Code has not written any transcripts on this machine.')

    # 5. the server (optional — the CLI works without it)
    target = args.url or (DEFAULT_BASE.rstrip('/') + '/v1/usage')
    try:
        payload = fetch_http(target, args.timeout)
        schema = payload.get('schema')
        _check(checks, 'server', 'ok' if schema == 'usage/v1' else 'warn', 'Server',
               f'{target} · schema {schema} · {len(payload.get("providers") or [])} card(s)',
               None if schema == 'usage/v1' else 'Unexpected schema — is that really usage-tracker?')
    except Exception as exc:
        _check(checks, 'server', 'warn', 'Server', f'{target} unreachable ({type(exc).__name__})',
               'Start it with `./service.sh start` — the CLI works without it, surfaces do not.')

    # 6. can we build the numbers here, and how long does it take
    started = time.time()
    try:
        if args.probe:
            wire = engine.usage_wire()
            cards = len(wire.get('providers') or [])
            detail = f'assembled in {time.time() - started:.2f}s · {cards} card(s) · providers probed'
        else:
            limits = engine.compute_usage()
            spend = engine.compute_spend(30)
            detail = (f'assembled in {time.time() - started:.2f}s · '
                      f'session {fmt_pct((limits.get("session") or {}).get("pct"))} · '
                      f'30d {fmt_money(spend.get("total"), spend.get("currency"))}'
                      f' · provider adapters not probed (use --probe)')
        _check(checks, 'wire', 'ok', 'Local computation', detail)
    except Exception as exc:
        _check(checks, 'wire', 'fail', 'Local computation', f'{type(exc).__name__}: {exc}',
               'This is the core engine failing — please open an issue with this line.')

    # 7. live Anthropic limits
    try:
        live = (engine.compute_usage() or {}).get('live') or {}
        if live.get('ok'):
            age = live.get('ageSec')
            level = 'warn' if live.get('stale') else 'ok'
            _check(checks, 'live', level, 'Live limits',
                   f'ok · {int(age)}s old' if age is not None else 'ok',
                   'The live figures are older than 10 minutes.' if level == 'warn' else None)
        else:
            reason = live.get('error') or live.get('staleReason') or 'no live data'
            _check(checks, 'live', 'warn', 'Live limits', str(reason),
                   'Percentages fall back to calibration, which can be wrong after a reset.')
    except Exception as exc:
        _check(checks, 'live', 'warn', 'Live limits', f'{type(exc).__name__}: {exc}')

    # 8. which provider keys exist — NAMES ONLY, never values
    configured, missing = [], []
    for provider, names in sorted(PROVIDER_ENV.items()):
        if any(os.environ.get(n) for n in names):
            configured.append(f'{provider} ({"/".join(names)})')
        else:
            missing.append(provider)
    _check(checks, 'provider-keys', 'ok', 'Provider keys',
           (f'set: {", ".join(configured)}' if configured else 'none set')
           + (f' · unset: {", ".join(missing)}' if missing else ''),
           'Values are never printed here — only whether the variable exists.')

    problems = sum(1 for c in checks if c['level'] == 'fail')
    warnings = sum(1 for c in checks if c['level'] == 'warn')
    return {'ok': problems == 0, 'problems': problems, 'warnings': warnings,
            'version': version(), 'checks': checks}


def cmd_doctor(args) -> int:
    report = collect_doctor(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        marks = {'ok': '✓', 'warn': '!', 'fail': '✗'}
        print(f'usage-tracker {report["version"]} · doctor')
        for check in report['checks']:
            print(f'  {marks[check["level"]]} {check["title"]:<18}{check["detail"]}')
            if check.get('hint') and check['level'] != 'ok':
                print(f'      → {check["hint"]}')
        print(f'\n{report["problems"]} problem(s), {report["warnings"]} warning(s)')
    return 0 if report['ok'] else 1


# ── command: config ───────────────────────────────────────────────────────────
def cmd_config(args) -> int:
    config = viewconfig.get_config()
    hidden = list(config.get('hidden_providers') or [])

    changed = False
    for provider in args.hide or []:
        if provider not in hidden:
            hidden.append(provider)
            changed = True
    for provider in args.show or []:
        if provider in hidden:
            hidden.remove(provider)
            changed = True
    if args.reset and hidden:
        hidden, changed = [], True

    if changed and not viewconfig.save_config({**config, 'hidden_providers': hidden}):
        _fail('could not save the configuration')
        return 1

    if args.json:
        print(json.dumps({'hidden': hidden, 'path': str(viewconfig.CONFIG_PATH)},
                         ensure_ascii=False))
        return 0

    print(f'config file: {viewconfig.CONFIG_PATH}')
    print(f'hidden providers: {", ".join(hidden) if hidden else "(none)"}')
    if not changed and not hidden:
        print('hide a card with `config --hide <id>`; `providers` lists the ids')
    return 0


# ── plumbing ──────────────────────────────────────────────────────────────────
def _wire_for(args, all_providers=False):
    return load_wire(url=getattr(args, 'url', None), local=getattr(args, 'local', False),
                     timeout=getattr(args, 'timeout', 3.0), all_providers=all_providers)


def _fail(message: str) -> None:
    print(f'usage-tracker: {message}', file=sys.stderr)


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a bad flag — and 2 already means 'critical' to guard's callers."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f'{self.prog}: error: {message}\n')


def _add_source_flags(parser):
    parser.add_argument('--url', help='ask this server only (no local fallback)')
    parser.add_argument('--local', action='store_true',
                        help='compute here; never ask a server')
    parser.add_argument('--timeout', type=float, default=3.0, help='server timeout (seconds)')


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog='usage-tracker',
                     description='AI usage, limits and real spend — from the command line.',
                     epilog='exit codes for `guard`: 0 ok · 1 warn · 2 critical · 3 unknown')
    parser.add_argument('--version', action='version', version=f'usage-tracker {version()}')
    subparsers = parser.add_subparsers(dest='command', metavar='command')

    p_usage = subparsers.add_parser('usage', help='show limits and spend')
    p_usage.add_argument('--format', choices=('text', 'json', 'waybar'), default='text')
    p_usage.add_argument('--provider', help='only this card (id), or "all"')
    p_usage.add_argument('--pretty', action='store_true', help='indent JSON output')
    _add_source_flags(p_usage)
    p_usage.set_defaults(func=cmd_usage)

    p_prov = subparsers.add_parser('providers', help='list provider cards and their status')
    p_prov.add_argument('--format', choices=('text', 'json'), default='text')
    p_prov.add_argument('--all', action='store_true', help='include cards hidden by config')
    p_prov.add_argument('--pretty', action='store_true', help='indent JSON output')
    _add_source_flags(p_prov)
    p_prov.set_defaults(func=cmd_providers)

    p_guard = subparsers.add_parser(
        'guard', help='exit 1/2 when usage crosses a threshold (for scripts)',
        description='Answers with an exit code so a script can decide: '
                    '0 = ok, 1 = warn, 2 = critical, 3 = no usable number.')
    p_guard.add_argument('--provider', default='claude', help='card id, or "all" (default: claude)')
    p_guard.add_argument('--threshold', type=float,
                         help='single boundary: exit 1 above it (disables the crit tier)')
    p_guard.add_argument('--warn', type=float, help='override the server warn threshold')
    p_guard.add_argument('--crit', type=float, help='override the server crit threshold')
    p_guard.add_argument('--json', action='store_true')
    p_guard.add_argument('--quiet', '-q', action='store_true', help='exit code only')
    _add_source_flags(p_guard)
    p_guard.set_defaults(func=cmd_guard)

    p_watch = subparsers.add_parser(
        'watch', help='poll and fire once per threshold crossing',
        description='Edge-triggered: fires when the level changes (ok → warn → crit and back), '
                    'not on every poll.')
    p_watch.add_argument('--interval', type=int, default=300, help='seconds between polls (default 300)')
    p_watch.add_argument('--provider', default='claude', help='card id, or "all"')
    p_watch.add_argument('--threshold', type=float, help='single boundary instead of warn/crit')
    p_watch.add_argument('--warn', type=float)
    p_watch.add_argument('--crit', type=float)
    p_watch.add_argument('--exec', help='shell command to run on a crossing (data in UT_* env vars)')
    p_watch.add_argument('--notify', action='store_true', help='send a desktop notification')
    p_watch.add_argument('--once', action='store_true', help='check once and exit (for cron/timers)')
    p_watch.add_argument('--json', action='store_true', help='print events as JSON lines')
    p_watch.add_argument('--state-file', help='where the last level is remembered')
    _add_source_flags(p_watch)
    p_watch.set_defaults(func=cmd_watch)

    p_doc = subparsers.add_parser('doctor', help='diagnose this installation')
    p_doc.add_argument('--json', action='store_true')
    p_doc.add_argument('--probe', action='store_true',
                       help='also contact provider APIs (network, uses your keys)')
    _add_source_flags(p_doc)
    p_doc.set_defaults(func=cmd_doctor)

    p_cfg = subparsers.add_parser('config', help='show or edit which cards are visible')
    p_cfg.add_argument('--hide', action='append', metavar='ID')
    p_cfg.add_argument('--show', action='append', metavar='ID')
    p_cfg.add_argument('--reset', action='store_true', help='make every card visible again')
    p_cfg.add_argument('--json', action='store_true')
    p_cfg.set_defaults(func=cmd_config)

    return parser


def _ensure_utf8_output() -> None:
    """`—`, `✓` and `¥` must not be able to kill the process.

    A Windows console still defaults to a legacy code page in plenty of setups, and there
    printing a single em dash raises UnicodeEncodeError — the tool would crash while doing
    nothing but reporting a number. Replacement characters are ugly; a traceback is worse.
    ⚠️ Windows'ta doğrulanmadı (makine yok) — CI yalnız test çıktısını görüyor.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main(argv=None) -> int:
    _ensure_utf8_output()
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not getattr(args, 'command', None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except BrokenPipeError:                          # `usage | head` is not an error
        return 0


if __name__ == '__main__':
    raise SystemExit(main())

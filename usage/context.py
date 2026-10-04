#!/usr/bin/env python3
"""
Per-session context window breakdown — the same view as Claude Code's `/context`
(Messages · Memory files · System tools · Skills · Custom agents …) for EVERY open session.

Two layers, because they cost very differently:
  total      — last assistant `usage` of the session transcript (input + cache_read +
               cache_creation). Cheap tail read, refreshed on every request.
  breakdown  — headless `/context` on a *fork* of the session:
                 claude -p --resume <sid> --fork-session --no-session-persistence \
                        --settings '{"disableAllHooks":true}' --output-format json "/context"
               ~1 s per session. `--fork-session` + `--no-session-persistence` together keep
               the source transcript untouched (md5 measured before/after); hooks are
               disabled because SessionStart hooks have side effects (rename panes, move files).
               Cached; re-measured only when the total moved ≥ REMEASURE_PCT of the window or
               the entry is older than REMEASURE_SEC. One background worker, one fork at a time.

Open sessions come from `~/.claude/sessions/<pid>.json` (pid, sessionId, cwd, name, status).

Honesty notes:
  - The `total_cost_usd` of the headless run is the resumed session's *past* spend, not a new
    charge — it is never read here.
  - `/context` shows a fixed ~33k "Autocompact buffer". With CLAUDE_AUTOCOMPACT_PCT_OVERRIDE
    the real compaction point is pct × window; we report that as `compact_at`.

READ-ONLY: never writes under ~/.claude.
"""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get('CLAUDE_CONFIG_DIR') or (Path.home() / '.claude'))
SESSIONS_DIR = CLAUDE_DIR / 'sessions'
PROJECTS_DIR = CLAUDE_DIR / 'projects'

REMEASURE_PCT = 2.0        # re-run /context when the total moved this many % of the window
REMEASURE_SEC = 120.0      # … or when the breakdown is older than this
FORK_TIMEOUT = 60
DEFAULT_WINDOW = 200_000   # until the first breakdown tells us the real window

_LOCK = threading.Lock()
_CACHE = {}                # sid -> {'at', 'total', 'data'|None, 'error'|None}
_QUEUE = []                # sids waiting for a breakdown
_WORKER = {'alive': False}


# ── open sessions ───────────────────────────────────────────────────────────
def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def open_sessions():
    out = []
    try:
        files = sorted(SESSIONS_DIR.glob('*.json'))
    except OSError:
        return out
    for f in files:
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        sid, pid = d.get('sessionId'), d.get('pid')
        if not sid or not _pid_alive(pid):
            continue
        out.append({'pid': pid, 'session_id': sid, 'cwd': d.get('cwd') or '',
                    'name': d.get('name') or '', 'status': d.get('status') or '',
                    'updated_at': d.get('updatedAt')})
    return out


def _transcript(sid):
    try:
        for p in PROJECTS_DIR.glob(f'*/{sid}.jsonl'):
            return p
    except OSError:
        pass
    return None


# ── cheap total ─────────────────────────────────────────────────────────────
def _last_usage(path):
    """Context size = last real assistant usage. Reads the tail, growing the window
    until a usage line is found (long tool outputs can push it far back)."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    for chunk in (300_000, 1_500_000, 6_000_000):
        with path.open('rb') as fh:
            fh.seek(max(0, size - chunk))
            lines = fh.read().splitlines()
        for raw in reversed(lines):
            if b'"usage"' not in raw or b'"assistant"' not in raw:
                continue
            try:
                d = json.loads(raw)
            except Exception:
                continue
            msg = d.get('message') or {}
            u = msg.get('usage') or {}
            if d.get('type') != 'assistant' or msg.get('model') == '<synthetic>':
                continue
            tot = (u.get('input_tokens') or 0) + (u.get('cache_read_input_tokens') or 0) \
                + (u.get('cache_creation_input_tokens') or 0)
            if tot > 0:
                return {'total': tot, 'model': msg.get('model'), 'at': d.get('timestamp')}
        if chunk >= size:
            break
    return None


def _compact_pct(pid):
    """CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: the session's own env wins, then settings.json env."""
    try:
        env = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        for kv in env:
            if kv.startswith(b'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE='):
                return float(kv.split(b'=', 1)[1])
    except Exception:
        pass
    try:
        s = json.loads((CLAUDE_DIR / 'settings.json').read_text(encoding='utf-8'))
        v = (s.get('env') or {}).get('CLAUDE_AUTOCOMPACT_PCT_OVERRIDE')
        return float(v) if v else None
    except Exception:
        return None


# ── breakdown (headless /context on a fork) ─────────────────────────────────
def _claude_bin():
    """systemd user services get a minimal PATH without ~/.local/bin — where the native
    installer puts `claude`. USAGE_CLAUDE_BIN overrides."""
    for c in (os.environ.get('USAGE_CLAUDE_BIN'), shutil.which('claude'),
              str(Path.home() / '.local' / 'bin' / 'claude'),
              str(CLAUDE_DIR / 'local' / 'claude')):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _run_context(sid, cwd):
    exe = _claude_bin()
    if not exe:
        raise RuntimeError('claude CLI not found (set USAGE_CLAUDE_BIN)')
    # TMUX*: hooks are off, but keep the fork away from the caller's pane anyway.
    # CLAUDE_CODE_CHILD_SESSION: set when this server was started from inside a session.
    env = {k: v for k, v in os.environ.items()
           if k not in ('TMUX', 'TMUX_PANE', 'CLAUDE_CODE_CHILD_SESSION')}
    r = subprocess.run(
        [exe, '-p', '--resume', sid, '--fork-session', '--no-session-persistence',
         '--settings', '{"disableAllHooks":true}', '--output-format', 'json', '/context'],
        cwd=cwd if cwd and os.path.isdir(cwd) else None, env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=FORK_TIMEOUT)
    try:
        items = json.loads(r.stdout)
    except Exception:
        raise RuntimeError((r.stderr or r.stdout or 'no output').strip()[:200])
    for it in items if isinstance(items, list) else [items]:
        cu = it.get('context_usage') if isinstance(it, dict) else None
        if cu:
            return cu
    raise RuntimeError('no context_usage in output')


def _shape(cu):
    def top(lst, key, n=None):
        lst = sorted(lst or [], key=lambda x: -(x.get('tokens') or 0))
        return [{'name': x.get(key), 'tokens': x.get('tokens') or 0,
                 **({'source': x['source']} if x.get('source') else {}),
                 **({'type': x['type']} if x.get('type') else {}),
                 **({'server': x['server_name']} if x.get('server_name') else {})}
                for x in (lst[:n] if n else lst)]
    return {
        'model': cu.get('model'),
        'window': cu.get('raw_max_tokens'),
        'measured_total': cu.get('total_tokens'),
        'categories': [{'name': c.get('name'), 'tokens': c.get('tokens') or 0,
                        'kind': c.get('kind')} for c in cu.get('categories') or []],
        'memory_files': top(cu.get('memory_files'), 'path'),
        'skills': top(cu.get('skills'), 'name'),
        'agents': top(cu.get('agents'), 'agent_type'),
        'mcp_tools': top(cu.get('mcp_tools'), 'name'),
    }


def _worker():
    while True:
        with _LOCK:
            if not _QUEUE:
                _WORKER['alive'] = False
                return
            sid, cwd, total = _QUEUE.pop(0)
        entry = {'at': time.time(), 'total': total, 'data': None, 'error': None}
        try:
            entry['data'] = _shape(_run_context(sid, cwd))
        except Exception as e:
            entry['error'] = str(e)[:200]
            old = _CACHE.get(sid)
            if old and old.get('data'):          # keep the last good breakdown
                entry['data'] = old['data']
        with _LOCK:
            _CACHE[sid] = entry


def _schedule(sid, cwd, total):
    with _LOCK:
        if any(q[0] == sid for q in _QUEUE):
            return
        _QUEUE.append((sid, cwd, total))
        if not _WORKER['alive']:
            _WORKER['alive'] = True
            threading.Thread(target=_worker, name='context-breakdown', daemon=True).start()


def _stale(entry, total, window):
    if entry is None:
        return True
    if time.time() - entry['at'] > REMEASURE_SEC:
        return True
    return abs(total - (entry.get('total') or 0)) * 100.0 / (window or DEFAULT_WINDOW) >= REMEASURE_PCT


_MODEL_WINDOW = {}         # model -> window, learned from breakdowns (same model = same window)


def _known_window(bd, model):
    """Window from this session's breakdown, else one learned for the same model. None until
    measured: guessing 200k vs 1M turned a 190k session into "95 % full" (it was 19 %)."""
    if bd and bd.get('window'):
        return bd['window']
    return _MODEL_WINDOW.get(model)


# ── public ──────────────────────────────────────────────────────────────────
def compute(breakdown=True):
    """All open sessions with total fill; breakdown served from cache (refreshed in background)."""
    sessions = []
    for s in open_sessions():
        tp = _transcript(s['session_id'])
        last = _last_usage(tp) if tp else None
        entry = _CACHE.get(s['session_id'])
        bd = entry.get('data') if entry else None
        total = (last or {}).get('total')
        model = (last or {}).get('model') or (bd or {}).get('model')
        if bd and bd.get('window') and bd.get('model'):
            _MODEL_WINDOW[bd['model']] = bd['window']
        window = _known_window(bd, model)
        pct = _compact_pct(s['pid'])
        row = dict(s)
        row.update({
            'model': model,
            'total': total,
            'window': window,
            'percent': round(total * 100.0 / window, 1) if total and window else None,
            'compact_at': int(window * pct / 100) if pct and window else None,
            'last_turn_at': (last or {}).get('at'),
            'breakdown': bd,
            'breakdown_age_sec': int(time.time() - entry['at']) if entry else None,
            'breakdown_error': entry.get('error') if entry else None,
        })
        if bd and total:
            # Categories were measured at an earlier total; the drift since then is all
            # conversation, so it goes to Messages and out of Free space.
            drift = total - (bd.get('measured_total') or total)
            cats = [dict(c) for c in bd['categories']]
            for c in cats:
                if c['name'] == 'Messages':
                    c['tokens'] = max(0, c['tokens'] + drift)
                elif c['kind'] == 'free':
                    c['tokens'] = max(0, c['tokens'] - drift)
            row['breakdown'] = dict(bd, categories=cats)
        if breakdown and total and _stale(entry, total, window or DEFAULT_WINDOW):
            _schedule(s['session_id'], s['cwd'], total)
        sessions.append(row)
    sessions.sort(key=lambda r: -(r['total'] or 0))
    return {'sessions': sessions, 'pending': len(_QUEUE),
            'updated': time.strftime('%H:%M:%S')}


if __name__ == '__main__':
    compute()
    while _WORKER['alive']:
        time.sleep(0.5)
    print(json.dumps(compute(breakdown=False), indent=1, ensure_ascii=False)[:3000])

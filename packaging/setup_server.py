#!/usr/bin/env python3
"""setup_server.py — the graphical face of ./setup.sh.

Started by `./setup.sh --ui`, serves the wizard page, and exits when you're
done. It does not implement any setup logic of its own: every mutation shells
out to `setup.sh do|undo`, so the terminal wizard and this one can never drift
apart.

WHY THIS IS A SEPARATE, TEMPORARY SERVER
----------------------------------------
The long-running server (server.py, :8770) is read-only by design — that is
what lets this tool claim nothing leaves your machine. Endpoints that edit your
waybar config, install a systemd unit or touch your API keys do not belong
there, because a browser can be tricked into calling a localhost server:

  * CSRF — any web page you have open can POST to http://127.0.0.1:<port>.
    CORS stops the page from *reading* the reply; it does not stop the request.
  * DNS rebinding — a domain that resolves to 127.0.0.1 becomes same-origin,
    and then the page can read replies too, API keys included.

So the write surface lives here, and here only, guarded four ways:

  1. Host must be 127.0.0.1:<port> or localhost:<port>  → kills DNS rebinding
  2. Origin, when sent, must be our own                 → kills cross-site POSTs
  3. every API call carries a one-time token            → unguessable
  4. the port is ephemeral, and the process exits when idle or when you finish

On top of that: no key value is ever returned by any endpoint, and keys travel
to setup.sh over stdin — never argv, which is world-readable in /proc.
"""
from __future__ import annotations

import argparse
import hmac
import json
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP_SH = ROOT / 'setup.sh'
WEBD = ROOT / 'web'

# Only these files are reachable — an allowlist, so path traversal has nothing
# to traverse to.
STATIC = {
    '/': 'setup.html',
    '/setup.html': 'setup.html',
    '/setup.js': 'setup.js',
    '/setup.css': 'setup.css',
    '/styles.css': 'styles.css',
}
MIME = {'.html': 'text/html; charset=utf-8',
        '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8'}

STEPS = {'base', 'server', 'waybar', 'widget', 'tray', 'keys', 'verify'}
PREVIEWABLE = {'server', 'waybar', 'widget', 'tray', 'keys'}
KEY_RE = re.compile(r'[A-Z][A-Z0-9_]{2,63}')
MAX_BODY = 16 * 1024
STEP_TIMEOUT = 180          # a systemd install + server poll can take a while

TOKEN = secrets.token_urlsafe(32)
ALLOWED_HOSTS: set[str] = set()
ALLOWED_ORIGINS: set[str] = set()

_last_seen = time.monotonic()
_seen_lock = threading.Lock()


def touch() -> None:
    global _last_seen
    with _seen_lock:
        _last_seen = time.monotonic()


def idle_seconds() -> float:
    with _seen_lock:
        return time.monotonic() - _last_seen


# ── talking to setup.sh ─────────────────────────────────────────────────────
def run_setup(args: list[str], stdin_data: str | None = None) -> dict:
    """Run `setup.sh <args>` and return its JSON. Never raises."""
    try:
        proc = subprocess.run(
            [str(SETUP_SH), *args],
            input=stdin_data, capture_output=True, text=True,
            timeout=STEP_TIMEOUT, cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'step timed out after {STEP_TIMEOUT}s'}
    except OSError as e:
        return {'ok': False, 'error': f'cannot run setup.sh: {e}'}
    try:
        return json.loads(proc.stdout or '{}')
    except json.JSONDecodeError:
        # setup.sh narrates on stderr; surface the tail so the page can show
        # something truthful instead of a blank failure.
        return {'ok': False, 'error': 'setup.sh returned no JSON',
                'stderr': (proc.stderr or '')[-2000:]}


def build_args(action: str, step: str, opts: dict) -> list[str]:
    """Translate the page's intent into flags. Nothing arbitrary gets through."""
    args = [action, step]
    if action == 'do':
        if step == 'server' and opts.get('mode') in ('systemd', 'session'):
            args += ['--mode', opts['mode']]
        if step in ('widget', 'tray'):
            if opts.get('autostart'):
                args.append('--autostart')
            if step == 'widget' and opts.get('open'):
                args.append('--open')
            if step == 'tray' and opts.get('start'):
                args.append('--start')
        if step == 'keys':
            if opts.get('import_shell'):
                args.append('--import-shell')
            if opts.get('set'):
                args.append('--set-stdin')
    if step == 'keys':
        name = opts.get('delete')
        if isinstance(name, str) and KEY_RE.fullmatch(name):
            args += ['--delete', name]
    return args


def keys_stdin(keys) -> str | None:
    """NAME=VALUE lines for `--set-stdin`, with the names validated here too.

    setup.sh validates again. Two checks, because this one decides what leaves
    the browser and that one decides what reaches the file.
    """
    if not isinstance(keys, dict):
        return None
    lines = []
    for name, value in keys.items():
        if not isinstance(name, str) or not KEY_RE.fullmatch(name):
            continue
        if not isinstance(value, str) or '\n' in value or '\r' in value:
            continue
        lines.append(f'{name}={value.strip()}')
    return '\n'.join(lines) + '\n' if lines else None


# ── http ────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = 'usage-tracker-setup/1'

    def log_message(self, fmt, *args):   # noqa: A003 — never log; bodies hold keys
        pass

    # -- plumbing -----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                         "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    def _reject(self, why: str) -> None:
        self._json(403, {'ok': False, 'error': why})

    def _guard(self, need_token: bool) -> bool:
        # 1) Host — the one check that stops DNS rebinding.
        if (self.headers.get('Host') or '').strip() not in ALLOWED_HOSTS:
            self._reject('bad Host'); return False
        # 2) Origin — absent on top-level navigation, wrong on cross-site calls.
        origin = self.headers.get('Origin')
        if origin is not None and origin not in ALLOWED_ORIGINS:
            self._reject('bad Origin'); return False
        # 3) token — unguessable, one process, one value.
        if need_token and not hmac.compare_digest(self.headers.get('X-UT-Token', ''), TOKEN):
            self._reject('bad token'); return False
        touch()
        return True

    def _body(self) -> dict | None:
        if 'application/json' not in (self.headers.get('Content-Type') or '').lower():
            return None
        try:
            n = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            obj = json.loads(self.rfile.read(n).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return obj if isinstance(obj, dict) else None

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        path = self.path.split('?')[0].split('#')[0]

        if path == '/api/setup/probe':
            if not self._guard(need_token=True):
                return
            self._json(200, run_setup(['probe']))
            return

        if path.startswith('/api/setup/preview/'):
            if not self._guard(need_token=True):
                return
            step = path.rsplit('/', 1)[-1]
            if step not in PREVIEWABLE:
                self._json(400, {'ok': False, 'error': f'no preview for {step}'})
                return
            self._json(200, run_setup(['preview', step]))
            return

        if path in STATIC:
            if not self._guard(need_token=False):
                return
            fp = WEBD / STATIC[path]
            if not fp.is_file():
                self._json(404, {'ok': False, 'error': f'missing {fp.name}'})
                return
            self._send(200, fp.read_bytes(), MIME.get(fp.suffix, 'text/plain'))
            return

        self._json(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        path = self.path.split('?')[0]
        if not self._guard(need_token=True):
            return

        if path == '/api/setup/quit':
            self._json(200, {'ok': True, 'bye': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if path not in ('/api/setup/do', '/api/setup/undo'):
            self._json(404, {'ok': False, 'error': 'not found'})
            return

        req = self._body()
        if req is None:
            self._json(400, {'ok': False, 'error': 'expected a small JSON body'})
            return
        step = req.get('step')
        if step not in STEPS:
            self._json(400, {'ok': False, 'error': f'unknown step: {step!r}'})
            return
        action = 'do' if path.endswith('/do') else 'undo'
        opts = req.get('opts') if isinstance(req.get('opts'), dict) else {}
        stdin_data = keys_stdin(req.get('keys')) if (step == 'keys' and action == 'do') else None
        if stdin_data and not opts.get('set'):
            opts = {**opts, 'set': True}
        self._json(200, run_setup(build_args(action, step, opts), stdin_data))


# ── lifecycle ───────────────────────────────────────────────────────────────
def watchdog(srv, timeout: float) -> None:
    """Close the write surface when nobody's using it."""
    while True:
        time.sleep(5)
        if idle_seconds() > timeout:
            srv.shutdown()
            return


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url-file', help='write the wizard URL here (setup.sh reads it)')
    ap.add_argument('--idle-timeout', type=float, default=600.0,
                    help='seconds of inactivity before the wizard closes itself')
    ap.add_argument('--port', type=int, default=0, help='0 = pick a free one (recommended)')
    a = ap.parse_args()

    if not SETUP_SH.is_file():
        print(f'setup.sh not found next to {Path(__file__).name}', file=sys.stderr)
        return 1

    srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    port = srv.server_address[1]
    ALLOWED_HOSTS.update({f'127.0.0.1:{port}', f'localhost:{port}'})
    ALLOWED_ORIGINS.update({f'http://127.0.0.1:{port}', f'http://localhost:{port}'})

    url = f'http://127.0.0.1:{port}/setup.html#{TOKEN}'
    if a.url_file:
        # setup.sh is driving: it reads the file and prints the URL with
        # context. Printing here too would just put a bare token on screen.
        Path(a.url_file).write_text(url, encoding='utf-8')
    else:
        print(url, flush=True)

    threading.Thread(target=watchdog, args=(srv, a.idle_timeout), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

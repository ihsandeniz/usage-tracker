"""`usage-tracker setup --ui` — sihirbazın tarayıcı yüzü, paketin içinden.

`packaging/setup_server.py` ile aynı güvenlik modeli, iki farkla: bu sunucu `setup.sh`'ı
değil `usage.wizard`'ı çağırır (Windows'ta bash yok) ve **paketin içinde** yaşar, çünkü
donmuş ikilide `packaging/` diye bir şey yoktur.

Neden kalıcı sunucuya (`server.py`) eklenmedi — bu, FAZ 6'da verilmiş bir karardır ve hâlâ
geçerlidir: kurulum sihirbazı **yazar**, kalıcı sunucu gün boyu açıktır. Yazma yetkisini
rastgele port + tek kullanımlık jeton + boşta kendini kapatma ile sınırlayabilen tek yer,
kullanıcının o an başlattığı geçici süreçtir.

Dört kapı, `setup_server.py`'dekinin aynısı:
  1. `Host` 127.0.0.1:<port> değilse 403      → DNS rebinding'i kapatan tek kontrol
  2. yabancı `Origin` → 403                    → siteler arası POST
  3. jetonsuz API çağrısı → 403                → jeton URL **fragment**'ında: sunucuya hiç gitmez
  4. boşta 10 dk / "Bitir" → kendini kapatır   → açık kalan bir yazma ucu bırakma
Ayrıca: hiç log tutulmaz (gövdede anahtar olabilir) ve CSP `default-src 'none'`.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import platform as _paths
from . import wizard

TOKEN = secrets.token_urlsafe(32)
IDLE_TIMEOUT = 600.0
MAX_BODY = 8192
WEBD = _paths.resource_dir() / 'web'

STATIC = {'/': 'wizard.html', '/wizard.html': 'wizard.html',
          '/wizard.js': 'wizard.js', '/setup.css': 'setup.css'}
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8'}

_last_seen = time.monotonic()
_lock = threading.Lock()


def _touch() -> None:
    global _last_seen
    with _lock:
        _last_seen = time.monotonic()


def _idle_seconds() -> float:
    with _lock:
        return time.monotonic() - _last_seen


class Handler(BaseHTTPRequestHandler):
    server_version = 'usage-tracker-wizard/1'

    def log_message(self, fmt, *args):        # asla log tutma: gövde anahtar taşıyabilir
        pass

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

    def _guard(self, need_token: bool) -> bool:
        host = (self.headers.get('Host') or '').strip()
        if host not in self.server.allowed_hosts:
            self._json(403, {'ok': False, 'error': 'bad Host'}); return False
        origin = self.headers.get('Origin')
        if origin is not None and origin not in self.server.allowed_origins:
            self._json(403, {'ok': False, 'error': 'bad Origin'}); return False
        if need_token and not hmac.compare_digest(self.headers.get('X-UT-Token', ''), TOKEN):
            self._json(403, {'ok': False, 'error': 'bad token'}); return False
        _touch()
        return True

    def _body(self):
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

    def do_GET(self):
        path = self.path.split('?')[0].split('#')[0]

        if path == '/api/wizard/probe':
            if not self._guard(need_token=True):
                return
            self._json(200, wizard.probe())
            return

        if path.startswith('/api/wizard/preview/'):
            if not self._guard(need_token=True):
                return
            self._json(200, wizard.preview(path.rsplit('/', 1)[-1]))
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

        if path == '/api/wizard/quit':
            self._json(200, {'ok': True, 'bye': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        req = self._body()
        if req is None:
            self._json(400, {'ok': False, 'error': 'expected a small JSON body'})
            return

        if path == '/api/wizard/do':
            opts = req.get('opts') if isinstance(req.get('opts'), dict) else {}
            self._json(200, wizard.do(req.get('step'), opts))
            return
        if path == '/api/wizard/undo':
            self._json(200, wizard.undo(req.get('step')))
            return
        if path == '/api/wizard/auto':
            self._json(200, wizard.auto())
            return

        self._json(404, {'ok': False, 'error': 'not found'})


def _watchdog(srv) -> None:
    """Kullanıcı sekmeyi kapatıp gidebilir; yazma yetkisi olan bir sunucu öylece kalamaz."""
    while True:
        time.sleep(5)
        if _idle_seconds() > IDLE_TIMEOUT:
            srv.shutdown()
            return


def serve(open_browser: bool = True) -> int:
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = srv.server_address[1]
    srv.allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    srv.allowed_origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}

    url = f'http://127.0.0.1:{port}/wizard.html#{TOKEN}'
    # `flush=True` süs değil: stdout bir boruya bağlıyken Python blok tamponlar ve adres
    # sunucu kapanana kadar tamponda kalır. Terminalde fark edilmez (satır tamponlu), ama
    # sihirbazı saran her şey — bir script, bir test, ileride bir kurulum aracı — adresi
    # okuyamaz ve **askıda kalır**. Ölçüldü 2026-08-13: konteyner turu tam burada 60 sn
    # bekleyip düştü, sebebi sunucu değil tampondu.
    print('usage-tracker setup — the wizard is a page now:', flush=True)
    print(f'  {url}', flush=True)
    print('  (the address carries a one-time token in the # part, which browsers never send '
          'to a server)\n  Closing the page or 10 idle minutes shuts this down.', flush=True)
    # `UT_NO_BROWSER=1`: başsız makinede ve testte pencere açtırmamak için. FAZ 6'da izole
    # PATH kurulurken gerçek bir tarayıcı penceresi ihsan'ın masaüstünde açılmıştı — bir
    # testin kullanıcının ekranına dokunmasının tek sebebi, dokunmamayı seçebilmemesidir.
    if open_browser and os.environ.get('UT_NO_BROWSER') != '1':
        try:
            webbrowser.open(url)
        except Exception:                      # başsız makine: URL zaten yazıldı
            pass

    threading.Thread(target=_watchdog, args=(srv,), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    print('wizard closed.')
    return 0

#!/usr/bin/env python3
"""
usage-tracker sunucu — "Pane for Linux" (bağımsız; Odak Paneli/dashboard'dan ayrı).
Port 8770 · 127.0.0.1 bind · stdlib-only · sıfır bağımlılık · build'siz.

  GET  /                 → web/index.html (Vanilla JS UI)
  GET  /api/usage        → Claude limit paneli (oturum/haftalık, kalibrasyon)
  GET  /api/spend[?days] → gerçek $ harcama (Today/Yesterday/30g + per-model + per-day)
  GET  /v1/usage         → interop wire-format (overlay/waybar/eww için stabil sözleşme)
  POST /api/calibrate    → gerçek /usage %'siyle limit bütçesini çapala

Güvenlik: yalnız loopback · credential dosyalarına dokunmaz · SADECE ~/.claude/projects OKUR.
"""
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usage import engine, settings, viewconfig   # noqa: E402

VERSION = '0.2.2'                                  # tek kaynak: Server başlığı + panel rozeti
HOST = '127.0.0.1'                                 # loopback-only (güvenlik — değiştirme)
PORT = int(os.environ.get('USAGE_PORT', '8770'))   # port çakışmasında USAGE_PORT ile değiştir
from usage import platform as _paths          # noqa: E402
WEBD = _paths.resource_dir() / 'web'       # donmuş pakette sys._MEIPASS/web

# DNS rebinding koruması: Host başlığı allowlist (setup_server.py'den uyarlanmış)
# Meşru olanlar: 127.0.0.1:<PORT>, localhost:<PORT>, [::1]:<PORT>, portsuz varyantlar
ALLOWED_HOSTS = {
    f'127.0.0.1:{PORT}', '127.0.0.1',
    f'localhost:{PORT}', 'localhost',
    f'[::1]:{PORT}', '[::1]', '::1',
}

MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
        '.svg': 'image/svg+xml', '.ico': 'image/x-icon'}


class Handler(BaseHTTPRequestHandler):
    server_version = f'usage-tracker/{VERSION}'

    def log_message(self, fmt, *args):          # sessiz — stderr'i kirletme
        pass

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache, no-store')
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, msg: str) -> None:
        """API error response — JSON format (tutarlı hata şeması)"""
        self._json(code, {'ok': False, 'error': msg})

    def _check_host(self) -> bool:
        """DNS rebinding guard: Host başlığını allowlist'e karşı doğrula.

        curl --http1.0 gibi HTTP/1.0 istemcileri Host başlığı göndermeyebilir;
        bunları meşru sayıyoruz (waybar besleyicisi curl kullanıyor, onu kırma).
        Kötü niyetli Host (evil.com vb.) → 403.
        """
        host_header = (self.headers.get('Host') or '').strip()
        # Host başlığı yoksa (HTTP/1.0): meşru sayıl
        if not host_header:
            return True
        # Başlık varsa allowlist'te olmalı
        if host_header not in ALLOWED_HOSTS:
            self._error(403, 'bad Host header'); return False
        return True

    def _check_origin(self) -> bool:
        """CSRF guard (§güvenlik 2 — setup_server.py'den kalıcı sunucuya taşındı).

        `Content-Type: application/json` şartı tek başına kilit değil: kilit olduğu
        varsayımı tarayıcı sürümüne bağlıdır. İnternetteki bir sayfa 127.0.0.1'e istek
        atabilir — atarsa `Origin` başlığını **kendi** adresiyle gönderir ve buradan döner.

        Origin **yoksa** geçer: tarayıcı dışı istemcilerde (curl, script) yoktur ve bir
        sayfa kendi Origin'ini silemez — yani yokluk bir saldırı yüzeyi değil.
        """
        origin = (self.headers.get('Origin') or '').strip()
        if not origin:
            return True
        netloc = urlparse(origin).netloc
        host = (self.headers.get('Host') or '').strip()
        if netloc and (netloc == host or netloc in ALLOWED_HOSTS):
            return True
        self._error(403, 'cross-origin write refused'); return False

    def _read_json_body(self, limit: int = 2048):
        try:
            n = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            return None
        if n <= 0 or n > limit:
            return None
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return None

    def do_GET(self):
        # DNS rebinding koruması: Host başlığını kontrol et
        if not self._check_host():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path in ('/api/usage', '/api/usage/'):
            self._json(200, engine.compute_usage()); return

        if path in ('/api/live', '/api/live/'):
            # canlı Anthropic usage yanıtı (ham + normalize) — doğrulama/hata ayıklama için
            from usage import live
            force = parse_qs(parsed.query).get('force', ['0'])[0] == '1'
            self._json(200, live.fetch(force=force)); return

        if path in ('/api/spend', '/api/spend/'):
            try:
                days = int(parse_qs(parsed.query).get('days', ['30'])[0])
            except ValueError:
                days = 30
            days = max(1, min(90, days))
            self._json(200, engine.compute_spend(days)); return

        if path in ('/api/providers', '/api/providers/'):
            try:
                days = int(parse_qs(parsed.query).get('days', ['30'])[0])
            except ValueError:
                days = 30
            days = max(1, min(90, days))
            self._json(200, {'providers': engine.compute_providers(days),
                             'updated': datetime.now().strftime('%H:%M:%S')}); return

        if path in ('/v1/usage', '/v1/usage/'):
            # Tam liste mi istersin (seçim UI için) ya da süzülü (waybar/panel/widget)?
            wire = engine.usage_wire()
            # ?all=1 query varsa süzME (tüm sağlayıcılar — config UI için)
            if parse_qs(parsed.query).get('all', ['0'])[0] == '1':
                self._json(200, wire); return
            # Yoksa user config'e göre süz
            self._json(200, viewconfig.filter_wire(wire)); return

        if path in ('/api/settings', '/api/settings/'):
            # Eşik/yenileme/görüntüleme tercihleri + anahtarların **durumu**.
            # `keys` yalnız ad + set/unset taşır — değer hiçbir GET'ten çıkmaz (§güvenlik 3).
            self._json(200, {'settings': settings.get_settings(),
                             'keys': settings.key_status(),
                             'keysAreReadOnly': True}); return

        if path in ('/api/view-config', '/api/view-config/'):
            # Config + available sağlayıcıları dön (GET only)
            cfg = viewconfig.get_config()
            wire = engine.usage_wire()
            available = [{'id': p.get('id'), 'name': p.get('name')}
                        for p in (wire.get('providers') or [])
                        if isinstance(p, dict) and p.get('id')]
            self._json(200, {'config': cfg, 'available': available}); return

        # statik dosya (web/)
        if path == '/':
            path = '/index.html'
        fp = (WEBD / path.lstrip('/')).resolve()
        if WEBD in fp.parents and fp.is_file():          # path-traversal koruması
            body = fp.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', MIME.get(fp.suffix.lower(), 'application/octet-stream'))
            self.send_header('Content-Length', str(len(body)))
            # cache header — HTML: no-cache, JS/CSS: 1 saat (public caching saf loopback)
            if fp.suffix.lower() == '.html':
                self.send_header('Cache-Control', 'no-cache')
            elif fp.suffix.lower() in ('.js', '.css'):
                self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(body)
        else:
            # API-like 404 döndürme: `/api/*` → JSON, digerleri HTML
            if path.startswith('/api/') or path.startswith('/v1/'):
                self._error(404, 'endpoint not found')
            else:
                self.send_error(404)

    def do_POST(self):
        # DNS rebinding koruması: Host başlığını kontrol et
        if not self._check_host():
            return
        # CSRF: yabancı bir sayfa loopback'e yazamaz
        if not self._check_origin():
            return

        path = self.path.split('?')[0]
        if path in ('/api/settings', '/api/settings/'):
            ct = self.headers.get('Content-Type', '').lower()
            if 'application/json' not in ct:
                self._error(400, 'expected Content-Type: application/json'); return
            req = self._read_json_body()
            if not isinstance(req, dict):
                self._error(400, 'invalid request body'); return
            ok, err = settings.save_settings(req)
            if ok:
                self._json(200, {'ok': True, 'settings': settings.get_settings()})
            else:
                self._error(400, err)
            return

        if path in ('/api/calibrate', '/api/calibrate/'):
            # Content-Type validation
            ct = self.headers.get('Content-Type', '').lower()
            if 'application/json' not in ct:
                self._error(400, 'expected Content-Type: application/json'); return
            req = self._read_json_body()
            ok, err = engine.calibrate_usage(req)
            self._json(400 if not ok else 200, {'ok': ok, 'error': err}); return

        if path in ('/api/view-config', '/api/view-config/'):
            # Config kaydet
            ct = self.headers.get('Content-Type', '').lower()
            if 'application/json' not in ct:
                self._error(400, 'expected Content-Type: application/json'); return
            req = self._read_json_body()
            if not isinstance(req, dict):
                self._error(400, 'invalid request body'); return
            hidden = req.get('hidden_providers')
            if not isinstance(hidden, list):
                self._error(400, 'hidden_providers must be a list'); return
            # Tüm elemanlar string mi?
            if not all(isinstance(x, str) for x in hidden):
                self._error(400, 'hidden_providers must contain only strings'); return
            ok = viewconfig.save_config({'hidden_providers': hidden})
            if ok:
                self._json(200, {'ok': True})
            else:
                self._error(500, 'failed to save configuration')
            return

        self._error(404, 'endpoint not found')


# CLI alt komutları (usage/cli.py). Tek giriş noktası bilinçli: donmuş paketin içinde
# ikinci bir çalıştırılabilir yok — `usage-tracker.exe guard` çalışmak zorunda.
CLI_COMMANDS = ('usage', 'providers', 'guard', 'watch', 'doctor', 'config')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] in CLI_COMMANDS:
        from usage import cli
        return cli.main(argv)

    if '--version' in argv or '-V' in argv:
        # Tek kaynak: VERSION. `git tag` ile hizalı olduğunu CI doğrular (.github/workflows/ci.yml)
        print(f'usage-tracker {VERSION}')
        return 0

    if '--help' in argv or '-h' in argv:
        print(f'usage-tracker {VERSION} — AI kullanım/limit/harcama izleyici\n'
              f'\n'
              f'  python3 server.py            panel sunucusu (http://{HOST}:{PORT})\n'
              f'  python3 server.py --version  sürüm\n'
              f'\n'
              f'Ortam:\n'
              f'  USAGE_PORT   port (varsayılan 8770)\n'
              f'  USAGE_DEMO=1 sentetik demo verisi\n'
              f'  USAGE_PRICES fiyat kataloğu dosyası (bkz. python3 -m usage.catalog)\n')
        return 0

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'usage-tracker {VERSION} → http://{HOST}:{PORT}  (Ctrl+C ile durdur)')

    # Fiyat kataloğu eksik/bayatsa başlangıçta söyle — panelde görünmesini beklemeden.
    try:
        from usage import pricing
        warning = (pricing.catalog_status() or {}).get('warning')
        if warning:
            print(f'⚠️  {warning}', file=sys.stderr)
    except Exception:                      # katalog uyarısı hiçbir zaman sunucuyu düşürmesin
        pass

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\ndurduruldu.')
        srv.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

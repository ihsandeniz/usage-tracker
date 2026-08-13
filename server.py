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

VERSION = '0.4.0'                                  # tek kaynak: Server başlığı + panel rozeti
HOST = '127.0.0.1'                                 # loopback-only (güvenlik — değiştirme)
PORT = int(os.environ.get('USAGE_PORT', '8770'))   # port çakışmasında USAGE_PORT ile değiştir
from usage import platform as _paths          # noqa: E402
# `.resolve()` şart: aşağıdaki traversal koruması bu tabanı **çözülmüş** bir yolla
# karşılaştırıyor. Taban çözülmemişse ikisi aynı dizini gösterse bile eşleşmez ve panelin
# TÜM statik dosyaları 404 döner — API çalışırken ekran boş kalır. Windows'ta olağan hâl
# budur: `%TEMP%` çoğu kurulumda 8.3 kısa adıdır (`RUNNER~1`), `sys._MEIPASS` onu taşır,
# `resolve()` ise uzun adı verir.
WEBD = (_paths.resource_dir() / 'web').resolve()   # donmuş pakette sys._MEIPASS/web


def static_file(path: str, root=None):
    """İstenen yolun `root` içindeki gerçek dosya karşılığı, yoksa None (traversal koruması).

    Ayrı bir fonksiyon, çünkü buradaki tek kural — "çözülmüş yol tabanın altında olmalı" —
    yalnız çalıştırılarak sınanabilir ve bir platformda sessizce ters dönebilir.
    """
    base = WEBD if root is None else Path(root).resolve()
    fp = (base / path.lstrip('/')).resolve()
    return fp if (base in fp.parents and fp.is_file()) else None

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

    def _drain_request_body(self, limit: int = 1 << 20) -> None:
        """Reddedilen bir yazmanın gövdesi okunmadan soket kapanırsa istemci 403'ü göremez.

        Kapanış anında okunmamış veri kalmışsa çekirdek RST gönderir; istemcinin
        `getresponse()`'u yanıtı okumadan `ConnectionAbortedError` ile düşer. Yani CSRF
        guard'ı **kendini açıklayamıyordu**: tarayıcı "ağ hatası" görüyordu, gerekçeyi
        değil. Linux'ta görünmüyordu çünkü küçük gövde soket tamponuna sığıyor — Windows
        py3.13 runner'ı 2026-08-13'te gösterdi.
        """
        if getattr(self, '_body_consumed', False):
            return
        self._body_consumed = True
        try:
            remaining = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            return
        if remaining > limit:              # tamamını okumak sınırsız bir söz olurdu
            self.close_connection = True
            remaining = limit
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 8192))
            if not chunk:
                break
            remaining -= len(chunk)

    def _error(self, code: int, msg: str) -> None:
        """API error response — JSON format (tutarlı hata şeması)"""
        self._drain_request_body()
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
            return None                    # okunmadı: `_error` gövdeyi boşaltmakla yükümlü
        try:
            raw = self.rfile.read(n)
        except Exception:
            return None
        self._body_consumed = True         # iki kez okumak istemciyi beklemeye sokar
        try:
            return json.loads(raw.decode('utf-8'))
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
        fp = static_file(path)                           # path-traversal koruması
        if fp is not None:
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
        self._body_consumed = False        # aynı bağlantıda ikinci istek (keep-alive)
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
            # Tek doğrulayıcı: aynı kural CLI'da da geçerli (iki kopya biri diğerini
            # yalanlar). Claude kartı burada da reddedilir — wire sözleşmesi bir görünüm
            # tercihiyle kırılamaz.
            ok, err = viewconfig.validate_config({'hidden_providers': req.get('hidden_providers')})
            if not ok:
                self._error(400, err); return
            ok = viewconfig.save_config({'hidden_providers': req.get('hidden_providers')})
            if ok:
                self._json(200, {'ok': True})
            else:
                self._error(500, 'failed to save configuration')
            return

        self._error(404, 'endpoint not found')


# CLI alt komutları (usage/cli.py). Tek giriş noktası bilinçli: donmuş paketin içinde
# ikinci bir çalıştırılabilir yok — `usage-tracker.exe guard` çalışmak zorunda.
CLI_COMMANDS = ('usage', 'providers', 'guard', 'watch', 'doctor', 'config',
                'setup', 'panel')


def main(argv=None):
    # İlk satır, çünkü ilk `print`'ten önce olmak zorunda: açılış satırındaki `→` cp1252'de
    # yok ve çıktı boruya gittiğinde paket daha portu bağlamadan çöküyordu (Windows,
    # package.yml'in ilk koşusu). Gerekçe: usage/platform.py:ensure_utf8_output.
    _paths.ensure_utf8_output()
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] in CLI_COMMANDS:
        from usage import cli
        return cli.main(argv)

    if '--version' in argv or '-V' in argv:
        # Tek kaynak: VERSION. `git tag` ile hizalı olduğunu CI doğrular (.github/workflows/ci.yml)
        print(f'usage-tracker {VERSION}')
        return 0

    # Tanımadığını **sunucu başlatarak** karşılamak, `guard`'ın çıkış-kodu sözleşmesini
    # kapının önünde deliyordu: `usage-tracker gaurd` (yazım hatası) ya da
    # `usage-tracker --threshold 80` (alt komutu unutmak) sessizce HTTP servisi açıp
    # 0 ile çıkıyordu — `… || atla` yazan script atlamıyor, asılıyordu. Alt komut
    # ayrıştırıcısı 64 döndürüyordu ama donmuş pakette **yalnız bu dağıtıcı** erişilebilir.
    # Kural: argüman varsa ya bilinen bir komuttur ya bilinen bir bayrak; üçüncü şık yok.
    no_open = '--no-open' in argv
    argv = [a for a in argv if a != '--no-open']

    if argv and argv[0] not in ('--version', '-V', '--help', '-h'):
        from usage.cli import EXIT_USAGE
        hint = (f"unknown command '{argv[0]}'" if not argv[0].startswith('-')
                else f"unknown option '{argv[0]}'")
        print(f'usage-tracker: {hint}\n'
              f"commands: {' · '.join(CLI_COMMANDS)}\n"
              f'run without arguments to serve the panel; --help for details', file=sys.stderr)
        return EXIT_USAGE

    if '--help' in argv or '-h' in argv:
        print(f'usage-tracker {VERSION} — AI kullanım/limit/harcama izleyici\n'
              f'\n'
              f'  python3 server.py            panel sunucusu (http://{HOST}:{PORT})\n'
              f'  python3 server.py --no-open   tarayıcı açma (oturum açılışı için)\n'
              f'  python3 server.py setup       kurulum sihirbazı (--ui ile tarayıcıda)\n'
              f'  python3 server.py --version   sürüm\n'
              f'\n'
              f'Ortam:\n'
              f'  USAGE_PORT   port (varsayılan 8770)\n'
              f'  USAGE_DEMO=1 sentetik demo verisi\n'
              f'  USAGE_PRICES fiyat kataloğu dosyası (bkz. python3 -m usage.catalog)\n')
        return 0

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    _say_now(f'usage-tracker {VERSION} → http://{HOST}:{PORT}')

    # Fiyat kataloğu eksik/bayatsa başlangıçta söyle — panelde görünmesini beklemeden.
    try:
        from usage import pricing
        warning = (pricing.catalog_status() or {}).get('warning')
        if warning:
            print(f'⚠️  {warning}', file=sys.stderr)
    except Exception:                      # katalog uyarısı hiçbir zaman sunucuyu düşürmesin
        pass

    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _greet_and_open(no_open)

    try:
        threading.Event().wait()          # Ctrl+C'yi bekleyen tek yer burası
    except KeyboardInterrupt:
        from usage import i18n
        print('\n' + ' · '.join(i18n.both('stopped')))
        srv.shutdown()
    return 0


def _say_now(text: str = '') -> None:
    """Kullanıcıya *ne yapacağını* söyleyen satırlar tamponda bekleyemez.

    Aynı kusur bu turda iki kez çıktı: sihirbazın adresi ve şimdi karşılama satırları.
    stdout bir dosyaya/boruya bağlıyken Python blok tamponlar; süreç kapanana kadar hiçbir
    şey görünmez — ve bu satırların tek işi görünmek. Windows koşucusunda ölçüldü: sihirbaz
    açıldı, panel devraldı, ama pencerenin ne dediğini kimse okuyamadı.
    """
    print(text, flush=True)


def _greet_and_open(no_open: bool) -> None:
    """Çift tıklayan kullanıcının gördüğü her şey.

    🔴 Bu fonksiyon bir kusurdan doğdu: ihsan `.exe`'yi **gerçek bir Windows makinesinde**
    çift tıkladı, siyah bir pencere açıldı ve orada kaldı — sunucu çalışıyordu ama tarayıcı
    açılmıyordu ve sihirbaza ulaşmak için `usage-tracker setup` yazması gerektiğini
    bilmesinin hiçbir yolu yoktu. `docs/WINDOWS.md` bile "çift tıkla, panel açılır" diyordu;
    açılmıyordu. Bir kurulum, kullanıcıdan komut yazmasını istediği anda kurulum olmaktan
    çıkar.

    Şimdi: ilk çalıştırmada **sihirbaz** tarayıcıda açılır (kullanıcı bitirince panel gelir),
    sonrakilerde doğrudan panel. Pencere iki dilde ne olduğunu söyler — makinenin dilini
    yanlış tahmin etmenin bedeli, iki satır fazladan metinden büyüktür.

    `--no-open`: oturum açılışındaki gizli başlatıcı ve systemd bunu geçer — her açılışta
    tarayıcı açan bir servis, kullanıcının kapatmak isteyeceği bir servistir.
    """
    from usage import i18n
    from usage import wizard

    # Bu satır burada da duruyor, `main()` zaten çağırdığı hâlde. Sebep: bu fonksiyonun
    # bastığı ilk şey **Türkçe** ve Türkçe'nin ilk harfi `İ` (U+0130) — cp1252'de yok.
    # Koruma çalışmadan çağrılırsa (test, ileride başka bir giriş noktası) karşılama satırı
    # tam da hoş geldin derken çöker. Windows koşucusunda ölçüldü, 2026-08-13: iki dilli
    # çıktı, v0.2.2'de kapatılan kusur sınıfını yeni bir kapıdan geri getirdi.
    _paths.ensure_utf8_output()

    # Yalnız Windows. Linux'ta bu ikili çoğunlukla systemd altında, konteynerde ya da SSH
    # oturumunda koşuyor; oralarda tarayıcı açmak (ya da sihirbazı bloklamak) yardım değil
    # sürpriz olur. Linux'un kendi yolu `./setup.sh` ve o yol bozulmadı.
    if no_open or not wizard.is_windows():
        return

    import webbrowser

    url = f'http://{HOST}:{PORT}'
    if wizard.is_first_run():
        for line in i18n.both('first_run'):
            _say_now(f'  {line}')
        from usage import wizard_server
        wizard_server.serve()                      # kullanıcı "Bitir" diyene kadar bloklar
        for line in i18n.both('wizard_done'):
            _say_now(f'  {line}')

    for line in i18n.both('panel_running', url=url):
        _say_now(f'  {line}')
    for line in i18n.both('panel_opening'):
        _say_now(f'  {line}')
    _say_now()
    for line in i18n.both('keep_open'):
        _say_now(f'  {line}')
    if not wizard.is_first_run():
        for line in i18n.both('hide_window'):
            _say_now(f'  {line}')

    if os.environ.get('UT_NO_BROWSER') == '1':     # başsız makine, CI, test
        return
    try:
        webbrowser.open(url)
    except Exception:                              # tarayıcı yoksa: adres zaten yazıldı
        pass


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""
usage-tray — native sistem tepsisi ikonu (FAZ 4a Dalga 3, OPSİYONEL modül).

Renk-kodlu tepsi ikonu (en yüksek Claude limiti %'sine göre yeşil/amber/kırmızı/gri) +
zengin tooltip (Claude + seçili sağlayıcılar) + sağ-tık menü + sol-tık → panel/widget.

⚠️ GUI toolkit gerektirir (bu yüzden opsiyonel — çekirdek stdlib kalır):
  Qt binding: PyQt5 VEYA PySide6 (`QSystemTrayIcon`, Wayland'da SNI → waybar tray modülü host eder).
  Wayland/Hyprland: waybar config'de "tray" modülü açık olmalı (SNI host).

Kullanım:
  surface/usage-tray.py               # tepsi ikonunu başlat (uzun-ömürlü)
  Hyprland autostart: exec-once = /ABS/PATH/surface/usage-tray.py

Config (surface.conf veya env): USAGE_URL (varsayılan http://127.0.0.1:8770), TRAY_INTERVAL (sn, 30).
Veri seçimine (view_config.json) otomatik uyar — /v1/usage zaten süzülmüş gelir.
"""
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SELF_DIR = Path(__file__).resolve().parent

# surface.conf (varsa) → env
_conf = SELF_DIR / 'surface.conf'
if _conf.exists():
    try:
        for line in _conf.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass

BASE = os.environ.get('USAGE_URL', 'http://127.0.0.1:8770').rstrip('/')
USAGE_URL = BASE + '/v1/usage'
INTERVAL = int(os.environ.get('TRAY_INTERVAL', '30')) * 1000  # ms

# Tepsiden başlatılan kardeş süreçlerin (usage-widget) hata çıktısı buraya düşer.
# DEVNULL'a yazmak "hiçbir şey olmadı" ile "sessizce patladı"yı aynı gösteriyordu.
LOG_PATH = Path(os.environ.get('XDG_STATE_HOME')
                or (Path.home() / '.local' / 'state')) / 'usage-tracker' / 'tray.log'
# Kardeş script bu süre içinde ölürse çıkış kodunu görürüz. Menü tıklamasını
# bekletiyor, o yüzden kısa: başarı yolunda ~0,1-0,3 sn'de zaten döner.
SURFACE_WAIT_SEC = float(os.environ.get('TRAY_SURFACE_WAIT', '1.0'))

# ── Qt binding shim (PyQt5 → PySide6) ────────────────────────────────────────
_QT = None
try:
    from PyQt5 import QtWidgets, QtGui, QtCore
    _QT = 'pyqt5'
except ImportError:
    try:
        from PySide6 import QtWidgets, QtGui, QtCore
        _QT = 'pyside6'
    except ImportError:
        pass

if _QT is None:
    sys.stderr.write(
        'usage-tray: Qt binding yok. Kur: `pip install PyQt5` (veya PySide6).\n'
        'Native tray opsiyoneldir — waybar rozeti + floating widget bağımsız çalışır.\n')
    sys.exit(1)

# Sinyal adı PyQt5/PySide arasında aynı: .connect kullanılır.
_COLORS = {
    'ok':   '#3ddc84',   # yeşil
    'warn': '#ffa500',   # amber
    'crit': '#ff6b6b',   # kırmızı
    'off':  '#6b7280',   # gri (sunucu kapalı)
}

_CURRENCY_SYMBOLS = {
    'USD': '$', 'EUR': '€', 'CNY': '¥', 'GBP': '£', 'TRY': '₺'
}


def _fetch():
    """(/v1/usage) → dict | None (sunucu kapalı)."""
    try:
        req = urllib.request.Request(USAGE_URL, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def _bar(pct):
    p = 0 if pct is None else max(0, min(100, pct))
    n = int(p // 10)
    return '█' * n + '░' * (10 - n)


# Son çare geri düşüş — gerçek eşik daima wire'dan (`data['thresholds']`) gelir.
# Tek yerde durması, üç ayrı yerde sessizce ayrışmasını engelliyor (Y6).
_FALLBACK_TH = {'warn': 75, 'crit': 90}


def _col(pct, th=None):
    if pct is None:
        return _COLORS['off']
    if th is None:
        th = dict(_FALLBACK_TH)
    return _COLORS['crit'] if pct >= th['crit'] else _COLORS['warn'] if pct >= th['warn'] else '#22d3ee'


def _cbar(pct, th=None):
    return '<span color="%s">%s</span>' % (_col(pct, th), _bar(pct))


def _round_half_up(v, decimals=1):
    """Half-up rounding (0.5 yukarı yuvarla)."""
    factor = 10 ** decimals
    return math.floor(v * factor + 0.5) / factor

def _cpct(v, th=None):
    if v is None:
        return '<span color="%s">—</span>' % _COLORS['off']
    return '<span color="%s">%s%%</span>' % (_col(v, th), _round_half_up(v, 1))


def _dur(sec):
    try:
        s = int(sec)
    except (TypeError, ValueError):
        return ''
    if s <= 0:
        return ''
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    if d > 0:
        return '%dg%ds' % (d, h)
    if h > 0:
        return '%ds%ddk' % (h, m)
    return '%ddk' % m


def _rst(sec):
    t = _dur(sec)
    return '  <span color="%s">↺%s</span>' % (_COLORS['off'], t) if t else ''


def _summarize(data):
    """(class, headline_pct, tooltip_html, menu_headline) döndür."""
    if not data:
        return 'off', None, 'usage-tracker kapalı (%s)' % BASE, 'usage-tracker · kapalı'
    th = data.get('thresholds') or {'warn': 75, 'crit': 90}
    provs = {p.get('id'): p for p in (data.get('providers') or [])}
    claude = provs.get('claude') or {}
    limits = claude.get('limits') or {}
    sess = limits.get('session') or {}
    week = limits.get('weekly') or {}
    wmodel = limits.get('weeklyModel') or {}
    sp = sess.get('pct')
    wp = week.get('pct')
    hi = max([v for v in (sp, wp) if v is not None] or [0])
    cls = 'crit' if hi >= th['crit'] else 'warn' if hi >= th['warn'] else 'ok'

    lines = ['<b>Claude</b>',
             '  %s oturum %s%s' % (_cbar(sp, th), _cpct(sp, th), _rst(sess.get('resetInSec'))),
             '  %s haftalık %s%s' % (_cbar(wp, th), _cpct(wp, th), _rst(week.get('resetInSec')))]
    if wmodel.get('pct') is not None:
        lines.append('  %s %s %s' % (_cbar(wmodel.get('pct'), th),
                                     wmodel.get('name') or 'model', _cpct(wmodel.get('pct'), th)))
    fc = week.get('forecast') or {}
    if fc.get('willExceed') and fc.get('etaText'):
        lines.append('  <span color="%s">⚠ %s</span>' % (_COLORS['warn'], fc.get('etaText')))
    # Bayat sayı işaretsiz gösterilmez: ağ kesildikten sonra son iyi yüzde donuyor ve
    # ikonun rengi "her şey yolunda" demeye devam ediyordu (2026-08-12 ölçümü, guard'da).
    lv = claude.get('live') or {}
    if lv.get('stale'):
        lines.append('  <span color="%s">⚠ bayat veri%s</span>'
                     % (_COLORS['warn'],
                        (' · %s önce' % _dur(lv.get('ageSec'))) if _dur(lv.get('ageSec')) else ''))
    spend = claude.get('spend') or {}
    curr = spend.get('currency', 'USD')
    headline = 'Claude · %d%%' % round(hi)
    if spend.get('today') is not None:
        lines.append('  💰 bugün %s · 30g %s' % (_num(spend.get('today'), curr), _num(spend.get('last30d'), curr)))
        headline = 'Claude %d%% · bugün %s' % (round(hi), _num(spend.get('today'), curr))
    for p in (data.get('providers') or []):
        if p.get('id') == 'claude':
            continue
        line = _provider_line(p, th)
        if line:
            lines.append(line)
    return cls, hi, '\n'.join(lines), headline


def _limit_lines(p, th):
    """Claude şeklindeki `limits` bloğunu tepsi ipucu satırlarına çevir.

    Anahtar kartın kimliği değil ALANI: bu bloğu yayınlayan her sağlayıcı (Codex) aynı
    satırları alır, tepsinin sağlayıcı adı bilmesine gerek kalmaz.
    """
    lim = p.get('limits')
    if not isinstance(lim, dict):
        return ''
    out = ''
    for key, label in (('session', 'session'), ('weekly', 'weekly ')):
        b = lim.get(key)
        if not isinstance(b, dict):
            continue
        out += '\n  %s %s %s' % (_cbar(b.get('pct'), th), label, _cpct(b.get('pct'), th))
        if b.get('expired'):
            # Ölçüm ölü bir pencereye ait: sıfır demek yerine sebebini yaz.
            out += '  <span color="%s">↺ pencere sıfırlandı</span>' % _COLORS['off']
        else:
            out += _rst(b.get('resetInSec'))
    return out


def _provider_line(p, th=None):
    if th is None:
        th = dict(_FALLBACK_TH)
    kind = p.get('kind')
    name = p.get('name', '?')
    st = p.get('status')
    if st == 'error':
        return '<b>%s</b>  <span color="%s">⚠ hata</span>' % (name, _COLORS['crit'])
    if kind == 'spend':
        sp = p.get('spend') or {}
        bal = p.get('balance') or {}
        lim = p.get('limit') or {}
        curr = p.get('currency', 'USD')
        bits = []
        if sp.get('today') is not None:
            bits.append('bugün %s' % _num(sp.get('today'), curr))
        if sp.get('month') is not None:
            bits.append('ay %s' % _num(sp.get('month'), curr))
        if bal.get('remaining') is not None:
            bits.append('kredi %s' % _num(bal.get('remaining'), curr))
        line = '<b>%s</b>  %s' % (name, ' · '.join(bits)) if bits else '<b>%s</b>' % name
        if lim.get('pct') is not None:
            line += '\n  %s limit %s' % (_cbar(lim.get('pct'), th), _cpct(lim.get('pct'), th))
            if lim.get('reset'):
                line += '  <span color="%s">↺%s</span>' % (_COLORS['off'], lim.get('reset'))
        return line if (bits or lim.get('pct') is not None) else None
    if kind == 'quota':
        q = p.get('quota') or {}
        if q.get('pct') is None:
            return None
        line = '<b>%s</b>\n  %s %s  <span color="%s">%s/%s %s</span>' % (
            name, _cbar(q.get('pct'), th), _cpct(q.get('pct'), th), _COLORS['off'],
            q.get('used'), q.get('limit'), q.get('unit', ''))
        if q.get('reset'):
            t = _dur(q.get('reset') - time.time())
            if t:
                line += '  <span color="%s">↺%s</span>' % (_COLORS['off'], t)
        return line
    if kind == 'tokens':
        if st == 'offline':
            return None
        tot = (p.get('tokens') or {}).get('total') or 0
        usd = (p.get('total') or {}).get('usd') or 0
        curr = p.get('currency', 'USD')
        plan = p.get('plan')
        head = '<b>%s</b>%s  %sM tok%s' % (
            name, (' <span color="%s">%s</span>' % (_COLORS['off'], plan)) if plan else '',
            round(tot / 1e6, 1), (' ≈ %s' % _num(usd, curr)) if usd else '')
        return head + _limit_lines(p, th)
    if kind == 'local':
        if st == 'offline':
            return '<b>%s</b>  <span color="%s">servis kapalı</span>' % (name, _COLORS['off'])
        return '<b>%s</b>  %s model' % (name, p.get('modelCount', 0))
    return None


def _pct(v):
    return '—' if v is None else '%s%%' % _round_half_up(v, 1)


def _num(v, currency='USD'):
    try:
        symbol = _CURRENCY_SYMBOLS.get(currency, currency + ' ' if currency else '$')
        return symbol + ('%.2f' % float(v))
    except (TypeError, ValueError):
        return '0.00'


def _make_icon(cls):
    """Renkli daire ikon (QIcon)."""
    size = 64
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    pnt = QtGui.QPainter(pm)
    pnt.setRenderHint(QtGui.QPainter.Antialiasing)
    color = QtGui.QColor(_COLORS.get(cls, _COLORS['off']))
    pnt.setBrush(color)
    pnt.setPen(QtGui.QPen(QtGui.QColor('#0b0f14'), 4))
    pnt.drawEllipse(6, 6, size - 12, size - 12)
    pnt.end()
    return QtGui.QIcon(pm)


def _log(satir):
    """Tepsi olayını dosyaya ekle. Yazamazsak sessiz kal — log uğruna tepsi ölmez."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open('a', encoding='utf-8') as fh:
            fh.write('%s  %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), satir))
    except OSError:
        pass


def _son_log_satiri():
    """Kardeş scriptin son stderr satırı — kullanıcıya gösterilecek kısa sebep."""
    try:
        satirlar = [s.strip() for s in LOG_PATH.read_text(
            encoding='utf-8', errors='replace').splitlines() if s.strip()]
    except OSError:
        return ''
    return satirlar[-1] if satirlar else ''


def _run_surface(arg):
    """usage-widget kardeş scriptini çağır. `(ok, mesaj)` döndürür.

    ⚠️ Eskiden stderr DEVNULL'a gidiyor ve dönüş değeri yalnız Popen'in patlayıp
    patlamadığını ölçüyordu → script `exit 2` ile ölse bile True dönüyordu.
    Menüden "Widget Aç/Kapa"ya basan kullanıcı hiçbir tepki almıyor, hata da
    hiçbir yere düşmüyordu. Artık stderr log dosyasına akar ve erken ölen süreç
    yakalanır. (BL-205)
    """
    widget = SELF_DIR / 'usage-widget'
    if not widget.exists():
        mesaj = 'usage-widget kurulu değil: %s' % widget
        _log(mesaj)
        return False, mesaj

    # stderr'i BORU değil DOSYA'ya veriyoruz: `open` alt kabuğu (`_place &`) fd'yi
    # ~8 sn açık tutuyor; boru dolarsa o süreç bloke olurdu, dosya olmaz.
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        hedef = LOG_PATH.open('a', encoding='utf-8')
    except OSError:
        hedef = None
    try:
        proc = subprocess.Popen(
            [str(widget), arg],
            stdout=subprocess.DEVNULL,
            stderr=(hedef or subprocess.DEVNULL))
    except OSError as exc:
        mesaj = 'usage-widget %s başlatılamadı: %s' % (arg, exc)
        _log(mesaj)
        return False, mesaj
    finally:
        if hedef is not None:
            hedef.close()   # çocuk kendi kopyasını (dup) taşır

    # Script kısa ömürlü: pencereyi arka plana atıp (setsid+disown) hemen çıkar.
    # Bu pencerede ölürse çıkış kodunu görürüz; ölmezse çalışıyor kabul edilir.
    bitis = time.monotonic() + SURFACE_WAIT_SEC
    while time.monotonic() < bitis:
        rc = proc.poll()
        if rc is None:
            time.sleep(0.05)
            continue
        if rc == 0:
            return True, ''
        mesaj = 'usage-widget %s başarısız (çıkış %d)' % (arg, rc)
        ayrinti = _son_log_satiri()
        _log(mesaj)
        return False, (mesaj + ' — ' + ayrinti) if ayrinti else mesaj
    return True, ''


def _open_panel():
    ok, mesaj = _run_surface('open')
    if ok:
        return ''
    # Widget yoksa/patladıysa panel yine de açılabilir — tarayıcıya düş.
    try:
        subprocess.Popen(['xdg-open', BASE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return ''
    except OSError as exc:
        yedek = 'xdg-open da başarısız: %s' % exc
        _log(yedek)
        return (mesaj + ' · ' + yedek) if mesaj else yedek


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = QtWidgets.QSystemTrayIcon()
        self.icon.setIcon(_make_icon('off'))
        menu = QtWidgets.QMenu()
        self.header = menu.addAction('usage-tracker')   # canlı manşet (devre dışı bilgi satırı)
        self.header.setEnabled(False)
        menu.addSeparator()
        menu.addAction('Panel Aç', self._panel_ac)
        menu.addAction('Widget Aç/Kapa', self._widget_toggle)
        menu.addAction('Yenile', self.refresh)
        menu.addSeparator()
        menu.addAction('Çıkış', app.quit)
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activate)
        self.icon.show()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(INTERVAL)
        self.refresh()

    def _uyar(self, mesaj):
        """Sessiz başarısızlığı görünür kıl — tepsi balonu + log yolu."""
        if not mesaj:
            return
        try:
            self.icon.showMessage('usage-tracker', mesaj + '\n(ayrıntı: %s)' % LOG_PATH,
                                  QtWidgets.QSystemTrayIcon.Warning, 6000)
        except Exception:
            # Bazı SNI host'ları showMessage'ı desteklemez; log yine de yazıldı.
            sys.stderr.write('usage-tray: %s\n' % mesaj)

    def _panel_ac(self):
        self._uyar(_open_panel())

    def _widget_toggle(self):
        ok, mesaj = _run_surface('toggle')
        if not ok:
            self._uyar(mesaj)

    def _on_activate(self, reason):
        # Trigger = sol tık
        trig = getattr(QtWidgets.QSystemTrayIcon, 'Trigger', None)
        if reason == trig:
            self._panel_ac()

    def refresh(self):
        cls, hi, tip, headline = _summarize(_fetch())
        self.icon.setIcon(_make_icon(cls))
        # Qt tooltip pango/HTML destekler (rich text)
        self.icon.setToolTip(tip.replace('\n', '<br>'))
        self.header.setText(headline)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        sys.stderr.write(
            'usage-tray: sistem tepsisi mevcut değil. Wayland/Hyprland\'da waybar "tray" '
            'modülünü aç (SNI host). GNOME için AppIndicator eklentisi gerekebilir.\n')
        sys.exit(2)
    Tray(app)
    sys.exit(app.exec() if _QT == 'pyside6' else app.exec_())


if __name__ == '__main__':
    main()

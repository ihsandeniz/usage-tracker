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
import os
import subprocess
import sys
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


def _fetch():
    """(/v1/usage) → dict | None (sunucu kapalı)."""
    try:
        req = urllib.request.Request(USAGE_URL, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def _summarize(data):
    """(class, headline_pct, tooltip_text) döndür."""
    if not data:
        return 'off', None, 'usage-tracker kapalı (%s)' % BASE
    provs = {p.get('id'): p for p in (data.get('providers') or [])}
    claude = provs.get('claude') or {}
    limits = claude.get('limits') or {}
    sess = (limits.get('session') or {}).get('pct')
    week = (limits.get('weekly') or {}).get('pct')
    hi = max([v for v in (sess, week) if v is not None] or [0])
    cls = 'crit' if hi >= 90 else 'warn' if hi >= 75 else 'ok'

    lines = ['<b>Claude</b>  oturum %s · haftalık %s' % (_pct(sess), _pct(week))]
    spend = claude.get('spend') or {}
    if spend.get('today') is not None:
        lines.append('  bugün $%s · 30g $%s' % (_num(spend.get('today')), _num(spend.get('last30d'))))
    for p in (data.get('providers') or []):
        if p.get('id') == 'claude':
            continue
        line = _provider_line(p)
        if line:
            lines.append(line)
    return cls, hi, '\n'.join(lines)


def _provider_line(p):
    kind = p.get('kind')
    name = p.get('name', '?')
    st = p.get('status')
    if kind == 'spend':
        sp = p.get('spend') or {}
        bal = p.get('balance') or {}
        bits = []
        if sp.get('today') is not None:
            bits.append('bugün $%s' % _num(sp.get('today')))
        if sp.get('month') is not None:
            bits.append('ay $%s' % _num(sp.get('month')))
        if bal.get('remaining') is not None:
            bits.append('kredi $%s' % _num(bal.get('remaining')))
        return '<b>%s</b>  %s' % (name, ' · '.join(bits)) if bits else None
    if kind == 'quota':
        q = p.get('quota') or {}
        if q.get('limit'):
            return '<b>%s</b>  %s/%s %s (%s)' % (name, q.get('used'), q.get('limit'),
                                                 q.get('unit', ''), _pct(q.get('pct')))
        return None
    if kind == 'tokens':
        if st == 'offline':
            return None
        tot = (p.get('tokens') or {}).get('total') or 0
        usd = (p.get('total') or {}).get('usd') or 0
        return '<b>%s</b>  %sM tok%s' % (name, round(tot / 1e6, 1),
                                         (' ≈ $%s' % _num(usd)) if usd else '')
    if kind == 'local':
        if st == 'offline':
            return None
        return '<b>%s</b>  %s model' % (name, p.get('modelCount', 0))
    return None


def _pct(v):
    return '—' if v is None else '%s%%' % (round(v * 10) / 10)


def _num(v):
    try:
        return '%.2f' % float(v)
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


def _run_surface(arg):
    """usage-widget kardeş scriptini çağır (varsa)."""
    widget = SELF_DIR / 'usage-widget'
    if widget.exists():
        try:
            subprocess.Popen([str(widget), arg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            pass
    return False


def _open_panel():
    if not _run_surface('open'):
        try:
            subprocess.Popen(['xdg-open', BASE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = QtWidgets.QSystemTrayIcon()
        self.icon.setIcon(_make_icon('off'))
        menu = QtWidgets.QMenu()
        menu.addAction('Panel Aç', _open_panel)
        menu.addAction('Widget Aç/Kapa', lambda: _run_surface('toggle'))
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

    def _on_activate(self, reason):
        # Trigger = sol tık
        trig = getattr(QtWidgets.QSystemTrayIcon, 'Trigger', None)
        if reason == trig:
            _open_panel()

    def refresh(self):
        cls, hi, tip = _summarize(_fetch())
        self.icon.setIcon(_make_icon(cls))
        # Qt tooltip pango/HTML destekler (rich text)
        self.icon.setToolTip(tip.replace('\n', '<br>'))


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

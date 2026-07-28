#!/usr/bin/env python3
"""
Kullanım motoru (bağımsız — Odak Paneli/dashboard'dan tamamen ayrı).

İki boyut:
  1) LİMİT (FAZ 0): ~/.claude/projects/**/*.jsonl turn'lerinden AĞIRLIKLI birim → kullanıcının
     bir kez girdiği gerçek /usage %'siyle çapalanır. 5s oturum + 7g haftalık pencere, reset geri sayım.
  2) HARCAMA (FAZ 1): aynı turn'lere models.dev gerçek fiyatı uygulanır → Today/Yesterday/30g $ + per-model.

Kaynak dosyalar SADECE OKUNUR. Kalibrasyon proje-yerel usage_calib.json'da. Stdlib-only.
"""
import json
import os
import threading
import time
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from . import pricing
from . import live

CLAUDE_PROJECTS_DIR  = Path.home() / '.claude' / 'projects'
CALIB_PATH           = Path(__file__).resolve().parent.parent / 'usage_calib.json'
USAGE_SESSION_WINDOW = 5 * 3600            # sn — 5 saatlik oturum limiti
USAGE_WEEKLY_WINDOW  = 7 * 24 * 3600       # sn — haftalık limit
# Kalibrasyondan gelen yüzde bunu aşarsa bütçe tahmini bayat/yanlış demektir —
# gerçekten limit aşımı değil. Üstünü "bilinmiyor" say (bkz. bar() içindeki not).
_CALIB_SANITY_PCT    = 110.0

_CACHE = {}                                # {path: (mtime, size, [Turn...])}
_LOCK  = threading.Lock()

# ms=epoch ms · model=ham model str · units=ağırlıklı birim · raw=ham token toplamı
# inp/out/cc/cr = fiyatlama için token bileşenleri · mid=dedup için message id
Turn = namedtuple('Turn', 'ms model units raw inp out cc cr mid')


# ── ağırlık (limit boyutu — gerçek $ DEĞİL, kalibrasyon mutlak ölçeği düzeltir) ──
def _model_weight(model: str) -> float:
    m = (model or '').lower()
    if 'opus' in m or 'fable' in m: return 1.0
    if 'sonnet' in m: return 0.25
    if 'haiku' in m:  return 0.05
    return 0.5


def _short_model(model: str) -> str:
    m = (model or '').lower()
    for tag in ('opus', 'sonnet', 'haiku', 'fable'):
        if tag in m:
            return tag
    parts = m.split('-')
    return parts[1] if len(parts) > 1 else (m or 'model')


def _iso_to_ms(ts: str):
    try:
        return int(datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp() * 1000)
    except Exception:
        return None


def _scan_turns(since_ms: int):
    """since_ms'ten yeni turn'ler (dedup'lı). Dosya-mtime cache'li."""
    out = []
    if not CLAUDE_PROJECTS_DIR.exists():
        return out
    cutoff_mtime = (since_ms / 1000) - 3600            # biraz pay
    seen_ids = set()

    def _walk_jsonl(root: Path):
        """Recursively yield .jsonl file paths using os.scandir (stat-cached DirEntry)."""
        try:
            for entry in os.scandir(root):
                if entry.is_file(follow_symlinks=False) and entry.name.endswith('.jsonl'):
                    yield Path(entry.path)
                elif entry.is_dir(follow_symlinks=False):
                    yield from _walk_jsonl(Path(entry.path))
        except OSError:
            pass

    for f in _walk_jsonl(CLAUDE_PROJECTS_DIR):
        try:
            stt = f.stat()
        except OSError:
            continue
        if stt.st_mtime < cutoff_mtime:
            continue
        key = str(f)
        with _LOCK:
            cached = _CACHE.get(key)
        if cached and cached[0] == stt.st_mtime and cached[1] == stt.st_size:
            turns = cached[2]
        else:
            turns = []
            try:
                with f.open(encoding='utf-8') as fh:
                    for line in fh:
                        if '"usage"' not in line:
                            continue
                        try:
                            o = json.loads(line)
                        except ValueError:
                            continue
                        msg = o.get('message')
                        if not isinstance(msg, dict):
                            continue
                        u = msg.get('usage')
                        ts = o.get('timestamp')
                        if not isinstance(u, dict) or not ts:
                            continue
                        ms = _iso_to_ms(ts)
                        if ms is None:
                            continue
                        model = msg.get('model', '')
                        inp = u.get('input_tokens', 0) or 0
                        out_ = u.get('output_tokens', 0) or 0
                        cc  = u.get('cache_creation_input_tokens', 0) or 0
                        cr  = u.get('cache_read_input_tokens', 0) or 0
                        raw = inp + out_ + cc + cr
                        # Ağırlıklı limit birim heuristigi: output 4×, input 1×, cache_write (cc) 1×, cache_read (cr) 0.1×.
                        # Bu ağırlıklar gerçek /usage % ile kalibrasyon dosyasında çapalanır (usage_calib.json).
                        # Sayısal katsayıları değiştirmek kalibrasyon ilişkisini bozar — düzeltmeden önce live veriyle doğrula.
                        units = _model_weight(model) * (out_ * 4 + inp + cc + cr * 0.1)
                        turns.append(Turn(ms, model, units, raw, inp, out_, cc, cr,
                                          msg.get('id') or ''))
            except OSError:
                continue
            with _LOCK:
                _CACHE[key] = (stt.st_mtime, stt.st_size, turns)
        for t in turns:
            if t.ms < since_ms:
                continue
            if t.mid:
                if t.mid in seen_ids:
                    continue
                seen_ids.add(t.mid)
            out.append(t)
    return out


# ── kalibrasyon dosyası ──────────────────────────────────────────────────────
def _load_calib() -> dict:
    if not CALIB_PATH.exists():
        return {}
    try:
        d = json.loads(CALIB_PATH.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_calib(d: dict) -> bool:
    try:
        tmp = CALIB_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(CALIB_PATH)
        return True
    except Exception:
        return False


def _roll_reset(reset_ms: int, window_ms: int, now_ms: int) -> int:
    if not reset_ms:
        return reset_ms
    while now_ms >= reset_ms:
        reset_ms += window_ms
    return reset_ms


def _top_model(turns, since_ms):
    agg = {}
    for t in turns:
        if t.ms >= since_ms:
            n = _short_model(t.model)
            agg[n] = agg.get(n, 0) + t.units
    return max(agg, key=agg.get) if agg else None


def _compute_forecast(pct, elapsed_ms, reset_at_ms, now_ms, window_ms):
    """
    Proaktif forecast hesabı: bu hızla ne zaman dolar?

    Args:
        pct: mevcut yüzde (0-100)
        elapsed_ms: pencere başından bu yana geçen süre (ms)
        reset_at_ms: reset zamanı (epoch ms)
        now_ms: şu an (epoch ms)
        window_ms: pencere süresi (ms) — oturum 5h, hafta 7d

    Returns:
        dict: {willExceed: bool, etaMs: int|None, etaText: str|None}
        — willExceed: true ise reset öncesinde dolar
        — etaMs: dolar zamanı (epoch ms), None ise hesaplayamadık
        — etaText: "2g 4s" formatta metin, None ise gösterme

    Güven kapısı:
        — elapsed < %5 pencere (gürültü) → forecast=null
        — pct < %2 (başında) → forecast=null
        — pct >= 99 (yaklaşan) → göster ama emin
    """
    # Güven kapısı
    if pct is None or pct < 0:
        return {'willExceed': False, 'etaMs': None, 'etaText': None}

    if elapsed_ms is None or elapsed_ms < 0:
        return {'willExceed': False, 'etaMs': None, 'etaText': None}

    # Pencere çok yeni başladı → gürültülü
    min_elapsed = max(window_ms * 0.05, 300_000)  # %5 veya 5 dakika, hangisi büyükse
    if elapsed_ms < min_elapsed:
        return {'willExceed': False, 'etaMs': None, 'etaText': None}

    # Yüzde çok düşük → gürültülü
    if pct < 2.0:
        return {'willExceed': False, 'etaMs': None, 'etaText': None}

    # Burn rate hesapla: %/ms
    burn_rate = pct / elapsed_ms  # unit: percent points per ms

    # Kalan yüzdeyi tüketme süresi (ms)
    remaining_pct = 100.0 - pct
    if burn_rate <= 0:
        return {'willExceed': False, 'etaMs': None, 'etaText': None}

    time_to_full_ms = remaining_pct / burn_rate
    projected_full_at = now_ms + time_to_full_ms

    # Reset'ten ÖNCE dolar mı?
    will_exceed = projected_full_at < reset_at_ms if reset_at_ms else False

    if not will_exceed:
        return {'willExceed': False, 'etaMs': None, 'etaText': None}

    # ETA text'i oluştur
    eta_sec = int(time_to_full_ms / 1000)
    if eta_sec < 0:
        eta_sec = 0

    hours = eta_sec // 3600
    minutes = (eta_sec % 3600) // 60
    seconds = eta_sec % 60

    parts = []
    if hours > 0:
        parts.append(f'{hours}s')
    if minutes > 0 or hours == 0:
        parts.append(f'{minutes}dk')
    if seconds > 0 and hours == 0 and minutes < 5:
        parts.append(f'{seconds}sn')

    eta_text = ' '.join(parts) if parts else '≈ şimdi'

    return {
        'willExceed': True,
        'etaMs': int(projected_full_at),
        'etaText': f'≈ {eta_text} sonra dolar',
    }


# ── FAZ 0: LİMİTLER ──────────────────────────────────────────────────────────
def compute_usage() -> dict:
    if os.environ.get('USAGE_DEMO') == '1':
        from . import demo
        return demo.compute_usage()
    now_ms = int(time.time() * 1000)
    calib  = _load_calib()
    sess_win = USAGE_SESSION_WINDOW * 1000
    week_win = USAGE_WEEKLY_WINDOW * 1000

    sess_reset = _roll_reset(int(calib.get('sessionResetAtMs') or 0), sess_win, now_ms)
    week_reset = _roll_reset(int(calib.get('weeklyResetAtMs')  or 0), week_win, now_ms)
    if calib and (sess_reset != calib.get('sessionResetAtMs') or week_reset != calib.get('weeklyResetAtMs')):
        calib['sessionResetAtMs'] = sess_reset
        calib['weeklyResetAtMs']  = week_reset
        _save_calib(calib)

    sess_start = (sess_reset - sess_win) if sess_reset else (now_ms - sess_win)
    week_start = (week_reset - week_win) if week_reset else (now_ms - week_win)

    turns = _scan_turns(min(sess_start, week_start))
    sess_u = sum(t.units for t in turns if t.ms >= sess_start)
    sess_r = sum(t.raw   for t in turns if t.ms >= sess_start)
    week_u = sum(t.units for t in turns if t.ms >= week_start)
    week_r = sum(t.raw   for t in turns if t.ms >= week_start)

    mname = (calib.get('weeklyModelName') or _top_model(turns, week_start) or '')
    mod_u = sum(t.units for t in turns if t.ms >= week_start and mname and mname in (t.model or '').lower())
    mod_r = sum(t.raw   for t in turns if t.ms >= week_start and mname and mname in (t.model or '').lower())

    def bar(units, raw, budget, reset_ms, window_ms=None):
        pct = round(units / budget * 100, 1) if budget else None
        # A calibration-derived percentage way past 100 doesn't mean "you blew
        # through your limit" — it means the calibrated budget is wrong (stale
        # calibration, plan change, a new machine). Reporting 410% as a red
        # CRIT badge is a confident lie; "unknown" is the honest answer. The
        # live overlay, when it succeeds, overwrites this with the real number.
        suspect = pct is not None and pct > _CALIB_SANITY_PCT
        if suspect:
            pct = None
        forecast = None
        if pct is not None and reset_ms and window_ms:
            window_start_ms = reset_ms - window_ms
            elapsed_ms = now_ms - window_start_ms
            forecast = _compute_forecast(pct, elapsed_ms, reset_ms, now_ms, window_ms)
        return {
            'units':      int(units),
            'rawTokens':  int(raw),
            'budget':     int(budget) if budget else None,
            'pct':        pct,
            'calibSuspect': suspect or None,
            'resetAtMs':  reset_ms or None,
            'resetInSec': int((reset_ms - now_ms) / 1000) if reset_ms else None,
            'forecast':   forecast,
        }

    out = {
        'updated':     datetime.now().strftime('%H:%M:%S'),
        'calibrated':  bool(calib.get('sessionBudget') or calib.get('weeklyBudget')),
        'source':      'calibration',
        'session':     bar(sess_u, sess_r, calib.get('sessionBudget'),      sess_reset, sess_win),
        'weeklyAll':   bar(week_u, week_r, calib.get('weeklyBudget'),        week_reset, week_win),
        'weeklyModel': {**bar(mod_u, mod_r, calib.get('weeklyModelBudget'),  week_reset, week_win), 'name': mname} if mname else None,
        'thresholds':  calib.get('thresholds') if isinstance(calib.get('thresholds'), dict) else {'warn': 75, 'crit': 90},
    }
    _overlay_live(out, now_ms)
    return out


def _reset_in(resets_at, now_ms):
    """resets_at (ISO str veya epoch s/ms) → (resetAtMs, resetInSec)."""
    if resets_at is None:
        return None, None
    ms = None
    if isinstance(resets_at, str):
        ms = _iso_to_ms(resets_at)
    elif isinstance(resets_at, (int, float)):
        ms = int(resets_at * 1000) if resets_at < 1e12 else int(resets_at)
    if ms is None:
        return None, None
    return ms, int((ms - now_ms) / 1000)


def _overlay_live(out: dict, now_ms: int):
    """Anthropic'in canlı gerçek utilization %'sini limit barlarına bindir (kalibrasyonsuz)."""
    lv = live.fetch()
    out['live'] = {'ok': lv.get('ok'), 'error': lv.get('error'), 'cached': lv.get('cached'),
                   'stale': lv.get('stale'), 'rateLimited': lv.get('rateLimited'),
                   'rateLimitTier': lv.get('rateLimitTier')}
    if not lv.get('ok'):
        return
    windows = lv.get('windows') or {}
    def apply(bar, win_key):
        w = windows.get(win_key)
        if not bar or not w or w.get('utilization') is None:
            return
        bar['pct'] = w['utilization']
        bar['live'] = True
        rms, rsec = _reset_in(w.get('resets_at'), now_ms)
        if rms:
            bar['resetAtMs'], bar['resetInSec'] = rms, rsec
        if w.get('remaining') is not None:
            bar['remaining'] = w['remaining']
    apply(out['session'],   'five_hour')
    apply(out['weeklyAll'], 'seven_day')
    if out.get('weeklyModel'):
        mn = (out['weeklyModel'].get('name') or '').lower()
        apply(out['weeklyModel'], 'seven_day_opus' if 'opus' in mn else
              'seven_day_sonnet' if 'sonnet' in mn else 'seven_day')
    out['source'] = 'live'
    out['calibrated'] = True     # canlı = kalibrasyona gerek yok


def calibrate_usage(req) -> tuple:
    if not isinstance(req, dict):
        return False, 'geçersiz gövde'
    now_ms = int(time.time() * 1000)

    # Alan-bazlı parse — her alan kendi validation'ı
    try:
        sess_reset = int(req.get('sessionResetAtMs') or 0)
    except (TypeError, ValueError):
        return False, 'sessionResetAtMs geçersiz'

    try:
        week_reset = int(req.get('weeklyResetAtMs') or 0)
    except (TypeError, ValueError):
        return False, 'weeklyResetAtMs geçersiz'

    try:
        sess_pct = float(req.get('sessionPct') or 0)
    except (TypeError, ValueError):
        return False, 'sessionPct geçersiz'

    try:
        week_pct = float(req.get('weeklyAllPct') or 0)
    except (TypeError, ValueError):
        return False, 'weeklyAllPct geçersiz'

    try:
        mname = (req.get('weeklyModelName') or '').lower().strip()[:20] or None
    except (TypeError, ValueError, AttributeError):
        return False, 'weeklyModelName geçersiz'

    try:
        mod_pct = float(req.get('weeklyModelPct') or 0)
    except (TypeError, ValueError):
        return False, 'weeklyModelPct geçersiz'
    if sess_reset and not (now_ms - 3600_000 <= sess_reset <= now_ms + 6 * 3600_000):
        return False, 'oturum reset zamanı aralık dışı'
    if week_reset and not (now_ms - 3600_000 <= week_reset <= now_ms + 8 * 24 * 3600_000):
        return False, 'haftalık reset zamanı aralık dışı'
    for p in (sess_pct, week_pct, mod_pct):
        if not (0 <= p <= 100):
            return False, 'yüzde 0-100 aralığında olmalı'

    sess_win = USAGE_SESSION_WINDOW * 1000
    week_win = USAGE_WEEKLY_WINDOW * 1000
    sess_start = (sess_reset - sess_win) if sess_reset else (now_ms - sess_win)
    week_start = (week_reset - week_win) if week_reset else (now_ms - week_win)
    turns = _scan_turns(min(sess_start, week_start))
    sess_u = sum(t.units for t in turns if t.ms >= sess_start)
    week_u = sum(t.units for t in turns if t.ms >= week_start)
    mod_u  = sum(t.units for t in turns if t.ms >= week_start and mname and mname in (t.model or '').lower()) if mname else 0

    calib = _load_calib()
    calib['sessionResetAtMs']  = sess_reset
    calib['weeklyResetAtMs']   = week_reset
    calib['sessionBudget']     = round(sess_u / (sess_pct / 100)) if sess_pct > 0 and sess_u > 0 else None
    calib['weeklyBudget']      = round(week_u / (week_pct / 100)) if week_pct > 0 and week_u > 0 else None
    calib['weeklyModelName']   = mname
    calib['weeklyModelBudget'] = round(mod_u / (mod_pct / 100)) if mname and mod_pct > 0 and mod_u > 0 else None
    calib['calibratedAtMs']    = now_ms
    th = req.get('thresholds')
    if isinstance(th, dict):
        w, c = th.get('warn'), th.get('crit')
        if isinstance(w, (int, float)) and isinstance(c, (int, float)) and 0 < w < c <= 100:
            calib['thresholds'] = {'warn': w, 'crit': c}
    ok = _save_calib(calib)
    return (ok, None if ok else 'kaydedilemedi')


# ── FAZ 1: GERÇEK $ HARCAMA ──────────────────────────────────────────────────
def _day_start_ms(dt: datetime) -> int:
    return int(datetime(dt.year, dt.month, dt.day).timestamp() * 1000)


def compute_spend(days: int = 30) -> dict:
    """Today / Yesterday / son N gün gerçek $ + per-model + per-day kırılım (yerel gün sınırı)."""
    if os.environ.get('USAGE_DEMO') == '1':
        from . import demo
        return demo.compute_spend(days)
    now = datetime.now()
    now_ms = int(now.timestamp() * 1000)
    today_start = _day_start_ms(now)
    yday_start  = today_start - 86_400_000
    window_start = today_start - (days - 1) * 86_400_000

    turns = _scan_turns(window_start)

    today = yesterday = total = 0.0
    by_model = {}     # full model -> {usd, tokens{...}, source, short}
    by_day = {}       # 'YYYY-MM-DD' -> usd
    estimated = set()
    tokens_total = {'input': 0, 'output': 0, 'cache_write': 0, 'cache_read': 0}

    for t in turns:
        usd, source = pricing.turn_cost_usd(t.inp, t.out, t.cc, t.cr, t.model)
        total += usd
        if t.ms >= today_start:
            today += usd
        elif t.ms >= yday_start:
            yesterday += usd

        day_key = datetime.fromtimestamp(t.ms / 1000).strftime('%Y-%m-%d')
        by_day[day_key] = by_day.get(day_key, 0.0) + usd

        mk = t.model or '(bilinmiyor)'
        e = by_model.setdefault(mk, {
            'usd': 0.0, 'source': source, 'short': _short_model(t.model),
            'tokens': {'input': 0, 'output': 0, 'cache_write': 0, 'cache_read': 0},
        })
        e['usd'] += usd
        e['tokens']['input']       += t.inp
        e['tokens']['output']      += t.out
        e['tokens']['cache_write'] += t.cc
        e['tokens']['cache_read']  += t.cr
        tokens_total['input']       += t.inp
        tokens_total['output']      += t.out
        tokens_total['cache_write'] += t.cc
        tokens_total['cache_read']  += t.cr
        if source in ('estimate', 'unknown'):
            estimated.add(mk)

    model_list = sorted(
        ({'model': k, **v, 'usd': round(v['usd'], 4)} for k, v in by_model.items()
         if pricing._norm(k) not in ('', '<synthetic>', 'synthetic')),
        key=lambda x: x['usd'], reverse=True,
    )
    day_list = [{'day': d, 'usd': round(by_day.get(d, 0.0), 4)}
                for d in (datetime.fromtimestamp((window_start + i * 86_400_000) / 1000).strftime('%Y-%m-%d')
                          for i in range(days))]

    return {
        'currency':      'USD',
        'updated':       now.strftime('%H:%M:%S'),
        'windowDays':    days,
        'today':         round(today, 4),
        'yesterday':     round(yesterday, 4),
        'total':         round(total, 4),
        'tokensTotal':   tokens_total,
        'byModel':       model_list,
        'byDay':         day_list,
        'priceComplete': len(estimated) == 0,
        'estimatedModels': sorted(estimated),
        'note': '1M-context (opus[1m]) premium ve batch/service_tier indirimi ayrıştırılmaz; '
                'estimate modelleri price_overrides.json ile düzeltilebilir.',
    }


# ── /v1/usage — interop wire-format (overlay/waybar/eww için stabil sözleşme) ──
def usage_wire() -> dict:
    if os.environ.get('USAGE_DEMO') == '1':
        from . import demo
        return demo.usage_wire()
    now_ms = int(time.time() * 1000)
    limits = compute_usage()
    spend  = compute_spend(30)

    def limit_bar(b):
        if not b:
            return None
        # 'used' = v0.2.0'da yayınlanan interop alan adı — dış tüketiciler (eww/tray/kullanıcı
        # scriptleri) buna bağlı, kaldırma. 'units' /api/usage ile aynı adı taşısın diye EK
        # olarak veriliyor; ikisi aynı değer. Tek ada indirme v0.3.0 (kırıcı) işi.
        return {'pct': b.get('pct'), 'used': b.get('units'), 'units': b.get('units'),
                'budget': b.get('budget'), 'calibSuspect': b.get('calibSuspect'),
                'resetAtMs': b.get('resetAtMs'), 'resetInSec': b.get('resetInSec')}

    claude = {
        'id': 'claude', 'name': 'Claude Code',
        'calibrated': limits['calibrated'],
        'limits': {
            'session': limit_bar(limits['session']),
            'weekly':  limit_bar(limits['weeklyAll']),
            'weeklyModel': ({**limit_bar(limits['weeklyModel']), 'name': limits['weeklyModel']['name']}
                            if limits.get('weeklyModel') else None),
            'thresholds': limits['thresholds'],
        },
        'spend': {
            'currency': spend['currency'],
            'today': spend['today'], 'yesterday': spend['yesterday'], 'last30d': spend['total'],
            'byModel': [{'model': m['model'], 'short': m['short'], 'usd': m['usd'], 'source': m['source']}
                        for m in spend['byModel']],
            'priceComplete': spend['priceComplete'],
            'estimatedModels': spend['estimatedModels'],
        },
    }
    return {
        'schema': 'usage/v1',
        'generatedAtMs': now_ms,
        'generatedAt': datetime.now().isoformat(timespec='seconds'),
        'providers': [claude] + compute_providers(30),   # + codex/ollama/openrouter (FAZ 2)
    }


# ── FAZ 2: ÇOK-SAĞLAYICI ──────────────────────────────────────────────────────
def compute_providers(days: int = 30) -> list:
    """Claude dışı sağlayıcı kartları (openrouter/codex/ollama). Yapılandırılmamış olan
       kart açmaz. Adaptör paketi kendi TTL cache'ini tutar."""
    if os.environ.get('USAGE_DEMO') == '1':
        from . import demo
        return demo.collect(days)
    try:
        from .providers import collect
        return collect(days)
    except Exception:
        return []


if __name__ == '__main__':
    # elle doğrulama: python3 -m usage.engine
    import pprint
    print('=== LİMİTLER ==='); pprint.pprint(compute_usage())
    print('\n=== HARCAMA (30g) ==='); s = compute_spend(30)
    print(f"Today ${s['today']} · Yesterday ${s['yesterday']} · 30g ${s['total']} · complete={s['priceComplete']}")
    for m in s['byModel']:
        print(f"  {m['model']:34s} [{m['source']:8s}] ${m['usd']:.4f}")

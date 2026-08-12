'use strict';
// usage-tracker UI — Vanilla JS, sıfır bağımlılık. /api/spend + /api/usage poll.

// ── i18n ──────────────────────────────────────────────────
let LANG = localStorage.getItem('ut_lang_v1')
  || ((navigator.language || 'en').toLowerCase().startsWith('tr') ? 'tr' : 'en');

const I18N = {
  en: {
    spend: 'Real Spending', limits: 'Rate Limits', calibration: 'Calibration (backup)', view: 'View',
    today: 'Today', yday: 'Yesterday', total: 'Last 30 days', month: 'This month',
    daily: 'Daily spending (30d)', max: 'max', dailyLimit: 'Daily limit',
    overview: 'Overview', provider: 'provider', modelCount: 'Installed model', used: 'Used',
    credit: 'Remaining credit', session: 'session', unreachable: 'unreachable',
    noData: 'no data', offline: 'service offline', uncalib: 'not calibrated',
    units: 'units', calibrated: '✓ calibrated', updated: 'updated', noModelData: 'no data',
    source: 'Source', refresh: 'Refresh', otherProviders: 'Other Providers',
    cSessPct: 'Session %', cSessReset: 'Session reset (HH:MM)',
    cWeekPct: 'Weekly (all) %', cWeekReset: 'Weekly reset (day+hour)',
    cModelName: 'Model name', cModelPct: 'Model %', calibrateBtn: 'Calibrate',
    phPct62: 'e.g. 62', phPct40: 'e.g. 40', phPct55: 'e.g. 55',
    pageTitle: 'usage-tracker · Claude usage & spending',
    provNote: 'An unconfigured provider opens no card. OpenRouter=real $ · Codex=subscription (API-equivalent $) · Ollama=local/free.',
    viewNote: 'Show the checked providers; unchecked ones are hidden from the panel/waybar/widget.',
    viewLocked: 'The Claude card cannot be hidden — the waybar badge, the tray and `guard` all read it.',
    footerSrc: 'source: ~/.claude/projects + ~/.codex + OpenRouter API (read-only)',
    provSearchPlaceholder: 'Search provider name...', provTabAll: 'All', provTabActive: 'Active',
    provTabApi: 'API key', provTabLocal: 'On disk', provNoMatch: 'No matches',
    provClearSearch: 'Clear the search',
    settings: 'Settings', setWarn: 'Warn threshold %', setCrit: 'Critical threshold %',
    setRefresh: 'Refresh (s)', setCurrency: 'Display currency', setSave: 'Save',
    setKeys: 'API keys', setSaved: '✓ saved', setSaving: 'saving…',
    setThreshNote: 'One place for the threshold: the panel, the waybar badge, the tray and the '
      + '`guard` command all read this pair.',
    setRateNote: 'You enter the rate — the app never fetches one. The converted amount is shown '
      + 'beside the dollars, with its rate and the day you entered it.',
    setKeysNote: 'This server never writes keys and never shows their value — only the environment '
      + 'variable name and whether it is set. To add a key: ./setup.sh --ui → step "keys".',
    keySet: 'set', keyUnset: 'not set', rateAt: 'rate', enteredOn: 'entered'
  },
  tr: {
    spend: 'Gerçek Harcama', limits: 'Claude Limitleri', calibration: 'Kalibrasyon (yedek)', view: 'Görünüm',
    today: 'Bugün', yday: 'Dün', total: 'Son 30 gün', month: 'Bu ay',
    daily: 'Günlük harcama (30g)', max: 'tavan', dailyLimit: 'Günlük limit',
    overview: 'Tek Bakış',
    provider: 'kaynak', modelCount: 'Kurulu model', used: 'Kullanılan',
    credit: 'Kalan kredi', session: 'oturum', unreachable: 'erişilemedi',
    noData: 'kaynak yok', offline: 'servis kapalı', uncalib: 'kalibre değil',
    units: 'birim', calibrated: '✓ kalibre edildi', updated: 'güncellendi', noModelData: 'veri yok',
    source: 'Kaynak', refresh: 'Yenile', otherProviders: 'Diğer Sağlayıcılar',
    cSessPct: 'Oturum %', cSessReset: 'Oturum reset (saat:dk)',
    cWeekPct: 'Haftalık (tüm) %', cWeekReset: 'Haftalık reset (gün+saat)',
    cModelName: 'Model adı', cModelPct: 'Model %', calibrateBtn: 'Kalibre et',
    phPct62: 'ör. 62', phPct40: 'ör. 40', phPct55: 'ör. 55',
    pageTitle: 'usage-tracker · Claude kullanım & harcama',
    provNote: 'Yapılandırılmamış sağlayıcı kart açmaz. OpenRouter=gerçek $ · Codex=abonelik (API-eşdeğeri $) · Ollama=yerel/ücretsiz.',
    viewNote: 'İşaretli sağlayıcıları göster; işaretsiz olanlar panel/waybar/widget\'tan gizlenir.',
    viewLocked: 'Claude kartı gizlenemez — waybar rozeti, tepsi ikonu ve `guard` bu kartı okuyor.',
    footerSrc: 'kaynak: ~/.claude/projects + ~/.codex + OpenRouter API (salt-okunur)',
    provSearchPlaceholder: 'Sağlayıcı adı ara...', provTabAll: 'Hepsi', provTabActive: 'Aktif',
    provTabApi: 'API anahtarı', provTabLocal: 'Diskten', provNoMatch: 'Eşleşme yok',
    provClearSearch: 'Aramayı temizle',
    settings: 'Ayarlar', setWarn: 'Uyarı eşiği %', setCrit: 'Kritik eşik %',
    setRefresh: 'Yenileme (sn)', setCurrency: 'Görüntüleme para birimi', setSave: 'Kaydet',
    setKeys: 'API anahtarları', setSaved: '✓ kaydedildi', setSaving: 'kaydediliyor…',
    setThreshNote: 'Eşik tek yerde: panel, waybar rozeti, tepsi ve `guard` komutu aynı çifti okur.',
    setRateNote: 'Kuru sen girersin — uygulama hiçbir yerden kur çekmez. Çevrilmiş tutar '
      + 'dolarların yanında, kuru ve girdiğin tarihle gösterilir.',
    setKeysNote: 'Bu sunucu anahtar yazmaz ve anahtarın değerini hiçbir zaman göstermez — yalnız '
      + 'ortam değişkeninin adını ve dolu/boş durumunu. Anahtar eklemek için: ./setup.sh --ui → “keys” adımı.',
    keySet: 'dolu', keyUnset: 'boş', rateAt: 'kur', enteredOn: 'girildi'
  }
};

const t = (k) => I18N[LANG]?.[k] ?? I18N.en?.[k] ?? k;

function updateI18n() {
  // textContent updates
  for (const el of document.querySelectorAll('[data-i18n]')) {
    const key = el.getAttribute('data-i18n');
    if (el.textContent && key) el.textContent = t(key);
  }
  // title attribute updates
  for (const el of document.querySelectorAll('[data-i18n-title]')) {
    const key = el.getAttribute('data-i18n-title');
    if (key) el.setAttribute('title', t(key));
  }
  // aria-label attribute updates
  for (const el of document.querySelectorAll('[data-i18n-aria]')) {
    const key = el.getAttribute('data-i18n-aria');
    if (key) el.setAttribute('aria-label', t(key));
  }
  // HTML content updates (sözlükten; XSS risk yok)
  for (const el of document.querySelectorAll('[data-i18n-html]')) {
    const key = el.getAttribute('data-i18n-html');
    if (key) el.innerHTML = t(key);
  }
  // placeholder attribute updates
  for (const el of document.querySelectorAll('[data-i18n-ph]')) {
    const key = el.getAttribute('data-i18n-ph');
    if (key) el.setAttribute('placeholder', t(key));
  }
  document.title = t('pageTitle');
  document.documentElement.lang = LANG;
}

const $ = (id) => document.getElementById(id);

// Para birimi sembol haritası
const CURRENCY_SYMBOLS = {
  'USD': '$', 'EUR': '€', 'CNY': '¥', 'GBP': '£', 'TRY': '₺'
};

// Half-up rounding (0.5 yukarı yuvarla, tüm yüzeylerle uyumlu)
const roundHalfUp = (n, decimals = 1) => {
  const factor = Math.pow(10, decimals);
  return Math.floor(n * factor + 0.5) / factor;
};

const fmtUsd = (n, currency = 'USD') => {
  const symbol = CURRENCY_SYMBOLS[currency] || (currency ? currency + ' ' : '$');
  return symbol + (n ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const fmtTok = (n) => (n ?? 0) >= 1e6 ? (n / 1e6).toFixed(1) + 'M'
  : (n ?? 0) >= 1e3 ? (n / 1e3).toFixed(0) + 'k' : String(n ?? 0);

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + ' → ' + r.status);
  return r.json();
}

// Global fallback eşikleri (başlangıç)
let GLOBAL_THRESHOLDS = { warn: 75, crit: 90 };

// ── HARCAMA ──────────────────────────────────────────────
function renderSpend(s) {
  $('s-today').textContent = fmtUsd(s.today, s.currency);
  $('s-yday').textContent = fmtUsd(s.yesterday, s.currency);
  $('s-total').textContent = fmtUsd(s.total, s.currency);
  $('updated').textContent = t('updated') + ' ' + s.updated;
  renderConverted(s);

  const flag = $('price-flag');
  if (s.priceComplete) { flag.textContent = '✓ tam fiyat'; flag.className = 'pill good'; }
  else { flag.textContent = '≈ ' + s.estimatedModels.length + ' model tahmini'; flag.className = 'pill warn'; }

  // sparkline
  const days = s.byDay || [];
  const max = Math.max(1e-9, ...days.map(d => d.usd));
  const todayKey = days.length ? days[days.length - 1].day : null;
  $('spark').innerHTML = days.map(d => {
    const h = Math.max(2, Math.round(d.usd / max * 100));
    const cls = d.day === todayKey ? 'bar today' : 'bar';
    return `<div class="${cls}" style="height:${h}%" title="${esc(d.day)}: ${fmtUsd(d.usd)}"></div>`;
  }).join('');
  $('spark-max').textContent = t('max') + ' ' + fmtUsd(max);

  // model tablosu
  $('model-rows').innerHTML = (s.byModel || []).map(m => {
    const tok = m.tokens || {};
    return `<tr>
      <td class="m-name">${esc(m.short || m.model)}<div class="muted">${esc(m.model)}</div></td>
      <td><span class="src ${m.source}">${esc(m.source)}</span></td>
      <td class="r">${fmtTok(tok.output)} / ${fmtTok(tok.input)} / ${fmtTok((tok.cache_read || 0) + (tok.cache_write || 0))}</td>
      <td class="r usd">${fmtUsd(m.usd)}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="4" class="muted">${t('noModelData')}</td></tr>`;

  $('spend-note').textContent = s.note || '';
}

// ── LİMİTLER ─────────────────────────────────────────────
function barClass(pct, th) {
  if (pct == null) return 'none';
  if (pct >= (th.crit ?? 90)) return 'crit';
  if (pct >= (th.warn ?? 75)) return 'warn';
  return 'ok';
}
function countdown(sec) {
  if (sec == null) return '';
  if (sec < 0) sec = 0;
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h}s ${m}dk` : `${m}dk`;
}
function lbar(name, sub, b, th) {
  const pct = b && b.pct != null ? b.pct : null;
  const w = pct == null ? 8 : Math.min(100, pct);
  const label = pct == null ? '—' : roundHalfUp(pct, 1) + '%';
  const reset = b && b.resetInSec != null ? `reset ${countdown(b.resetInSec)}` : t('uncalib');
  const unitsStr = (b?.units ?? null) == null ? '—' : b.units.toLocaleString();
  const budget = b && b.budget ? `${unitsStr} / ${b.budget.toLocaleString()} ${t('units')}` : `${unitsStr} ${t('units')}`;
  const forecast = b && b.forecast && b.forecast.willExceed ? b.forecast.etaText : null;
  const forecastClass = forecast && pct >= 75 ? 'forecast-warn' : 'forecast-ok';
  return `<div class="lbar">
    <div class="lbar-head"><span class="lbar-name">${esc(name)}${sub ? ` <small>${esc(sub)}</small>` : ''}</span>
      <span class="lbar-pct">${label}</span></div>
    <div class="track"><div class="fill ${barClass(pct, th)}" style="width:${w}%"></div></div>
    <div class="lbar-sub"><span>${budget}</span><span>${reset}</span></div>
    ${forecast ? `<div class="lbar-forecast ${forecastClass}">${esc(forecast)}</div>` : ''}
  </div>`;
}
function renderLimits(u) {
  const th = u.thresholds || { warn: 75, crit: 90 };
  GLOBAL_THRESHOLDS = th;  // Sunucu eşiklerini global olarak kaydet
  let html = lbar('Oturum (5s)', '', u.session, th) + lbar('Haftalık (tüm)', '', u.weeklyAll, th);
  if (u.weeklyModel) html += lbar('Haftalık model', u.weeklyModel.name, u.weeklyModel, th);
  $('limit-bars').innerHTML = html;

  // kaynak rozeti: canlı gerçek (Anthropic) > tahmini (kalibrasyon/ağırlık)
  const flag = $('calib-flag');
  const badge = liveFlag(u);
  flag.textContent = badge.text;
  flag.className = badge.cls;
}

// Yaşı okunabilir yap — rozet "ne kadar eski" sorusunu cevaplamalı, yoksa uyarı değil süs.
function fmtAge(sec) {
  if (typeof sec !== 'number' || !isFinite(sec) || sec <= 0) return '';
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return d + 'g' + h + 's';
  if (h) return h + 's' + m + 'dk';
  return m + 'dk';
}

// Kaynak rozeti saf fonksiyon: ekrandaki tek cümle, ölçülebilir olsun diye ayrıldı.
// 🔴 Buradaki kusur: `source === 'live'` bayatlığı hiç sormuyordu, yani ağ kesildikten
// yedi gün sonra bile panel "🟢 canlı · gerçek" yazıyordu. `live.py` sayıyı 'stale'
// işaretiyle göstermeyi bilinçli seçti — ama işaret ekrana çıkmazsa seçim yalana dönüşür.
function liveFlag(u) {
  const lv = (u && u.live) || {};
  if (lv.stale) {
    const age = fmtAge(lv.ageSec);
    return { text: '⚠ bayat · son canlı okuma' + (age ? ' ' + age + ' önce' : ' başarısız'),
             cls: 'pill warn' };
  }
  if (u && u.source === 'live') return { text: '🟢 canlı · gerçek', cls: 'pill good' };
  if (lv.rateLimited) return { text: '⏳ canlı limit (429) → tahmine düşüldü', cls: 'pill warn' };
  if (lv.error) return { text: '≈ tahmini (canlı: ' + lv.error + ')', cls: 'pill warn' };
  return { text: '≈ tahmini', cls: 'pill warn' };
}

function esc(s) { return String(s ?? '').replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c])); }

// ── TEK BAKIŞ ŞERİDİ (FAZ 5c) — tüm sağlayıcıların manşet durumu tek satırda, tık→karta kaydır
function pctCls(p) { return p == null ? 'none' : p >= 90 ? 'crit' : p >= 75 ? 'warn' : 'ok'; }
function gchip(name, dotCls, valText, valCls, pct, target) {
  const w = pct == null ? 0 : Math.min(100, pct);
  const bar = pct == null ? ''
    : `<div class="gtrack"><div class="gfill ${pctCls(pct)}" style="width:${w}%"></div></div>`;
  return `<button class="gchip" data-target="${esc(target)}" title="${esc(name)} → karta git">
    <div class="gchip-top"><span class="pdot ${dotCls}"></span><span class="gname">${esc(name)}</span>
      <span class="gval ${valCls}">${esc(valText)}</span></div>${bar}</button>`;
}
function renderGlanceStrip(usage, providers) {
  const items = [];
  // Claude — en yüksek limit %
  const cl = [usage.session, usage.weeklyAll, usage.weeklyModel].filter(l => l && l.pct != null);
  if (cl.length) {
    const hi = cl.reduce((a, b) => (b.pct > a.pct ? b : a));
    const cls = pctCls(hi.pct);
    items.push(gchip('Claude', cls, Math.round(hi.pct) + '%', cls, hi.pct, '.limits'));
  }
  // Diğer sağlayıcılar — tür bazlı manşet
  for (const c of providers) {
    let dot = c.status === 'ok' ? 'ok' : c.status === 'offline' ? 'off' : 'err';
    let val = '—', vcls = 'muted', pct = null;
    if (c.status === 'error') { val = 'hata'; vcls = 'crit'; dot = 'err'; }
    else if (c.kind === 'spend') {
      if (c.limit && c.limit.pct != null) { pct = c.limit.pct; dot = pctCls(pct); vcls = pctCls(pct); val = Math.round(pct) + '%'; }
      else if (c.spend && c.spend.today != null) { val = fmtUsd(c.spend.today); vcls = 'spend'; }
      else if (c.balance) { val = fmtUsd(c.balance.remaining); vcls = 'spend'; }
    } else if (c.kind === 'tokens') {
      if (c.status === 'offline') { val = 'kapalı'; vcls = 'muted'; }
      else { val = (c.total && c.total.usd != null) ? fmtUsd(c.total.usd) : fmtTok((c.tokens || {}).total); vcls = 'spend'; }
    } else if (c.kind === 'local') {
      if (c.status === 'offline') { val = 'kapalı'; vcls = 'muted'; }
      else { val = (c.modelCount || 0) + ' model'; vcls = 'ok'; }
    } else if (c.kind === 'quota') {
      if (c.quota && c.quota.pct != null) { pct = c.quota.pct; dot = pctCls(pct); vcls = pctCls(pct); val = Math.round(pct) + '%'; }
    }
    items.push(gchip(c.name, dot, val, vcls, pct, '#prov-' + c.id));
  }
  $('gstrip').innerHTML = items.join('') || `<span class="muted">${t('noData')}</span>`;
  $('gstrip-count').textContent = items.length + ' ' + t('provider');
  document.querySelectorAll('#gstrip .gchip').forEach(b => b.addEventListener('click', () => {
    const target = document.querySelector(b.getAttribute('data-target'));
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }));
}

// ── ÇOK-SAĞLAYICI (FAZ 2) ────────────────────────────────
function fmtSize(b) {
  if (!b) return '';
  const g = b / 1e9; return g >= 1 ? g.toFixed(1) + 'GB' : (b / 1e6).toFixed(0) + 'MB';
}
function fmtCount(n) {
  if (n == null || isNaN(n)) return '—';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k';
  return String(n);
}
function fmtResetUnix(sec) {
  if (!sec) return '—';
  const d = new Date(sec * 1000);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('tr-TR', { day: '2-digit', month: 'short' });
}
function provMiniBar(pct, cls) {
  const w = pct == null ? 0 : Math.min(100, pct);
  return `<div class="track sm"><div class="fill ${cls}" style="width:${w}%"></div></div>`;
}
function provSpark(days, gold) {
  const arr = (days || []).slice(-30);
  if (!arr.length || arr.every(d => !d.usd)) return '';
  const max = Math.max(1e-9, ...arr.map(d => d.usd));
  const last = arr[arr.length - 1].day;
  return `<div class="spark mini">${arr.map(d => {
    const h = d.usd ? Math.max(6, Math.round(d.usd / max * 100)) : 2;
    return `<div class="bar${gold && d.day === last ? ' today' : ''}" style="height:${h}%" title="${esc(d.day)}: ${fmtUsd(d.usd)}"></div>`;
  }).join('')}</div>`;
}
// ── FAZ 3 §3: sağlayıcı kategorisi ve durumu ──────────────────────────────
function categorizeProvider(c) {
  return { kind: c.kind, status: c.status };
}

function matchesSearchQuery(c, query) {
  if (!query.trim()) return true;
  const q = query.toLowerCase();
  return c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q);
}

function filterProviders(list, selectedTab, searchQuery) {
  // searchQuery'ye göre filtrele
  let filtered = list.filter(c => matchesSearchQuery(c, searchQuery));

  // Kategori sekmesine göre filtrele
  if (selectedTab === 'active') {
    filtered = filtered.filter(c => c.status === 'ok' || c.status === 'partial');
  } else if (selectedTab === 'api') {
    // API: sağlayıcının ucuna gidiyoruz, ANAHTAR gerekiyor. spend ($) + quota (kota).
    filtered = filtered.filter(c => ['spend', 'quota'].includes(c.kind));
  } else if (selectedTab === 'local') {
    // Yerel: veri DİSKTEN geliyor, anahtar gerekmiyor.
    //   'local'  → yerel çalıştırıcı (Ollama/LM Studio/Jan)
    //   'tokens' → bulut aracının yerel logu (Codex/Aider/Continue/Cody/Windsurf)
    // 'tokens'ı API sekmesine koymak somut zarar verir: kartı görünmeyen kullanıcı,
    // var olmayan bir API anahtarını aramaya çıkar. Sekmenin cevapladığı soru
    // "bu veri nereden geliyor" — ikisinde de cevap: diskten.
    filtered = filtered.filter(c => ['local', 'tokens'].includes(c.kind));
  }
  // selectedTab === 'all' ise hepsi

  // nodata/offline kartlarını uzun kuyruk'a ayır
  const main = filtered.filter(c => !(c.status === 'nodata' || c.status === 'offline'));
  const longtrail = filtered.filter(c => c.status === 'nodata' || c.status === 'offline');

  return { main, longtrail };
}

function renderProviderCard(c) {
  if (c.status === 'error')
    return provShell(c, `<div class="prov-err">⚠ ${esc(c.error || t('unreachable'))}</div>`);

  if (c.status === 'partial')
    return provShell(c, `<div class="prov-note warn">⚠ ${esc(c.note || 'Tarama eksik (truncated)')}</div>`);

  if (c.kind === 'spend') {           // OpenRouter/OpenAI — gerçek $ (bazıları sadece bakiye)
    const sp = c.spend || {}, bal = c.balance, lim = c.limit;
    const cells = [];
    if (sp.today != null) cells.push(`<div><span class="pl">${t('today')}</span><b>${fmtUsd(sp.today, c.currency)}</b></div>`);
    if (sp.month != null) cells.push(`<div><span class="pl">${t('month')}</span><b class="gold">${fmtUsd(sp.month, c.currency)}</b></div>`);
    if (bal) cells.push(`<div><span class="pl">${t('credit')}</span><b>${fmtUsd(bal.remaining, c.currency)}</b></div>`);
    if (!cells.length) cells.push(`<div><span class="pl muted">${t('noData')}</span><b>—</b></div>`);
    let body = `<div class="prov-stats">${cells.join('')}</div>`;
    if (lim) {
      const cls = barClass(lim.pct, GLOBAL_THRESHOLDS);
      body += `<div class="prov-limit"><div class="pl-row"><span>${t('dailyLimit')} (${esc(lim.reset || '')})</span>
        <span>${fmtUsd(lim.used, c.currency)} / ${fmtUsd(lim.amount, c.currency)}</span></div>${provMiniBar(lim.pct, cls)}</div>`;
    }
    return provShell(c, body);
  }

  if (c.kind === 'tokens') {          // Codex — abonelik, token + $ tahmini
    const tk = c.tokens || {}, tot = c.total || {}, td = c.today || {};
    const srcCls = c.usdSource === 'catalog' ? 'catalog' : 'estimate';
    return provShell(c, `<div class="prov-stats">
      <div><span class="pl">${c.windowDays}g token</span><b>${fmtTok(tk.total)}</b></div>
      <div><span class="pl">≈ maliyet <span class="src ${srcCls}">${esc(c.usdSource)}</span></span><b class="gold">${fmtUsd(tot.usd, c.currency)}</b></div>
      <div><span class="pl">${t('today')}</span><b>${fmtTok(td.tokens)} · ${fmtUsd(td.usd, c.currency)}</b></div>
    </div>
    ${provSpark(c.byDay, true)}
    <div class="pl-row muted"><span>${esc(((c.byModel||[])[0]||{}).short || '—')}</span><span>${c.sessions} ${t('session')} · ${esc(c.auth)}</span></div>`);
  }

  if (c.kind === 'local') {           // Ollama — yerel
    if (c.status === 'offline')
      return provShell(c, `<div class="prov-err off">● ${t('offline')} — <code>ollama serve</code></div>`);
    const run = (c.running || []).length;
    return provShell(c, `<div class="prov-stats">
      <div><span class="pl">${t('modelCount')}</span><b>${c.modelCount || 0}</b></div>
      <div><span class="pl">${t('used')}</span><b class="${run ? 'gold' : ''}">${run}</b></div>
    </div>
    <div class="prov-models">${(c.models || []).slice(0, 4).map(m =>
      `<span class="chip">${esc(m.name)} <small>${fmtSize(m.size)}</small></span>`).join('') || `<span class="muted">${t('noData')}</span>`}</div>`);
  }

  if (c.kind === 'quota') {           // ElevenLabs — karakter kotası ($ yok)
    const q = c.quota || {};
    const cls = barClass(q.pct, GLOBAL_THRESHOLDS);
    const unit = q.unit || t('units');
    let body = `<div class="prov-stats">
      <div><span class="pl">${t('used')}</span><b>${fmtCount(q.used)}</b></div>
      <div><span class="pl">Kalan</span><b class="gold">${fmtCount(q.remaining)}</b></div>
    </div>
    <div class="prov-limit"><div class="pl-row"><span>Aylık ${esc(unit)} (${q.pct == null ? '—' : q.pct + '%'})</span>
      <span>${fmtCount(q.used)} / ${fmtCount(q.limit)}</span></div>${provMiniBar(q.pct, cls)}</div>`;
    if (q.reset) body += `<div class="pl-row muted"><span>Sıfırlanma</span><span>${fmtResetUnix(q.reset)}</span></div>`;
    return provShell(c, body);
  }
  return provShell(c, '');
}

function renderProviders(list) {
  // Global state güncelle
  currentProviders = list;

  // Bayrak güncelle
  const flag = $('prov-flag');
  const okCount = list.filter(c => c.status === 'ok').length;
  flag.textContent = list.length ? `${okCount}/${list.length} aktif` : 'sağlayıcı yok';
  flag.className = 'pill' + (okCount ? ' good' : '');

  // Filtrele ve render et
  updateProviderDisplay();
}
function provShell(c, body) {
  const dot = c.status === 'ok' ? 'ok' : c.status === 'offline' ? 'off' : 'err';
  const badge = c.kind === 'quota' ? (c.tier || 'kota')
    : c.kind === 'local' ? 'yerel'
    : c.kind === 'tokens' ? (c.auth === 'yerel-log' ? 'yerel-log' : 'abonelik')
    : (c.tier || '');
  return `<div class="prov" id="prov-${esc(c.id)}">
    <div class="prov-head"><span class="pdot ${dot}"></span><span class="prov-name">${esc(c.name)}</span>
      ${badge ? `<span class="prov-badge">${esc(badge)}</span>` : ''}</div>
    ${body}</div>`;
}

// ── kalibrasyon gönderimi ────────────────────────────────
async function submitCalib() {
  const timeToMs = (v) => { if (!v || !v.includes(':')) return 0; const d = new Date(); const [h, m] = v.split(':'); if (isNaN(+h) || isNaN(+m)) return 0; d.setHours(+h, +m, 0, 0); return d.getTime(); };
  const dtToMs = (v) => v ? new Date(v).getTime() : 0;
  const body = {
    sessionPct: parseFloat($('c-sess-pct').value) || 0,
    sessionResetAtMs: timeToMs($('c-sess-reset').value),
    weeklyAllPct: parseFloat($('c-week-pct').value) || 0,
    weeklyResetAtMs: dtToMs($('c-week-reset').value),
    weeklyModelName: $('c-model-name').value.trim(),
    weeklyModelPct: parseFloat($('c-model-pct').value) || 0,
  };
  const msg = $('calib-msg');
  msg.textContent = 'kaydediliyor…';
  try {
    const r = await getJSON('/api/calibrate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (r.ok) { msg.textContent = t('calibrated'); refresh(); }
    else { msg.textContent = '✗ ' + (r.error || 'hata'); }
  } catch (e) { msg.textContent = '✗ ' + e.message; }
}

// ── döngü ────────────────────────────────────────────────
// Widget mode (floating app window): ?w=1 → compact, chromeless layout.
// Glance mode (floating glance window): ?w=2 → ultra-compact, single-metric display.
const urlParams = new URLSearchParams(location.search);
const wParam = urlParams.get('w');

async function refresh() {
  try {
    const [spend, usage, prov] = await Promise.all([
      getJSON('/api/spend?days=30'), getJSON('/api/usage'), getJSON('/api/providers?days=30'),
    ]);
    if (wParam === '2') {
      renderGlance(spend, usage);
    } else {
      renderSpend(spend);
      renderLimits(usage);
      currentProviders = prov.providers || [];  // Global state güncelle
      renderProviders(currentProviders);
      renderGlanceStrip(usage, currentProviders);
    }
  } catch (e) {
    $('updated').textContent = 'hata: ' + e.message;
  }
  updateI18n();
}

// Set body class based on widget mode
if (wParam === '2') {
  document.body.classList.add('glance');
} else if (urlParams.has('w')) {
  document.body.classList.add('widget');
}

// ── GLANCE MODE ──────────────────────────────────────────────
function renderGlance(spend, usage) {
  // Find the highest limit percentage
  const limits = [usage.session, usage.weeklyAll, usage.weeklyModel].filter(l => l && l.pct != null);
  if (!limits.length) {
    $('glance-content').innerHTML = '<div class="glance-empty">⚠ kalibrasyon yapılmamış</div>';
    return;
  }

  const maxLimit = limits.reduce((a, b) => (b.pct ?? 0) > (a.pct ?? 0) ? b : a);
  const pct = maxLimit.pct;

  // Color code based on percentage (GLOBAL_THRESHOLDS kullan)
  let colorClass = barClass(pct, GLOBAL_THRESHOLDS);
  if (colorClass === 'none') colorClass = 'ok';  // unknown → ok (yeşil)
  // Glance'de 'mid' seviyesi yok, warn/crit/ok/none kullan

  // Determine which limit name
  let limitName = 'Limit';
  if (maxLimit === usage.session) limitName = 'Oturum (5s)';
  else if (maxLimit === usage.weeklyAll) limitName = 'Haftalık (tüm)';
  else if (maxLimit === usage.weeklyModel) limitName = `Haftalık (${maxLimit.name || 'model'})`;

  // Reset countdown
  const resetSec = maxLimit.resetInSec ?? 0;
  const resetText = countdown(resetSec);

  // Today's spend
  const todaySpend = spend.today ?? 0;

  // Build glance content
  const html = `
    <div class="glance-main">
      <div class="glance-metric">
        <div class="glance-label">${esc(limitName)}</div>
        <div class="glance-pct ${colorClass}">${Math.round(pct)}%</div>
        <div class="glance-bar">
          <div class="glance-fill ${colorClass}" style="width:${Math.min(100, pct)}%"></div>
        </div>
      </div>
      <div class="glance-meta">
        <span>reset ${resetText}</span>
        <span class="glance-spend">bugün ${fmtUsd(todaySpend)}</span>
      </div>
    </div>
  `;
  $('glance-content').innerHTML = html;
}

// ── AYARLAR (FAZ 6/B) ─────────────────────────────────────
// Eşik, yenileme aralığı ve görüntüleme para birimi. Anahtar YAZILMAZ — bkz. usage/settings.py.
let SETTINGS = null;
let POLL_TIMER = null;

function schedulePoll(seconds) {
  const sec = Number(seconds) > 0 ? Number(seconds) : 30;
  if (POLL_TIMER) clearInterval(POLL_TIMER);
  POLL_TIMER = setInterval(() => refresh().catch(e => console.error('refresh failed:', e)), sec * 1000);
}

function renderConverted(s) {
  // Çevrilmiş tutar **ikinci** sıradadır: doları asla değiştirmez, yanına eklenir. Kur ve
  // girildiği gün her zaman görünür — görünmezse tutar savunulamaz bir sayıya dönüşür.
  const el = $('spend-converted');
  if (!el) return;
  const d = SETTINGS && SETTINGS.display;
  const rate = d && Number(d.rate);
  if (!d || !rate || rate <= 0 || (d.currency || 'USD') === 'USD' || (s.currency || 'USD') !== 'USD') {
    el.style.display = 'none';
    return;
  }
  const day = d.rateSetAtMs ? new Date(d.rateSetAtMs).toLocaleDateString() : '?';
  el.textContent = '≈ ' + fmtUsd(s.total * rate, d.currency) + ' · '
    + t('rateAt') + ' 1 USD = ' + rate + ' ' + d.currency + ' · ' + t('enteredOn') + ' ' + day;
  el.style.display = '';
}

function renderKeyStatus(keys) {
  // Yalnız ad + dolu/boş. Değer sunucudan zaten hiç gelmiyor (§güvenlik 3).
  $('settings-keys').innerHTML = (keys || []).map(k => `
    <div class="set-key-row">
      <span class="set-key-name">${esc(k.provider)}</span>
      <code>${esc(k.env)}</code>
      <span class="pill ${k.set ? 'good' : 'muted'}">${k.set ? t('keySet') : t('keyUnset')}</span>
    </div>`).join('') || '<div class="muted">—</div>';
}

function syncRateField() {
  const cur = $('set-currency').value;
  const field = $('set-rate-field');
  field.style.display = cur === 'USD' ? 'none' : '';
  $('set-rate-label').textContent = '1 USD = ? ' + cur;
}

async function loadSettings() {
  try {
    const data = await getJSON('/api/settings');
    SETTINGS = data.settings;
    $('set-warn').value = SETTINGS.thresholds.warn;
    $('set-crit').value = SETTINGS.thresholds.crit;
    $('set-refresh').value = SETTINGS.refreshSeconds;
    $('set-currency').value = SETTINGS.display.currency || 'USD';
    $('set-rate').value = SETTINGS.display.rate ?? '';
    syncRateField();
    renderKeyStatus(data.keys);
    schedulePoll(SETTINGS.refreshSeconds);
  } catch (e) {
    console.error('loadSettings failed:', e);
    schedulePoll(30);            // ayar okunamadıysa panel yine de tazelenmeli
  }
}

async function submitSettings() {
  const msg = $('settings-msg');
  msg.textContent = t('setSaving');
  msg.className = 'pill muted';

  const patch = {
    thresholds: { warn: Number($('set-warn').value), crit: Number($('set-crit').value) },
    refreshSeconds: Number($('set-refresh').value),
    display: { currency: $('set-currency').value },
  };
  if (patch.display.currency !== 'USD') patch.display.rate = Number($('set-rate').value);

  try {
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    const body = await r.json();
    if (r.ok && body.ok) {
      SETTINGS = body.settings;
      // Sunucunun döndürdüğünü geri yaz: kur tarihini sunucu bastı, ekran onu göstermeli.
      $('set-rate').value = SETTINGS.display.rate ?? '';
      msg.textContent = t('setSaved');
      msg.className = 'pill good';
      setTimeout(() => { msg.textContent = '—'; msg.className = 'pill muted'; }, 2000);
      schedulePoll(SETTINGS.refreshSeconds);
      refresh();
    } else {
      // Sunucunun gerekçesi gösterilir — "kaydedilemedi" kullanıcıya hiçbir şey söylemez.
      msg.textContent = '✗ ' + (body.error || r.status);
      msg.className = 'pill warn';
    }
  } catch (e) {
    msg.textContent = '✗ ' + e.message;
    msg.className = 'pill warn';
  }
}

// ── GÖRÜNÜM AYARLARI (FAZ 4a) ─────────────────────────────
async function loadViewConfig() {
  try {
    const data = await getJSON('/api/view-config');
    const { config, available } = data;
    const hiddenProviders = config?.hidden_providers || [];
    renderViewProviders(available || [], hiddenProviders);
  } catch (e) {
    console.error('loadViewConfig failed:', e);
    $('view-providers').innerHTML = '<div class="muted">Yapılandırma yüklenemedi</div>';
  }
}

// Gizlenemeyen kartlar. Sunucu da reddediyor (usage/viewconfig.PROTECTED_PROVIDERS);
// buradaki kilit, kullanıcının reddedilecek bir kutuya tıklamasını en baştan önlüyor.
const LOCKED_PROVIDERS = ['claude'];

function renderViewProviders(available, hidden) {
  const list = available.map(prov => {
    const locked = LOCKED_PROVIDERS.includes(prov.id);
    const isHidden = !locked && hidden.includes(prov.id);
    const checked = !isHidden ? 'checked' : '';
    // Kilitli kart, gizlenebilir bir tercihmiş gibi sunulmamalı: onay kutusu ekranda
    // duruyordu ve tıklayan kullanıcı waybar rozetiyle `guard`'ı da kapatıyordu.
    return `<label class="view-checkbox"${locked ? ` title="${esc(t('viewLocked'))}"` : ''}>
      <input type="checkbox" data-provider="${esc(prov.id)}" ${checked}${locked ? ' disabled' : ''}>
      <span>${esc(prov.name)}${locked ? ' 🔒' : ''}</span>
    </label>`;
  }).join('');
  $('view-providers').innerHTML = list || '<div class="muted">Sağlayıcı yok</div>';

  // Add event listeners to checkboxes
  document.querySelectorAll('#view-providers input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', submitViewConfig);
  });
}

async function submitViewConfig() {
  const msg = $('view-save-msg');
  const checkboxes = document.querySelectorAll('#view-providers input[type="checkbox"]');

  // Collect hidden providers (unchecked)
  const hiddenProviders = Array.from(checkboxes)
    .filter(cb => !cb.checked)
    .map(cb => cb.getAttribute('data-provider'));

  msg.textContent = 'kaydediliyor…';
  msg.className = 'pill muted';

  try {
    const r = await getJSON('/api/view-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hidden_providers: hiddenProviders }),
    });
    if (r.ok) {
      msg.textContent = '✓ kaydedildi';
      msg.className = 'pill good';
      setTimeout(() => {
        msg.textContent = '—';
        msg.className = 'pill muted';
      }, 2000);
      refresh();
    } else {
      msg.textContent = '✗ ' + (r.error || 'hata');
      msg.className = 'pill warn';
    }
  } catch (e) {
    msg.textContent = '✗ ' + e.message;
    msg.className = 'pill warn';
  }
}

// ── FAZ 3 §3: Sekme ve arama event listener'ları ──────────────────────────────
let currentProviders = [];  // Global state: mevcut sağlayıcı listesi

// Kaydedilmiş panel durumu GÜVENİLMEZ girdidir: sürüm yükseltmelerinden sağ çıkar, elle
// düzenlenebilir ve 127.0.0.1'deki başka bir sayfa da yazabilir. Bozuk bir değer paneli
// çökertmiyordu ama daha kötüsünü yapıyordu — sessizce boş bir ızgara gösteriyordu:
// hiçbir düğmeye uymayan sekme + "{bozuk-json" arama kutusu → "Eşleşme yok" (2026-08-12).
const PANEL_TABS = ['all', 'active', 'api', 'local'];
const SEARCH_MAX = 128;

function sanitizeTab(value) {
  return PANEL_TABS.includes(value) ? value : 'all';
}

function sanitizeSearch(value) {
  if (typeof value !== 'string') return '';
  return value.slice(0, SEARCH_MAX);
}

function updateProviderDisplay() {
  const savedTab = sanitizeTab(localStorage.getItem('ut.panel.tab.v1'));
  const savedSearch = sanitizeSearch(localStorage.getItem('ut.panel.search.v1'));

  // Sekmeleri aktifleştir
  document.querySelectorAll('.prov-tab').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === savedTab);
  });

  // Arama kutusunu sync et (event listener tetiklemez)
  const searchInput = $('prov-search');
  if (searchInput) searchInput.value = savedSearch;

  // Filtrele ve render et
  const { main, longtrail } = filterProviders(currentProviders, savedTab, savedSearch);

  // Ana ızgarayı render et
  const mainHtml = main.length
    ? main.map(c => renderProviderCard(c)).join('')
    : '<div class="muted" style="grid-column:1/-1;text-align:center;padding:20px;">Yapılandırılmış sağlayıcı yok.</div>';
  $('provider-cards').innerHTML = mainHtml;

  // Uzun kuyruk
  const ltDetails = $('prov-longtrail');
  const ltCards = $('prov-longtrail-cards');
  if (longtrail.length) {
    ltDetails.style.display = 'block';
    const n = longtrail.length;
    ltDetails.querySelector('summary').textContent = `${n} sağlayıcı veri döndürmedi`;
    ltCards.innerHTML = longtrail.map(c => renderProviderCard(c)).join('');
  } else {
    ltDetails.style.display = 'none';
  }

  // Arama sonucu yoksa mesaj
  const noMatch = $('prov-no-match');
  if (main.length === 0 && longtrail.length === 0 && currentProviders.length > 0 && savedSearch.trim()) {
    noMatch.style.display = 'block';
  } else {
    noMatch.style.display = 'none';
  }
}

// Sekme düğmeleri
document.querySelectorAll('.prov-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.getAttribute('data-tab');
    localStorage.setItem('ut.panel.tab.v1', tab);
    updateProviderDisplay();
  });
});

// Arama kutusu (anlık)
const searchInput = $('prov-search');
if (searchInput) {
  searchInput.addEventListener('input', (e) => {
    const query = e.target.value;
    localStorage.setItem('ut.panel.search.v1', query);
    updateProviderDisplay();
  });
}

// Boş ekrandan tek tıkla çıkış. Önceki oturumdan kalan (ya da bozulmuş) bir arama,
// kullanıcı o an hiçbir şey yazmamışken ızgarayı boşaltabiliyordu; "Eşleşme yok"
// yazısı sebebi söylüyor ama çıkışı göstermiyordu.
const clearSearchBtn = $('prov-clear-search');
if (clearSearchBtn) {
  clearSearchBtn.addEventListener('click', () => {
    localStorage.setItem('ut.panel.search.v1', '');
    if (searchInput) searchInput.value = '';
    updateProviderDisplay();
  });
}

// ── EVENT LISTENERS & STARTUP ────────────────────────────────
$('refresh').addEventListener('click', () => refresh().catch(e => console.error('refresh failed:', e)));
$('calib-save').addEventListener('click', submitCalib);

// Lang switcher
const langBtn = $('lang');
if (langBtn) {
  langBtn.textContent = LANG === 'tr' ? 'EN' : 'TR';
  langBtn.addEventListener('click', () => {
    LANG = LANG === 'tr' ? 'en' : 'tr';
    langBtn.textContent = LANG === 'tr' ? 'EN' : 'TR';
    localStorage.setItem('ut_lang_v1', LANG);
    updateI18n();
    refresh();
  });
}

// i18n startup
updateI18n();

// version badge — sunucunun Server başlığından; elle yazılan sürüm bayatlıyor (CANLI-07)
fetch('/api/usage', { method: 'GET' }).then(r => {
  const sv = r.headers.get('Server') || '';
  const m = sv.match(/usage-tracker\/(\S+)/);
  const el = $('ver');
  if (el) el.textContent = m ? 'v' + m[1] : '';
}).catch(() => {});

// footer address
const footerAddr = $('footer-addr');
if (footerAddr) footerAddr.textContent = location.host;

refresh();
loadViewConfig();
// Yoklama aralığı artık sabit değil — ayarlardan gelir (FAZ 6/B). loadSettings() zamanlayıcıyı
// kendisi kurar; uç erişilemezse 30 sn'ye düşer, panel yine de tazelenir.
loadSettings();

$('settings-save').addEventListener('click', submitSettings);
$('set-currency').addEventListener('change', syncRateField);

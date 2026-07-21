'use strict';
// usage-tracker UI — Vanilla JS, sıfır bağımlılık. /api/spend + /api/usage poll.

const $ = (id) => document.getElementById(id);
const fmtUsd = (n) => '$' + (n ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtTok = (n) => (n ?? 0) >= 1e6 ? (n / 1e6).toFixed(1) + 'M'
  : (n ?? 0) >= 1e3 ? (n / 1e3).toFixed(0) + 'k' : String(n ?? 0);

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + ' → ' + r.status);
  return r.json();
}

// ── HARCAMA ──────────────────────────────────────────────
function renderSpend(s) {
  $('s-today').textContent = fmtUsd(s.today);
  $('s-yday').textContent = fmtUsd(s.yesterday);
  $('s-total').textContent = fmtUsd(s.total);
  $('updated').textContent = 'güncellendi ' + s.updated;

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
  $('spark-max').textContent = 'tavan ' + fmtUsd(max);

  // model tablosu
  $('model-rows').innerHTML = (s.byModel || []).map(m => {
    const t = m.tokens || {};
    return `<tr>
      <td class="m-name">${esc(m.short || m.model)}<div class="muted">${esc(m.model)}</div></td>
      <td><span class="src ${m.source}">${esc(m.source)}</span></td>
      <td class="r">${fmtTok(t.output)} / ${fmtTok(t.input)} / ${fmtTok((t.cache_read || 0) + (t.cache_write || 0))}</td>
      <td class="r usd">${fmtUsd(m.usd)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="4" class="muted">veri yok</td></tr>';

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
  const label = pct == null ? '—' : pct.toFixed(1) + '%';
  const reset = b && b.resetInSec != null ? `reset ${countdown(b.resetInSec)}` : 'kalibre değil';
  const budget = b && b.budget ? `${(b.used || 0).toLocaleString()} / ${b.budget.toLocaleString()} birim` : `${(b && b.used || 0).toLocaleString()} birim`;
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
  let html = lbar('Oturum (5s)', '', u.session, th) + lbar('Haftalık (tüm)', '', u.weeklyAll, th);
  if (u.weeklyModel) html += lbar('Haftalık model', u.weeklyModel.name, u.weeklyModel, th);
  $('limit-bars').innerHTML = html;

  // kaynak rozeti: canlı gerçek (Anthropic) > tahmini (kalibrasyon/ağırlık)
  const flag = $('calib-flag');
  const lv = u.live || {};
  if (u.source === 'live') { flag.textContent = '🟢 canlı · gerçek'; flag.className = 'pill good'; }
  else if (lv.rateLimited) { flag.textContent = '⏳ canlı limit (429) → tahmine düşüldü'; flag.className = 'pill warn'; }
  else if (lv.error) { flag.textContent = '≈ tahmini (canlı: ' + lv.error + ')'; flag.className = 'pill warn'; }
  else { flag.textContent = '≈ tahmini'; flag.className = 'pill warn'; }
}

function esc(s) { return String(s ?? '').replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c])); }

// ── ÇOK-SAĞLAYICI (FAZ 2) ────────────────────────────────
function fmtSize(b) {
  if (!b) return '';
  const g = b / 1e9; return g >= 1 ? g.toFixed(1) + 'GB' : (b / 1e6).toFixed(0) + 'MB';
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
function renderProviders(list) {
  const flag = $('prov-flag');
  const okCount = list.filter(c => c.status === 'ok').length;
  flag.textContent = list.length ? `${okCount}/${list.length} aktif` : 'sağlayıcı yok';
  flag.className = 'pill' + (okCount ? ' good' : '');

  $('provider-cards').innerHTML = list.map(c => {
    if (c.status === 'error')
      return provShell(c, `<div class="prov-err">⚠ ${esc(c.error || 'erişilemedi')}</div>`);

    if (c.kind === 'spend') {           // OpenRouter — gerçek $
      const sp = c.spend || {}, bal = c.balance, lim = c.limit;
      let body = `<div class="prov-stats">
        <div><span class="pl">Bugün</span><b>${fmtUsd(sp.today)}</b></div>
        <div><span class="pl">Bu ay</span><b class="gold">${fmtUsd(sp.month)}</b></div>
        ${bal ? `<div><span class="pl">Kalan kredi</span><b>${fmtUsd(bal.remaining)}</b></div>` : ''}
      </div>`;
      if (lim) {
        const cls = lim.pct == null ? 'none' : lim.pct >= 90 ? 'crit' : lim.pct >= 75 ? 'warn' : 'ok';
        body += `<div class="prov-limit"><div class="pl-row"><span>Günlük limit (${esc(lim.reset || '')})</span>
          <span>${fmtUsd(lim.used)} / ${fmtUsd(lim.amount)}</span></div>${provMiniBar(lim.pct, cls)}</div>`;
      }
      return provShell(c, body);
    }

    if (c.kind === 'tokens') {          // Codex — abonelik, token + $ tahmini
      const tk = c.tokens || {}, tot = c.total || {}, td = c.today || {};
      const srcCls = c.usdSource === 'catalog' ? 'catalog' : 'estimate';
      return provShell(c, `<div class="prov-stats">
        <div><span class="pl">${c.windowDays}g token</span><b>${fmtTok(tk.total)}</b></div>
        <div><span class="pl">≈ maliyet <span class="src ${srcCls}">${esc(c.usdSource)}</span></span><b class="gold">${fmtUsd(tot.usd)}</b></div>
        <div><span class="pl">Bugün</span><b>${fmtTok(td.tokens)} · ${fmtUsd(td.usd)}</b></div>
      </div>
      ${provSpark(c.byDay, true)}
      <div class="pl-row muted"><span>${esc(((c.byModel||[])[0]||{}).short || '—')}</span><span>${c.sessions} oturum · ${esc(c.auth)}</span></div>`);
    }

    if (c.kind === 'local') {           // Ollama — yerel
      if (c.status === 'offline')
        return provShell(c, `<div class="prov-err off">● servis kapalı — <code>ollama serve</code></div>`);
      const run = (c.running || []).length;
      return provShell(c, `<div class="prov-stats">
        <div><span class="pl">Kurulu model</span><b>${c.modelCount || 0}</b></div>
        <div><span class="pl">Çalışan</span><b class="${run ? 'gold' : ''}">${run}</b></div>
      </div>
      <div class="prov-models">${(c.models || []).slice(0, 4).map(m =>
        `<span class="chip">${esc(m.name)} <small>${fmtSize(m.size)}</small></span>`).join('') || '<span class="muted">model yok</span>'}</div>`);
    }
    return provShell(c, '');
  }).join('') || '<div class="muted">Yapılandırılmış başka sağlayıcı yok.</div>';
}
function provShell(c, body) {
  const dot = c.status === 'ok' ? 'ok' : c.status === 'offline' ? 'off' : 'err';
  const badge = c.currency === null ? 'yerel' : c.kind === 'tokens' ? 'abonelik' : (c.tier || '');
  return `<div class="prov">
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
    if (r.ok) { msg.textContent = '✓ kalibre edildi'; refresh(); }
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
      renderProviders(prov.providers || []);
    }
  } catch (e) {
    $('updated').textContent = 'hata: ' + e.message;
  }
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

  // Color code based on percentage
  let colorClass = 'ok';
  if (pct >= 90) colorClass = 'crit';
  else if (pct >= 75) colorClass = 'warn';
  else if (pct >= 50) colorClass = 'mid';
  // else stays 'ok' (green)

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
        <div class="glance-pct ${colorClass}">${pct.toFixed(0)}%</div>
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

// ── EVENT LISTENERS & STARTUP ────────────────────────────────
$('refresh').addEventListener('click', () => refresh().catch(e => console.error('refresh failed:', e)));
$('calib-save').addEventListener('click', submitCalib);
refresh();
setInterval(() => refresh().catch(e => console.error('refresh failed:', e)), 30000);   // 30sn'de bir tazele

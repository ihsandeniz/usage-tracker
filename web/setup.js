/* usage-tracker — setup wizard front-end.
 *
 * This page has no setup logic of its own. It renders `setup.sh probe`, asks
 * `setup.sh preview <step>` what a step would write, and posts `do`/`undo`.
 * If you change how setup works, change setup.sh — this file just draws it.
 *
 * The one-time token arrives in the URL fragment (fragments are never sent to
 * a server, so it can't land in a log). We move it into sessionStorage and
 * scrub the address so a reload still works without leaving it on screen.
 */
'use strict';

let TOKEN = sessionStorage.getItem('ut_token') || '';
if (location.hash.length > 1) {
  TOKEN = location.hash.slice(1);
  sessionStorage.setItem('ut_token', TOKEN);
  history.replaceState(null, '', location.pathname);
}

// ── tiny DOM helper ─────────────────────────────────────────────────────────
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// ── i18n ────────────────────────────────────────────────────────────────────
const I18N = {
  en: {
    tag: 'setup',
    introTitle: "Let's get you set up",
    introBody: 'Six steps. Every one shows you exactly what it will write before it writes it, backs the file up first, and can be undone from this same page. Nothing needs root, nothing leaves this machine.',
    autoAll: 'Set up the recommended parts',
    autoNote: '— server, badge and any keys you already export. Skips the optional surfaces.',
    doneTitle: 'Finished?',
    doneBody: 'Closing shuts this wizard down — the panel and badge keep running. It also closes itself after 10 idle minutes.',
    finish: 'Finish & close',
    footPanel: 'panel', footUndo: 'undo everything',

    apply: 'Apply', undo: 'Undo', recheck: 'Re-check', copy: 'Copy', copied: 'Copied',
    whatWrites: 'what this writes', working: 'working…',
    closed: 'wizard closed — you can close this window',
    unreachable: 'lost contact with the wizard',

    s_ok: 'done', s_off: 'not set up', s_partial: 'partly', s_blocked: 'unavailable',
    s_running: 'running', s_autostart: 'autostart', s_present: 'installed',
    s_manual: 'manual step', s_none: 'not found', s_notrun: 'not run yet',

    st1: 'Dependencies & base config',
    st1d: 'What the tracker needs, and its own config files. Only the missing ones matter — a red line here just means one surface will be unavailable.',
    st1b: 'Create base config',
    st2: 'Server',
    st2d: 'Everything else reads from it. Bound to loopback only, so nothing leaves this machine.',
    st2_systemd: 'Start on login (systemd user service)',
    st2_session: 'Just for this session',
    st3: 'waybar badge',
    st3d: 'A live percentage in your bar. Your config is edited surgically — comments and formatting survive, and we refuse to touch it if we cannot be sure.',
    st3_manual: 'Your config cannot be edited safely, so it was left untouched. Paste this into it yourself, then add "custom/usage" to a modules-* list.',
    st4: 'Desktop surfaces',
    st4d: 'Both optional, both independent of the badge.',
    st4_widget: 'Floating widget',
    st4_widgetd: 'The panel as a movable, resizable, always-on-top window.',
    st4_tray: 'Tray icon',
    st4_trayd: 'A colour-coded dot in your system tray with a rich tooltip.',
    st4_needqt: 'Needs PyQt5 or PySide6.',
    st4_needbrowser: 'Needs a Chromium-family browser.',
    st5: 'Provider keys',
    st5d: 'Claude Code, Codex and local runners need no key. A missing key just hides that card — nothing breaks.',
    st5_import: 'Import from shell',
    st5_importd: 'exported in your shell but missing from the keys file — and shell exports never reach the autostarted service. That is the gap that leaves cards silently empty.',
    st5_add: 'Add a key',
    st5_save: 'Save',
    st5_never: 'Values are written straight to the keys file (chmod 600) and are never read back into this page.',
    st6: 'Verify',
    st6d: 'Proof, not promises — the same checks the terminal wizard runs at the end.',
    st6b: 'Run the checks',

    onLogin: 'on login', openNow: 'open now', startNow: 'start now',
    k_set: 'set', k_shell: 'in shell only', k_unset: 'not set', k_remove: 'Remove',
    autoRunning: 'Setting things up…', autoDone: 'Recommended setup finished — see the steps below.',
    autoFailed: 'Finished, but these steps did not succeed —',
  },
  tr: {
    tag: 'kurulum',
    introTitle: 'Kuruluma başlayalım',
    introBody: 'Altı adım. Her biri yazmadan önce tam olarak ne yazacağını gösterir, dosyayı önce yedekler ve aynı sayfadan geri alınabilir. Hiçbiri root istemez, hiçbir şey bu makineden çıkmaz.',
    autoAll: 'Önerilen parçaları kur',
    autoNote: '— sunucu, rozet ve shell\'de zaten tanımlı anahtarlar. Opsiyonel yüzeyleri atlar.',
    doneTitle: 'Bitti mi?',
    doneBody: 'Kapatmak yalnızca bu sihirbazı durdurur — panel ve rozet çalışmaya devam eder. 10 dakika işlem olmazsa kendi de kapanır.',
    finish: 'Bitir ve kapat',
    footPanel: 'panel', footUndo: 'hepsini geri al',

    apply: 'Uygula', undo: 'Geri al', recheck: 'Yeniden bak', copy: 'Kopyala', copied: 'Kopyalandı',
    whatWrites: 'ne yazacak', working: 'çalışıyor…',
    closed: 'sihirbaz kapandı — bu pencereyi kapatabilirsin',
    unreachable: 'sihirbazla bağlantı koptu',

    s_ok: 'tamam', s_off: 'kurulu değil', s_partial: 'kısmen', s_blocked: 'kullanılamaz',
    s_running: 'çalışıyor', s_autostart: 'girişte açılıyor', s_present: 'kurulu',
    s_manual: 'elle adım', s_none: 'bulunamadı', s_notrun: 'henüz çalışmadı',

    st1: 'Bağımlılıklar ve temel config',
    st1d: 'Aracın ihtiyaç duyduğu şeyler ve kendi config dosyaları. Yalnızca eksik olanlar önemli — kırmızı bir satır sadece bir yüzeyin kullanılamayacağı anlamına gelir.',
    st1b: 'Temel config\'i oluştur',
    st2: 'Sunucu',
    st2d: 'Diğer her şey buradan okur. Yalnızca loopback\'e bağlanır, bu makineden hiçbir şey çıkmaz.',
    st2_systemd: 'Girişte başlat (systemd kullanıcı servisi)',
    st2_session: 'Sadece bu oturum için',
    st3: 'waybar rozeti',
    st3d: 'Çubuğunda canlı yüzde. Config\'in cerrahi olarak düzenlenir — yorumlar ve biçim korunur, emin olamazsak dokunmayı reddederiz.',
    st3_manual: 'Config\'in güvenle düzenlenemedi, bu yüzden dokunulmadı. Bunu kendin yapıştır, sonra "custom/usage"\'ı bir modules-* listesine ekle.',
    st4: 'Masaüstü yüzeyleri',
    st4d: 'İkisi de opsiyonel, ikisi de rozetten bağımsız.',
    st4_widget: 'Yüzen pencere',
    st4_widgetd: 'Panel, taşınabilir + boyutlandırılabilir, hep üstte duran bir pencere olarak.',
    st4_tray: 'Tepsi ikonu',
    st4_trayd: 'Sistem tepsinde renk kodlu bir nokta ve zengin bir tooltip.',
    st4_needqt: 'PyQt5 veya PySide6 gerekir.',
    st4_needbrowser: 'Chromium ailesinden bir tarayıcı gerekir.',
    st5: 'Sağlayıcı anahtarları',
    st5d: 'Claude Code, Codex ve yerel çalıştırıcılar anahtar istemez. Eksik anahtar sadece o kartı gizler — hiçbir şey bozulmaz.',
    st5_import: 'Shell\'den aktar',
    st5_importd: 'shell\'inde tanımlı ama anahtar dosyanda yok — ve shell export\'ları girişte açılan servise hiç ulaşmaz. Kartları sessizce boş bırakan boşluk tam olarak bu.',
    st5_add: 'Anahtar ekle',
    st5_save: 'Kaydet',
    st5_never: 'Değerler doğrudan anahtar dosyasına yazılır (chmod 600) ve bu sayfaya bir daha asla okunmaz.',
    st6: 'Doğrulama',
    st6d: 'Vaat değil kanıt — terminal sihirbazının sonda koştuğu kontrollerin aynısı.',
    st6b: 'Kontrolleri çalıştır',

    onLogin: 'girişte', openNow: 'şimdi aç', startNow: 'şimdi başlat',
    k_set: 'tanımlı', k_shell: 'sadece shell\'de', k_unset: 'tanımsız', k_remove: 'Kaldır',
    autoRunning: 'Kuruluyor…', autoDone: 'Önerilen kurulum bitti — adımlara bak.',
    autoFailed: 'Bitti, ama şu adımlar başarısız oldu —',
  },
};
let LANG = localStorage.getItem('ut_lang')
  || ((navigator.language || 'en').toLowerCase().startsWith('tr') ? 'tr' : 'en');
const t = (k) => I18N[LANG][k] ?? I18N.en[k] ?? k;

// ── api ─────────────────────────────────────────────────────────────────────
let ALIVE = true;

async function api(path, { method = 'GET', body = null } = {}) {
  const headers = { 'X-UT-Token': TOKEN };
  if (body) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    ALIVE = false;
    setConn(t('closed'));
    return { ok: false, error: t('unreachable') };
  }
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (typeof data.ok !== 'boolean') data.ok = res.ok;
  if (!res.ok && !data.error) data.error = `HTTP ${res.status}`;
  return data;
}

const setConn = (txt) => { document.getElementById('conn').textContent = txt || ''; };

// ── state ───────────────────────────────────────────────────────────────────
const state = {
  probe: null,
  open: new Set(),        // which disclosures are expanded
  previews: {},           // step -> preview payload
  logs: {},               // step -> messages from the last do/undo
  busy: null,             // step currently running
  drafts: {},             // key name -> typed value (never sent until Save)
  addingKey: false,
  verified: null,
};

// ── step definitions ────────────────────────────────────────────────────────
// Each entry owns its own facts + controls. `status` drives the pill and the
// numbered circle; `render` returns the interactive part.
const STEPS = [
  {
    id: 'base', num: 1, title: 'st1', desc: 'st1d',
    status(p) {
      const d = p.deps || {};
      if (!d.python3) return ['crit', 's_blocked'];
      if (!(p.base || {}).env_file) return ['off', 's_off'];
      return (d.curl && d.jq) ? ['good', 's_ok'] : ['warn', 's_partial'];
    },
    render(p) {
      const d = p.deps || {}, b = p.base || {}, hint = d.pkg_hint || {};
      const dep = (name, val, need) => [
        name,
        val ? (typeof val === 'string' ? val : '✓') : (hint[name] || t('s_none')),
        val ? 'yes' : (need ? 'warn' : 'no'),
      ];
      return [
        facts([
          dep('python3', d.python3, true),
          dep('curl', d.curl, true),
          dep('jq', d.jq, true),
          dep('browser', d.browser, false),
          dep('hyprctl', d.hyprctl, false),
          dep('systemctl', d.systemctl, false),
          dep('Qt (tray)', d.qt, false),
          ['surface.conf', b.surface_conf ? '✓' : '—', b.surface_conf ? 'yes' : 'no'],
          ['keys file', (b.env_file ? '✓ ' : '— ') + (p.keys || {}).file,
            b.env_file ? 'yes' : 'no'],
        ]),
        actions([
          btn('primary', t('st1b'), () => doStep('base'), b.surface_conf && b.env_file),
        ]),
      ];
    },
  },

  {
    id: 'server', num: 2, title: 'st2', desc: 'st2d',
    status(p) {
      const s = p.server || {};
      if (s.alive && s.mode === 'systemd') return ['good', 's_autostart'];
      if (s.alive) return ['good', 's_running'];
      return ['off', 's_off'];
    },
    render(p) {
      const s = p.server || {}, d = p.deps || {};
      const mode = state.drafts.__servermode || (d.systemctl ? 'systemd' : 'session');
      const radio = (val, label, disabled) => h('label', { class: 'opt' },
        h('input', {
          type: 'radio', name: 'srvmode', value: val, checked: mode === val,
          disabled: disabled || undefined,
          onchange: () => { state.drafts.__servermode = val; },
        }), label);
      return [
        facts([
          ['url', s.url, ''],
          ['status', s.alive ? t('s_running') : t('s_off'), s.alive ? 'yes' : 'no'],
          ['autostart', s.mode === 'systemd' ? '✓ systemd' : '—', s.mode ? 'yes' : 'no'],
        ]),
        disclosure('server'),
        h('div', { class: 'radio-row' },
          radio('systemd', t('st2_systemd'), !d.systemctl),
          radio('session', t('st2_session'), false)),
        actions([
          btn('primary', t('apply'), () => doStep('server', { mode }), s.alive && !!s.mode),
          s.mode ? btn('txtbtn', t('undo'), () => undoStep('server')) : null,
        ]),
      ];
    },
  },

  {
    id: 'waybar', num: 3, title: 'st3', desc: 'st3d',
    status(p) {
      const w = p.waybar || {};
      if (w.badge === 'present') return ['good', 's_present'];
      if (w.badge === 'no-config') return ['off', 's_none'];
      return w.editable ? ['off', 's_off'] : ['warn', 's_manual'];
    },
    render(p) {
      const w = p.waybar || {}, pv = state.previews.waybar;
      const manual = w.badge === 'absent' && !w.editable;
      const out = [];
      if (w.badge === 'no-config') {
        out.push(h('div', { class: 'banner' }, t('s_none') + ' — ~/.config/waybar/'));
        return out;
      }
      out.push(facts([
        ['config', w.config || '—', w.config ? '' : 'no'],
        ['badge', t(w.badge === 'present' ? 's_present' : 's_off'),
          w.badge === 'present' ? 'yes' : 'no'],
        ['style.css', w.styled ? '✓' : '—', w.styled ? 'yes' : 'no'],
      ]));
      if (manual) out.push(h('div', { class: 'banner warn' }, w.reason || t('st3_manual')));
      if (manual && pv && pv.snippet) out.push(snippetBlock(pv.snippet));
      if (w.badge !== 'present') out.push(disclosure('waybar'));
      out.push(actions([
        btn('primary', t('apply'), () => doStep('waybar'),
          w.badge === 'present' || w.badge === 'no-config'),
        w.badge === 'present' ? btn('txtbtn', t('undo'), () => undoStep('waybar')) : null,
      ]));
      return out;
    },
  },

  {
    id: 'surfaces', num: 4, title: 'st4', desc: 'st4d',
    status(p) {
      const on = [(p.widget || {}).autostart || (p.widget || {}).running,
        (p.tray || {}).autostart || (p.tray || {}).running].filter(Boolean).length;
      if (on === 2) return ['good', 's_ok'];
      if (on === 1) return ['good', 's_partial'];
      return ['off', 's_off'];
    },
    render(p) {
      const w = p.widget || {}, tr = p.tray || {}, d = p.deps || {};
      const sub = (key, titleKey, descKey, blocked, blockMsg, running, autostart,
        nowKey, onApply, onUndo, previewStep) => {
        const box = h('div', { class: 'subsurface' },
          h('div', { class: 'code-label head', text: t(titleKey) }),
          h('p', { class: 'step-desc', text: t(descKey) }));
        if (blocked) {
          box.append(h('div', { class: 'banner' }, blockMsg));
          return box;
        }
        box.append(facts([
          ['autostart', autostart ? '✓' : '—', autostart ? 'yes' : 'no'],
          ['status', running ? t('s_running') : t('s_off'), running ? 'yes' : 'no'],
        ]));
        box.append(disclosure(previewStep));
        const cbAuto = h('input', { type: 'checkbox', checked: !!state.drafts[key + 'A'] || undefined,
          onchange: (e) => { state.drafts[key + 'A'] = e.target.checked; } });
        const cbNow = h('input', { type: 'checkbox', checked: state.drafts[key + 'N'] !== false || undefined,
          onchange: (e) => { state.drafts[key + 'N'] = e.target.checked; } });
        box.append(actions([
          h('label', { class: 'opt' }, cbAuto, t('onLogin')),
          h('label', { class: 'opt' }, cbNow, t(nowKey)),
          h('span', { class: 'spacer' }),
          btn('primary', t('apply'), () => onApply(cbAuto.checked, cbNow.checked)),
          (autostart || running) ? btn('txtbtn', t('undo'), onUndo) : null,
        ]));
        return box;
      };
      return [
        sub('w', 'st4_widget', 'st4_widgetd', !d.browser, t('st4_needbrowser'),
          w.running, w.autostart, 'openNow',
          (a, n) => doStep('widget', { autostart: a, open: n }),
          () => undoStep('widget'), 'widget'),
        h('div', { style: 'height:14px' }),
        sub('t', 'st4_tray', 'st4_trayd', !d.qt, `${t('st4_needqt')} ${(d.pkg_hint || {}).qt || ''}`,
          tr.running, tr.autostart, 'startNow',
          (a, n) => doStep('tray', { autostart: a, start: n }),
          () => undoStep('tray'), 'tray'),
      ];
    },
  },

  {
    id: 'keys', num: 5, title: 'st5', desc: 'st5d',
    status(p) {
      const k = p.keys || {};
      if ((k.shell_only || []).length) return ['warn', 's_partial'];
      return (k.set || []).length ? ['good', 's_ok'] : ['off', 's_off'];
    },
    render(p) {
      const k = p.keys || {};
      const set = new Set(k.set || []), shell = new Set(k.shell_only || []);
      const out = [];
      if (shell.size) {
        out.push(h('div', { class: 'banner warn' },
          h('b', { text: [...shell].join(', ') }), ' ', t('st5_importd')));
        out.push(actions([
          btn('primary', `${t('st5_import')} (${shell.size})`,
            () => doStep('keys', { import_shell: true })),
        ]));
      }
      const rows = h('div', { class: 'keys' });
      for (const name of (k.known || [])) {
        const isSet = set.has(name), inShell = shell.has(name);
        const draft = state.drafts[name];
        const row = h('div', { class: 'keyrow' },
          h('span', { class: 'kname', text: name }),
          h('span', {
            class: 'kstate ' + (isSet ? 'set' : inShell ? 'shell' : ''),
            text: isSet ? '••••••  ' + t('k_set') : inShell ? t('k_shell') : t('k_unset'),
          }),
          h('button', {
            class: 'txtbtn',
            text: draft === undefined ? (isSet ? t('k_remove') : t('st5_add')) : t('st5_save'),
            onclick: () => {
              if (draft === undefined) {
                if (isSet) { doStep('keys', { delete: name }); return; }
                state.drafts[name] = ''; render(); return;
              }
              const v = String(state.drafts[name] || '').trim();
              delete state.drafts[name];
              if (v) doStep('keys', { keys: { [name]: v } }); else render();
            },
          }));
        if (draft !== undefined) {
          row.append(h('input', {
            type: 'password', value: draft, autocomplete: 'off', spellcheck: 'false',
            placeholder: name,
            oninput: (e) => { state.drafts[name] = e.target.value; },
          }));
        }
        rows.append(row);
      }
      out.push(rows);
      out.push(h('p', { class: 'note', text: t('st5_never') }));
      return out;
    },
  },

  {
    id: 'verify', num: 6, title: 'st6', desc: 'st6d',
    status() {
      if (state.verified === null) return ['off', 's_notrun'];
      return state.verified ? ['good', 's_ok'] : ['crit', 's_off'];
    },
    render() {
      return [actions([btn('primary', t('st6b'), () => doStep('verify'))])];
    },
  },
];

// ── shared bits ─────────────────────────────────────────────────────────────
function facts(rows) {
  const dl = h('dl', { class: 'facts' });
  for (const [k, v, cls] of rows) {
    dl.append(h('dt', { text: k }), h('dd', { class: cls || '', text: String(v) }));
  }
  return dl;
}

function actions(kids) { return h('div', { class: 'step-actions' }, kids.filter(Boolean)); }

function btn(cls, label, onclick, disabled) {
  return h('button', { class: cls, text: label, onclick, disabled: disabled || undefined });
}

function codeBlock(lines) {
  const pre = h('pre', { class: 'code' });
  for (const l of lines) {
    const cls = l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del'
      : l.startsWith('@@') ? 'hunk' : '';
    pre.append(h('span', { class: cls, text: l }));
  }
  return pre;
}

function snippetBlock(lines) {
  const wrap = h('div');
  const copyBtn = btn('txtbtn', t('copy'), async () => {
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      copyBtn.textContent = t('copied');
      setTimeout(() => { copyBtn.textContent = t('copy'); }, 1600);
    } catch { /* clipboard denied — the text is on screen anyway */ }
  });
  wrap.append(codeBlock(lines), h('div', { class: 'step-actions' }, copyBtn));
  return wrap;
}

/* Lazily ask setup.sh what a step would write, and show it verbatim. For
   waybar that is a real unified diff of the real edit, produced by the same
   code path that will run — not a hand-written illustration of one. */
function disclosure(step) {
  const open = state.open.has(step);
  const body = h('div', { class: 'disclose-body' });
  const box = h('div', { class: 'disclose' + (open ? ' open' : '') },
    h('button', {
      class: 'disclose-btn', text: t('whatWrites'), type: 'button',
      'aria-expanded': open ? 'true' : 'false',
      onclick: () => toggle(step),
    }),
    body);
  const pv = state.previews[step];
  if (open) {
    if (!pv) {
      body.append(h('p', { class: 'note', text: t('working') }));
    } else {
      if (pv.note) body.append(h('div', { class: 'banner' }, pv.note));
      if (pv.diff) body.append(h('div', { class: 'code-label', text: pv.config || 'diff' }),
        codeBlock(pv.diff));
      if (pv.snippet && !pv.diff) body.append(snippetBlock(pv.snippet));
      if (pv.style) body.append(h('div', { class: 'code-label', text: 'style.css' }),
        codeBlock(pv.style));
      if (pv.autostart) body.append(h('div', { class: 'code-label', text: pv.target || '' }),
        codeBlock(pv.autostart));
      if (pv.target && !pv.autostart && !pv.diff) {
        body.append(h('div', { class: 'code-label', text: pv.target }));
      }
      if (pv.importable) body.append(codeBlock(pv.importable.map((k) => `${k}=…`)));
    }
  }
  return box;
}

async function toggle(step) {
  if (state.open.has(step)) { state.open.delete(step); render(); return; }
  state.open.add(step);
  render();
  if (!state.previews[step]) {
    const pv = await api(`/api/setup/preview/${encodeURIComponent(step)}`);
    state.previews[step] = pv;
    render();
  }
}

function logBlock(step) {
  const msgs = state.logs[step];
  const box = h('div', { class: 'step-log' });
  if (!msgs) return box;
  const sym = { ok: '✓', warn: '!', bad: '✗', info: '▸' };
  for (const m of msgs) {
    const i = m.indexOf('|');
    const lvl = i > 0 ? m.slice(0, i) : 'info';
    const txt = i > 0 ? m.slice(i + 1) : m;
    box.append(h('div', { class: 'logline ' + lvl },
      h('span', { class: 'sym', text: sym[lvl] || '·' }), h('span', { text: txt })));
  }
  return box;
}

// ── actions ─────────────────────────────────────────────────────────────────
async function runStep(action, step, body) {
  state.busy = step;
  state.logs[step] = null;
  render();
  const res = await api(`/api/setup/${action}`, { method: 'POST', body: { step, ...body } });
  state.busy = null;
  state.logs[step] = res.messages || (res.error ? [`bad|${res.error}`] : []);
  if (res.stderr) state.logs[step].push(`bad|${res.stderr.split('\n').slice(-3).join(' ')}`);
  if (step === 'verify') state.verified = !!res.ok;
  if (step === 'waybar' && res.needs_manual && res.snippet) {
    state.previews.waybar = { ...(state.previews.waybar || {}), snippet: res.snippet };
  } else {
    delete state.previews[step];          // it changed; the old preview is a lie
  }
  await probe();
  return res;
}

const doStep = (step, opts = {}) => {
  const { keys, ...rest } = opts;
  return runStep('do', step, { opts: rest, ...(keys ? { keys } : {}) });
};
const undoStep = (step) => runStep('undo', step, {});

async function autoAll() {
  const btnEl = document.getElementById('auto-all');
  btnEl.disabled = true;
  setConn(t('autoRunning'));
  // Steps are independent, so one failure shouldn't abort the rest — but it
  // must not vanish either. Collect the failures and name them at the end.
  const failed = [];
  const run = async (step, opts) => {
    const res = await doStep(step, opts);
    if (!res || !res.ok) failed.push(step);
    return res;
  };
  const p = state.probe || {};
  await run('base');
  await run('server', { mode: (p.deps || {}).systemctl ? 'systemd' : 'session' });
  const w = (state.probe || {}).waybar || {};
  if (w.badge === 'absent' && w.editable) await run('waybar');
  const shell = ((state.probe || {}).keys || {}).shell_only || [];
  if (shell.length) await run('keys', { import_shell: true });
  await run('verify');
  setConn(failed.length ? `${t('autoFailed')} ${failed.join(', ')}` : t('autoDone'));
  btnEl.disabled = false;
}

// ── render ──────────────────────────────────────────────────────────────────
function render() {
  const p = state.probe;
  const list = document.getElementById('steps');
  list.textContent = '';
  if (!p) return;

  for (const def of STEPS) {
    const [pillCls, pillKey] = def.status(p);
    const busy = state.busy === def.id
      || (def.id === 'surfaces' && ['widget', 'tray'].includes(state.busy));
    const li = h('li', {
      class: 'step' + (pillCls === 'good' ? ' is-done' : '')
        + (pillCls === 'crit' ? ' is-blocked' : '') + (busy ? ' is-busy' : ''),
    });
    li.append(h('div', { class: 'step-num', text: String(def.num) }));
    const body = h('div', { class: 'card step-body' });
    body.append(h('div', { class: 'step-head' },
      h('h3', { text: t(def.title) }),
      h('span', { class: 'pill ' + pillCls, text: busy ? t('working') : t(pillKey) })));
    body.append(h('p', { class: 'step-desc', text: t(def.desc) }));
    // Each step places its own disclosure: "here's what it'll write" only
    // means something after "here's what you have now".
    for (const node of def.render(p)) body.append(node);
    body.append(logBlock(def.id));
    li.append(body);
    list.append(li);
  }

  for (const el of document.querySelectorAll('[data-i18n]')) {
    el.textContent = t(el.getAttribute('data-i18n'));
  }
  document.getElementById('lang').textContent = LANG === 'tr' ? 'EN' : 'TR';
  document.documentElement.lang = LANG;
  const url = ((state.probe || {}).server || {}).url;
  if (url) document.getElementById('panel-url').textContent = url;
}

async function probe() {
  const p = await api('/api/setup/probe');
  if (p.ok) { state.probe = p; if (ALIVE) setConn(''); }
  render();
}

// ── boot ────────────────────────────────────────────────────────────────────
document.getElementById('refresh').addEventListener('click', () => {
  state.previews = {};
  probe();
});
document.getElementById('lang').addEventListener('click', () => {
  LANG = LANG === 'tr' ? 'en' : 'tr';
  localStorage.setItem('ut_lang', LANG);
  render();
});
document.getElementById('auto-all').addEventListener('click', autoAll);
document.getElementById('finish').addEventListener('click', async () => {
  await api('/api/setup/quit', { method: 'POST', body: {} });
  ALIVE = false;
  setConn(t('closed'));
  document.getElementById('finish').disabled = true;
});

render();
probe();

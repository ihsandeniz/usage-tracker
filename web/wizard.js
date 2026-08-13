/* usage-tracker setup — the wizard, as a page.
 *
 * This file has no setup logic of its own. It renders whatever `usage-tracker setup --probe`
 * reports and calls back into the same do/undo the terminal uses, so the two surfaces cannot
 * drift apart. The step *list* comes from the server too — that is how one page serves both
 * Windows and Linux without knowing which is which.
 *
 * The token lives in the URL fragment: browsers never send `#...` to a server, so it cannot
 * end up in a log or a Referer. We take it, then scrub the address bar.
 */
'use strict';

const TOKEN = location.hash.slice(1);
history.replaceState(null, '', location.pathname);

const state = { probe: null, lang: localStorage.getItem('ut-wizard-lang') || 'en', busy: false };

const I18N = {
  en: {
    tag: 'setup',
    introTitle: "Let's get you set up",
    introBody: 'Every step shows you exactly what it will write before it writes it, keeps a ' +
               'copy of anything it did not write itself, and can be undone from this page. ' +
               'Nothing needs administrator rights and nothing leaves this machine.',
    autoNote: '— the recommended steps; it never writes a key without asking',
    doneTitle: 'Finished?',
    doneBody: 'Closing shuts the wizard down — the panel keeps running. It also closes ' +
              'itself after 10 idle minutes.',
    footPanel: 'panel', footUndo: 'undo everything',
    apply: 'Apply', undo: 'Undo', preview: 'What it writes', hide: 'Hide',
    working: 'working…', done: 'done', failed: 'did not work',
    keysOpen: 'Add keys',
    keysTitle: 'Keys are stored by name; values are never shown back to you.',
    keysSave: 'Save keys', keysEmpty: 'Leave a field empty to skip it.',
    lost: 'wizard closed — this page is no longer connected',
    autoRunning: 'Setting things up…', autoDone: 'Recommended setup finished.',
    autoFailed: 'Finished, but these steps did not succeed:',
    set: 'set', notSet: 'not set', already: 'already done',
  },
  tr: {
    tag: 'kurulum',
    introTitle: 'Kurulumu birlikte yapalım',
    introBody: 'Her adım, yazmadan önce tam olarak ne yazacağını gösterir; kendi yazmadığı ' +
               'bir dosyanın kopyasını saklar ve bu sayfadan geri alınabilir. Hiçbir adım ' +
               'yönetici hakkı istemez, hiçbir şey bu makineden çıkmaz.',
    autoNote: '— önerilen adımlar; anahtarı sormadan asla yazmaz',
    doneTitle: 'Bitti mi?',
    doneBody: 'Kapatmak sihirbazı durdurur — panel çalışmaya devam eder. 10 dakika boşta ' +
              'kalırsa da kendini kapatır.',
    footPanel: 'panel', footUndo: 'hepsini geri al',
    apply: 'Uygula', undo: 'Geri al', preview: 'Ne yazar?', hide: 'Gizle',
    working: 'çalışıyor…', done: 'tamam', failed: 'olmadı',
    keysOpen: 'Anahtar ekle',
    keysTitle: 'Anahtarlar adıyla saklanır; değerleri sana geri gösterilmez.',
    keysSave: 'Anahtarları kaydet', keysEmpty: 'Boş bıraktığın alan atlanır.',
    lost: 'sihirbaz kapandı — bu sayfa artık bağlı değil',
    autoRunning: 'Kuruluyor…', autoDone: 'Önerilen kurulum bitti.',
    autoFailed: 'Bitti, ama şu adımlar başarılı olmadı:',
    set: 'dolu', notSet: 'boş', already: 'zaten yapılmış',
  },
};

const STEP_TEXT = {
  en: {
    install:   ['Put the program somewhere permanent',
                'Copies the binary into your own user folder, so a shortcut still works after ' +
                'you empty your Downloads.'],
    autostart: ['Start it automatically when you log in',
                'Windows: a hidden launcher in your Startup folder. Linux: a systemd user service.'],
    shortcut:  ['Add a shortcut that opens the panel',
                'One click: it opens the panel, starting the server first if nothing is running.'],
    keys:      ['Provider API keys (optional)',
                'Only needed for hosted providers like OpenRouter. Claude usage is read from ' +
                'your own transcripts and needs no key.'],
    verify:    ['Check that everything works',
                'Runs the same checks as `usage-tracker doctor`. Writes nothing.'],
  },
  tr: {
    install:   ['Programı kalıcı bir yere koy',
                'İkiliyi kendi kullanıcı klasörüne kopyalar; İndirilenler’i boşaltınca kısayol ' +
                'kırılmasın diye.'],
    autostart: ['Oturum açınca kendiliğinden başlasın',
                'Windows: Başlangıç klasörüne gizli başlatıcı. Linux: systemd kullanıcı servisi.'],
    shortcut:  ['Paneli açan bir kısayol ekle',
                'Tek tık: paneli açar, sunucu kapalıysa önce onu başlatır.'],
    keys:      ['Sağlayıcı API anahtarları (isteğe bağlı)',
                'Yalnız OpenRouter gibi dış sağlayıcılar için gerekir. Claude kullanımı senin ' +
                'kendi kayıtlarından okunur, anahtar istemez.'],
    verify:    ['Her şey çalışıyor mu, bak',
                '`usage-tracker doctor` ile aynı kontroller. Hiçbir şey yazmaz.'],
  },
};

const t = (key) => (I18N[state.lang] || I18N.en)[key] || key;
const stepText = (id) => (STEP_TEXT[state.lang] || STEP_TEXT.en)[id] || [id, ''];

async function api(path, { method = 'GET', body } = {}) {
  try {
    const res = await fetch(path, {
      method,
      headers: Object.assign({ 'X-UT-Token': TOKEN },
                             body ? { 'Content-Type': 'application/json' } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    return await res.json();
  } catch (err) {
    setConn(t('lost'));
    return { ok: false, messages: [`error|${err}`] };
  }
}

function setConn(text) {
  document.getElementById('conn').textContent = text || '';
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function statusOf(id, probe) {
  const s = probe[id] || {};
  const tr = state.lang === 'tr';
  if (id === 'install') return s.frozen ? (s.installed ? t('already') : t('notSet'))
                                        : (tr ? 'kaynaktan' : 'source checkout');
  if (id === 'autostart') return s.enabled ? t('already') : t('notSet');
  if (id === 'shortcut') return s.exists ? t('already') : t('notSet');
  if (id === 'keys') return `${(s.set || []).length}/${(s.known || []).length} ${t('set')}`;
  if (id === 'verify') return s.server ? (tr ? 'sunucu ayakta' : 'server up')
                                       : (tr ? 'sunucu kapalı' : 'server not running');
  return '';
}

function renderMessages(box, messages) {
  box.innerHTML = '';
  for (const line of messages || []) {
    const [level, ...rest] = String(line).split('|');
    const text = rest.join('|') || level;
    const row = el('div', `logline ${level}`);
    row.textContent = `${{ ok: '✓', info: '·', warn: '!', error: '✗' }[level] || '·'} ${text}`;
    box.appendChild(row);
  }
}

function keysForm(step, log) {
  const wrap = el('div', 'keys');
  wrap.appendChild(el('p', 'note', t('keysTitle') + ' ' + t('keysEmpty')));
  const status = (state.probe.keys || {});
  const inputs = {};
  for (const name of status.known || []) {
    const row = el('div', 'keyrow');
    const label = el('label', 'code-label', name);
    const input = el('input');
    input.type = 'password';
    input.autocomplete = 'off';
    input.placeholder = (status.set || []).includes(name) ? t('set') : t('notSet');
    inputs[name] = input;
    row.appendChild(label);
    row.appendChild(input);
    wrap.appendChild(row);
  }
  const save = el('button', 'primary', t('keysSave'));
  save.addEventListener('click', async () => {
    const keys = {};
    for (const [name, input] of Object.entries(inputs)) {
      if (input.value.trim()) keys[name] = input.value.trim();
    }
    if (!Object.keys(keys).length) return;
    save.disabled = true;
    const res = await api('/api/wizard/do', { method: 'POST', body: { step: 'keys', opts: { keys } } });
    renderMessages(log, res.messages);
    for (const input of Object.values(inputs)) input.value = '';
    save.disabled = false;
    await probe();
  });
  wrap.appendChild(save);
  return wrap;
}

function renderStep(def, index) {
  const [title, desc] = stepText(def.id);
  const done = isDone(def.id);
  // Yapı `setup.css`'in beklediğinin AYNISI olmak zorunda: `.step` iki sütunlu bir grid
  // (`40px 1fr`) ve tam iki çocuk bekliyor — numara ve gövde. İlk sürüm başlığı da doğrudan
  // `.step` içine koyuyordu; sonuç, gerçek bir Windows makinesinde çekilen ekran
  // görüntüsündeki gibi **üst üste binen** başlık, açıklama ve düğmelerdi. Stili başka bir
  // sayfadan ödünç almak, o sayfanın DOM'unu da ödünç almak demek.
  const li = el('li', 'step' + (done ? ' is-done' : ''));
  li.appendChild(el('div', 'step-num', String(index + 1)));

  const body = el('div', 'card step-body');
  const head = el('div', 'step-head');
  head.appendChild(el('h3', null, title));
  head.appendChild(el('span', 'pill ' + (done ? 'good' : 'off'), statusOf(def.id, state.probe)));
  body.appendChild(head);
  body.appendChild(el('p', 'step-desc', desc));

  const log = el('div', 'step-log');
  log.dataset.log = def.id;
  const pre = el('pre', 'code');
  pre.hidden = true;

  const actions = el('div', 'step-actions');

  if (def.id !== 'keys') {
    const runLabel = state.lang === 'tr' ? 'Kontrol et' : 'Run checks';
    const apply = el('button', 'primary', def.id === 'verify' ? runLabel : t('apply'));
    apply.addEventListener('click', async () => {
      apply.disabled = true;
      renderMessages(log, [`info|${t('working')}`]);
      const res = await api('/api/wizard/do', { method: 'POST', body: { step: def.id } });
      renderMessages(log, res.messages);
      apply.disabled = false;
      await probe(log, def.id);
    });
    actions.appendChild(apply);
  }

  if (def.id !== 'verify' && def.id !== 'keys') {
    const show = el('button', 'txtbtn', t('preview'));
    show.addEventListener('click', async () => {
      if (!pre.hidden) { pre.hidden = true; show.textContent = t('preview'); return; }
      const res = await api(`/api/wizard/preview/${encodeURIComponent(def.id)}`);
      pre.textContent = (res.lines || []).join('\n');
      pre.hidden = false;
      show.textContent = t('hide');
    });
    actions.appendChild(show);
  }

  if (def.id !== 'verify') {
    const undo = el('button', 'txtbtn', t('undo'));
    undo.addEventListener('click', async () => {
      undo.disabled = true;
      const res = await api('/api/wizard/undo', { method: 'POST', body: { step: def.id } });
      renderMessages(log, res.messages);
      undo.disabled = false;
      await probe(log, def.id);
    });
    actions.appendChild(undo);
  }

  body.appendChild(actions);
  body.appendChild(pre);
  if (def.id === 'keys') {
    // İsteğe bağlı bir adım ekranın yarısını kaplamamalı: 9 sağlayıcı alanı açıkken sihirbaz
    // "doldurulacak bir form" gibi görünüyordu, oysa çoğu kullanıcı buraya hiç dokunmayacak.
    const open = el('button', 'txtbtn', t('keysOpen'));
    const form = keysForm(def, log);
    form.hidden = true;
    open.addEventListener('click', () => {
      form.hidden = !form.hidden;
      open.textContent = form.hidden ? t('keysOpen') : t('hide');
    });
    actions.insertBefore(open, actions.firstChild);
    body.appendChild(form);
  }
  body.appendChild(log);
  li.appendChild(body);
  return li;
}

function isDone(id) {
  const s = (state.probe || {})[id] || {};
  if (id === 'install') return !!s.installed || s.frozen === false;
  if (id === 'autostart') return !!s.enabled;
  if (id === 'shortcut') return !!s.exists;
  if (id === 'keys') return (s.set || []).length > 0;
  return false;
}

function render() {
  const p = state.probe;
  if (!p) return;
  document.getElementById('tag').textContent = t('tag');
  for (const key of ['introTitle', 'introBody', 'autoNote', 'doneTitle', 'doneBody',
                     'footPanel', 'footUndo']) {
    const node = document.getElementById(`t-${key}`);
    if (node) node.textContent = t(key);
  }
  document.getElementById('auto-all').textContent =
    state.lang === 'tr' ? 'Önerilen kurulumu yap' : 'Set up the recommended parts';
  document.getElementById('lang').textContent = state.lang === 'tr' ? 'EN' : 'TR';
  document.getElementById('finish').textContent =
    state.lang === 'tr' ? 'Bitir ve kapat' : 'Finish & close';
  document.documentElement.lang = state.lang;
  document.getElementById('panel-url').textContent = p.panelUrl;
  document.getElementById('machine').textContent =
    `${p.platform}${p.frozen ? ' · single-file build' : ' · source checkout'} — ${p.binary}`;

  const list = document.getElementById('steps');
  list.innerHTML = '';
  (p.steps || []).forEach((def, i) => list.appendChild(renderStep(def, i)));
}

async function probe(keepLog, keepStep) {
  const carried = keepLog ? keepLog.innerHTML : null;
  const p = await api('/api/wizard/probe');
  if (p && p.ok) {
    state.probe = p;
    setConn('');
    render();
    // Yeniden çizim, kullanıcının az önce okuduğu sonucu silmemeli: "Uygula"ya basınca
    // mesaj bir an görünüp kaybolursa, geriye yalnız değişmiş bir rozet kalır.
    if (carried && keepStep) {
      const box = document.querySelector(`[data-log="${keepStep}"]`);
      if (box) box.innerHTML = carried;
    }
  }
}

document.getElementById('refresh').addEventListener('click', probe);
document.getElementById('lang').addEventListener('click', () => {
  state.lang = state.lang === 'tr' ? 'en' : 'tr';
  localStorage.setItem('ut-wizard-lang', state.lang);
  render();
});
document.getElementById('auto-all').addEventListener('click', async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  setConn(t('autoRunning'));
  const res = await api('/api/wizard/auto', { method: 'POST', body: {} });
  setConn(res.failed && res.failed.length ? `${t('autoFailed')} ${res.failed.join(', ')}`
                                          : t('autoDone'));
  btn.disabled = false;
  await probe();
});
document.getElementById('finish').addEventListener('click', async () => {
  await api('/api/wizard/quit', { method: 'POST', body: {} });
  setConn(t('lost'));
  document.body.style.opacity = '0.5';
});

probe();

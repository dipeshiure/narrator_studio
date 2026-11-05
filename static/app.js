// Narrator Studio — front-end (vanilla JS, talks to server.py)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {},
    ...opts,
    body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};
const toast = (msg) => {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 2600);
};
const fmtSize = (b) => b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : (b / 1e6).toFixed(0) + ' MB';
const fmtDate = (s) => new Date(s * 1000).toLocaleString(I18N.locale, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

// ---------------------------------------------------------------- UI state persistence
// Screen state lives in ui_state.json on the server (see /api/ui-state), so it
// survives F5, a server restart, and even a different browser. Changes are
// batched (300 ms) and flushed with sendBeacon when the page is closed/reloaded.
let UI = {};
let uiPending = {}, uiTimer;
const postBeacon = (url, data) =>
  navigator.sendBeacon(url, new Blob([JSON.stringify(data)], { type: 'application/json' }));
function saveUI(patch) {
  Object.assign(UI, patch); Object.assign(uiPending, patch);
  clearTimeout(uiTimer); uiTimer = setTimeout(() => flushUI(), 300);
}
function flushUI(onUnload = false) {
  clearTimeout(uiTimer);
  if (!Object.keys(uiPending).length) return;
  const body = uiPending; uiPending = {};
  if (onUnload) postBeacon('/api/ui-state', body);
  else api('/api/ui-state', { method: 'POST', body }).catch(() => Object.assign(uiPending, body));
}

// ---------------------------------------------------------------- language
$('#set-lang').innerHTML = Object.entries(I18N.langs).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
$('#set-lang').value = I18N.lang;
$('#set-lang').addEventListener('change', e => I18N.set(e.target.value));

// ---------------------------------------------------------------- settings page
// display choices live in localStorage (this browser), like the language
const PAGES = ['story', 'upload', 'library', 'videos', 'posts', 'rights', 'settings'];
$('#set-theme').value = localStorage.getItem('theme') || 'dark';
$('#set-theme').addEventListener('change', e => {
  localStorage.setItem('theme', e.target.value); applyTheme();
});
$('#set-start').value = localStorage.getItem('start_page') || 'last';
$('#set-start').addEventListener('change', e => { localStorage.setItem('start_page', e.target.value); toast(t('js.app.saved')); });
async function loadSettings() {
  loadFinish();
  loadKeyStatus();
  try {
    const r = await api('/api/system');
    $('#sys-mem').textContent = r.mem_free_pct + '%';
    $('#sys-mem').className = 'metric-value' + (r.mem_free_pct < 25 ? ' status-warn' : '');
    const pw = $('#sys-power'); pw.hidden = !(r.low_power || r.on_battery);
    pw.textContent = r.low_power ? t('settings.low_power') : t('settings.on_battery');
    $('#sys-swap').textContent = r.swap_used_mb == null ? '—' : fmtSize(r.swap_used_mb * 1048576);
    $('#sys-disk').textContent = fmtSize(r.disk_free);
    $('#sys-parts').innerHTML = r.parts.map(p => `<li><span class="badge ${p.ok ? 'badge-done' : 'badge-idle'}">${p.ok ? t('settings.ready') : t('settings.not_ready')}</span>
      <span class="sys-name">${p.name}</span><span class="video-meta">${t('settings.part_' + p.id)}</span></li>`).join('');
  } catch (e) { toast(e.message); }
}
$('#sys-refresh').addEventListener('click', loadSettings);

// ---- finishing look: pick a filter; saved on the server and applied to every video
let FIN = null, finSave = null;
const FIN_SEGS = ['art', 'transition', 'transition_speed', 'zoom_amount', 'zoom_focus'];
const LOOK_ORDER = ['original', 'vivid', 'vivid_warm', 'vivid_cool', 'dramatic', 'dramatic_warm', 'dramatic_cool',
                    'mono', 'silvertone', 'noir', 'warm', 'cool', 'cinema', 'sepia'];
function paintFinish(first = false) {
  if (first) {                                           // one thumbnail per filter, made by the same ffmpeg filter as the video
    $('#fin-looks').innerHTML = LOOK_ORDER.filter(k => FIN.looks.includes(k)).map(k => `<button data-look="${k}" aria-pressed="false">
        <img alt="" width="160" height="90" loading="lazy" data-look-img="${k}"><span>${t('finish.look_' + k)}</span></button>`).join('');
    $$('#fin-looks img').forEach(async img => {
      try {
        const r = await fetch('/api/finish/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ look: img.dataset.lookImg, saturation: 1, contrast: 1, brightness: 0, warmth: 0, w: 320 }) });
        if (r.ok) img.src = URL.createObjectURL(await r.blob());
      } catch { /* no sample picture yet */ }
    });
  }
  $$('#fin-looks button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.look === FIN.look)));
  FIN_SEGS.forEach(k => $$('#fin-' + k + ' button').forEach(b => b.setAttribute('aria-pressed', String(FIN[k] === b.dataset.v))));
}
async function loadFinish() { FIN = await api('/api/finish'); paintFinish(true); }
function changeFinish(patch) {
  FIN = { ...FIN, ...patch }; paintFinish();
  clearTimeout(finSave);
  finSave = setTimeout(async () => {
    try { await api('/api/finish', { method: 'POST', body: FIN }); const b = $('#finish-saved'); b.hidden = false; setTimeout(() => b.hidden = true, 1500); }
    catch (e) { toast(e.message); }
  }, 300);
}
$('#fin-looks').addEventListener('click', e => { const b = e.target.closest('button'); if (b) changeFinish({ look: b.dataset.look, saturation: 1, contrast: 1, brightness: 0, warmth: 0 }); });
FIN_SEGS.forEach(k => $('#fin-' + k).addEventListener('click', e => { const b = e.target.closest('button'); if (b) changeFinish({ [k]: b.dataset.v }); }));
$('#fin-reset').addEventListener('click', () => changeFinish({ look: 'original', saturation: 1, contrast: 1, brightness: 0, warmth: 0, art: 'mono', transition: 'crosszoom', transition_speed: 'normal', zoom_amount: 'normal', zoom_focus: 'face' }));

// ---------------------------------------------------------------- navigation
let current = 'story';
function show(page) {
  current = page; saveUI({ page });
  $$('.nav button').forEach(b => b.dataset.page === page ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current'));
  $$('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
  history.replaceState(null, '', '#' + page);
  ({ upload: () => window.loadInbox && loadInbox(), story: () => window.loadStory && loadStory(), auto: loadAuto, library: loadLibrary, videos: loadVideos, posts: () => window.loadPosts && loadPosts(), rights: () => window.loadRights && loadRights(), settings: loadSettings }[page] || (() => {}))();
}
$$('.nav button').forEach(b => b.addEventListener('click', () => show(b.dataset.page)));
addEventListener('hashchange', () => {
  const p = location.hash.slice(1);
  if (p !== current && PAGES.includes(p)) show(p);
});

// sidebar: full <-> YouTube-style icon rail (remembered across reloads)
function setSidebar(open, focus = true) {
  $('.app').classList.toggle('collapsed', !open);
  const tg = $('#sidebar-toggle'), label = open ? t('js.app.collapse') : t('js.app.expand');
  tg.setAttribute('aria-expanded', open); tg.setAttribute('aria-label', label); tg.title = label;
  $('use', tg).setAttribute('href', open ? '#i-panel-close' : '#i-panel-open');
  localStorage.setItem('sidebar', open ? 'open' : 'closed');
  if (focus) tg.focus({ preventScroll: true });
}
$('#sidebar-toggle').addEventListener('click', () => setSidebar($('.app').classList.contains('collapsed')));
$('#brand-home').addEventListener('click', () => show('story'));
// rail mode shows only icons + short labels: give every item a tooltip
I18N.apply();                         // translate now so labels copied below are in the chosen language
$$('.nav button').forEach(b => b.title = $('.lbl', b).textContent);
setSidebar(localStorage.getItem('sidebar') !== 'closed', false);

// ---------------------------------------------------------------- settings
let S = {};
function buildRanges(root = document) {
  $$('[data-range]', root).forEach(f => {
    const k = f.dataset.range, id = 'r-' + k;
    f.innerHTML = `<div class="range-head"><label for="${id}">${t(f.dataset.label)}</label><output for="${id}" id="o-${k}"></output></div>
      <input type="range" id="${id}" data-key="${k}" min="${f.dataset.min}" max="${f.dataset.max}" step="${f.dataset.step}">`;
  });
}
function paintSettings() {
  $$('#settings [data-key]').forEach(el => {
    const v = S[el.dataset.key];
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.type === 'color') el.value = '#' + v;
    else el.value = v;
    if (el.type === 'range') $('#o-' + el.dataset.key).textContent = v + t($(`[data-range="${el.dataset.key}"]`).dataset.unit || '');
  });
  $$('#settings [data-seg]').forEach(g => $$('button', g).forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.v) === String(S[g.dataset.seg]))));
  const kc = S.subs_engine === 'kinetic';
  // kinetic-captions decides size / colour / motion per word: only its own knobs apply
  $$('#settings .kc-only').forEach(el => el.hidden = !kc);
  $$('#settings .ass-only').forEach(el => el.hidden = kc);
  const sync = $('#settings [data-key="qa_sync"]');
  if (sync) sync.disabled = !S.qa || !S.subs;
  paintPreview();
}
function paintPreview() {
  const p = $('#preview'), c = $('#preview-caption');
  p.classList.toggle('portrait', S.aspect === '9:16');
  if (!S.subs) { c.innerHTML = ''; return; }
  const words = ['never', 'give', 'up', 'on', 'yourself'];
  const shown = words;
  const hl = false;
  c.classList.toggle('kinetic', S.subs_engine === 'kinetic');
  c.innerHTML = shown.map(w => {
    const t = S.uppercase ? w.toUpperCase() : w;
    return w === 'give' && hl ? `<span style="color:#${S.highlight_color}">${t}</span>` : t;
  }).join(' ');
  const W = S.aspect === '9:16' ? 1080 : 1920, H = S.aspect === '9:16' ? 1920 : 1080;
  // scale ASS pixel values to the preview width (cqw = 1% of preview width)
  const px = (v) => `${(v / W) * 100}cqw`;
  Object.assign(c.style, {
    fontFamily: `"${S.font}", Impact, sans-serif`, fontSize: px(S.font_size), color: '#' + S.color,
    WebkitTextStroke: `${px(S.outline * 0.9)} #${S.outline_color}`, paintOrder: 'stroke fill',
    textShadow: S.shadow ? `${px(S.shadow)} ${px(S.shadow)} 0 rgba(0,0,0,.7)` : 'none',
    top: '', bottom: '', transform: '',
  });
  const a = Number(S.alignment);
  if (a === 2) c.style.bottom = `${(S.margin_v / H) * 100}%`;
  else if (a === 8) c.style.top = `${(S.margin_v / H) * 100}%`;
  else { c.style.top = '50%'; c.style.transform = 'translateY(-50%)'; }
}
let saveTimer = null;
function setSetting(k, v) {
  S[k] = v; paintSettings();
  const st = $('#save-state'); st.classList.add('saving'); st.lastChild.textContent = t('js.app.saving');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    saveTimer = null;
    try {
      S = await api('/api/settings', { method: 'POST', body: S });
      st.classList.remove('saving'); st.lastChild.textContent = t('js.app.saved');
    } catch { st.lastChild.textContent = t('js.app.save_failed'); }
  }, 400);
}
// F5 / closing the tab within the 400 ms debounce must not lose the last change
addEventListener('pagehide', () => {
  if (saveTimer) { clearTimeout(saveTimer); postBeacon('/api/settings', S); }
  flushUI(true);
});
$$('#settings details.group').forEach(d => d.addEventListener('toggle', () =>
  saveUI({ open_groups: $$('#settings details.group[open] > summary').map(s => s.textContent.trim()) })));
$('#settings').addEventListener('input', e => {
  const el = e.target, k = el.dataset.key; if (!k) return;
  const v = el.type === 'checkbox' ? el.checked : el.type === 'color' ? el.value.slice(1).toUpperCase()
    : el.type === 'range' ? Number(el.value) : el.value;
  setSetting(k, v);
});
$('#settings').addEventListener('click', e => {
  const b = e.target.closest('[data-seg] button'); if (!b || b.disabled) return;
  const k = b.parentElement.dataset.seg;
  setSetting(k, k === 'alignment' ? Number(b.dataset.v) : b.dataset.v);
});
$('#reset-settings').addEventListener('click', async () => {
  if (!confirm(t('js.app.reset_confirm'))) return;
  S = await api('/api/settings/reset', { method: 'POST' }); paintSettings(); toast(t('js.app.reset_done'));
});

// ---------------------------------------------------------------- create
let files = [], selected = null;
async function loadFiles() {
  const r = await api('/api/files?folder=' + encodeURIComponent($('#folder').value));
  files = r.files; $('#folder').value = r.folder;
  renderFiles(); if (r.error) toast(r.error); else saveUI({ folder: r.folder });
}
function renderFiles() {
  const q = $('#file-search').value.toLowerCase();
  const list = files.filter(f => f.name.toLowerCase().includes(q));
  $('#file-count').textContent = t('js.app.files_done', { done: files.filter(f => f.done).length, total: files.length });
  $('#file-list').innerHTML = list.length ? list.map(f => `<li><button role="option" data-path="${encodeURIComponent(f.path)}"
      aria-selected="${f.path === selected}"><span class="file-name">${f.name}</span>
      ${f.done ? `<span class="badge badge-done">${t('js.app.badge_done')}</span>` : ''}${f.uploaded ? `<span class="badge badge-idle">${t('js.app.badge_uploaded')}</span>` : ''}</button></li>`).join('')
    : `<li class="empty">${t('js.app.no_mp3')}</li>`;
}
function select(path) {
  selected = path; renderFiles(); saveUI({ selected: path });
  const f = files.find(x => x.path === path);
  $('#selected-label').textContent = f ? t('js.app.selected', { name: f.name }) : t('create.13');
  $('#generate').disabled = !f;
}
$('#file-list').addEventListener('click', e => { const b = e.target.closest('button'); if (b) select(decodeURIComponent(b.dataset.path)); });
$('#file-search').addEventListener('input', e => { renderFiles(); saveUI({ file_search: e.target.value }); });
$('#overwrite').addEventListener('change', e => saveUI({ overwrite: e.target.checked }));
$('#folder-load').addEventListener('click', loadFiles);
$('#folder').addEventListener('keydown', e => e.key === 'Enter' && loadFiles());

async function uploadFile(file) {
  const fd = new FormData(); fd.append('file', file);
  toast(t('upload.23'));
  const r = await api('/api/upload', { method: 'POST', body: fd });
  await loadFiles(); select(r.path); toast(t('js.app.uploaded', { name: r.name }));
}
$('#upload').addEventListener('change', e => e.target.files[0] && uploadFile(e.target.files[0]));
const dz = $('#dropzone');
['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('over'); }));
dz.addEventListener('drop', e => e.dataTransfer.files[0] && uploadFile(e.dataTransfer.files[0]));

$('#generate').addEventListener('click', async () => {
  const btn = $('#generate'); btn.disabled = true;
  $('#job').hidden = false; $('#result').hidden = true;
  $('#job-log').textContent = ''; $('#job-status').textContent = t('create.14'); $('#job-spinner').hidden = false;
  try {
    const { job } = await api('/api/generate', { method: 'POST', body: { path: selected, overwrite: $('#overwrite').checked } });
    await pollJob(job, $('#job-log'), (j) => {
      $('#job-spinner').hidden = true;
      if (j.status === 'done') {
        $('#job-status').textContent = t('js.app.done');
        $('#result-video').src = '/media/' + encodeURIComponent(j.result) + '?t=' + Date.now();
        $('#result').hidden = false; toast(t('js.app.video_ready')); loadFiles();
      } else $('#job-status').textContent = t('js.app.failed');
    });
  } catch (err) { $('#job-status').textContent = err.message; $('#job-spinner').hidden = true; }
  btn.disabled = false;
});

function pollJob(id, logEl, onEnd) {
  return new Promise(resolve => {
    const tick = async () => {
      const j = await api('/api/jobs/' + id);
      logEl.textContent = j.log.join('\n'); logEl.scrollTop = logEl.scrollHeight;
      if (j.status === 'running') return setTimeout(tick, 1000);
      onEnd(j); resolve(j);
    };
    tick();
  });
}

// ---------------------------------------------------------------- auto
let workers = 3;
$('#workers').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  setWorkers(Number(b.dataset.v)); saveUI({ workers });
});
function setWorkers(n) {
  workers = n; $$('#workers button').forEach(x => x.setAttribute('aria-pressed', Number(x.dataset.v) === n));
}
$('#auto-folder').addEventListener('change', e => saveUI({ auto_folder: e.target.value }));
async function loadAuto() {
  const r = await api('/api/auto?folder=' + encodeURIComponent($('#auto-folder').value));
  $('#auto-folder').value ||= r.folder;
  $('#m-state').innerHTML = r.running ? `<span class="badge badge-live" style="font-size:16px"><span class="pulse"></span>${t('js.app.running')} ${r.running}</span>` : t('js.app.stopped');
  $('#m-done').textContent = `${r.done} / ${r.total}`;
  const pct = r.total ? Math.round(r.done / r.total * 100) : 0;
  $('#auto-progress').setAttribute('aria-valuenow', pct);
  $('#auto-progress > div').style.width = pct + '%';
  $('#auto-start').disabled = !!r.running; $('#auto-stop').disabled = !r.running;
  $('#auto-logs').innerHTML = r.logs.length ? r.logs.map(l =>
    `<div class="label" style="margin-top:12px">${l.name}</div><pre class="log" style="margin-top:6px">${l.lines.join('\n') || '—'}</pre>`).join('')
    : `<p class="empty">${t('js.app.no_logs')}</p>`;
  setNavBadge(r.running);
  if (!$('#m-lib').dataset.loaded) api('/api/library').then(l => { $('#m-lib').textContent = t('js.app.fresh_n', { n: l.fresh }); $('#m-lib').dataset.loaded = 1; });
}
function setNavBadge(n) {
  const b = $('#nav-auto-badge');
  if (!b) return;                       // 自動生成 is no longer in the menu
  b.className = 'badge ' + (n ? 'badge-live' : 'badge-idle');
  b.innerHTML = n ? `<span class="pulse"></span>${t('js.app.running')}` : t('auto.10');
}
$('#auto-start').addEventListener('click', async () => {
  try { await api('/api/auto/start', { method: 'POST', body: { folder: $('#auto-folder').value, workers } }); toast(t('js.app.auto_started')); }
  catch (e) { toast(e.message); }
  loadAuto();
});
$('#auto-stop').addEventListener('click', async () => {
  if (!confirm(t('js.app.auto_stop_confirm'))) return;
  await api('/api/auto/stop', { method: 'POST' }); toast(t('js.app.auto_stopped')); loadAuto();
});
setInterval(() => {
  if (document.hidden) return;
  if (current === 'auto') loadAuto();
  else api('/api/auto').then(r => setNavBadge(r.running)).catch(() => {});
}, 3000);

// ---------------------------------------------------------------- library
$('#per-field').dataset.range = 'per';
Object.assign($('#per-field').dataset, { min: 3, max: 15, step: 1, label: 'js.app.per_keyword', unit: 'js.app.unit_clips' });
buildRanges($('#page-library'));
function setPer(n) { $('#r-per').value = n; $('#o-per').textContent = n + t('js.app.unit_clips'); }
setPer(6);
$('#r-per').addEventListener('input', e => { setPer(e.target.value); saveUI({ lib_per: Number(e.target.value) }); });
async function loadKeyStatus() {
  const r = await api('/api/pexels-key');
  const b = $('#key-status');
  b.className = 'badge ' + (r.has_key ? 'badge-done' : 'badge-err');
  b.textContent = r.has_key ? t('js.app.key_set') : t('js.app.key_unset');
}
$('#save-key').addEventListener('click', async () => {
  try {
    await api('/api/pexels-key', { method: 'POST', body: { key: $('#pexels-key').value } });
    $('#pexels-key').value = ''; toast(t('js.app.key_saved')); loadKeyStatus();
  } catch (e) { toast(e.message); }
});
async function loadMusic() {
  const tracks = await api('/api/music');
  $('#music-count').textContent = t('js.app.tracks_on', { on: tracks.filter(x => x.enabled !== false).length, total: tracks.length });
  const fmt = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  $('#music-list').innerHTML = tracks.length ? tracks.map(tr => `<li>
      <label class="check" style="min-height:0"><input type="checkbox" data-file="${encodeURIComponent(tr.file)}" ${tr.enabled !== false ? 'checked' : ''} aria-label="${t('js.app.use_track', { title: tr.title })}"></label>
      <div class="music-info"><div class="music-title">${tr.title}</div>
        <div class="video-meta">${fmt(tr.length || 0)} ・ ${(tr.feel || []).join(', ')} ・ ${t('js.app.uses', { n: tr.uses || 0 })}</div></div>
      <audio controls preload="none" src="${tr.url}"></audio></li>`).join('')
    : `<li class="empty">${t('js.app.no_tracks')}</li>`;
}
$('#music-list').addEventListener('change', async e => {
  const f = e.target.dataset.file; if (!f) return;
  await api(`/api/music/${f}/enabled`, { method: 'POST', body: { enabled: e.target.checked } });
  toast(e.target.checked ? t('js.app.track_on') : t('js.app.track_off')); loadMusic();
});
$('#music-fetch').addEventListener('click', async () => {
  const btn = $('#music-fetch'); btn.disabled = true;
  const log = $('#music-log'); log.hidden = false; log.textContent = t('js.app.searching');
  const { job } = await api('/api/music/fetch', { method: 'POST', body: { n: 6 } });
  await pollJob(job, log, () => { toast(t('js.app.tracks_added')); loadMusic(); });
  btn.disabled = false;
});
async function loadLibrary() {
  loadMusic();
  loadKeyStatus();
  const r = await api('/api/library');
  $('#l-fresh').textContent = t('js.app.n_clips', { n: r.fresh }); $('#l-used').textContent = t('js.app.n_clips', { n: r.used }); $('#l-size').textContent = fmtSize(r.size);
  $('#chips').innerHTML = r.categories.length ? r.categories.map(([n, c]) => `<span class="chip">${n}<b>${c}</b></span>`).join('')
    : `<span class="video-meta">${t('js.app.no_fresh')}</span>`;
  if (!$('#queries').value) $('#queries').value = r.queries.join('\n');
}
$('#lib-download').addEventListener('click', async () => {
  const btn = $('#lib-download'); btn.disabled = true;
  const log = $('#lib-log'); log.hidden = false; log.textContent = t('js.app.downloading');
  const { job } = await api('/api/library/download', { method: 'POST',
    body: { queries: $('#queries').value.split('\n'), per: Number($('#r-per').value) } });
  await pollJob(job, log, () => { toast(t('js.app.download_done')); loadLibrary(); });
  btn.disabled = false;
});
$('#lib-reset').addEventListener('click', async () => {
  if (!confirm(t('js.app.recycle_confirm'))) return;
  const r = await api('/api/library/recycle', { method: 'POST' }); toast(t('js.app.recycled', { n: r.moved })); loadLibrary();
});

// ---------------------------------------------------------------- videos
let currentVideo = null;
async function loadVideos() {
  const vids = await api('/api/videos');
  $('#vid-count').textContent = t('js.app.n_videos', { n: vids.length });
  $('#video-grid').innerHTML = vids.length ? vids.map(v => `<button class="video-card" role="option" data-name="${encodeURIComponent(v.name)}"
      aria-selected="${v.name === currentVideo}">
      <div class="video-thumb"><svg width="28" height="28" aria-hidden="true"><use href="#i-play"/></svg></div>
      <div class="file-name">${v.name.replace(/\.mp4$/, '')}</div>
      <div class="video-meta">${fmtSize(v.size)} · ${fmtDate(v.mtime)}</div></button>`).join('')
    : `<p class="empty">${t('js.app.no_videos')}</p>`;
  // restore the last opened video (if it still exists)
  if (currentVideo && $('#player-wrap').hidden && vids.some(v => v.name === currentVideo)) openVideo(currentVideo);
}
function openVideo(name) {
  currentVideo = name;
  $('#player').src = '/media/' + encodeURIComponent(name);
  $('#player-name').textContent = name; $('#player-wrap').hidden = false; $('#reveal').disabled = false;
  $$('.video-card').forEach(c => c.setAttribute('aria-selected', decodeURIComponent(c.dataset.name) === name));
}
$('#video-grid').addEventListener('click', e => {
  const b = e.target.closest('.video-card'); if (!b) return;
  openVideo(decodeURIComponent(b.dataset.name)); saveUI({ current_video: currentVideo });
  window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
});
$('#reveal').addEventListener('click', () => api('/api/reveal', { method: 'POST', body: { name: currentVideo } }));

// ---------------------------------------------------------------- init
(async () => {
  buildRanges($('#settings'));
  // 1) saved values (server falls back to defaults on first run)
  [S, UI] = await Promise.all([api('/api/settings'), api('/api/ui-state')]);
  paintSettings();
  // 2) put the screen back the way it was
  $('#folder').value = UI.folder;
  $('#file-search').value = UI.file_search;
  $('#overwrite').checked = UI.overwrite;
  $('#auto-folder').value = UI.auto_folder;
  setWorkers(UI.workers); setPer(UI.lib_per);
  currentVideo = UI.current_video;
  $$('#settings details.group').forEach(d =>
    d.open = UI.open_groups.includes($('summary', d).textContent.trim()));
  await loadFiles();
  if (UI.selected && files.some(f => f.path === UI.selected)) select(UI.selected);
  const start = location.hash.slice(1), pref = localStorage.getItem('start_page') || 'last';   // 作成(台本) is the main page
  show(PAGES.includes(start) ? start : pref !== 'last' && PAGES.includes(pref) ? pref : PAGES.includes(UI.page) ? UI.page : 'story');
  api('/api/auto').then(r => setNavBadge(r.running));
})();

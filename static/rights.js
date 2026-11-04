// Rights page (権利確認): speech sources, Shazam "registered audio" check, and the
// YouTube check result the user records per video. Uses helpers from app.js.
(() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const SHAZAM = {
    match: [t('js.rights.match'), 'badge-high'], none: [t('js.rights.none'), 'badge-low'],
    error: [t('js.inbox.qa_failed'), 'badge-work'], unchecked: [t('js.rights.unchecked'), 'badge-idle'],
  };
  let items = [], statuses = {}, filter = 'all', polling = null;

  async function loadRights() {
    const r = await api('/api/rights');
    items = r.items; statuses = r.statuses;
    render();
    if (r.batch_job) watch(r.batch_job);
    items.filter(i => i.check_job).forEach(i => watchOne(i.stem, i.check_job));
  }
  window.loadRights = loadRights;

  function counts() {
    $('#r-high').textContent = items.filter(i => i.risk === 'high').length;
    $('#r-claimed').textContent = items.filter(i => i.risk === 'claimed').length;
    $('#r-unchecked').textContent = items.filter(i => ['unchecked', undefined].includes(i.shazam.status)).length;
    const b = $('#nav-rights-badge'), n = items.filter(i => i.risk === 'claimed').length;
    b.hidden = !n; b.className = 'badge badge-err'; b.textContent = t('js.rights.claims', { n });
  }

  function visible() {
    return items.filter(i => filter === 'all' || i.risk === filter
      || (filter === 'unknown' && ['unchecked', 'error', undefined].includes(i.shazam.status)));
  }

  function row(i) {
    const [label, cls] = SHAZAM[i.shazam.status] || SHAZAM.unchecked;
    const m = (i.shazam.matches || [])[0];
    const match = m ? `<div class="rights-match">「<a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a>」/ ${esc(m.artist)}${m.label ? `(${t('js.rights.label')}: ${esc(m.label)})` : ''}</div>` : '';
    const suggest = i.detected_source && i.detected_source !== i.speech_source
      ? `<button class="btn btn-sm" data-act="use-detected" title="${t('js.rights.use_detected_tip')}">${t('js.rights.use_detected', { name: esc(i.detected_source) })}</button>` : '';
    const opts = Object.entries(statuses).map(([v, l]) => `<option value="${v}" ${i.youtube.status === v ? 'selected' : ''}>${t('js.rights.yt_' + v) === 'js.rights.yt_' + v ? l : t('js.rights.yt_' + v)}</option>`).join('');
    const num = i.number < 1e9 ? `#${String(i.number).padStart(2, '0')} ` : '';
    return `<li data-stem="${esc(i.stem)}">
      ${i.thumbnail_url ? `<img class="rights-thumb" src="${i.thumbnail_url}" alt="" loading="lazy">` : '<div class="rights-thumb"></div>'}
      <div class="rights-col"><div class="rights-title">${num}${esc(i.title_en || i.name)}</div>
        <div class="rights-file" title="${esc(i.stem)}">${esc(i.stem)}</div></div>
      <div class="rights-col">
        <label class="label" for="src-${esc(i.stem)}">${t('js.rights.source')}</label>
        <div class="row" style="margin-top:0;flex-wrap:nowrap"><input class="input" id="src-${esc(i.stem)}" data-act="source-input" value="${esc(i.speech_source)}" ${i.has_kit ? '' : 'disabled'}>
          <button class="btn btn-sm" data-act="save-source" ${i.has_kit ? '' : 'disabled'}>${t('js.rights.save')}</button></div>
        <div class="row">${suggest}</div>
        <div class="row"><span class="badge ${cls}">${label}</span>
          <button class="btn btn-sm" data-act="check" ${i.check_job ? 'disabled' : ''}>${i.check_job ? t('js.rights.checking') : (i.shazam.status === 'unchecked' ? t('js.rights.check') : t('js.rights.recheck'))}</button></div>
        ${match}
      </div>
      <div class="rights-col">
        <label class="label" for="yt-${esc(i.stem)}">${t('js.rights.yt_result')}</label>
        <select id="yt-${esc(i.stem)}" data-act="youtube">${opts}</select>
        <div class="row"><input class="input" data-act="note" placeholder="${t('js.rights.note_ph')}" value="${esc(i.youtube.note || '')}" aria-label="${t('js.rights.note')}"></div>
      </div>
    </li>`;
  }

  function render() {
    counts();
    const list = visible();
    $('#rights-list').innerHTML = list.length ? list.map(row).join('') : `<li class="empty" style="display:block">${t('js.rights.empty')}</li>`;
  }

  async function update(stem, patch) {
    const i = items.find(x => x.stem === stem);
    Object.assign(i, patch); render();
  }

  $('#rights-filter').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    filter = b.dataset.v; $$('#rights-filter button').forEach(x => x.setAttribute('aria-pressed', x === b)); render();
  });

  $('#rights-list').addEventListener('click', async e => {
    const el = e.target.closest('[data-act]'); if (!el) return;
    const li = el.closest('li[data-stem]'), stem = li && li.dataset.stem;
    const enc = encodeURIComponent(stem);
    if (el.dataset.act === 'check') {
      const { job } = await api(`/api/rights/${enc}/check`, { method: 'POST' });
      update(stem, { check_job: job }); watchOne(stem, job);
    } else if (el.dataset.act === 'save-source' || el.dataset.act === 'use-detected') {
      const i = items.find(x => x.stem === stem);
      const source = el.dataset.act === 'use-detected' ? i.detected_source : $(`input[data-act="source-input"]`, li).value;
      const r = await api(`/api/rights/${enc}/source`, { method: 'POST', body: { source } });
      update(stem, r); toast(t('js.rights.source_saved'));
    }
  });

  $('#rights-list').addEventListener('change', async e => {
    const el = e.target, li = el.closest('li[data-stem]'); if (!li) return;
    if (!['youtube', 'note'].includes(el.dataset.act)) return;
    const stem = li.dataset.stem;
    const body = { status: $('select[data-act="youtube"]', li).value, note: $('input[data-act="note"]', li).value };
    const r = await api(`/api/rights/${encodeURIComponent(stem)}/youtube`, { method: 'POST', body });
    update(stem, r); toast(t('js.rights.yt_saved'));
  });

  function watchOne(stem, job) {
    const tick = async () => {
      const j = await api('/api/jobs/' + job).catch(() => null);
      if (j && j.status === 'running') return setTimeout(tick, 2000);
      const fresh = (await api('/api/rights')).items.find(x => x.stem === stem);
      if (fresh) update(stem, fresh);
    };
    setTimeout(tick, 2000);
  }

  function watch(job) {
    if (polling === job) return;
    polling = job;
    const log = $('#rights-log'), btn = $('#rights-check-all');
    log.hidden = false; btn.disabled = true;
    const tick = async () => {
      const j = await api('/api/jobs/' + job).catch(() => null);
      if (!j) { polling = null; btn.disabled = false; return; }
      log.textContent = j.log.slice(-8).join('\n');
      if (j.status === 'running') {
        if (current === 'rights') { const r = await api('/api/rights'); items = r.items; render(); }
        return setTimeout(tick, 4000);
      }
      polling = null; btn.disabled = false; toast(t('js.rights.done')); loadRights();
    };
    tick();
  }

  $('#rights-check-all').addEventListener('click', async () => {
    const r = await api('/api/rights/check-all', { method: 'POST', body: {} });
    if (!r.count) { toast(t('js.rights.nothing')); return; }
    toast(t('js.rights.checking_n', { n: r.count })); watch(r.job);
  });
})();

// Upload page: bulk upload, status list, review of the AI's cuts, generate/delete.
// Uses helpers from app.js ($, $$, api, toast, show, fmtSize).
(() => {
  const STATUS = {
    queued: [t('js.inbox.queued'), 'badge-wait'], analyzing: [t('js.inbox.analyzing'), 'badge-work'], ready: [t('js.inbox.ready'), 'badge-ready'],
    waiting: [t('js.inbox.waiting'), 'badge-wait'], generating: [t('js.inbox.generating'), 'badge-work'],
    publishing: [t('js.inbox.publishing'), 'badge-work'], checking: [t('js.inbox.checking'), 'badge-work'], done: [t('js.inbox.done'), 'badge-done'],
    error: [t('js.inbox.error'), 'badge-err'],
  };
  const BUSY = new Set(['queued', 'analyzing', 'waiting', 'generating', 'publishing', 'checking']);
  const RUNNING = new Set(['analyzing', 'generating', 'publishing', 'checking']);   // cannot be deleted now
  const QA = { pass: [t('js.inbox.qa_ok'), 'badge-done'], warn: [t('js.inbox.qa_check'), 'badge-work'], fail: [t('js.inbox.qa_check'), 'badge-err'], error: [t('js.inbox.qa_failed'), 'badge-err'] };
  const fmtT = (t) => { t = Math.max(0, Math.round(t || 0)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; };
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  let items = [], selected = new Set(), openId = null, detail = null, pollTimer = null;
  const audio = new Audio();
  let stopAt = null;
  audio.addEventListener('timeupdate', () => { if (stopAt !== null && audio.currentTime >= stopAt) { audio.pause(); stopAt = null; } });

  // ------------------------------------------------------------ upload
  function upload(files) {
    files = [...files].filter(f => /\.(mp3|m4a|wav|aac|flac|ogg|aiff?)$/i.test(f.name) && !f.name.startsWith('.'));
    if (!files.length) { toast(t('js.inbox.no_audio')); return; }
    const fd = new FormData();
    files.forEach(f => fd.append('files', f, f.name));
    const box = $('#upload-progress'), bar = $('#upload-progress .progress > div');
    box.hidden = false; $('#upload-label').textContent = t('js.inbox.uploading', { n: files.length });
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/inbox/upload');
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = Math.round(e.loaded / e.total * 100);
      bar.style.width = p + '%'; $('#upload-pct').textContent = p + '%';
    };
    xhr.onload = () => {
      box.hidden = true; bar.style.width = '0';
      const r = JSON.parse(xhr.responseText || '{}');
      toast(t('js.inbox.added', { n: (r.added || []).length }) + ((r.skipped || []).length ? t('js.inbox.skipped', { n: r.skipped.length }) : ''));
      loadInbox();
    };
    xhr.onerror = () => { box.hidden = true; toast(t('js.inbox.upload_failed')); };
    xhr.send(fd);
  }
  $('#inbox-files').addEventListener('change', e => { upload(e.target.files); e.target.value = ''; });
  $('#inbox-folder').addEventListener('change', e => { upload(e.target.files); e.target.value = ''; });
  $('#pick-files').addEventListener('click', () => $('#inbox-files').click());
  $('#pick-folder').addEventListener('click', () => $('#inbox-folder').click());
  const drop = $('#inbox-drop');
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', async e => {
    // folders dropped: walk them (webkitGetAsEntry)
    const entries = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (!entries.some(en => en.isDirectory)) return upload(e.dataTransfer.files);
    const files = [];
    const walk = (entry) => new Promise(res => {
      if (entry.isFile) return entry.file(f => { files.push(f); res(); }, res);
      const reader = entry.createReader(), all = [];
      const next = () => reader.readEntries(async batch => {
        if (!batch.length) { await Promise.all(all.map(walk)); return res(); }
        all.push(...batch); next();
      }, res);
      next();
    });
    await Promise.all(entries.map(walk));
    upload(files);
  });

  // ------------------------------------------------------------ list
  let lastSig = '';
  async function loadInbox() {
    let fresh;
    try { fresh = await api('/api/inbox'); } catch { schedule(); return; }
    // only redraw when something changed, so buttons / scroll / focus are not reset under the user
    const sig = JSON.stringify(fresh);
    if (sig !== lastSig) {
      lastSig = sig; items = fresh;
      selected = new Set([...selected].filter(id => items.some(i => i.id === id)));
      render();
    }
    schedule();
  }
  window.loadInbox = loadInbox;

  function schedule() {
    clearTimeout(pollTimer);
    const busy = items.some(i => BUSY.has(i.status));
    pollTimer = setTimeout(() => { if (!document.hidden && current === 'upload') loadInbox(); else schedule(); }, busy ? 2000 : 10000);
    const n = items.filter(i => i.status === 'ready').length, b = $('#nav-inbox-badge');
    b.hidden = !(n || busy);
    b.className = 'badge ' + (busy ? 'badge-live' : 'badge-ready');
    b.innerHTML = busy ? `<span class="pulse"></span>${t('js.inbox.busy')}` : t('js.inbox.review_n', { n });
  }

  function musicLabel(i) {
    if (i.music_db === null || i.music_db === undefined) return '';
    return i.music_db > -45 ? `<span>${t('js.inbox.music_yes')}</span>` : `<span>${t('js.inbox.music_no')}</span>`;
  }

  function render() {
    const counts = {};
    items.forEach(i => counts[i.status] = (counts[i.status] || 0) + 1);
    $('#inbox-summary').textContent = items.length
      ? t('js.inbox.n_items', { n: items.length }) + ' ・ ' + Object.entries(counts).map(([k, v]) => `${STATUS[k][0]} ${v}`).join(' ・ ') : '';
    $('#inbox-all').checked = items.length > 0 && selected.size === items.length;
    $('#inbox-gen-sel').disabled = $('#inbox-del-sel').disabled = selected.size === 0;
    $('#inbox-gen-ready').disabled = !items.some(i => i.status === 'ready');
    const prevReview = $('#review');
    const keepReview = prevReview && prevReview.dataset.for === openId ? prevReview : null;
    if (!items.length) {
      $('#inbox-list').innerHTML = `<li class="empty">${t('js.inbox.empty')}</li>`;
      return;
    }
    $('#inbox-list').innerHTML = items.map(i => {
      const [label, cls] = STATUS[i.status] || [i.status, 'badge-idle'];
      const busy = BUSY.has(i.status);
      const meta = [
        i.duration ? `<span>${t('js.inbox.length')} ${fmtT(i.duration)}${i.cuts ? ` → ${fmtT(i.final)}` : ''}</span>` : '',
        musicLabel(i),
        i.duration !== undefined ? (i.cuts ? `<span class="cut-tag">${t('js.inbox.ads_cut', { n: i.cuts })}</span>` : `<span>${t('js.inbox.no_ads')}</span>`) : '',
        `<span>${fmtSize(i.size)}</span>`,
      ].join('');
      return `<li data-id="${i.id}">
        <div class="inbox-row">
          <input type="checkbox" aria-label="${esc(t('js.inbox.select', { name: i.name }))}" data-act="select" ${selected.has(i.id) ? 'checked' : ''}>
          <div style="min-width:0">
            <div class="row" style="gap:8px"><span class="inbox-name">${esc(i.name)}</span>
              <span class="badge ${cls}">${busy ? '<span class="pulse"></span>' : ''}${label}</span>
              ${i.status === 'done' && QA[i.qa] ? `<span class="badge ${QA[i.qa][1]}" title="${esc((i.qa_reasons || []).join('\n') || t('js.inbox.no_issues'))}">${QA[i.qa][0]}</span>` : ''}</div>
            <div class="inbox-meta">${meta}</div>
            ${busy && i.step ? `<div class="inbox-step">${esc(i.step)}</div>` : ''}
            ${i.error ? `<div class="inbox-step inbox-err">${esc(i.error)}</div>` : ''}
            ${i.status === 'done' && i.warning ? `<div class="inbox-step inbox-warn">${t('js.inbox.warning')}: ${esc(i.warning)}</div>` : ''}
          </div>
          <div class="inbox-actions">
            ${i.duration !== undefined ? `<button class="btn btn-sm" data-act="review" aria-expanded="${openId === i.id}">
              <svg width="16" height="16" aria-hidden="true"><use href="#i-scissors"/></svg>${openId === i.id ? t('js.inbox.close') : t('js.inbox.review')}</button>` : ''}
            ${['ready', 'done', 'error'].includes(i.status) && i.duration !== undefined ? `<button class="btn btn-sm ${i.status === 'ready' ? 'btn-primary' : ''}" data-act="gen">${i.status === 'ready' ? t('js.inbox.generate') : t('js.inbox.regenerate')}</button>` : ''}
            ${i.video_exists ? `<button class="btn btn-sm" data-act="watch"><svg width="16" height="16" aria-hidden="true"><use href="#i-play"/></svg>${t('js.inbox.watch')}</button>` : ''}
            <button class="icon-btn" data-act="delete" aria-label="${esc(t('js.inbox.delete_one', { name: i.name }))}" title="${t('js.inbox.delete')}" ${RUNNING.has(i.status) ? 'disabled' : ''}>
              <svg width="18" height="18" aria-hidden="true"><use href="#i-trash"/></svg></button>
          </div>
        </div>
        ${openId === i.id ? '<div class="review" id="review"></div>' : ''}
      </li>`;
    }).join('');
    // keep the open review panel (its scroll position, playing audio) instead of rebuilding it
    const slot = $('#review');
    if (slot && keepReview) slot.replaceWith(keepReview);
    else if (openId && detail) renderReview();
  }

  $('#inbox-all').addEventListener('change', e => {
    selected = e.target.checked ? new Set(items.map(i => i.id)) : new Set(); render();
  });
  $('#inbox-list').addEventListener('click', async e => {
    const el = e.target.closest('[data-act]'); if (!el) return;
    const li = el.closest('li[data-id]'), id = li && li.dataset.id;
    const item = items.find(i => i.id === id);
    switch (el.dataset.act) {
      case 'select': el.checked ? selected.add(id) : selected.delete(id); render(); break;
      case 'review':
        if (openId === id) { openId = null; detail = null; audio.pause(); render(); break; }
        openId = id; detail = await api(`/api/inbox/${id}`); render(); break;
      case 'gen': await generate([id]); break;
      case 'watch': show('videos'); setTimeout(() => window.openVideo && openVideo(item.video), 300); break;
      case 'delete': await remove([id]); break;
      case 'play': {
        const x = detail.sentences[+el.dataset.i];
        if (!audio.src.endsWith(`/api/inbox/${openId}/audio`)) audio.src = `/api/inbox/${openId}/audio`;
        audio.currentTime = x.s; stopAt = x.e + 0.2; audio.play(); break;
      }
      case 'toggle': {
        const i = +el.dataset.i, cut = !detail.sentences[i].cut;
        detail.sentences[i].cut = cut; renderReview();
        const sum = await api(`/api/inbox/${openId}/sentences`, { method: 'POST', body: { cut: { [i]: cut } } });
        Object.assign(items.find(x => x.id === openId), sum); render();
        break;
      }
      case 'reset-ai': {
        const changes = {};
        detail.sentences.forEach((x, i) => { if (x.cut !== x.auto) { changes[i] = x.auto; x.cut = x.auto; } });
        const sum = await api(`/api/inbox/${openId}/sentences`, { method: 'POST', body: { cut: changes } });
        Object.assign(items.find(x => x.id === openId), sum); render(); toast(t('js.inbox.ai_restored')); break;
      }
      case 'reanalyze':
        await api(`/api/inbox/${openId}/reanalyze`, { method: 'POST' });
        openId = null; detail = null; toast(t('js.inbox.reanalyzing')); loadInbox(); break;
    }
  });

  function renderReview() {
    const box = $('#review'); if (!box || !detail) return;
    box.dataset.for = openId;
    const scroll = box.querySelector('.sentences')?.scrollTop || 0;
    const total = detail.duration || 1;
    const cuts = detail.sentences.filter(x => x.cut);
    const bar = cuts.map(x => `<span style="left:${x.s / total * 100}%;width:${Math.max(0.4, (x.e - x.s) / total * 100)}%"></span>`).join('');
    box.innerHTML = `
      <div class="timeline" aria-hidden="true">${bar}</div>
      <div class="timeline-legend"><span><i style="background:var(--color-track)"></i>${t('js.inbox.keep')}</span><span><i style="background:var(--color-accent)"></i>${t('js.inbox.cut')}</span>
        <span>${t('js.inbox.final_len', { final: fmtT(items.find(i => i.id === openId).final), total: fmtT(total) })}</span></div>
      <ul class="sentences">${detail.sentences.map((x, i) => `
        <li class="${x.cut ? 'is-cut' : ''}">
          <button class="icon-btn" data-act="play" data-i="${i}" aria-label="${t('js.inbox.play_from', { at: fmtT(x.s) })}" title="${t('js.inbox.play_line')}"><svg width="16" height="16" aria-hidden="true"><use href="#i-play"/></svg></button>
          <span class="s-time">${fmtT(x.s)}</span>
          <span><span class="s-text">${esc(x.text)}</span>${x.cut && x.reason ? `<span class="s-reason">${esc(x.reason)}</span>` : ''}</span>
          <button class="btn btn-sm ${x.cut ? '' : 'btn-danger'}" data-act="toggle" data-i="${i}">${x.cut ? t('js.inbox.keep') : t('js.inbox.cut')}</button>
        </li>`).join('')}</ul>
      <div class="review-actions">
        <span class="video-meta">${cuts.length ? t('js.inbox.cutting', { n: cuts.length }) : t('js.inbox.nothing_cut')}</span>
        <button class="btn btn-sm" data-act="reset-ai">${t('js.inbox.reset_ai')}</button>
        <button class="btn btn-sm" data-act="reanalyze">${t('js.inbox.reanalyze')}</button>
        <button class="btn btn-sm btn-primary" data-act="gen">${t('js.inbox.gen_this')}</button>
      </div>`;
    box.querySelector('.sentences').scrollTop = scroll;
  }

  async function generate(ids) {
    const r = await api('/api/inbox/generate', { method: 'POST', body: { ids } });
    toast(r.queued.length ? t('js.inbox.queued_n', { n: r.queued.length }) : t('js.inbox.nothing_to_gen'));
    loadInbox();
  }
  async function remove(ids) {
    const names = items.filter(i => ids.includes(i.id)).map(i => i.name);
    if (!confirm(t('js.inbox.delete_confirm', { n: names.length }) + '\n\n' + names.slice(0, 8).join('\n'))) return;
    const r = await api('/api/inbox/delete', { method: 'POST', body: { ids } });
    ids.forEach(id => selected.delete(id));
    if (ids.includes(openId)) { openId = null; detail = null; }
    toast(t('js.inbox.deleted', { n: r.deleted.length }) + (r.busy.length ? t('js.inbox.busy_kept', { n: r.busy.length }) : ''));
    loadInbox();
  }
  $('#inbox-gen-sel').addEventListener('click', () => generate([...selected]));
  $('#inbox-del-sel').addEventListener('click', () => remove([...selected]));
  $('#inbox-gen-ready').addEventListener('click', () => generate(items.filter(i => i.status === 'ready').map(i => i.id)));

  loadInbox();
})();

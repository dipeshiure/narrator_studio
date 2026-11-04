// Posts page (投稿): copy YouTube metadata (title / description / tags) for each
// finished video in output/videos/, preview it, and track what is already uploaded.
// Also shows the automatic quality check (<stem>.qa.json) and can rebuild the kit.
// Uses helpers from app.js ($, $$, api, toast, fmtSize, saveUI, UI).
(() => {
  const LIMITS = { title: 100, description: 5000, tags: 500 };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtDur = (t) => { if (t == null) return ''; t = Math.round(t); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; };
  const fmtDay = (s) => new Date(s * 1000).toLocaleDateString(I18N.locale, { month: 'numeric', day: 'numeric' });
  const tagsText = (p) => p.tags.join(', ');
  // YouTube counts a multi-word tag with its surrounding quotes
  const tagsLen = (p) => p.tags.reduce((n, t) => n + t.length + (/\s/.test(t) ? 2 : 0), 0) + Math.max(0, p.tags.length - 1);

  // field id -> [label, text getter, limit kind, collapsible]
  const FIELDS = [
    ['title_en', t('js.posts.title_en'), p => p.title_en, 'title'],
    ['title_ja', t('js.posts.title_ja'), p => p.title_ja, 'title'],
    ['desc_en', t('js.posts.desc_en'), p => p.description_en_full, 'description', true],
    ['desc_ja', t('js.posts.desc_ja'), p => p.description_ja_full, 'description', true],
    ['tags', t('js.posts.tags'), tagsText, 'tags'],
    ['hashtags', t('js.posts.hashtags'), p => p.hashtags.join(' ')],
  ];
  const fieldText = (p, id) => (id === 'set_en'
    ? `${p.title_en}\n\n${p.description_en_full}\n\n${tagsText(p)}`
    : FIELDS.find(f => f[0] === id)[2](p)) || '';

  let posts = [], filter = 'all', loaded = false;
  const QA = { pass: [t('js.inbox.qa_ok'), 'badge-done'], warn: [t('js.inbox.qa_check'), 'badge-work'], fail: [t('js.inbox.qa_check'), 'badge-err'] };
  const QA_ICON = { pass: 'OK', warn: t('js.posts.qa_warn'), fail: 'NG', skip: '—' };
  const hasNum = (p) => p.number < 1e9;
  const watching = new Set();   // stems whose republish job is being polled

  function qaHtml(p) {
    const q = p.qa;
    if (!q || !QA[q.status]) return `<div class="qa-box"><span class="badge badge-idle">${t('js.posts.qa_none')}</span></div>`;
    const [label, cls] = QA[q.status];
    const sync = q.sync_median != null ? t('js.posts.sync', { med: q.sync_median.toFixed(2), max: q.sync_max.toFixed(2) }) : '';
    const tip = (q.reasons.length ? q.reasons.join('\n') : t('js.inbox.no_issues')) + (sync ? '\n' + sync : '');
    return `<details class="qa-box" ${q.status === 'pass' ? '' : 'open'}>
      <summary title="${esc(tip)}"><span class="badge ${cls}">${label}</span>
        ${sync ? `<span class="qa-sync">${esc(sync)}</span>` : ''}<span class="link-btn">${t('js.posts.details')}</span></summary>
      <ul>${q.checks.map(c => `<li class="${esc(c.status)}"><b>${QA_ICON[c.status] || ''} ${esc(c.label)}</b><span>${esc(c.detail || '')}</span></li>`).join('')}</ul>
    </details>`;
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch {
      const ta = Object.assign(document.createElement('textarea'), { value: text });
      ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch { /* ignore */ }
      ta.remove(); return ok;
    }
  }

  function counter(p, f) {
    const kind = f[3]; if (!kind) return '';
    const n = kind === 'tags' ? tagsLen(p) : (f[2](p) || '').length, max = LIMITS[kind];
    const over = n > max;
    return `<span class="char-count${over ? ' over' : ''}" title="${t('js.posts.limit', { max })}">${n} / ${max}${over ? ' ' + t('js.posts.over') : ''}</span>`;
  }

  function fieldHtml(p, f) {
    const [id, label, get, , collapsible] = f;
    const text = get(p) || '';
    const fid = `pf-${p.uid}-${id}`;
    return `<div class="post-field" data-field="${id}">
      <div class="post-field-head"><span class="label" id="${fid}">${label}</span>${counter(p, f)}
        <button class="btn btn-sm copy-btn" data-copy="${id}" aria-describedby="${fid}" ${text ? '' : 'disabled'}>
          <svg width="16" height="16" aria-hidden="true"><use href="#i-copy"/></svg><span>${t('js.posts.copy')}</span></button></div>
      <div class="post-text${collapsible ? ' clamp' : ''}" ${collapsible ? `id="${fid}-t"` : ''}>${text ? esc(text) : `<span class="video-meta">${t('js.posts.none')}</span>`}</div>
      ${collapsible && text.split('\n').length > 4 || collapsible && text.length > 280
        ? `<button class="link-btn" data-expand aria-expanded="false" aria-controls="${fid}-t">${t('js.posts.show_all')}</button>` : ''}
    </div>`;
  }

  function cardHtml(p) {
    const thumb = p.thumbnail_url
      ? `<img src="${esc(p.thumbnail_url)}" alt="" loading="lazy" decoding="async" width="320" height="180">`
      : `<span class="video-meta">${t('js.posts.no_thumb')}</span>`;
    const busy = !!p.republish_job;
    return `<article class="card post-card${p.posted ? ' is-posted' : ''}" data-stem="${esc(p.stem)}" aria-labelledby="pt-${p.uid}">
      <div class="post-side">
        <button class="post-thumb" data-play aria-label="${esc(t('js.posts.play', { name: p.title_en || p.name }))}">${thumb}
          <span class="post-play"><svg width="36" height="36" aria-hidden="true"><use href="#i-play"/></svg></span>
          ${p.duration != null ? `<span class="post-dur">${fmtDur(p.duration)}</span>` : ''}</button>
        <div class="post-actions">
          <button class="btn btn-sm btn-primary copy-btn" data-copy="set_en"><svg width="16" height="16" aria-hidden="true"><use href="#i-copy"/></svg><span>${t('js.posts.copy_set')}</span></button>
          ${p.thumbnail_url ? `<a class="btn btn-sm" href="/api/posts/${encodeURIComponent(p.stem)}/download/thumbnail" download><svg width="16" height="16" aria-hidden="true"><use href="#i-download"/></svg>${t('js.posts.save_thumb')}</a>` : ''}
          <button class="btn btn-sm" data-reveal><svg width="16" height="16" aria-hidden="true"><use href="#i-folder"/></svg>${t('js.posts.reveal')}</button>
          <button class="btn btn-sm" data-republish ${busy ? 'disabled aria-busy="true"' : ''}><svg width="16" height="16" aria-hidden="true"><use href="#i-refresh"/></svg>${busy ? t('js.posts.redoing') : t('js.posts.redo_kit')}</button>
          <label class="check posted-toggle"><input type="checkbox" data-posted ${p.posted ? 'checked' : ''}>${t('js.posts.posted')}${p.posted && p.posted_at ? `<span class="video-meta">(${fmtDay(p.posted_at)})</span>` : ''}</label>
        </div>
      </div>
      <div class="post-main">
        <div class="post-title-row">
          ${hasNum(p) ? `<span class="post-num">#${String(p.number).padStart(2, '0')}</span>` : ''}
          <h2 id="pt-${p.uid}" class="post-name" title="${esc(p.name)}">${esc(p.name)}</h2>
          ${p.posted ? `<span class="badge badge-done">${t('js.posts.posted')}</span>` : `<span class="badge badge-idle">${t('js.posts.not_posted')}</span>`}
          ${p.has_kit ? '' : `<span class="badge ${busy ? 'badge-work' : 'badge-err'}">${busy ? t('js.inbox.publishing') : t('js.posts.no_kit')}</span>`}
        </div>
        ${qaHtml(p)}
        <div class="video-meta">${fmtSize(p.size)}${p.duration != null ? ' · ' + fmtDur(p.duration) : ''}${p.thumbnail_text ? ' · ' + t('js.posts.thumb_text') + ': ' + esc(p.thumbnail_text) : ''}</div>
        <div class="post-fields">${FIELDS.map(f => fieldHtml(p, f)).join('')}</div>
      </div>
    </article>`;
  }

  function visible() {
    const q = $('#posts-search').value.trim().toLowerCase();
    return posts.filter(p => (filter === 'all' || (filter === 'done') === p.posted)
      && (!q || [p.stem, p.title_en, p.title_ja, ...p.tags, ...p.hashtags].join(' ').toLowerCase().includes(q)));
  }

  function renderCounts() {
    const done = posts.filter(p => p.posted).length;
    $('#posts-total').textContent = t('js.posts.total', { n: posts.length });
    $('#posts-done').textContent = t('js.posts.posted_n', { n: done });
    const b = $('#nav-posts-badge');
    b.hidden = !posts.length; b.textContent = `${posts.length - done}`;
    b.title = t('js.posts.not_posted_n', { n: posts.length - done });
  }

  function render() {
    renderCounts();
    $$('#posts-filter button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === filter));
    const list = visible();
    $('#posts-shown').textContent = list.length === posts.length ? '' : t('js.posts.shown', { n: list.length });
    $('#post-list').innerHTML = list.length ? list.map(cardHtml).join('')
      : `<p class="empty">${posts.length ? t('js.posts.no_match') : t('js.posts.no_posts')}</p>`;
  }

  async function loadPosts() {
    if (!loaded && typeof UI === 'object') {
      filter = ['all', 'todo', 'done'].includes(UI.posts_filter) ? UI.posts_filter : 'all';
      $('#posts-search').value = UI.posts_search || '';
    }
    try {
      posts = await api('/api/posts'); loaded = true;
      posts.forEach((p, i) => { p.uid = i; if (p.republish_job) watchJob(p.stem, p.republish_job); });
      render();
    }
    catch (e) { $('#post-list').innerHTML = `<p class="empty">${t('js.posts.load_failed')}: ${esc(e.message)}</p>`; }
  }
  window.loadPosts = loadPosts;

  const byStem = (el) => posts.find(p => p.stem === el.closest('.post-card')?.dataset.stem);

  // ------------------------------------------------------------ republish (background job)
  function rerenderCard(p) {
    const card = $(`.post-card[data-stem="${CSS.escape(p.stem)}"]`);
    if (card) card.outerHTML = cardHtml(p);
  }
  function watchJob(stem, jid) {
    if (watching.has(stem)) return;
    watching.add(stem);
    const tick = async () => {
      let j;
      try { j = await api('/api/jobs/' + jid); } catch { j = { status: 'error', log: [] }; }
      if (j.status === 'running') return setTimeout(tick, 2000);
      watching.delete(stem);
      toast(j.status === 'done' ? t('js.posts.kit_done') : t('js.posts.kit_failed') + ': ' + (j.log.slice(-1)[0] || ''));
      if (current === 'posts') loadPosts();
    };
    setTimeout(tick, 2000);
  }
  async function republish(p) {
    try {
      const r = await api(`/api/posts/${encodeURIComponent(p.stem)}/republish`, { method: 'POST' });
      p.republish_job = r.job; rerenderCard(p);
      toast(t('js.posts.kit_started'));
      watchJob(p.stem, r.job);
    } catch (e) { toast(e.message); }
  }

  // ------------------------------------------------------------ preview modal
  const modal = $('#post-modal');
  function openPreview(p) {
    $('#post-modal-title').textContent = p.title_en || p.name;
    $('#post-modal-body').innerHTML = `<video controls autoplay preload="metadata" playsinline
      src="${esc(p.video_url)}" ${p.thumbnail_url ? `poster="${esc(p.thumbnail_url)}"` : ''}></video>
      ${p.thumbnail_url ? `<details class="modal-thumb"><summary>${t('js.posts.big_thumb')}</summary><img src="${esc(p.thumbnail_url)}" alt="${esc(t('js.posts.thumb_of', { name: p.title_en }))}"></details>` : ''}`;
    modal.showModal();
  }
  function closePreview() { modal.close(); }
  modal.addEventListener('close', () => { $('#post-modal-body').innerHTML = ''; });   // stops playback
  modal.addEventListener('click', e => { if (e.target === modal) closePreview(); });   // backdrop
  $('#post-modal-close').addEventListener('click', closePreview);

  // ------------------------------------------------------------ events
  $('#post-list').addEventListener('click', async e => {
    const p = byStem(e.target); if (!p) return;
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      const id = copy.dataset.copy, ok = await copyText(fieldText(p, id));
      if (!ok) { toast(t('js.posts.copy_failed')); return; }
      const span = $('span', copy), use = $('use', copy), orig = span.textContent;
      copy.classList.add('copied'); span.textContent = t('js.posts.copied'); use.setAttribute('href', '#i-check');
      clearTimeout(copy._t);
      copy._t = setTimeout(() => { copy.classList.remove('copied'); span.textContent = copy.dataset.label; use.setAttribute('href', '#i-copy'); }, 1500);
      copy.dataset.label ||= orig;
      const label = id === 'set_en' ? t('js.posts.set_en') : FIELDS.find(f => f[0] === id)[1];
      toast(t('js.posts.copied_x', { label }));
      return;
    }
    const exp = e.target.closest('[data-expand]');
    if (exp) {
      const open = exp.getAttribute('aria-expanded') !== 'true';
      $('#' + exp.getAttribute('aria-controls')).classList.toggle('clamp', !open);
      exp.setAttribute('aria-expanded', open); exp.textContent = open ? t('js.posts.collapse') : t('js.posts.show_all');
      return;
    }
    if (e.target.closest('[data-play]')) return openPreview(p);
    if (e.target.closest('[data-republish]')) return republish(p);
    if (e.target.closest('[data-reveal]')) {
      const r = await api('/api/posts/reveal', { method: 'POST', body: { name: p.video } }).catch(() => ({}));
      if (!r.ok) toast(t('js.posts.no_video'));
    }
  });

  $('#post-list').addEventListener('change', async e => {
    const box = e.target.closest('[data-posted]'); if (!box) return;
    const p = byStem(box); box.disabled = true;
    try {
      const r = await api(`/api/posts/${encodeURIComponent(p.stem)}/posted`, { method: 'POST', body: { posted: box.checked } });
      Object.assign(p, { posted: r.posted, posted_at: r.posted_at });
      toast(r.posted ? t('js.posts.marked_posted') : t('js.posts.marked_unposted'));
      // re-render just this card (keeps scroll position); hide it if it no longer matches the filter
      const card = box.closest('.post-card');
      if (visible().includes(p)) {
        card.outerHTML = cardHtml(p);
        $(`.post-card[data-stem="${CSS.escape(p.stem)}"] [data-posted]`)?.focus();
      } else card.remove();
      renderCounts();
    } catch (err) { box.checked = !box.checked; box.disabled = false; toast(err.message); }
  });

  $('#posts-filter').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    filter = b.dataset.v; saveUI({ posts_filter: filter }); render();
  });
  let searchTimer;
  $('#posts-search').addEventListener('input', e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { render(); saveUI({ posts_search: e.target.value }); }, 120);
  });
  $('#open-posting').addEventListener('click', async () => {
    const r = await api('/api/posts/open-folder', { method: 'POST' });
    toast(t('js.posts.opened') + ': ' + r.path.replace(/^.*\/Desktop\//, 'Desktop/'));
  });
})();

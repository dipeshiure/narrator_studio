// Caption fonts: built-in + Google Fonts the user added (kinetic_captions font registry,
// /api/fonts). Fills every caption font <select>, shows each face as a live sample, and
// adds new families from fonts.google.com (name, specimen URL or embed code).
(() => {
  let fonts = [];
  const fam = (id) => 'kcf-' + id;
  const loaded = new Set();

  async function face(f) {                 // register the TTF so samples render in that face
    if (loaded.has(f.id)) return;
    loaded.add(f.id);
    try { const ff = new FontFace(fam(f.id), `url(${f.url})`); await ff.load(); document.fonts.add(ff); }
    catch { loaded.delete(f.id); }
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const options = (auto) => `<option value="">${auto}</option>` +
    fonts.map(f => `<option value="${esc(f.id)}">${esc(f.label)}${f.user ? `(${t('js.fonts.added_tag')})` : ''}</option>`).join('');

  function fill() {
    const keep = (el) => el && el.value;
    const sel = [['#story-kc-font', t('js.fonts.auto_style')], ['#story-kc-accent', t('js.fonts.auto_accent')],
                 ['#kc-font', t('js.fonts.auto_style')]];
    for (const [q, auto] of sel) {
      const el = $(q); if (!el) continue;
      const v = keep(el); el.innerHTML = options(auto); el.value = v || '';
    }
    const list = $('#font-list');
    const mine = fonts.filter(f => f.user);
    list.innerHTML = mine.length ? mine.map(f => `<li><span class="fl-sample" style="font-family:'${fam(f.id)}'">never give up on yourself</span>
        <span class="fl-name">${esc(f.label)}</span>
        <button class="icon-btn" data-del="${esc(f.id)}" aria-label="${esc(t('js.inbox.delete_one', { name: f.label }))}" title="${t('js.inbox.delete')}"><svg width="18" height="18" aria-hidden="true"><use href="#i-trash"/></svg></button></li>`).join('')
      : `<li class="fl-name" style="border:0;padding:4px 0">${t('js.fonts.none')}</li>`;
    if (window.storyRepaint) storyRepaint();
    if (typeof S !== 'undefined' && $('#kc-font')) $('#kc-font').value = S.kc_font || '';
  }

  async function load() {
    try { fonts = await api('/api/fonts'); } catch { return; }
    fonts.forEach(face);
    fill();
  }

  // sample line: body face + the key word in the accent face (bhatt) or the same face
  window.KCFonts = {
    load,
    sample(el, body, accent, preset) {
      if (!el) return;
      const own = { bhatt: 'tight', cinema: 'serif_it', impact: 'bebas' }[preset] || 'montserrat';
      const b = body || own;
      const a = preset === 'bhatt' ? (accent || 'serif_it') : b;
      el.innerHTML = preset === 'bhatt' ? 'that <em>moment</em> of weakness'
        : preset === 'cinema' ? 'That <em>moment</em> of weakness' : 'THAT <em>MOMENT</em> OF WEAKNESS';
      el.dataset.preset = preset || '';
      el.style.fontFamily = `'${fam(b)}', sans-serif`;
      $('em', el).style.fontFamily = `'${fam(a)}', serif`;
      $('em', el).style.fontSize = { bhatt: '1.2em', cinema: '1em', impact: '1.25em' }[preset] || '1.35em';
      $('em', el).style.color = preset === 'impact' ? '#FF3B5C' : '';
    },
  };

  $('#font-add').addEventListener('click', async () => {
    const q = $('#font-query').value.trim();
    if (!q) { $('#font-query').focus(); return toast(t('js.fonts.need_query')); }
    const btn = $('#font-add'); btn.disabled = true; btn.lastChild.textContent = t('js.fonts.fetching');
    try {
      const f = await api('/api/fonts', { method: 'POST', body: { query: q, weight: $('#font-weight').value,
                                                                  italic: $('#font-italic').checked } });
      $('#font-query').value = '';
      await load();
      toast(t('js.fonts.added', { name: f.label }));
    } catch (e) { toast(e.message); }
    btn.disabled = false; btn.lastChild.textContent = t('js.fonts.add');
  });
  $('#font-query').addEventListener('keydown', e => e.key === 'Enter' && $('#font-add').click());
  $('#font-list').addEventListener('click', async e => {
    const b = e.target.closest('[data-del]'); if (!b) return;
    if (!confirm(t('js.fonts.del_confirm'))) return;
    try { await api('/api/fonts/remove', { method: 'POST', body: { id: b.dataset.del } }); await load(); }
    catch (err) { toast(err.message); }
  });
  load();
})();

// 台本から作る — script -> narrated video (story.py via /api/story/*)
// Draft, options and the running job id live in ui_state.json (UI.story), so a reload
// or a server restart keeps the script and reconnects to a render that is still going.
(() => {
  const PRESETS = {
    bhatt: {
      hint: t('js.story.bhatt_hint'),
      o: { tts: 'dia', dia_seed: 33, voice: 'en-US-ChristopherNeural', speed: 0.92, pitch: -4, visuals: 'ai', aspect: '16:9', grain: true,
           headings: false, engine: 'kinetic', kc_preset: 'bhatt', kc_font: '', kc_accent_font: '', kc_size: 1.0,
           bgm: true, mood: 'dark' },
    },
  };
  let opts = null;          // /api/story/options
  let st = null;            // { title, text, preset, o: {...}, job, draft_job, style, minutes }
  let polling = null;

  const saveState = () => saveUI({ story: { ...st } });
  const signed = (n, unit) => (n >= 0 ? '+' : '') + n + unit;
  const ratePct = (speed) => signed(Math.round((speed - 1) * 100), '%');   // edge-tts wants a percentage

  // ------------------------------------------------------------ paint
  function seg(id, v) {
    $$('#' + id + ' button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v) === String(v)));
  }
  function paint() {
    const o = st.o;
    seg('story-preset', st.preset); seg('story-visuals', o.visuals); seg('story-aspect', o.aspect);
    seg('story-engine', o.engine); seg('story-kc-preset', o.kc_preset); seg('story-mood', o.mood);
    seg('story-style', st.style); seg('story-minutes', st.minutes);
    $('#story-preset-hint').textContent = PRESETS[st.preset].hint;
    $('#story-voice').value = o.voice;
    const dia = o.tts === 'dia';
    seg('story-tts', dia ? 'dia' : 'edge');
    $('#story-voice').hidden = dia; $('#story-dia-voice').hidden = !dia;
    $('#story-dia-voice').value = o.dia_voice ? 'v:' + o.dia_voice : String(o.dia_seed || 33);
    $$('.story-dia').forEach(el => el.hidden = !dia);
    $$('.story-edge').forEach(el => el.hidden = dia);
    const th = $('#story-tts-hint');
    if (dia && opts && !opts.dia_ready) { th.className = 'hint status-warn'; th.textContent = t('js.story.dia_missing') + ': ' + opts.dia_setup; }
    else if (dia) { th.className = 'hint'; th.textContent = t('js.story.dia_hint'); }
    else { th.className = 'hint'; th.textContent = t('js.story.edge_hint'); }

    $('#story-rate').value = o.speed; $('#o-story-rate').textContent = o.speed.toFixed(2) + '×';
    $('#story-pitch').value = o.pitch; $('#o-story-pitch').textContent = signed(o.pitch, 'Hz');
    $('#story-kc-size').value = o.kc_size; $('#o-story-kc-size').textContent = Number(o.kc_size).toFixed(2) + '×';
    $('#story-kc-font').value = o.kc_font || '';
    $('#story-kc-accent').value = o.kc_accent_font || '';
    window.KCFonts && KCFonts.sample($('#story-font-sample'), o.kc_font, o.kc_accent_font, o.kc_preset);
    $('#story-hero').value = o.hero || '';
    $('#story-grain').checked = !!o.grain; $('#story-headings').checked = !!o.headings;
    $('#story-bgm').checked = !!o.bgm;
    $$('#story-mood button').forEach(b => b.disabled = !o.bgm);
    $('#story-hero-field').hidden = o.visuals !== 'ai';
    $('#story-folder-field').hidden = o.visuals !== 'folder';
    $('#story-match-card').hidden = o.visuals !== 'folder';
    $('#story-folder').value = o.media_folder || '';
    $$('.story-kc').forEach(el => el.hidden = o.engine !== 'kinetic');
    $('#story-kc-accent-field').hidden = o.engine !== 'kinetic' || o.kc_preset !== 'bhatt';   // only bhatt has a key-word face

    const ai = $('#story-ai-status');
    if (!opts || o.visuals !== 'ai') { ai.textContent = ''; ai.className = 'hint'; }
    else if (opts.ai_ready) { ai.className = 'hint status-ok'; ai.textContent = t('js.story.ai_ready'); }
    else if (opts.ai_downloading) { ai.className = 'hint status-warn'; ai.textContent = t('js.story.ai_downloading'); }
    else { ai.className = 'hint status-warn'; ai.textContent = t('js.story.ai_missing'); }
    const kc = $('#story-kc-status');
    kc.className = 'hint ' + (opts && !opts.kinetic_ready && o.engine === 'kinetic' ? 'status-warn' : '');
    kc.textContent = opts && !opts.kinetic_ready && o.engine === 'kinetic'
      ? t('js.story.kc_missing') + ': ' + opts.kinetic_setup
      : o.engine === 'kinetic' ? t('js.story.kc_hint') : '';
    stats();
  }

  // mirrors story.split_text: a paragraph over 70 words becomes ~45-word scenes
  function sceneCount(p) {
    const w = (p.match(/\S+/g) || []).length;
    return w <= 70 ? 1 : Math.max(2, Math.round(w / 45));
  }

  // words that will be spoken (tags and headings are not narrated)
  function stats() {
    const text = $('#story-text').value;
    const body = text.replace(/\[\[[^\]]*\]\]/g, ' ').split('\n').filter(l => !l.trim().startsWith('## ')).join('\n');
    const words = (body.match(/[A-Za-z0-9'$%.,-]+/g) || []).filter(w => /[A-Za-z0-9]/.test(w)).length;
    const paras = body.split(/\n\s*\n/).filter(p => p.trim()).reduce((n, p) => n + sceneCount(p), 0);   // after the automatic split
    const chapters = text.split('\n').filter(l => l.trim().startsWith('## ')).length;
    const min = words / (150 * st.o.speed);
    $('#story-stats').textContent = words ? t('js.story.stats', { words: words.toLocaleString(), paras }) + (chapters ? ' · ' + t('js.story.chapters', { n: chapters }) : '') : '';
    const est = $('#story-estimate');
    if (!words) est.textContent = t('js.story.need_text');
    else {
      const m = min < 1 ? t('js.story.sec', { n: Math.round(min * 60) }) : t('js.story.min', { n: Math.round(min) });
      const imgs = st.o.visuals === 'ai' && opts?.ai_ready ? ' · ' + t('js.story.images', { n: Math.max(paras, Math.round(min * 60 / 7)) }) : '';
      est.textContent = t('js.story.estimate', { len: m, imgs });
    }
    $('#story-generate').disabled = !words || !$('#story-title').value.trim() || !!st.job;
    paintBatchPick();
  }

  // ------------------------------------------------------------ jobs
  // Overall progress read from the job log. Narration takes most of the time (Dia), so it
  // owns most of the bar; the later stages are quick.
  const STAGES = [['voice', 0, 70], ['visual', 70, 85], ['encode', 85, 90], ['subs', 90, 99], ['done', 100, 100]];
  function progressOf(lines) {
    let stage = 'voice', frac = 0;
    for (const l of lines) {
      let m;
      if ((m = l.match(/(?:ナレーション|Narration) (\d+)\/(\d+)/))) { stage = 'voice'; frac = +m[1] / Math.max(1, +m[2]); }
      else if (/ナレーション完成|Narration done/.test(l)) { stage = 'visual'; frac = 0; }
      else if ((m = l.match(/(?:シーン|Scene) (\d+)\/(\d+)/))) { stage = 'visual'; frac = +m[1] / Math.max(1, +m[2]); }
      else if (/最終エンコード|Final encode/.test(l)) { stage = 'encode'; frac = 0.5; }
      else if ((m = l.match(/(?:字幕|Captions) (\d+)%/))) { stage = 'subs'; frac = +m[1] / 100; }
      else if (/kinetic-captions で字幕|captions with kinetic-captions/.test(l)) { stage = 'subs'; frac = 0; }
      else if (/完成しました|^\s*Done$/.test(l)) { stage = 'done'; frac = 1; }
    }
    const [, a, b] = STAGES.find(s => s[0] === stage);
    return { stage, pct: Math.round(a + (b - a) * Math.min(1, frac)) };
  }
  function paintProgress(lines, show) {
    const box = $('#story-progress'); box.hidden = !show;
    if (!show) return;
    const { stage, pct } = progressOf(lines);
    const bar = $('.progress', box); bar.setAttribute('aria-valuenow', pct);
    $('div', bar).style.width = pct + '%';
    const order = STAGES.map(s => s[0]);
    $$('#story-stages span').forEach(sp => {
      const i = order.indexOf(sp.dataset.st), k = order.indexOf(stage);
      sp.toggleAttribute('data-done', i < k || stage === 'done');
      sp.toggleAttribute('data-on', i === k && stage !== 'done');
    });
  }

  async function follow(id, onEnd, render = false) {
    $('#story-job').hidden = false; $('#story-spinner').hidden = false;
    const log = $('#story-log');
    const tick = async () => {
      let j;
      try { j = await api('/api/jobs/' + id); }
      catch { onEnd({ status: 'lost', log: [] }); return; }      // server restarted
      log.textContent = j.log.join('\n'); log.scrollTop = log.scrollHeight;
      // a batch shows the bar for the video being made now (its log starts at log_from)
      const cur = j.items && j.current != null ? j.items[j.current] : null;
      paintProgress(cur ? j.log.slice(cur.log_from || 0) : j.log, render && j.status !== 'lost' && (!j.items || cur));
      if (j.items) paintQueue(j);
      const last = [...j.log].reverse().find(l => l.trim());
      if (j.status === 'running') {
        if (last) $('#story-status').textContent = last.trim();
        polling = setTimeout(tick, 1500); return;
      }
      $('#story-spinner').hidden = true; onEnd(j);
    };
    clearTimeout(polling); tick();
  }
  function badge(on, text) { const b = $('#nav-story-badge'); b.hidden = !on; b.textContent = text || t('js.story.making'); }

  // batch queue: one row per script with its state; finished ones link to the video
  const QSTATE = { waiting: 'badge-wait', running: 'badge-work', done: 'badge-done', skipped: 'badge-idle', error: 'badge-err' };
  function paintQueue(j) {
    const items = j.items, done = items.filter(x => ['done', 'error', 'skipped'].includes(x.status)).length;
    const n = j.current != null ? j.current + 1 : done;
    $('#story-queue-box').hidden = false;
    $('#story-queue-head').textContent = t('js.story.batch_progress', { n: Math.min(n, items.length), total: items.length });
    $('#story-batch-cancel').hidden = j.status !== 'running' || j.cancel;
    $('#story-queue').innerHTML = items.map(x => `<li>
        <span class="badge ${QSTATE[x.status]}">${x.status === 'running' ? '<span class="pulse"></span>' : ''}${t('js.story.q_' + x.status)}</span>
        <span class="batch-title">${esc(x.title)}</span>
        ${x.status === 'done' ? `<a class="link-btn" href="/media/${encodeURIComponent(x.video)}" target="_blank" rel="noopener">${t('js.inbox.watch')}</a>` : ''}
        ${x.error ? `<span class="video-meta inbox-err" title="${esc(x.error)}">${esc(x.error.slice(0, 80))}</span>` : ''}</li>`).join('');
    if (j.status === 'running') badge(true, `${t('js.story.making')} ${Math.min(n, items.length)}/${items.length}`);
  }

  function followRender(id) {
    st.job = id; saveState(); badge(true); stats();
    $('#story-result').hidden = true; $('#story-queue-box').hidden = true;
    follow(id, (j) => {
      st.job = null; saveState(); badge(false); stats(); loadBatchList();
      if (j.items) {                                   // batch: the summary line is the last log line
        $('#story-progress').hidden = true;
        $('#story-status').textContent = j.status === 'lost' ? t('js.story.lost') : ([...j.log].reverse().find(l => l.trim()) || '');
        if (j.status === 'done') toast(t('js.story.batch_done'));
        return;
      }
      if (j.status === 'done') {
        $('#story-status').textContent = t('js.story.done') + ': ' + j.result;
        $('#story-video').src = '/media/' + encodeURIComponent(j.result) + '?t=' + Date.now();
        $('#story-result').hidden = false; toast(t('js.app.video_ready'));
      } else if (j.status === 'lost') {
        $('#story-progress').hidden = true;
        $('#story-status').textContent = t('js.story.lost');
      } else $('#story-status').textContent = t('js.story.failed');
    }, true);
  }

  // ------------------------------------------------------------ load
  window.storyRepaint = () => st && paint();
  window.loadStory = async () => {
    if (!st) {
      const saved = UI.story || {};
      st = { title: '', text: '', preset: 'bhatt', style: 'bhatt', minutes: 10, job: null, ...saved,
             ...(PRESETS[saved.preset] ? {} : { preset: 'bhatt' }),
             ...(['bhatt', 'motivation'].includes(saved.style) ? {} : { style: 'bhatt' }),
             o: { ...PRESETS.bhatt.o, hero: '', ...(saved.o || {}) } };
      if (typeof st.o.speed !== 'number') st.o.speed = Math.max(0.6, Math.min(1.4, 1 + (Number(st.o.rate) || 0) / 100));   // old % setting
      delete st.o.rate;
      if (!['kinetic', 'simple', 'off'].includes(st.o.engine)) st.o.engine = 'kinetic';   // SubtitleFX was removed
      $('#story-title').value = st.title; $('#story-text').value = st.text;
      if (st.job) followRender(st.job);
    }
    try {
      opts = await api('/api/story/options');
      const dv = $('#story-dia-voice');
      fillDiaVoices(opts.my_voices || []);
      const v = $('#story-voice');
      if (!v.options.length) v.innerHTML = opts.voices.map(x => `<option value="${x.id}">${x.label}</option>`).join('');
      const sel = $('#story-saved');
      sel.innerHTML = `<option value="">${t('js.story.open_saved')}</option>` + opts.scripts.map(s =>
        `<option value="${s.name}">${s.title.replace(/</g, '&lt;')}</option>`).join('');
      renderBatchList(opts.scripts);
    } catch (e) { toast(t('js.story.opts_failed') + ': ' + e.message); }
    paint();
  };

  // ------------------------------------------------------------ inputs
  const set = (k, v) => { st.o[k] = v; saveState(); paint(); };
  const onSeg = (id, fn) => $('#' + id).addEventListener('click', e => {
    const b = e.target.closest('button'); if (b && !b.disabled) fn(b.dataset.v);
  });
  onSeg('story-preset', v => { st.preset = v; st.o = { ...PRESETS[v].o, hero: st.o.hero }; saveState(); paint(); });
  onSeg('story-visuals', v => set('visuals', v));
  onSeg('story-aspect', v => set('aspect', v));
  onSeg('story-engine', v => set('engine', v));
  onSeg('story-kc-preset', v => set('kc_preset', v));
  onSeg('story-mood', v => set('mood', v));
  onSeg('story-style', v => { st.style = v; saveState(); paint(); });
  onSeg('story-minutes', v => { st.minutes = Number(v); saveState(); paint(); });
  $('#story-voice').addEventListener('change', e => set('voice', e.target.value));
  $('#story-dia-voice').addEventListener('change', e => {
    const v = e.target.value;
    if (v.startsWith('v:')) set('dia_voice', v.slice(2));
    else { st.o.dia_voice = ''; set('dia_seed', Number(v)); }
  });

  // ------------------------------------------------------------ my voices (recorded reference)
  function fillDiaVoices(mine) {
    const dv = $('#story-dia-voice');
    if (st.o.dia_voice && !mine.some(v => v.id === st.o.dia_voice)) st.o.dia_voice = '';   // deleted
    dv.innerHTML = (opts.dia_voices || []).map(x => `<option value="${x.seed}">${x.name} — ${x.label}</option>`).join('')
      + (mine.length ? `<optgroup label="${esc(t('js.voice.mine'))}">` + mine.map(v =>
        `<option value="v:${v.id}">${esc(v.name)}</option>`).join('') + '</optgroup>' : '');
    $('#my-voice-list').innerHTML = mine.map(v => `<li>
        <span class="batch-title">${esc(v.name)}</span><span class="video-meta">${v.seconds || ''} s</span>
        <button class="icon-btn" data-play="${v.id}" aria-label="${esc(t('js.voice.play', { name: v.name }))}" title="${esc(t('js.voice.play', { name: v.name }))}"><svg width="18" height="18" aria-hidden="true"><use href="#i-play"/></svg></button>
        <button class="icon-btn" data-del="${v.id}" aria-label="${esc(t('js.inbox.delete_one', { name: v.name }))}" title="${t('js.inbox.delete')}"><svg width="18" height="18" aria-hidden="true"><use href="#i-trash"/></svg></button></li>`).join('');
    paint();
  }
  $('#my-voice-list').addEventListener('click', async e => {
    const play = e.target.closest('[data-play]'), del = e.target.closest('[data-del]');
    if (play) { const a = $('#story-voice-audio'); a.src = `/api/story/voices/${play.dataset.play}/audio`; a.play(); }
    if (del && confirm(t('js.voice.del_confirm'))) {
      try { fillDiaVoices((await api(`/api/story/voices/${del.dataset.del}/delete`, { method: 'POST' })).voices); saveState(); }
      catch (err) { toast(err.message); }
    }
  });
  $('#my-voice-add').addEventListener('click', async () => {
    const file = $('#my-voice-file').files[0];
    if (!file) return toast(t('js.voice.need_file'));
    if (!$('#my-voice-consent').checked) return toast(t('js.voice.need_consent'));
    const fd = new FormData();
    fd.append('file', file, file.name); fd.append('name', $('#my-voice-name').value.trim());
    fd.append('text', $('#my-voice-text').value.trim()); fd.append('consent', '1');
    const btn = $('#my-voice-add'); btn.disabled = true; toast(t('js.voice.adding'));
    try {
      const r = await api('/api/story/voices', { method: 'POST', body: fd });
      opts.my_voices = r.voices; st.o.dia_voice = r.id; saveState(); fillDiaVoices(r.voices);
      $('#my-voice-file').value = ''; $('#my-voice-name').value = ''; $('#my-voice-text').value = '';
      toast(t('js.voice.added'));
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  });
  onSeg('story-tts', v => set('tts', v));
  $('#story-rate').addEventListener('input', e => set('speed', Math.round(Number(e.target.value) * 100) / 100));
  $('#story-pitch').addEventListener('input', e => set('pitch', Number(e.target.value)));
  $('#story-kc-size').addEventListener('input', e => set('kc_size', Number(e.target.value)));
  $('#story-kc-font').addEventListener('change', e => set('kc_font', e.target.value));
  $('#story-kc-accent').addEventListener('change', e => set('kc_accent_font', e.target.value));
  $('#story-hero').addEventListener('change', e => set('hero', e.target.value.trim()));
  $('#story-grain').addEventListener('change', e => set('grain', e.target.checked));
  $('#story-headings').addEventListener('change', e => set('headings', e.target.checked));
  $('#story-bgm').addEventListener('change', e => set('bgm', e.target.checked));
  let typing;
  const onType = () => {
    st.title = $('#story-title').value; st.text = $('#story-text').value; stats();
    clearTimeout(typing); typing = setTimeout(saveState, 600);
  };
  $('#story-title').addEventListener('input', onType);
  $('#story-text').addEventListener('input', onType);

  $('#story-saved').addEventListener('change', async e => {
    if (!e.target.value) return;
    if ($('#story-text').value.trim() && !confirm(t('js.story.replace_saved'))) { e.target.value = ''; return; }
    const s = await api('/api/story/scripts/' + encodeURIComponent(e.target.value));
    $('#story-title').value = s.title; $('#story-text').value = s.text; e.target.value = ''; onType();
  });

  // ------------------------------------------------------------ batch (まとめて作る)
  // saved scripts as a checklist; the picked names live in st.batchPick (ui_state)
  let scripts = [];
  function renderBatchList(list) {
    scripts = list || [];
    const have = new Set(scripts.map(s => s.name));
    st.batchPick = (st.batchPick || []).filter(n => have.has(n));
    $('#story-batch-list').innerHTML = scripts.length ? scripts.map(s => `<li>
        <label class="check" style="min-height:0"><input type="checkbox" data-name="${esc(s.name)}" ${st.batchPick.includes(s.name) ? 'checked' : ''}>
          <span class="batch-title">${esc(s.title)}</span></label>
        <span class="video-meta">${t('js.story.batch_paras', { n: s.paras })}</span>
        ${s.has_video ? `<span class="badge badge-done">${t('js.story.batch_has_video')}</span>` : ''}</li>`).join('')
      : `<li class="video-meta">${t('js.story.batch_none')}</li>`;
    paintBatchPick();
  }
  function paintBatchPick() {
    if (!st) return;
    const n = (st.batchPick || []).length;
    $('#story-batch-picked').textContent = t('js.story.batch_picked', { n });
    $('#story-batch-count').hidden = !n; $('#story-batch-count').textContent = n;
    $('#story-batch-start-label').textContent = n ? t('js.story.batch_start_n', { n }) : t('story.batch_start');
    $('#story-batch-start').disabled = !n || !!st.job;
    const all = $('#story-batch-all');
    all.checked = !!scripts.length && n === scripts.length; all.indeterminate = n > 0 && n < scripts.length;
  }
  async function loadBatchList() {
    try { renderBatchList((await api('/api/story/options')).scripts); } catch { /* list stays as it was */ }
  }
  $('#story-batch-list').addEventListener('change', e => {
    const name = e.target.dataset.name; if (!name) return;
    const pick = new Set(st.batchPick || []);
    e.target.checked ? pick.add(name) : pick.delete(name);
    st.batchPick = scripts.map(s => s.name).filter(n => pick.has(n));   // keep list order = make order
    saveState(); paintBatchPick();
  });
  $('#story-batch-all').addEventListener('change', e => {
    st.batchPick = e.target.checked ? scripts.map(s => s.name) : [];
    $$('#story-batch-list input[type=checkbox]').forEach(c => { c.checked = e.target.checked; });
    saveState(); paintBatchPick();
  });
  async function uploadScripts(files) {
    files = [...files].filter(f => /\.txt$/i.test(f.name));
    if (!files.length) return toast(t('js.story.batch_txt_only'));
    const fd = new FormData(); files.forEach(f => fd.append('files', f, f.name));
    try {
      const r = await api('/api/story/scripts/upload', { method: 'POST', body: fd });
      st.batchPick = [...new Set([...(st.batchPick || []), ...r.added])]; saveState();
      await loadBatchList();
      toast(t('js.story.batch_added', { n: r.added.length }) + (r.rejected.length ? ' / ' + r.rejected.map(x => `${x.name}: ${x.error}`).join(' / ') : ''));
    } catch (e) { toast(e.message); }
  }
  const bdrop = $('#story-batch-drop');
  ['dragenter', 'dragover'].forEach(ev => bdrop.addEventListener(ev, e => { e.preventDefault(); bdrop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => bdrop.addEventListener(ev, e => { e.preventDefault(); bdrop.classList.remove('over'); }));
  bdrop.addEventListener('drop', e => uploadScripts(e.dataTransfer.files));
  $('#story-batch-files').addEventListener('change', e => { uploadScripts(e.target.files); e.target.value = ''; });
  $('#story-batch-start').addEventListener('click', async () => {
    const names = st.batchPick || [];
    if (!names.length) return;
    if (!confirm(t('js.story.batch_confirm', { n: names.length }))) return;
    $('#story-batch-start').disabled = true;
    try {
      const { job } = await api('/api/story/batch', { method: 'POST', body: {
        names, preset: st.preset, overwrite: $('#story-overwrite').checked, options: storyOptions() } });
      $('#story-status').textContent = t('js.story.started');
      followRender(job);
      $('#story-job').scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (e) { toast(e.message); paintBatchPick(); }
  });
  $('#story-batch-cancel').addEventListener('click', async () => {
    if (!st.job || !confirm(t('js.story.batch_cancel_confirm'))) return;
    try { await api(`/api/story/batch/${st.job}/cancel`, { method: 'POST' }); $('#story-batch-cancel').hidden = true; toast(t('js.story.batch_cancelling')); }
    catch (e) { toast(e.message); }
  });

  $('#story-voice-play').addEventListener('click', async () => {
    const btn = $('#story-voice-play'); btn.disabled = true;
    try {
      const res = await fetch('/api/story/voice-sample', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ engine: st.o.tts === 'dia' ? 'dia' : 'edge', seed: st.o.dia_seed || 33, voice_id: st.o.dia_voice || '',
                               voice: st.o.voice, rate: ratePct(st.o.speed), pitch: signed(st.o.pitch, 'Hz') }) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const a = $('#story-voice-audio'); a.src = URL.createObjectURL(await res.blob()); await a.play();
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  });

  $('#story-draft').addEventListener('click', async () => {
    const theme = $('#story-theme').value.trim();
    if (!theme) { $('#story-theme').focus(); return toast(t('js.story.need_theme')); }
    if ($('#story-text').value.trim() && !confirm(t('js.story.replace_ai'))) return;
    const btn = $('#story-draft'); btn.disabled = true;
    try {
      const { job } = await api('/api/story/draft', { method: 'POST', body: { theme, minutes: st.minutes, style: st.style } });
      $('#story-status').textContent = t('js.story.writing'); $('#story-result').hidden = true;
      await new Promise(done => follow(job, j => {
        if (j.status === 'done') {
          $('#story-title').value = j.result.title; $('#story-text').value = j.result.text; onType();
          $('#story-status').textContent = t('js.story.draft_done_hint');
          toast(t('js.story.draft_done'));
        } else $('#story-status').textContent = t('js.story.draft_failed');
        done();
      }));
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  });

  // the 仕上がり panel as server options — shared by "動画を作る" and the batch
  function storyOptions() {
    const o = st.o;
    return { tts_engine: o.tts === 'dia' ? 'dia' : 'edge', dia_seed: o.dia_seed || 33, dia_voice: o.dia_voice || '', voice: o.voice, tts_rate: ratePct(o.speed), tts_speed: o.speed, tts_pitch: signed(o.pitch, 'Hz'),
             visuals: o.visuals, hero: o.hero || '', media_folder: o.media_folder || '', aspect: o.aspect, grain: o.grain,
             show_headings: o.headings, subs: o.engine !== 'off',
             subs_engine: o.engine === 'off' ? 'simple' : o.engine,
             kc_preset: o.kc_preset, kc_font: o.kc_font || '', kc_accent_font: o.kc_accent_font || '', kc_size: o.kc_size,
             bgm: o.bgm, bgm_mood: o.mood };
  }
  $('#story-generate').addEventListener('click', async () => {
    const body = {
      title: $('#story-title').value.trim(), text: $('#story-text').value, preset: st.preset,
      overwrite: $('#story-overwrite').checked, options: storyOptions(),
    };
    $('#story-generate').disabled = true;
    try {
      const { job } = await api('/api/story/generate', { method: 'POST', body });
      $('#story-status').textContent = t('js.story.started');
      followRender(job);
    } catch (e) { toast(e.message); stats(); }
  });
  $('#story-to-posts').addEventListener('click', () => show('posts'));

  // ------------------------------------------------------------ own folder
  $('#story-folder').addEventListener('change', e => set('media_folder', e.target.value.trim()));
  $('#story-folder-pick').addEventListener('click', async () => {
    const btn = $('#story-folder-pick'); btn.disabled = true;
    try {
      const r = await api('/api/story/pick-folder', { method: 'POST' });
      if (r.folder) set('media_folder', r.folder);
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  });
  const esc = (x) => String(x).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  $('#story-match').addEventListener('click', async () => {
    if (!st.o.media_folder) { $('#story-folder').focus(); return toast(t('js.story.need_folder')); }
    const btn = $('#story-match'), status = $('#story-match-status'), list = $('#story-match-list');
    btn.disabled = true; list.innerHTML = ''; status.textContent = t('js.story.analyzing');
    try {
      const { job } = await api('/api/story/match-preview', { method: 'POST', body: {
        text: $('#story-text').value, preset: st.preset,
        options: { media_folder: st.o.media_folder, tts_rate: ratePct(st.o.speed) } } });
      const tick = async () => {
        const j = await api('/api/jobs/' + job);
        const last = [...j.log].reverse().find(l => l.trim());
        if (j.status === 'running') { if (last) status.textContent = last.trim(); return setTimeout(tick, 1000); }
        btn.disabled = false;
        if (j.status !== 'done') { status.textContent = last || t('js.story.check_failed'); return; }
        const r = j.result;
        status.textContent = t('js.story.matched', { n: r.count, paras: r.scenes.length });
        list.innerHTML = r.scenes.map((sc, i) => `<li>
          <div><div class="match-text">${i + 1}. ${esc(sc.text)}${sc.text.length >= 160 ? '…' : ''}</div>
            <div class="match-vis">${t('js.story.searched')}: ${esc(sc.visuals.join(' / '))}</div></div>
          <div class="match-shots">${sc.shots.map(sh => `<figure class="${sh.score < 0.26 ? 'match-weak' : ''}" title="${esc(sh.name)} (${t('js.story.score')} ${sh.score})">
            <img src="${sh.thumb}" alt="${esc(sh.name)}" loading="lazy"><figcaption>${esc(sh.name)}</figcaption></figure>`).join('')}</div></li>`).join('');
      };
      tick();
    } catch (e) { status.textContent = e.message; btn.disabled = false; }
  });
})();

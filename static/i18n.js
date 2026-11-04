// UI translations: static/i18n/<lang>.json (flat "key": "text" maps, {name} placeholders).
//   HTML : <span data-i18n="key">日本語</span>              -> textContent
//          <input data-i18n-attr="placeholder:key;title:key2">  -> attributes
//   JS   : t('key', { n: 3 })
// The language is remembered in localStorage ("lang"); Japanese is the default and the
// fallback for any key missing from the other files. Loaded before every other script.
(() => {
  const LANGS = { ja: '日本語', en: 'English', zh: '简体中文', ko: '한국어', es: 'Español', fr: 'Français' };
  const LOCALES = { ja: 'ja-JP', en: 'en-US', zh: 'zh-CN', ko: 'ko-KR', es: 'es-ES', fr: 'fr-FR' };
  const load = (lang) => {
    try {                                   // synchronous on purpose: the other scripts use t() at once
      const x = new XMLHttpRequest();
      x.open('GET', `/static/i18n/${lang}.json?v=${Date.now() >> 16}`, false);
      x.send();
      return x.status === 200 ? JSON.parse(x.responseText) : {};
    } catch { return {}; }
  };
  let lang = localStorage.getItem('lang');
  if (!LANGS[lang]) lang = LANGS[(navigator.language || '').slice(0, 2)] ? navigator.language.slice(0, 2) : 'en';
  const base = load('ja');
  // other languages fall back to English, then Japanese, for any key they don't have yet
  const dict = lang === 'ja' ? base : lang === 'en' ? { ...base, ...load('en') } : { ...base, ...load('en'), ...load(lang) };

  window.t = (key, params) => {
    let s = dict[key] ?? base[key] ?? key;
    if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m));
    return s;
  };
  window.I18N = {
    lang, langs: LANGS, locale: LOCALES[lang],
    apply(root = document) {
      root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
      root.querySelectorAll('[data-i18n-attr]').forEach(el => {
        el.dataset.i18nAttr.split(';').forEach(pair => {
          const [attr, key] = pair.split(':');
          if (attr && key) el.setAttribute(attr.trim(), t(key.trim()));
        });
      });
      document.documentElement.lang = lang;
    },
    set(next) { if (LANGS[next] && next !== lang) { localStorage.setItem('lang', next); location.reload(); } },
  };
  document.documentElement.lang = lang;
  document.cookie = `lang=${lang}; path=/; max-age=31536000; SameSite=Lax`;   // server.py translates its messages too
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => I18N.apply());
  else I18N.apply();
})();

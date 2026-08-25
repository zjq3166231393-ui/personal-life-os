/* Personal Life OS — client-side i18n.
 * Dependency-free: loads static/i18n/ui.json (Chinese -> {lang}) and swaps
 * text on chrome elements by exact-match. Default language is zh (server render),
 * so no-JS and tests keep Chinese. Falls back to English, then to original.
 *
 * We cache the ORIGINAL Chinese string on each element (data-i18n-orig /
 * data-i18n-attr-*) so switching languages repeatedly (incl. back to zh)
 * works without a page reload. */
(function () {
  var KEY = 'lifeos-lang';
  var D = null;
  var SUPPORTED = ['zh', 'en', 'es', 'fr', 'de', 'ja', 'ru', 'pt'];
  var origTitle = null;

  function getLang() {
    var l = localStorage.getItem(KEY) || 'zh';
    return SUPPORTED.indexOf(l) >= 0 ? l : 'zh';
  }
  function setHtmlLang(c) { document.documentElement.setAttribute('lang', c === 'zh' ? 'zh-CN' : c); }

  function pick(text) {
    if (!text || !D) return null;
    var entry = D[text];
    if (!entry) return null;
    var lang = getLang();
    return entry[lang] || entry['en'] || null;
  }

  function translateText(el) {
    if (el.childElementCount > 0) return;
    var orig = el.dataset.i18nOrig;
    if (orig === undefined) {
      orig = (el.textContent || '').trim();
      el.dataset.i18nOrig = orig;
    }
    if (!orig) return;
    if (getLang() === 'zh') { el.textContent = orig; return; }
    var tr = pick(orig);
    if (tr) el.textContent = tr;
  }

  function translateAttr(el, a) {
    var cur = el.getAttribute(a);
    if (cur === null) return;
    var store = 'i18nAttr' + a.charAt(0).toUpperCase() + a.slice(1);
    var orig = el.dataset[store];
    if (orig === undefined) { orig = cur; el.dataset[store] = orig; }
    if (getLang() === 'zh') { el.setAttribute(a, orig); return; }
    var tr = pick(orig);
    if (tr) el.setAttribute(a, tr);
  }

  function translateTitle() {
    if (origTitle === null) origTitle = document.title;
    var suffix = ' · Personal Life OS';
    if (getLang() === 'zh') { document.title = origTitle; return; }
    var pre = origTitle, tp = null;
    if (origTitle.indexOf(suffix) >= 0) {
      pre = origTitle.slice(0, origTitle.indexOf(suffix));
      tp = pick(pre);
      if (tp) { document.title = tp + suffix; return; }
    }
    tp = pick(origTitle);
    if (tp) document.title = tp;
  }

  function apply() {
    if (!D) return;
    translateTitle();
    var sel = '.lf-page-header__title,.lf-section-title,.lf-bottom-nav span,' +
      '.lf-quick-tile__label,.lf-quick-tile__sub,.lf-export-tile__label,.lf-export-tile__sub,' +
      '.lf-setting-row__label,.lf-section-header__hint,label.lf-label,option,dt,.lf-seg__btn,.lf-badge';
    document.querySelectorAll(sel).forEach(translateText);
    document.querySelectorAll('button,a.lf-btn,input[type=submit]').forEach(function (el) {
      if (el.childElementCount > 0) return;
      translateText(el);
    });
    document.querySelectorAll(sel + ',button,a.lf-btn,input[type=submit]').forEach(function (el) {
      ['title', 'aria-label', 'placeholder'].forEach(function (a) { translateAttr(el, a); });
    });
  }

  function load(cb) {
    fetch('/static/i18n/ui.json', { cache: 'no-cache' })
      .then(function (r) { return r.json(); })
      .then(function (d) { D = d; setHtmlLang(getLang()); apply(); if (cb) cb(); })
      .catch(function () {});
  }

  window.setLang = function (code) {
    localStorage.setItem(KEY, code);
    document.cookie = 'lifeos-lang=' + code + ';path=/;max-age=31536000';
    setHtmlLang(code);
    if (!D) load(); else apply();
  };
  window.t = function (k) {
    if (D && D[k]) { var l = getLang(); return D[k][l] || D[k]['en'] || k; }
    return k;
  };

  if (document.readyState !== 'loading') load();
  else document.addEventListener('DOMContentLoaded', function () { load(); });
})();

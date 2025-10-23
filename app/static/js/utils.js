(function (window, document) {
  const CSRF_META_SELECTOR = 'meta[name="csrf-token"]';

  function csrf() {
    const meta = document.querySelector(CSRF_META_SELECTOR);
    return meta ? meta.getAttribute('content') || '' : '';
  }

  window.Zissou = window.Zissou || {};
  window.Zissou.csrf = csrf;
  window.Zissou.getCsrfToken = csrf;
})(window, document);

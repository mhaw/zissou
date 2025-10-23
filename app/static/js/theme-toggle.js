(function (window, document) {
  const STORAGE_KEY = 'theme';
  const root = document.documentElement;
  const prefersDark = () => window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;

  function getStoredTheme() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      return null;
    }
  }

  function storeTheme(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (error) {
      /* ignore */
    }
  }

  function applyTheme(theme) {
    const effective = theme === 'dark' || (theme === null && prefersDark()) ? 'dark' : 'light';
    root.classList.toggle('dark', effective === 'dark');
    root.dataset.theme = effective;

    const toggle = document.getElementById('theme-toggle');
    if (!toggle) {
      return;
    }
    toggle.setAttribute('aria-pressed', effective === 'dark' ? 'true' : 'false');
    toggle.querySelectorAll('[data-theme-icon="moon"]').forEach((node) => {
      node.classList.toggle('hidden', effective === 'dark');
    });
    toggle.querySelectorAll('[data-theme-icon="sun"]').forEach((node) => {
      node.classList.toggle('hidden', effective !== 'dark');
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    applyTheme(getStoredTheme());

    const toggle = document.getElementById('theme-toggle');
    if (!toggle) {
      return;
    }

    toggle.addEventListener('click', () => {
      const current = root.classList.contains('dark') ? 'dark' : 'light';
      const next = current === 'dark' ? 'light' : 'dark';
      storeTheme(next);
      applyTheme(next);
    });
  });
})(window, document);

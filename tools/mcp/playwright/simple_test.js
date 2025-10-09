import { chromium } from 'playwright';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    extraHTTPHeaders: {
      'Authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE3ZjBmMGYxNGU5Y2FmYTlhYjUxODAxNTBhZTcxNGM5ZmQxYjVjMjYiLCJ0eXAiOiJKV1QifQ.eyJhdWQiOiIzMjU1NTk0MDU1OS5hcHBzLmdvb2dsZXVzZXJjb250ZW50LmNvbSIsImF6cCI6InBsYXl3cmlnaHQtdGVzdGVyQHppc3NvdS00NzE2MDMuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLCJlbWFpbCI6InBsYXl3cmlnaHQtdGVzdGVyQHppc3NvdS00NzE2MDMuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLCJlbWFpbF92ZXJpZmllZCI6dHJ1ZSwiZXhwIjoxNzU5ODE4NTg5LCJpYXQiOjE3NTk4MTQ5ODksImlzcyI6Imh0dHBzOi8vYWNjb3VudHMuZ29vZ2xlLmNvbSIsInN1YiI6IjExODM1Nzg4OTUwNDQwMjA5MjI0OCJ9.OxxYERra_nt8ctGDEwzsZGpFCkYh5Q1UDQUB5oF1Iq8AIcdDciSn2IdD2QNeXxO7HeKQY5cSnBVQt2GHSoxgQ6E-wUXuJzZIZYBsstAaTTfqEWnNeadlr7IY1HtOs67znA1mDKsMbwXvGShniAhFfgn0RyYuKyyCHlFudAyMF75sSWI8rXBWyA1uH4WxThTyaJ40_qxuZvnFM7TMfmJoWzOMf_XlyeaGDOXVIUYLnj_U0WIhvnohFlg_F7jFQ1zy5Lp-Rna21eed_hC4XCHCzHr6cG5DlwIR8Sw5tkzheVS--ua5Q6r8ZzcVyHd0kK33eemOFkJaFNrglqUn4EvW1w'
    }
  });
  const page = await context.newPage();
  await page.goto('https://zissou-498379484787.us-central1.run.app/');
  await page.screenshot({ path: 'screenshot.png' });
  await browser.close();
})();

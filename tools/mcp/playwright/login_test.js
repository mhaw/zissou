import { chromium } from 'playwright';

(async () => {
  const browser = await chromium.launch({ headless: false }); // Run in headed mode
  const context = await browser.newContext();
  const page = await context.newPage();

  const email = 'admin@zissou-audio.com';
  const password = 'zissou';

  await page.goto('http://localhost:8080/');

  await page.waitForSelector('#auth-form');

  await page.fill('#email', email);
  await page.click('#continue-btn');

  await page.waitForSelector('#password:not(.hidden)', { timeout: 5000 });

  await page.fill('#password', password);

  // Pause the script here
  console.log('\n>>> The script is now paused. <<<');
  console.log('A browser window should be open.');
  console.log('Please try to complete the login manually by clicking the "Sign In" button.');
  console.log('After you are done, you can close the browser window to end the test.\n');
  await page.pause();

  await browser.close();
})();
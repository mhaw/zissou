import { chromium } from 'playwright';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.goto('http://localhost:8080/');
    console.log('Successfully navigated to http://localhost:8080/');
    await page.screenshot({ path: 'local_test_screenshot.png' });
    console.log('Screenshot saved to local_test_screenshot.png');
  } catch (error) {
    console.error('Failed to connect to the local server. Make sure the dev server is running:', error);
  }
  await browser.close();
})();

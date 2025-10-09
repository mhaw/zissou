const { test, expect } = require("@playwright/test");

test.describe("Health check endpoint", () => {
  test("returns OK response", async ({ page, baseURL }) => {
    const targetUrl = `${baseURL || "http://localhost:8080"}/health`;
    const response = await page.goto(targetUrl);

    await expect(response, "should return a 200 response").not.toBeNull();
    const status = response?.status();
    expect(status).toBe(200);
    await expect(page.locator("body")).toContainText("OK");
  });
});

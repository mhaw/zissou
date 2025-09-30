#!/usr/bin/env node

import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";
import { Server } from "@modelcontextprotocol/sdk/server.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/stdio.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const artifactsDir = path.join(__dirname, "artifacts");

await mkdir(artifactsDir, { recursive: true });

const browser = await chromium.launch({ headless: true });

const server = new Server({
  name: "zissou-playwright",
  version: "0.1.0",
  description: "Playwright-backed MCP server for Zissou",
});

server.tool(
  "navigate",
  {
    description:
      "Navigate to a URL, optionally waiting for network idle and capturing a screenshot.",
    inputSchema: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description: "Destination URL to open.",
        },
        waitUntil: {
          type: "string",
          enum: ["load", "domcontentloaded", "networkidle"],
          description: "Playwright waitUntil option (default: load).",
        },
        viewport: {
          type: "object",
          description: "Optional viewport size { width, height }.",
          properties: {
            width: { type: "number" },
            height: { type: "number" },
          },
        },
        screenshot: {
          type: "boolean",
          description: "Whether to capture a full-page screenshot (default: false).",
        },
      },
      required: ["url"],
    },
  },
  async ({
    url,
    waitUntil = "load",
    viewport,
    screenshot = false,
  }) => {
    const context = await browser.newContext(
      viewport && viewport.width && viewport.height
        ? {
            viewport: {
              width: Math.floor(viewport.width),
              height: Math.floor(viewport.height),
            },
          }
        : undefined
    );
    const page = await context.newPage();
    try {
      await page.goto(url, { waitUntil });
      const currentUrl = page.url();
      const title = await page.title();
      const contents = [
        {
          type: "text",
          text: `Navigated to ${currentUrl}\nTitle: ${title}`,
        },
      ];

      if (screenshot) {
        const filename = `screenshot-${Date.now()}.png`;
        const filePath = path.join(artifactsDir, filename);
        await page.screenshot({ path: filePath, fullPage: true });
        contents.push({
          type: "resource",
          resource: {
            uri: `file://${filePath}`,
            mimeType: "image/png",
            description: "Full-page screenshot",
          },
        });
      }

      return { content: contents };
    } finally {
      await context.close();
    }
  }
);

server.tool(
  "get_text",
  {
    description: "Return the text content for a selector on the requested page.",
    inputSchema: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description: "Page url to load before querying the selector.",
        },
        selector: {
          type: "string",
          description: "CSS selector to evaluate.",
        },
        timeout: {
          type: "number",
          description:
            "Optional timeout in milliseconds to wait for the selector before failing.",
        },
      },
      required: ["url", "selector"],
    },
  },
  async ({ url, selector, timeout = 5000 }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    try {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      const locator = page.locator(selector);
      await locator.waitFor({ timeout });
      const text = (await locator.innerText()).trim();
      return {
        content: [
          {
            type: "text",
            text: text || "(no text content)",
          },
        ],
      };
    } catch (error) {
      return {
        isError: true,
        content: [
          {
            type: "text",
            text: error instanceof Error ? error.message : String(error),
          },
        ],
      };
    } finally {
      await context.close();
    }
  }
);

const transport = new StdioServerTransport();
await server.start(transport);

const shutdown = async () => {
  try {
    await browser.close();
  } finally {
    process.exit(0);
  }
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

#!/usr/bin/env node

import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const artifactsDir = path.join(__dirname, "artifacts");

await mkdir(artifactsDir, { recursive: true });

const browser = await chromium.launch({ headless: true });

const server = new McpServer({
  name: "zissou-playwright",
  version: "0.1.0",
  description: "Playwright-backed MCP server for Zissou",
});

server.registerTool(
  "navigate",
  {
    description:
      "Navigate to a URL, optionally waiting for network idle and capturing a screenshot.",
    inputSchema: z.object({
      url: z.string().describe("Destination URL to open."),
      waitUntil: z.enum(["load", "domcontentloaded", "networkidle"]).optional().describe("Playwright waitUntil option (default: load)."),
      viewport: z.object({
        width: z.number(),
        height: z.number(),
      }).optional().describe("Optional viewport size { width, height }."),
      screenshot: z.boolean().optional().describe("Whether to capture a full-page screenshot (default: false)."),
    }),
  },
  async ({
    url,
    waitUntil = "load",
    viewport,
    screenshot = false,
  }) => {
    const context = await browser.newContext({
      extraHTTPHeaders: {
        'Authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE3ZjBmMGYxNGU5Y2FmYTlhYjUxODAxNTBhZTcxNGM5ZmQxYjVjMjYiLCJ0eXAiOiJKV1QifQ.eyJhdWQiOiIzMjU1NTk0MDU1OS5hcHBzLmdvb2dsZXVzZXJjb250ZW50LmNvbSIsImF6cCI6InBsYXl3cmlnaHQtdGVzdGVyQHppc3NvdS00NzE2MDMuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLCJlbWFpbCI6InBsYXl3cmlnaHQtdGVzdGVyQHppc3NvdS00NzE2MDMuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLCJlbWFpbF92ZXJpZmllZCI6dHJ1ZSwiZXhwIjoxNzU5ODE4NTg5LCJpYXQiOjE3NTk4MTQ5ODksImlzcyI6Imh0dHBzOi8vYWNjb3VudHMuZ29vZ2xlLmNvbSIsInN1YiI6IjExODM1Nzg4OTUwNDQwMjA5MjI0OCJ9.OxxYERra_nt8ctGDEwzsZGpFCkYh5Q1UDQUB5oF1Iq8AIcdDciSn2IdD2QNeXxO7HeKQY5cSnBVQt2GHSoxgQ6E-wUXuJzZIZYBsstAaTTfqEWnNeadlr7IY1HtOs67znA1mDKsMbwXvGShniAhFfgn0RyYuKyyCHlFudAyMF75sSWI8rXBWyA1uH4WxThTyaJ40_qxuZvnFM7TMfmJoWzOMf_XlyeaGDOXVIUYLnj_U0WIhvnohFlg_F7jFQ1zy5Lp-Rna21eed_hC4XCHCzHr6cG5DlwIR8Sw5tkzheVS--ua5Q6r8ZzcVyHd0kK33eemOFkJaFNrglqUn4EvW1w'
      },
      ...(viewport && viewport.width && viewport.height
        ? {
            viewport: {
              width: Math.floor(viewport.width),
              height: Math.floor(viewport.height),
            },
          }
        : {})
    });
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

server.registerTool(
  "get_text",
  {
    description: "Return the text content for a selector on the requested page.",
    inputSchema: z.object({
      url: z.string().describe("Page url to load before querying the selector."),
      selector: z.string().describe("CSS selector to evaluate."),
      timeout: z.number().optional().describe("Optional timeout in milliseconds to wait for the selector before failing."),
    }),
  },
  async ({ url, selector, timeout = 5000 }) => {
    const context = await browser.newContext({
      extraHTTPHeaders: {
        'Authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE3ZjBmMGYxNGU5Y2FmYTlhYjUxODAxNTBhZTcxNGM5ZmQxYjVjMjYiLCJ0eXAiOiJKV1QifQ.eyJhdWQiOiIzMjU1NTk0MDU1OS5hcHBzLmdvb2dsZXVzZXJjb250ZW50LmNvbSIsImF6cCI6InBsYXl3cmlnaHQtdGVzdGVyQHppc3NvdS00NzE2MDMuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLCJlbWFpbCI6InBsYXl3cmlnaHQtdGVzdGVyQHppc3NvdS00NzE2MDMuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLCJlbWFpbF92ZXJpZmllZCI6dHJ1ZSwiZXhwIjoxNzU5ODE4NTg5LCJpYXQiOjE3NTk4MTQ5ODksImlzcyI6Imh0dHBzOi8vYWNjb3VudHMuZ29vZ2xlLmNvbSIsInN1YiI6IjExODM1Nzg4OTUwNDQwMjA5MjI0OCJ9.OxxYERra_nt8ctGDEwzsZGpFCkYh5Q1UDQUB5oF1Iq8AIcdDciSn2IdD2QNeXxO7HeKQY5cSnBVQt2GHSoxgQ6E-wUXuJzZIZYBsstAaTTfqEWnNeadlr7IY1HtOs67znA1mDKsMbwXvGShniAhFfgn0RyYuKyyCHlFudAyMF75sSWI8rXBWyA1uH4WxThTyaJ40_qxuZvnFM7TMfmJoWzOMf_XlyeaGDOXVIUYLnj_U0WIhvnohFlg_F7jFQ1zy5Lp-Rna21eed_hC4XCHCzHr6cG5DlwIR8Sw5tkzheVS--ua5Q6r8ZzcVyHd0kK33eemOFkJaFNrglqUn4EvW1w'
      }
    });
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
server.connect(transport)
  .then(() => {
    console.log('MCP Playwright Server connected and ready via stdio.');
  })
  .catch(error => {
    console.error('Failed to connect MCP Playwright Server:', error);
    process.exit(1);
  });

const shutdown = async () => {
  try {
    await server.disconnect();
    await browser.close();
  } finally {
    process.exit(0);
  }
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

# Playwright MCP Server

This repository ships a small Model Context Protocol (MCP) server that exposes
Playwright-driven browser automation so agents can exercise the Zissou web UI.
The server lives in `tools/mcp/playwright` and can be run locally over the
standard MCP stdio transport.

## Prerequisites

- Node.js 18+ (tested with Node 22)
- Network access to install npm dependencies and download Playwright browsers
- Optional: Chrome/Chromium libraries if you intend to run headed browsers

## Installation

```bash
cd tools/mcp/playwright
npm install
npm run install:browsers             # installs the Chromium bundle Playwright expects
```

> **Note:** The automated setup in this workspace cannot reach the public npm
> registry, so dependency installation must be performed manually on a connected
> machine.

## Running the server

The server speaks MCP over stdio, which is the default transport for most MCP
clients (including the OpenAI desktop client).

```bash
cd tools/mcp/playwright
npm start
```

When the process is running it will keep a single headless Chromium instance
alive and respond to MCP tool invocations.

### Tools exposed

| Tool        | Description                                                                                         |
|-------------|-----------------------------------------------------------------------------------------------------|
| `navigate`  | Opens a URL, optionally captures a screenshot, and reports the final URL plus document title.       |
| `get_text`  | Loads a page and returns the text contents for a CSS selector (handy for smoke-tests & assertions). |

Each tool validates input using JSON Schema; see `server.js` for the accepted
parameters. Screenshots are written to `tools/mcp/playwright/artifacts/` and
returned via MCP as `resource` payloads.

## Configuration

The server currently launches Chromium in headless mode. If you need a headed
browser, adjust the call to `chromium.launch` in `server.js`.

Playwright contexts are short-lived per tool invocation; if you need to share
state between calls, consider extending the server with a long-lived context or
additional tools.

## Integration tips

- **OpenAI Desktop / MCP clients:** add an entry pointing at
  `tools/mcp/playwright/server.js` using the stdio transport.
- **Automation pipelines:** the `npm start` script can be wrapped inside your
  test harness (e.g. `foreman` / `honcho`) alongside the Flask dev server.
- **Artifacts:** clean up `artifacts/` periodically if you enable screenshots on
  many calls; the directory is already excluded from version control via the
  local `.gitignore`.

Feel free to extend the server with additional Playwright helpers (form fills,
keyboard events, etc.) as your agent testing evolves.

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const client = new Client({
  name: "test-client",
  version: "1.0.0",
});

const transport = new StdioClientTransport({
  command: 'node',
  args: ['/home/mhaw/projects/zissou/tools/mcp/playwright/server.js'],
});

async function runClient() {
  try {
    await client.connect(transport);
    console.log("MCP Client connected.");

    const result = await client.callTool('navigate', { url: 'https://zissou-498379484787.us-central1.run.app/', screenshot: true });
    console.log("Result of navigate:", result);

  } catch (error) {
    console.error("MCP Client error:", error);
  } finally {
    client.close();
    console.log("MCP Client disconnected.");
  }
}

runClient();

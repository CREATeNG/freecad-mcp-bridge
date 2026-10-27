#!/usr/bin/env node
/*
 * FreeCAD MCP Bridge - Claude Desktop shim.
 *
 * A zero-dependency stdio <-> HTTP proxy. Claude Desktop speaks MCP over stdio
 * (newline-delimited JSON-RPC); this forwards each message to the addon's HTTP
 * endpoint (http://127.0.0.1:<port>/mcp), unwraps the JSON or SSE reply, and
 * writes it back on stdout.
 *
 * When FreeCAD isn't reachable, it answers initialize and tools/list itself
 * (tools.json is a copy of the addon's tool list), and answers a tools/call
 * with a tool-level error saying how to start the bridge, so the client
 * starts cleanly instead of reporting a failed server. Node stdlib only
 * (global fetch, >=18).
 */

const fs = require("fs");
const path = require("path");

const PORT = process.env.FREECAD_MCP_PORT || "39280";
const ENDPOINT = `http://127.0.0.1:${PORT}/mcp`;
const LEGACY_PROTOCOL_VERSION = "2025-11-25";
const MODERN_PROTOCOL_VERSION = "2026-07-28";
const META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion";
const TOOLS = JSON.parse(fs.readFileSync(path.join(__dirname, "tools.json"), "utf8"));
const VERSION = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8")).version;
const UNREACHABLE_HINT =
  "FreeCAD isn't reachable. Start FreeCAD and turn the bridge on with the MCP Bridge toolbar button, then try again.";

function writeLine(text) {
  process.stdout.write(text + "\n");
}

function offlineResult(message, err) {
  // What a client gets while FreeCAD is down, in place of a JSON-RPC error.
  switch (message.method) {
    case "initialize":
      return {
        protocolVersion: LEGACY_PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: { name: "freecad-mcp-bridge", version: VERSION },
      };
    case "tools/list":
      return { tools: TOOLS };
    case "tools/call":
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({
              page: [],
              has_more: false,
              error: `${UNREACHABLE_HINT} (${ENDPOINT}: ${err.message})`,
            }),
          },
        ],
        isError: true,
      };
    default:
      return null;
  }
}

function bridgeHeaders(message) {
  // Stdio has no headers. The version the body already declares is the
  // one the bridge checks; a body with none is the legacy handshake.
  const headers = { "Content-Type": "application/json" };
  const meta = message.params && message.params._meta;
  const declared = meta && meta[META_PROTOCOL_VERSION];
  const version = typeof declared === "string" ? declared : LEGACY_PROTOCOL_VERSION;
  headers["MCP-Protocol-Version"] = version;
  if (version === MODERN_PROTOCOL_VERSION) {
    if (typeof message.method === "string") headers["Mcp-Method"] = message.method;
    const name = message.params && message.params.name;
    if (message.method === "tools/call" && typeof name === "string") {
      headers["Mcp-Name"] = name;
    }
  }
  return headers;
}

async function forward(rawLine, message) {
  // JSON-RPC notifications omit "id" entirely and get no reply.
  const isNotification = message.id === undefined;

  let res;
  try {
    res = await fetch(ENDPOINT, {
      method: "POST",
      headers: bridgeHeaders(message),
      body: rawLine,
      redirect: "error",
    });
  } catch (err) {
    if (isNotification) return;
    const result = offlineResult(message, err);
    if (result) {
      writeLine(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }));
    } else {
      writeLine(
        JSON.stringify({
          jsonrpc: "2.0",
          id: message.id,
          error: {
            code: -32001,
            message:
              `FreeCAD MCP Bridge unreachable at ${ENDPOINT} (${err.message}). ` +
              "Is the bridge running? Toggle it on from the MCP Bridge toolbar in FreeCAD.",
          },
        })
      );
    }
    return;
  }

  const bodyText = await res.text();
  if (isNotification) return;

  const contentType = (res.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("text/event-stream")) {
    // SSE stream: pull each event's JSON-RPC envelope out of its data field(s)
    // and write one line per event.
    for (const event of bodyText.replace(/\r\n?/g, "\n").split("\n\n")) {
      const dataLines = event
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice("data:".length).trim());
      const joined = dataLines.join("\n").trim();
      if (!joined) continue;
      try {
        JSON.parse(joined);
        writeLine(joined.replace(/\n/g, " ").trim());
      } catch {
        // Lenient fallback: not SSE-standard parsing.
        writeLine(dataLines.join(""));
      }
    }
    return;
  }
  const payload = bodyText.trim();
  if (payload) writeLine(payload);
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let newline;
  while ((newline = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, newline).trim();
    buffer = buffer.slice(newline + 1);
    if (!line) continue;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      continue; // ignore non-JSON lines
    }
    forward(line, message);
  }
});
process.stdin.on("end", () => process.exit(0));

process.stderr.write(`[freecad-mcp-bridge shim] proxying stdio -> ${ENDPOINT}\n`);

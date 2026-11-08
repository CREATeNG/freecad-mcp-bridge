#!/usr/bin/env node
/*
 * FreeCAD MCP Bridge - Claude Desktop shim.
 *
 * A zero-dependency stdio <-> HTTP/HTTPS proxy. Claude Desktop speaks
 * MCP over stdio (newline-delimited JSON-RPC); this forwards each
 * tools/call to the addon's endpoint
 * (http://127.0.0.1:<port>/mcp, or https://127.0.0.1:<port>/mcp when
 * FREECAD_MCP_TLS_CERT_FILE is set, with Authorization: Bearer <token>
 * when FREECAD_MCP_TOKEN is set), unwraps the JSON reply, and writes
 * it back on stdout.
 *
 * The shim answers initialize and tools/list itself (tools.json is a
 * copy of the addon's tool list) and does not contact the bridge for
 * them, so the client starts cleanly whether or not FreeCAD is up.
 * server/discover gets a method-not-found error naming the method,
 * which tells a client that also speaks 2025-11-25 to fall back to
 * initialize. Only a tools/call is sent on. When that call finds
 * the bridge unreachable, serving HTTPS with no certificate
 * set, returning 401, or failing TLS, the tool result's error says
 * which. A ping is answered locally with an empty result. A
 * notification is not sent and gets no reply. Any other method gets
 * a method-not-found error naming the method. Node stdlib only
 * (global fetch and node:https, >=18).
 */

const fs = require("fs");
const https = require("https");
const path = require("path");
const tls = require("tls");

function envSetting(name) {
  const val = (process.env[name] || "").trim();
  if (!val || /^\$\{user_config\.[^}]+\}$/.test(val)) return "";
  return val;
}

const PORT = envSetting("FREECAD_MCP_PORT") || "39280";
const TOKEN = envSetting("FREECAD_MCP_TOKEN");
const TLS_CERT_FILE = envSetting("FREECAD_MCP_TLS_CERT_FILE");
const SCHEME = TLS_CERT_FILE ? "https" : "http";
const ENDPOINT = `${SCHEME}://127.0.0.1:${PORT}/mcp`;
const HTTPS_ENDPOINT = `https://127.0.0.1:${PORT}/mcp`;
const PLAIN_HTTP_TO_HTTPS_MESSAGE =
  `FreeCAD MCP Bridge serves HTTPS at ${HTTPS_ENDPOINT}, and this connection was plain HTTP. ` +
  "Set TLS certificate file (PEM) in the FreeCAD MCP Bridge extension settings to FreeCAD's certificate, " +
  "or untick Apply to loopback in FreeCAD's Edit → Preferences → MCP Bridge.";
const LEGACY_PROTOCOL_VERSION = "2025-11-25";
const MODERN_PROTOCOL_VERSION = "2026-07-28";
const META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion";
const TOOLS = JSON.parse(fs.readFileSync(path.join(__dirname, "tools.json"), "utf8"));
const VERSION = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8")).version;
const UNREACHABLE_HINT =
  "FreeCAD isn't reachable. Start FreeCAD and turn the bridge on with the MCP Bridge toolbar button, then try again.";
const UNAUTHORIZED_MESSAGE =
  `FreeCAD MCP Bridge requires a bearer token and did not accept this request's (401 Unauthorized at ${ENDPOINT}). ` +
  "Set Bearer token in the FreeCAD MCP Bridge extension settings in Claude Desktop to the token FreeCAD showed when it was generated, " +
  "or click Generate new token in FreeCAD's Edit → Preferences → MCP Bridge for a new one.";

function writeLine(text) {
  process.stdout.write(text + "\n");
}

function fetchErrorMessage(err) {
  return err.cause && err.cause.message ? `${err.message}: ${err.cause.message}` : err.message;
}

function errorCode(err) {
  return (err && err.cause && err.cause.code) || (err && err.code) || "";
}

// A plain HTTP POST to an HTTPS port fails without saying which it was.
// A TLS handshake that finishes, with certificate checks left off, means
// the port speaks HTTPS. A refused connection means nothing is listening.
function portServesHttps(port) {
  return new Promise((resolve) => {
    let settled = false;
    let socket = null;
    const timer = setTimeout(() => done(false), 2000);
    function done(servesHttps) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (socket) socket.destroy();
      resolve(servesHttps);
    }
    try {
      socket = tls.connect(
        {
          host: "127.0.0.1",
          port: Number(port),
          rejectUnauthorized: false,
        },
        () => done(true)
      );
      socket.on("error", () => done(false));
    } catch {
      done(false);
    }
  });
}

function tlsErrorMessage(err) {
  const detail = fetchErrorMessage(err);
  return (
    `FreeCAD MCP Bridge TLS connection failed at ${ENDPOINT} (${detail}). ` +
    "Check that TLS certificate file (PEM) in the extension settings is FreeCAD's certificate and covers 127.0.0.1. " +
    "Check that the Apply to loopback and Require TLS (HTTPS) settings are on in FreeCAD's Edit → Preferences → MCP Bridge. " +
    "While Apply to loopback is off, leave the certificate setting blank."
  );
}

function toolErrorResult(errorText) {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify({
          page: [],
          has_more: false,
          error: errorText,
        }),
      },
    ],
    isError: true,
  };
}

function sendErrorReply(message, errorText) {
  if (message.method === "tools/call") {
    writeLine(
      JSON.stringify({
        jsonrpc: "2.0",
        id: message.id,
        result: toolErrorResult(errorText),
      })
    );
  } else {
    writeLine(
      JSON.stringify({
        jsonrpc: "2.0",
        id: message.id,
        error: {
          code: -32001,
          message: errorText,
        },
      })
    );
  }
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
      return toolErrorResult(`${UNREACHABLE_HINT} (${ENDPOINT}: ${fetchErrorMessage(err)})`);
    default:
      return null;
  }
}

function bridgeHeaders(message) {
  // Stdio has no headers. The version the body already declares is the
  // one the bridge checks; a body with none is the legacy handshake.
  const headers = { "Content-Type": "application/json" };
  if (TOKEN) {
    headers["Authorization"] = `Bearer ${TOKEN}`;
  }
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

function postHttps(headers, body) {
  return new Promise((resolve, reject) => {
    let ca;
    try {
      ca = fs.readFileSync(TLS_CERT_FILE);
    } catch (err) {
      reject(err);
      return;
    }
    const payload = Buffer.from(body, "utf8");
    const req = https.request(
      ENDPOINT,
      {
        method: "POST",
        headers: { ...headers, "Content-Length": String(payload.length) },
        ca,
      },
      (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400) {
          res.resume();
          const err = new Error("redirect mode is set to 'error': unexpected redirect");
          err.code = "ERR_UNEXPECTED_REDIRECT";
          reject(err);
          return;
        }
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          resolve({
            status: res.statusCode || 0,
            text: async () => text,
          });
        });
      }
    );
    req.on("error", reject);
    req.end(payload);
  });
}

async function postBridge(message, rawLine) {
  const headers = bridgeHeaders(message);
  if (TLS_CERT_FILE) {
    return postHttps(headers, rawLine);
  }
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers,
    body: rawLine,
    redirect: "error",
  });
  return {
    status: res.status,
    text: () => res.text(),
  };
}

function writeResult(id, result) {
  writeLine(JSON.stringify({ jsonrpc: "2.0", id, result }));
}

async function forward(rawLine, message) {
  // A notification has no id and no reply. initialize, tools/list
  // and ping are answered here. Only a tool call contacts the bridge.
  const isNotification = message.id === undefined;
  if (isNotification) return;
  if (message.method === "initialize" || message.method === "tools/list") {
    writeResult(message.id, offlineResult(message, null));
    return;
  }
  if (message.method === "ping") {
    writeResult(message.id, {});
    return;
  }
  // server/discover falls through. A DiscoverResult would make a
  // newer client stay modern; method-not-found sends it to initialize.
  if (message.method !== "tools/call") {
    writeLine(
      JSON.stringify({
        jsonrpc: "2.0",
        id: message.id,
        error: {
          code: -32601,
          message: `Method not found: ${message.method}`,
        },
      })
    );
    return;
  }

  let res;
  try {
    res = await postBridge(message, rawLine);
  } catch (err) {
    const code = errorCode(err);
    const redirect = /redirect/i.test(fetchErrorMessage(err));
    if (
      !TLS_CERT_FILE &&
      code !== "ECONNREFUSED" &&
      !redirect &&
      (await portServesHttps(PORT))
    ) {
      sendErrorReply(message, PLAIN_HTTP_TO_HTTPS_MESSAGE);
      return;
    }
    if (TLS_CERT_FILE && code !== "ECONNREFUSED" && !redirect) {
      sendErrorReply(message, tlsErrorMessage(err));
      return;
    }
    writeResult(message.id, offlineResult(message, err));
    return;
  }

  const bodyText = await res.text();
  if (res.status === 401) {
    sendErrorReply(message, UNAUTHORIZED_MESSAGE);
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

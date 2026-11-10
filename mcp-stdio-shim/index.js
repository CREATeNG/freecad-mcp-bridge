#!/usr/bin/env node
/*
 * FreeCAD MCP Bridge - Claude Desktop shim.
 *
 * A zero-dependency stdio <-> HTTP/HTTPS proxy. Claude Desktop speaks
 * MCP over stdio (newline-delimited JSON-RPC); this forwards each
 * tools/call to the addon's endpoint on FREECAD_MCP_HOST (127.0.0.1 by
 * default; "localhost" in any letter case is taken as 127.0.0.1, and
 * 127.x.x.x and ::1 are treated as this machine). The bearer token
 * comes only from FREECAD_MCP_TOKEN, on this machine and for another
 * host. The shim reads no token file and writes nothing. When
 * serving-cert.pem is in FREECAD_MCP_TOKEN_DIR, or
 * <home>/.freecad-mcp-bridge otherwise, it connects over HTTPS and
 * trusts only that file. When the file is absent it connects over
 * HTTP. FREECAD_MCP_TLS_CERT_FILE is ignored on this machine. For
 * another host, the shim connects to https://<host>:<port>/mcp,
 * requires FREECAD_MCP_TLS_CERT_FILE, trusts only that certificate,
 * and uses the same pasted token. The JSON reply is unwrapped and
 * written back on stdout.
 *
 * The shim answers initialize and tools/list itself (tools.json is a
 * copy of the addon's tool list) and does not contact the bridge for
 * them, so the client starts cleanly whether or not FreeCAD is up.
 * server/discover gets a method-not-found error naming the method,
 * which tells a client that also speaks 2025-11-25 to fall back to
 * initialize. Only a tools/call is sent on. When the extension lacks
 * a certificate for another host, or a token, or when the call finds the
 * bridge unreachable, serving HTTPS on this machine with no
 * serving-cert.pem, returning 401, or failing TLS, the tool result's
 * error says which.
 * A ping is answered
 * locally with an empty result. A notification is not sent and gets no
 * reply. Any other method gets a method-not-found error naming the
 * method. Node stdlib only (global fetch and node:https, >=18).
 */

const fs = require("fs");
const https = require("https");
const net = require("net");
const os = require("os");
const path = require("path");
const tls = require("tls");

function envSetting(name) {
  const val = (process.env[name] || "").trim();
  if (!val || /^\$\{user_config\.[^}]+\}$/.test(val)) return "";
  return val;
}

const loopbackAddresses = new net.BlockList();
loopbackAddresses.addSubnet("127.0.0.0", 8, "ipv4");
loopbackAddresses.addAddress("::1", "ipv6");

function isLoopbackAddress(host) {
  const family = net.isIP(host);
  if (family === 4) return loopbackAddresses.check(host, "ipv4");
  if (family === 6) return loopbackAddresses.check(host, "ipv6");
  return false;
}

const RAW_HOST = envSetting("FREECAD_MCP_HOST") || "127.0.0.1";
const UNBRACKETED_HOST =
  RAW_HOST.startsWith("[") && RAW_HOST.endsWith("]")
    ? RAW_HOST.slice(1, -1)
    : RAW_HOST;
const BARE_HOST =
  UNBRACKETED_HOST.toLowerCase() === "localhost"
    ? "127.0.0.1"
    : UNBRACKETED_HOST;
const URL_HOST = BARE_HOST.includes(":") ? `[${BARE_HOST}]` : BARE_HOST;
const IS_DEFAULT_HOST = isLoopbackAddress(BARE_HOST);
const PORT = envSetting("FREECAD_MCP_PORT") || "39280";
const PASTED_TOKEN = envSetting("FREECAD_MCP_TOKEN");
const TOKEN_DIR_NAME = ".freecad-mcp-bridge";
const SERVING_CERT_NAME = "serving-cert.pem";
const NO_TOKEN_MESSAGE =
  "Paste the token FreeCAD showed into Bearer token, or make a new one with Generate new token in FreeCAD's MCP Bridge preferences.";
const SHORT_TOKEN_MESSAGE =
  "The token in Bearer token is shorter than 32 characters. Paste the token FreeCAD showed, or make a new one with Generate new token in FreeCAD's MCP Bridge preferences.";
const REFUSED_TOKEN_MESSAGE =
  "FreeCAD refused the pasted token. Check that it matches FreeCAD's token.";
const MIN_TOKEN_LENGTH = 32;

function pastedTokenProblem() {
  if (!PASTED_TOKEN) return NO_TOKEN_MESSAGE;
  if (PASTED_TOKEN.length < MIN_TOKEN_LENGTH) return SHORT_TOKEN_MESSAGE;
  return "";
}

function tokenDir() {
  const override = envSetting("FREECAD_MCP_TOKEN_DIR");
  if (override) return override;
  return path.join(os.homedir(), TOKEN_DIR_NAME);
}

function servingCertPath() {
  return path.join(tokenDir(), SERVING_CERT_NAME);
}

// On this machine HTTPS follows the bridge's copy, looked up per
// call so a later start is picked up. The extension's certificate
// setting is for another host only.
function localServingCert() {
  if (!IS_DEFAULT_HOST) return "";
  const file = servingCertPath();
  try {
    if (fs.existsSync(file)) return file;
  } catch {
    return "";
  }
  return "";
}

function bridgeEndpoint() {
  const https = !IS_DEFAULT_HOST || localServingCert() !== "";
  const scheme = https ? "https" : "http";
  return `${scheme}://${URL_HOST}:${PORT}/mcp`;
}


const TLS_CERT_FILE = envSetting("FREECAD_MCP_TLS_CERT_FILE");
const HTTPS_ENDPOINT = `https://${URL_HOST}:${PORT}/mcp`;
const PLAIN_HTTP_TO_HTTPS_MESSAGE =
  `FreeCAD MCP Bridge serves HTTPS at ${HTTPS_ENDPOINT}, and this connection was plain HTTP. ` +
  "On this machine the extension trusts serving-cert.pem in the bridge's folder, which is not there. Turn the bridge on in FreeCAD with a certificate set, so it writes that file.";
const MISSING_REMOTE_CERT_MESSAGE =
  `FreeCAD host is set to ${BARE_HOST}, which requires HTTPS. ` +
  "Set TLS certificate file (PEM) in the FreeCAD MCP Bridge extension settings to a copy of FreeCAD's certificate file, not the private key.";
const LEGACY_PROTOCOL_VERSION = "2025-11-25";
const MODERN_PROTOCOL_VERSION = "2026-07-28";
const META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion";
const TOOLS = JSON.parse(fs.readFileSync(path.join(__dirname, "tools.json"), "utf8"));
const VERSION = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8")).version;
const UNREACHABLE_HINT =
  "FreeCAD isn't reachable. Start FreeCAD and turn the bridge on with the MCP Bridge toolbar button, then try again.";
function unauthorizedMessage() {
  return REFUSED_TOKEN_MESSAGE;
}

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
          host: BARE_HOST,
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
  const where = `FreeCAD MCP Bridge TLS connection failed at ${bridgeEndpoint()} (${detail}). `;
  if (IS_DEFAULT_HOST) {
    return (
      where +
      `Check that serving-cert.pem in the bridge's folder is the certificate the bridge is serving, and covers ${BARE_HOST}.`
    );
  }
  return (
    where +
    `Check that TLS certificate file (PEM) in the FreeCAD MCP Bridge extension settings is a copy of FreeCAD's certificate file, not the private key, and covers ${BARE_HOST}.`
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
      return toolErrorResult(`${UNREACHABLE_HINT} (${bridgeEndpoint()}: ${fetchErrorMessage(err)})`);
    default:
      return null;
  }
}

let activeToken = "";

function bridgeHeaders(message) {
  // Stdio has no headers. The version the body already declares is the
  // one the bridge checks; a body with none is the legacy handshake.
  const headers = { "Content-Type": "application/json" };
  const token = activeToken;
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
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

function postHttps(headers, body, certFile) {
  return new Promise((resolve, reject) => {
    let ca;
    try {
      ca = fs.readFileSync(certFile);
    } catch (err) {
      reject(err);
      return;
    }
    const payload = Buffer.from(body, "utf8");
    const req = https.request(
      bridgeEndpoint(),
      {
        method: "POST",
        headers: { ...headers, "Content-Length": String(payload.length) },
        ca,
        rejectUnauthorized: true,
        agent: false,
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

async function postBridge(message, rawLine, certFile) {
  const headers = bridgeHeaders(message);
  if (certFile) {
    return postHttps(headers, rawLine, certFile);
  }
  const res = await fetch(bridgeEndpoint(), {
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

function writeBridgeLine(payload) {
  if (!payload) return;
  writeLine(payload);
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
  const tokenProblem = pastedTokenProblem();
  if (tokenProblem) {
    sendErrorReply(message, tokenProblem);
    return;
  }
  if (!IS_DEFAULT_HOST && !TLS_CERT_FILE) {
    sendErrorReply(message, MISSING_REMOTE_CERT_MESSAGE);
    return;
  }
  activeToken = PASTED_TOKEN;

  const certFile = IS_DEFAULT_HOST ? localServingCert() : TLS_CERT_FILE;
  let res;
  try {
    res = await postBridge(message, rawLine, certFile);
  } catch (err) {
    const code = errorCode(err);
    const redirect = /redirect/i.test(fetchErrorMessage(err));
    const refused = code === "ECONNREFUSED";
    if (
      !certFile &&
      IS_DEFAULT_HOST &&
      !refused &&
      !redirect &&
      (await portServesHttps(PORT))
    ) {
      sendErrorReply(message, PLAIN_HTTP_TO_HTTPS_MESSAGE);
      return;
    }
    if (certFile && !refused && !redirect) {
      if (
        IS_DEFAULT_HOST &&
        (code === "ENOENT" || code === "EACCES" || code === "EPERM")
      ) {
        sendErrorReply(
          message,
          `Could not read serving-cert.pem at ${certFile} (${fetchErrorMessage(err)}). ` +
            "Check that the file is readable by you, then try again."
        );
        return;
      }
      sendErrorReply(message, tlsErrorMessage(err));
      return;
    }
    writeResult(message.id, offlineResult(message, err));
    return;
  }

  const bodyText = await res.text();
  if (res.status === 401) {
    sendErrorReply(message, unauthorizedMessage());
    return;
  }
  writeBridgeLine(bodyText.trim());
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

process.stderr.write(`[freecad-mcp-bridge shim] proxying stdio -> ${bridgeEndpoint()}\n`);

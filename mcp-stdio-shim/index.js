#!/usr/bin/env node
/*
 * FreeCAD MCP Bridge - Claude Desktop shim.
 *
 * A zero-dependency stdio <-> HTTP/HTTPS proxy. Claude Desktop speaks
 * MCP over stdio (newline-delimited JSON-RPC); this forwards each
 * tools/call to the addon's endpoint on FREECAD_MCP_HOST (127.0.0.1 by
 * default; "localhost" in any letter case is taken as 127.0.0.1, and
 * 127.x.x.x and ::1 are treated as this machine). On this machine it
 * sends Authorization: Bearer from the token file in
 * FREECAD_MCP_TOKEN_DIR, or <home>/.freecad-mcp-bridge otherwise.
 * When serving-cert.pem is in that folder it connects over HTTPS and
 * trusts only that file. When the file is absent it connects over
 * HTTP. FREECAD_MCP_TLS_CERT_FILE is ignored on this machine. A pasted
 * token of at least 32 characters is written to the token file only
 * on this machine, only when its hash differs from the hash stored
 * beside the token, and only when the folder already exists. A
 * problem with that paste is a note on the tool result; the call is
 * still sent. For another host, the shim connects to
 * https://<host>:<port>/mcp, requires both
 * FREECAD_MCP_TLS_CERT_FILE and FREECAD_MCP_TOKEN, trusts only that
 * certificate, and uses the pasted token without writing to the local
 * token file. The JSON reply is unwrapped and written back on stdout.
 *
 * The shim answers initialize and tools/list itself (tools.json is a
 * copy of the addon's tool list) and does not contact the bridge for
 * them, so the client starts cleanly whether or not FreeCAD is up.
 * server/discover gets a method-not-found error naming the method,
 * which tells a client that also speaks 2025-11-25 to fall back to
 * initialize. Only a tools/call is sent on. When the extension lacks
 * a certificate or token for another host, or when the call finds the
 * bridge unreachable, serving HTTPS on this machine with no
 * serving-cert.pem, returning 401, or failing TLS, the tool result's
 * error says which. A ping is answered locally with an empty result.
 * A notification is not sent and gets no reply. Any other method gets
 * a method-not-found error naming the method. Node stdlib only
 * (global fetch and node:https, >=18).
 */

const crypto = require("crypto");
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
const MIN_TOKEN_LENGTH = 32;
const TOKEN_DIR_NAME = ".freecad-mcp-bridge";
const TOKEN_FILE_NAME = "token";
const SERVING_CERT_NAME = "serving-cert.pem";
const PASTE_RECORD_NAME = "pasted";
const SHORT_TOKEN_MESSAGE =
  "The bearer token pasted into the FreeCAD MCP Bridge extension was ignored because it's shorter than 32 characters.";
const MISSING_FOLDER_MESSAGE =
  "The token folder doesn't exist yet. Turn the bridge on in FreeCAD once, so it can create it.";
const WRITE_FAILED_MESSAGE =
  "The bearer token pasted into the FreeCAD MCP Bridge extension could not be written to the token file. Check that the folder is writable, then try again.";

function tokenDir() {
  const override = envSetting("FREECAD_MCP_TOKEN_DIR");
  if (override) return override;
  return path.join(os.homedir(), TOKEN_DIR_NAME);
}

function tokenFilePath() {
  return path.join(tokenDir(), TOKEN_FILE_NAME);
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

function pasteRecordPath() {
  return path.join(tokenDir(), PASTE_RECORD_NAME);
}

function pasteHash(token) {
  return crypto.createHash("sha256").update(token, "utf8").digest("hex");
}

function readTokenFile() {
  try {
    return fs.readFileSync(tokenFilePath(), "utf8").trim();
  } catch {
    return "";
  }
}

function readPasteRecord() {
  try {
    return fs.readFileSync(pasteRecordPath(), "utf8").trim();
  } catch {
    return "";
  }
}

const retrySleep = new Int32Array(new SharedArrayBuffer(4));

function sleepMs(ms) {
  Atomics.wait(retrySleep, 0, 0, ms);
}

function replaceFile(source, dest) {
  const attempts = 5;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      fs.renameSync(source, dest);
      return;
    } catch (err) {
      const sharing = process.platform === "win32"
        && (err.code === "EPERM" || err.code === "EBUSY" || err.code === "EACCES");
      if (!sharing || attempt === attempts - 1) throw err;
      sleepMs(50 * (attempt + 1));
    }
  }
}

function writeUserOnly(file, text) {
  const temporary = path.join(
    path.dirname(file),
    `.${path.basename(file)}.${process.pid}.${Date.now()}.tmp`
  );
  try {
    fs.writeFileSync(temporary, text, { encoding: "utf8", mode: 0o600 });
    if (process.platform !== "win32") fs.chmodSync(temporary, 0o600);
    replaceFile(temporary, file);
  } catch (err) {
    try {
      fs.unlinkSync(temporary);
    } catch {
      // The temporary file is already gone, or was never created.
    }
    throw err;
  }
}

let pastedNote = "";
function applyPastedToken() {
  pastedNote = "";
  if (!IS_DEFAULT_HOST) return;
  if (!PASTED_TOKEN) return;
  if (PASTED_TOKEN.length < MIN_TOKEN_LENGTH) {
    pastedNote = SHORT_TOKEN_MESSAGE;
    return;
  }
  const dir = tokenDir();
  if (!fs.existsSync(dir)) {
    pastedNote = MISSING_FOLDER_MESSAGE;
    return;
  }
  try {
    if (readPasteRecord() === pasteHash(PASTED_TOKEN)) return;
    writeUserOnly(tokenFilePath(), `${PASTED_TOKEN}\n`);
    writeUserOnly(pasteRecordPath(), `${pasteHash(PASTED_TOKEN)}\n`);
  } catch {
    pastedNote = WRITE_FAILED_MESSAGE;
  }
}
try {
  applyPastedToken();
} catch {
  pastedNote = WRITE_FAILED_MESSAGE;
}
const TLS_CERT_FILE = envSetting("FREECAD_MCP_TLS_CERT_FILE");
const HTTPS_ENDPOINT = `https://${URL_HOST}:${PORT}/mcp`;
const PLAIN_HTTP_TO_HTTPS_MESSAGE =
  `FreeCAD MCP Bridge serves HTTPS at ${HTTPS_ENDPOINT}, and this connection was plain HTTP. ` +
  "On this machine the extension trusts serving-cert.pem beside the token file, which is not there. Turn the bridge off and on again in FreeCAD, so it writes that file, and check that FREECAD_MCP_TOKEN_DIR, if set, is the same for both.";
const MISSING_REMOTE_CERT_MESSAGE =
  `FreeCAD host is set to ${BARE_HOST}, which requires HTTPS. ` +
  "Set TLS certificate file (PEM) in the FreeCAD MCP Bridge extension settings to a copy of FreeCAD's certificate file, not the private key.";
const MISSING_REMOTE_TOKEN_MESSAGE =
  `FreeCAD host is set to ${BARE_HOST}, another machine, so the extension cannot read its token file. ` +
  "Paste FreeCAD's bearer token into Bearer token in the FreeCAD MCP Bridge extension settings.";
const LEGACY_PROTOCOL_VERSION = "2025-11-25";
const MODERN_PROTOCOL_VERSION = "2026-07-28";
const META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion";
const TOOLS = JSON.parse(fs.readFileSync(path.join(__dirname, "tools.json"), "utf8"));
const VERSION = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8")).version;
const UNREACHABLE_HINT =
  "FreeCAD isn't reachable. Start FreeCAD and turn the bridge on with the MCP Bridge toolbar button, then try again.";
function unauthorizedMessage() {
  if (!IS_DEFAULT_HOST) {
    return (
      `FreeCAD at ${bridgeEndpoint()} refused the bearer token. ` +
      "Check that Bearer token in the FreeCAD MCP Bridge extension settings matches FreeCAD's token."
    );
  }
  const file = tokenFilePath();
  let token = "";
  let error = null;
  try {
    token = fs.readFileSync(file, "utf8").trim();
  } catch (err) {
    error = err;
  }
  if (token) {
    return (
      `FreeCAD refused the token from ${file}. ` +
      "FreeCAD and the extension must use the same token file. " +
      "Check that FreeCAD runs as the same user, and that " +
      "FREECAD_MCP_TOKEN_DIR, if set, is the same for both."
    );
  }
  if (error && error.code !== "ENOENT") {
    return (
      `Could not read the token file at ${file} (${error.message}). ` +
      "Check that the file is readable by you, then try again."
    );
  }
  return (
    `No token file at ${file}. ` +
    "Turn the bridge on in FreeCAD once, or check FREECAD_MCP_TOKEN_DIR."
  );
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
      "Turn the bridge off and on again in FreeCAD to rewrite it."
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
        result: withPastedNote(toolErrorResult(errorText)),
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

function bridgeHeaders(message) {
  // Stdio has no headers. The version the body already declares is the
  // one the bridge checks; a body with none is the legacy handshake.
  const headers = { "Content-Type": "application/json" };
  const token = IS_DEFAULT_HOST ? readTokenFile() : PASTED_TOKEN;
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

function withPastedNote(result) {
  if (!pastedNote || !result || typeof result !== "object") return result;
  const content = Array.isArray(result.content)
    ? result.content.map((item) => ({ ...item }))
    : [];
  if (result.isError && content[0] && content[0].type === "text") {
    try {
      const body = JSON.parse(content[0].text);
      if (body && typeof body === "object" && !Array.isArray(body)) {
        body.note = pastedNote;
        content[0].text = JSON.stringify(body);
        return { ...result, content };
      }
    } catch {
      // The error text is not the shim's JSON. Add a text item below.
    }
  }
  content.push({ type: "text", text: pastedNote });
  return { ...result, content };
}

function writeResult(id, result) {
  writeLine(JSON.stringify({ jsonrpc: "2.0", id, result }));
}

function writeBridgeLine(payload) {
  if (!payload) return;
  if (!pastedNote) {
    writeLine(payload);
    return;
  }
  try {
    const message = JSON.parse(payload);
    if (message.result && typeof message.result === "object") {
      message.result = withPastedNote(message.result);
      writeLine(JSON.stringify(message));
      return;
    }
  } catch {
    // The bridge line is not JSON. Leave it unchanged.
  }
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
  if (!IS_DEFAULT_HOST) {
    if (!TLS_CERT_FILE) {
      sendErrorReply(message, MISSING_REMOTE_CERT_MESSAGE);
      return;
    }
    if (!PASTED_TOKEN) {
      sendErrorReply(message, MISSING_REMOTE_TOKEN_MESSAGE);
      return;
    }
  }
  try {
    applyPastedToken();
  } catch {
    pastedNote = WRITE_FAILED_MESSAGE;
  }

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
    writeResult(message.id, withPastedNote(offlineResult(message, err)));
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

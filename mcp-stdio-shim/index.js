#!/usr/bin/env node
/*
 * MCP Bridge - Claude Desktop shim.
 *
 * A zero-dependency stdio <-> HTTP/HTTPS proxy. Claude Desktop speaks
 * MCP over stdio (newline-delimited JSON-RPC); this forwards each
 * tools/call to the addon's endpoint on FREECAD_MCP_HOST (127.0.0.1 by
 * default; "localhost" in any letter case is taken as 127.0.0.1, and
 * 127.x.x.x and ::1 are treated as this machine). The bearer token
 * comes only from FREECAD_MCP_TOKEN, on this machine and for another
 * host. The shim reads no token file and writes nothing. When
 * FREECAD_MCP_TLS_CERT_FILE is set, on any host, it connects over
 * HTTPS and pins that file's certificate by its SHA-256 fingerprint,
 * and never falls back to HTTP. With it empty on this machine, when
 * serving-cert.pem is in FREECAD_MCP_TOKEN_DIR, or
 * <home>/.freecad-mcp-bridge otherwise, it connects over HTTPS and
 * pins that file's certificate the same way, and when the file is
 * absent it connects over HTTP. For another host, the shim connects
 * to https://<host>:<port>/mcp, requires FREECAD_MCP_TLS_CERT_FILE,
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
 * error says which. A ping is answered locally with an empty result.
 * A notification is not sent and gets no reply. Any other method gets
 * a method-not-found error naming the method. At start one line on
 * stderr names the endpoint, the certificate file pinned, and whether
 * a bearer token was given, never the token. With the setting empty
 * on this machine, a further line follows whenever a call's choice
 * of certificate differs from the last call's. Node stdlib only
 * (global fetch, node:crypto, node:https and node:tls, >=18).
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
const TOKEN_DIR_NAME = ".freecad-mcp-bridge";
const SERVING_CERT_NAME = "serving-cert.pem";
const NO_TOKEN_MESSAGE =
  "Make a bearer token in the MCP Bridge addon in FreeCAD: Edit → Preferences → MCP Bridge → Generate new token, or Set token if you already have a bearer token to use. Paste that same token into the Bearer token field of the MCP Bridge for FreeCAD extension in Claude Desktop: Settings → Desktop App → Extensions → Manage → MCP Bridge for FreeCAD → Configure → Bearer token.";
const INVALID_TOKEN_MESSAGE =
  "The token in Bearer token is not a valid bearer token. Only letters, digits and - . _ ~ + / are allowed, with = at the end. Paste the token again, or make a new one with Generate new token in FreeCAD's MCP Bridge preferences and paste that into Bearer token.";
const REFUSED_TOKEN_MESSAGE =
  "FreeCAD refused the token in Bearer token. Make a new one with Generate new token in FreeCAD's MCP Bridge preferences, and paste it into Bearer token.";
const BEARER_TOKEN = /^[A-Za-z0-9._~+/-]+={0,}$/;

function pastedTokenProblem() {
  if (!PASTED_TOKEN) return NO_TOKEN_MESSAGE;
  if (!BEARER_TOKEN.test(PASTED_TOKEN)) return INVALID_TOKEN_MESSAGE;
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

const TLS_CERT_FILE = envSetting("FREECAD_MCP_TLS_CERT_FILE");

// With the certificate setting empty, on this machine HTTPS follows
// the bridge's copy, looked up per call so a later start is picked up.
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

// The certificate file a call pins. The extension's certificate
// setting wins on every host: with it set the call is HTTPS, and
// there is no plain HTTP. With it empty, this machine uses the
// bridge's copy when it is there, and another host has no file.
function certFileInUse() {
  if (TLS_CERT_FILE) return TLS_CERT_FILE;
  return localServingCert();
}

function bridgeEndpoint() {
  const https = !IS_DEFAULT_HOST || certFileInUse() !== "";
  const scheme = https ? "https" : "http";
  return `${scheme}://${URL_HOST}:${PORT}/mcp`;
}

const HTTPS_ENDPOINT = `https://${URL_HOST}:${PORT}/mcp`;
const HTTP_ENDPOINT = `http://${URL_HOST}:${PORT}/mcp`;
const LOG_PREFIX = "[freecad-mcp-bridge shim]";

// The extension's log is its stderr, which Claude Desktop keeps.
function logLine(text) {
  process.stderr.write(`${LOG_PREFIX} ${text}\n`);
}

// Whether a bearer token was given. Never the token, nor any part of it.
function tokenLogText() {
  if (!PASTED_TOKEN) {
    return "No bearer token was given, so each tool call returns the message saying where to paste it.";
  }
  if (!BEARER_TOKEN.test(PASTED_TOKEN)) {
    return "The bearer token given is not a valid bearer token, so each tool call returns the message saying so.";
  }
  return "A bearer token was given.";
}

// With the setting empty on this machine, the certificate is chosen at
// each call. This is the last call's choice: the bridge's copy, or "".
let lastLocalChoice = "";

function choosesPerCall() {
  return IS_DEFAULT_HOST && !TLS_CERT_FILE;
}

function startLogLine() {
  if (TLS_CERT_FILE) {
    return `Calls ${HTTPS_ENDPOINT}, pinning TLS certificate file (PEM) at ${TLS_CERT_FILE}. ${tokenLogText()}`;
  }
  if (!IS_DEFAULT_HOST) {
    return `Calls ${HTTPS_ENDPOINT}. TLS certificate file (PEM) is empty, and another host requires it, so each tool call returns that error. ${tokenLogText()}`;
  }
  lastLocalChoice = localServingCert();
  const now = lastLocalChoice ? "HTTPS" : "plain HTTP";
  return (
    `Calls FreeCAD on this machine, choosing at each tool call: ${HTTPS_ENDPOINT}, pinning serving-cert.pem at ${servingCertPath()}, while that file is there, ` +
    `and plain HTTP to ${HTTP_ENDPOINT} while it is not. Now: ${now}. ${tokenLogText()}`
  );
}

// One more line when a call's choice differs from the last call's.
function noteLocalChoice(certFile) {
  if (!choosesPerCall() || certFile === lastLocalChoice) return;
  lastLocalChoice = certFile;
  if (certFile) {
    logLine(`serving-cert.pem is now at ${certFile}: tool calls go to ${HTTPS_ENDPOINT}, pinning it.`);
  } else {
    logLine(`serving-cert.pem is no longer at ${servingCertPath()}: tool calls go over plain HTTP to ${HTTP_ENDPOINT}.`);
  }
}
const PLAIN_HTTP_TO_HTTPS_MESSAGE =
  `MCP Bridge serves HTTPS at ${HTTPS_ENDPOINT}, and this connection was plain HTTP. ` +
  "On this machine the extension trusts serving-cert.pem in the bridge's folder, which is not there. Turn the bridge off and on again in FreeCAD, so it writes that file, and check that FREECAD_MCP_TOKEN_DIR, if set, is the same for FreeCAD and Claude Desktop. Or set TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings to a copy of FreeCAD's certificate file, not the private key.";
const MISSING_REMOTE_CERT_MESSAGE =
  `FreeCAD host is set to ${BARE_HOST}, which requires HTTPS. ` +
  "Set TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings to a copy of FreeCAD's certificate file, not the private key.";
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
  const raw =
    err.cause && err.cause.message ? `${err.message}: ${err.cause.message}` : err.message;
  return String(raw || "").trim();
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

// A failure names the file in use: the certificate setting's wording
// when the setting is set, on this machine too, and serving-cert.pem's
// when the bridge's copy is in use.
function certFileErrorMessage(err, certFile) {
  const endpoint = bridgeEndpoint();
  if (err && err.certFileProblem === "invalid") {
    if (!TLS_CERT_FILE) {
      return (
        `serving-cert.pem at ${certFile} for ${endpoint} is not a PEM certificate. ` +
        "Turn the bridge off and on again in FreeCAD, so it rewrites serving-cert.pem in the bridge's folder."
      );
    }
    return (
      `TLS certificate file (PEM) at ${certFile} for ${endpoint} is not a PEM certificate. ` +
      "Set TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings to a copy of FreeCAD's certificate file, not the private key."
    );
  }
  const detail = fetchErrorMessage(err);
  if (!TLS_CERT_FILE) {
    return (
      `Could not read serving-cert.pem at ${certFile} for ${endpoint} (${detail}). ` +
      "Turn the bridge off and on again in FreeCAD, so it writes that file, and check that it is readable by you."
    );
  }
  return (
    `Could not read TLS certificate file (PEM) at ${certFile} for ${endpoint} (${detail}). ` +
    "Set TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings to a readable copy of FreeCAD's certificate file, not the private key."
  );
}

function certMismatchMessage(certFile) {
  const endpoint = bridgeEndpoint();
  if (!TLS_CERT_FILE) {
    return (
      `MCP Bridge TLS connection failed at ${endpoint}: the bridge's certificate does not match serving-cert.pem at ${certFile}. ` +
      "Turn the bridge off and on again in FreeCAD, so it rewrites serving-cert.pem in the bridge's folder, then try again."
    );
  }
  return (
    `MCP Bridge TLS connection failed at ${endpoint}: the bridge's certificate does not match TLS certificate file (PEM) at ${certFile}. ` +
    "Set TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings to a copy of FreeCAD's current certificate file, not the private key."
  );
}

function tlsErrorMessage(err) {
  const detail = fetchErrorMessage(err);
  const where = `MCP Bridge TLS connection failed at ${bridgeEndpoint()} (${detail}). `;
  if (!TLS_CERT_FILE) {
    return (
      where +
      "Turn the bridge off and on again in FreeCAD, so it rewrites serving-cert.pem in the bridge's folder, then try again."
    );
  }
  return (
    where +
    "Check that FreeCAD is serving HTTPS at that address and that TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings is a copy of FreeCAD's certificate file, not the private key."
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

// Node's own parser reads the trusted copy: crypto.X509Certificate
// takes the first certificate in the file, and its raw property is that
// certificate's DER bytes. That parser also takes a bare DER file, and
// the trusted copy is PEM, so the BEGIN CERTIFICATE line is asked for
// first. Whatever the parser refuses is reported as not a certificate.
function readCertFingerprint(certFile) {
  let pem;
  try {
    pem = fs.readFileSync(certFile, "utf8");
  } catch (err) {
    err.code = err.code || "ERR_CERT_READ";
    err.certFileProblem = "unreadable";
    throw err;
  }
  let raw = null;
  if (pem.includes("-----BEGIN CERTIFICATE-----")) {
    try {
      raw = new crypto.X509Certificate(pem).raw;
    } catch (_err) {
      raw = null;
    }
  }
  if (!Buffer.isBuffer(raw) || raw.length === 0) {
    const err = new Error("no PEM certificate found");
    err.code = "ERR_INVALID_CERT_FILE";
    err.certFileProblem = "invalid";
    throw err;
  }
  return crypto.createHash("sha256").update(raw).digest();
}

// A matching SHA-256 fingerprint is the whole check: the extension does
// no chain building, no host-name check, and no check of the
// certificate's dates. The trusted file is a copy of the bridge's own
// certificate, and completing the TLS handshake proves the bridge holds
// that certificate's private key.
//
// Complete the handshake and verify the fingerprint on the TLS socket
// before creating the HTTP request, so not one byte of the HTTP request
// (the bearer token included) is queued or written before they match.
//
// One connection carries one request. The request says Connection:
// close, and the socket is destroyed when the reply ends and on any
// failure, so the extension holds no connection open between calls.
function postHttps(headers, body, certFile) {
  return new Promise((resolve, reject) => {
    let expectedFingerprint;
    try {
      expectedFingerprint = readCertFingerprint(certFile);
    } catch (err) {
      reject(err);
      return;
    }
    let settled = false;
    let socket = null;
    function fail(err) {
      if (socket) socket.destroy();
      if (settled) return;
      settled = true;
      reject(err);
    }
    const connectOptions = {
      host: BARE_HOST,
      port: Number(PORT),
      rejectUnauthorized: false,
      checkServerIdentity: () => undefined,
    };
    if (net.isIP(BARE_HOST) === 0) {
      connectOptions.servername = BARE_HOST;
    }
    socket = tls.connect(connectOptions);
    const onPreRequestClose = () => {
      const err = new Error("TLS connection closed before handshake completed");
      err.code = "ECONNRESET";
      fail(err);
    };
    // This listener stays for the socket's whole life, so an error on
    // it at any moment fails the call and never goes unhandled.
    socket.on("error", fail);
    socket.on("close", onPreRequestClose);
    socket.once("secureConnect", () => {
      if (settled) {
        socket.destroy();
        return;
      }
      const peer = socket.getPeerCertificate(false);
      const raw = peer && Buffer.isBuffer(peer.raw) ? peer.raw : null;
      const actualFingerprint =
        raw && raw.length > 0
          ? crypto.createHash("sha256").update(raw).digest()
          : null;
      if (
        !actualFingerprint ||
        actualFingerprint.length !== expectedFingerprint.length ||
        !crypto.timingSafeEqual(actualFingerprint, expectedFingerprint)
      ) {
        socket.removeListener("close", onPreRequestClose);
        socket.destroy();
        const err = new Error("certificate fingerprint mismatch");
        err.code = "ERR_CERT_MISMATCH";
        fail(err);
        return;
      }
      socket.removeListener("close", onPreRequestClose);
      const payload = Buffer.from(body, "utf8");
      const req = https.request(
        bridgeEndpoint(),
        {
          method: "POST",
          headers: {
            ...headers,
            "Content-Length": String(payload.length),
            Connection: "close",
          },
          createConnection: () => socket,
        },
        (res) => {
          if (res.statusCode >= 300 && res.statusCode < 400) {
            res.resume();
            const err = new Error(
              "redirect mode is set to 'error': unexpected redirect"
            );
            err.code = "ERR_UNEXPECTED_REDIRECT";
            fail(err);
            return;
          }
          const chunks = [];
          res.on("data", (chunk) => chunks.push(chunk));
          res.on("end", () => {
            socket.destroy();
            if (settled) return;
            settled = true;
            const text = Buffer.concat(chunks).toString("utf8");
            resolve({
              status: res.statusCode || 0,
              text: async () => text,
            });
          });
          res.on("error", fail);
        }
      );
      req.on("error", fail);
      req.end(payload);
    });
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

  const certFile = certFileInUse();
  noteLocalChoice(certFile);
  let res;
  try {
    res = await postBridge(message, rawLine, certFile);
  } catch (err) {
    if (certFile && err && err.certFileProblem) {
      sendErrorReply(message, certFileErrorMessage(err, certFile));
      return;
    }
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
      if (code === "ERR_CERT_MISMATCH") {
        sendErrorReply(message, certMismatchMessage(certFile));
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

logLine(startLogLine());

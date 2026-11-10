#!/usr/bin/env node
/*
 * FreeCAD MCP Bridge - Claude Desktop shim.
 *
 * A zero-dependency stdio <-> HTTP/HTTPS proxy. Claude Desktop speaks
 * MCP over stdio (newline-delimited JSON-RPC); this forwards each
 * tools/call to the addon's endpoint on FREECAD_MCP_HOST (127.0.0.1 by
 * default; "localhost" in any letter case is taken as 127.0.0.1, and
 * 127.x.x.x and ::1 are treated as this machine). On this machine it
 * sends Authorization: Bearer from its own store, filled from the
 * hand-over file token-handover in FREECAD_MCP_TOKEN_DIR, or
 * <home>/.freecad-mcp-bridge otherwise. It does not read or write a
 * token file. A pasted token is never written back. On this machine
 * the order is the hand-over file just taken, else the stored token,
 * else the Bearer token setting. When serving-cert.pem is in that
 * folder it connects over HTTPS and trusts only that file. When the
 * file is absent it connects over HTTP. FREECAD_MCP_TLS_CERT_FILE is
 * ignored on this machine. For another host, the shim connects to
 * https://<host>:<port>/mcp, requires both
 * FREECAD_MCP_TLS_CERT_FILE and FREECAD_MCP_TOKEN, trusts only that
 * certificate, and uses the pasted token only. The JSON reply is
 * unwrapped and written back on stdout.
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
 * error says which.
 * A ping is answered
 * locally with an empty result. A notification is not sent and gets no
 * reply. Any other method gets a method-not-found error naming the
 * method. Node stdlib only (global fetch and node:https, >=18).
 */

const { spawnSync } = require("child_process");
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
const HANDOVER_NAME = "token-handover";
const DPAPI_NAME = "token.dpapi";
const EXTENSION_TOKEN_NAME = "token.extension";
const KEYCHAIN_SERVICE = "freecad-mcp-bridge";
const NO_LOCAL_TOKEN_MESSAGE =
  "No token for this machine. Turn on Share with Claude Desktop in FreeCAD's MCP Bridge preferences, or paste the token into Bearer token.";
const MIN_TOKEN_LENGTH = 32;

function tokenDir() {
  const override = envSetting("FREECAD_MCP_TOKEN_DIR");
  if (override) return override;
  return path.join(os.homedir(), TOKEN_DIR_NAME);
}

function servingCertPath() {
  return path.join(tokenDir(), SERVING_CERT_NAME);
}

function handoverPath() {
  return path.join(tokenDir(), HANDOVER_NAME);
}

// file forces the Linux store, including on Windows, so a test can
// exercise that path. Unset, the store follows the system.
function storeKind() {
  const forced = envSetting("FREECAD_MCP_EXTENSION_STORE");
  if (forced === "file" || forced === "dpapi" || forced === "keychain") {
    return forced;
  }
  if (process.platform === "win32") return "dpapi";
  if (process.platform === "darwin") return "keychain";
  return "file";
}

function storeLabel() {
  const kind = storeKind();
  if (kind === "dpapi") return `the DPAPI store at ${path.join(tokenDir(), DPAPI_NAME)}`;
  if (kind === "keychain") return `the Keychain item ${KEYCHAIN_SERVICE}`;
  return `the token file at ${path.join(tokenDir(), EXTENSION_TOKEN_NAME)}`;
}

function storeWriteMessage(err) {
  return (
    `Could not write the token to ${storeLabel()} (${err.message}). ` +
    "Check that the store is writable by you, then try again."
  );
}

function storeReadMessage(err) {
  return (
    `Could not read the token from ${storeLabel()} (${err.message}). ` +
    "Check that the store is readable by you, then try again."
  );
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

function powershellExe() {
  const windir = process.env.SystemRoot || "C:\\Windows";
  return path.join(
    windir,
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe"
  );
}

function runPowerShell(op, b64) {
  // -Command - runs only the first stdin line, so the script is encoded.
  const script = [
    "$ErrorActionPreference = 'Stop'",
    "Add-Type -AssemblyName System.Security",
    "$raw = [Convert]::FromBase64String($env:FREECAD_MCP_DPAPI_B64)",
    "if ($env:FREECAD_MCP_DPAPI_OP -eq 'protect') {",
    "  $out = [Security.Cryptography.ProtectedData]::Protect(",
    "    $raw, $null, 'CurrentUser')",
    "} else {",
    "  $out = [Security.Cryptography.ProtectedData]::Unprotect(",
    "    $raw, $null, 'CurrentUser')",
    "}",
    "[Console]::Out.Write([Convert]::ToBase64String($out))",
  ].join("\n");
  const encoded = Buffer.from(script, "utf16le").toString("base64");
  const result = spawnSync(
    powershellExe(),
    ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
    {
      encoding: "utf8",
      windowsHide: true,
      timeout: 30000,
      env: {
        ...process.env,
        FREECAD_MCP_DPAPI_OP: op,
        FREECAD_MCP_DPAPI_B64: b64,
      },
    }
  );
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || "PowerShell failed").trim();
    throw new Error(detail);
  }
  return (result.stdout || "").trim();
}

function writeDpapi(token) {
  const cipher = runPowerShell(
    "protect",
    Buffer.from(token, "utf8").toString("base64")
  );
  if (!cipher) throw new Error("PowerShell returned no ciphertext.");
  fs.mkdirSync(tokenDir(), { recursive: true });
  writeUserOnly(path.join(tokenDir(), DPAPI_NAME), `${cipher}\n`);
}

function readDpapi() {
  const file = path.join(tokenDir(), DPAPI_NAME);
  let cipher = "";
  try {
    cipher = fs.readFileSync(file, "utf8").trim();
  } catch (err) {
    if (err.code === "ENOENT") return "";
    throw err;
  }
  if (!cipher) return "";
  return Buffer.from(runPowerShell("unprotect", cipher), "base64").toString("utf8");
}

function keychainAccount() {
  const override = envSetting("FREECAD_MCP_KEYCHAIN_ACCOUNT");
  if (override) return override;
  return os.userInfo().username;
}

function securityQuote(value) {
  const text = String(value);
  if (/^[\w.@+\-]+$/.test(text)) return text;
  return `"${text.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function runSecurity(args, input) {
  const result = spawnSync("security", args, {
    input,
    encoding: "utf8",
    timeout: 30000,
  });
  if (result.error) throw result.error;
  return result;
}

function writeKeychain(token) {
  // security -i reads the command from stdin. -X is the password as
  // hex, so the token is not in the process's argument list.
  const hex = Buffer.from(token, "utf8").toString("hex");
  const line =
    "add-generic-password -U -s " +
    securityQuote(KEYCHAIN_SERVICE) +
    " -a " +
    securityQuote(keychainAccount()) +
    " -X " +
    hex +
    "\n";
  const result = runSecurity(["-i"], line);
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || "security failed").trim());
  }
}

function readKeychain() {
  const result = runSecurity([
    "find-generic-password",
    "-s",
    KEYCHAIN_SERVICE,
    "-a",
    keychainAccount(),
    "-w",
  ]);
  if (result.status !== 0) {
    const detail = (result.stderr || "").trim();
    if (/could not be found/i.test(detail)) return "";
    throw new Error(detail || "security failed");
  }
  return (result.stdout || "").replace(/\n$/, "");
}

function extensionTokenPath() {
  return path.join(tokenDir(), EXTENSION_TOKEN_NAME);
}

function writeExtensionFile(token) {
  fs.mkdirSync(tokenDir(), { recursive: true });
  writeUserOnly(extensionTokenPath(), `${token}\n`);
}

function readExtensionFile() {
  try {
    return fs.readFileSync(extensionTokenPath(), "utf8").trim();
  } catch (err) {
    if (err.code === "ENOENT") return "";
    throw err;
  }
}

function writeStore(token) {
  const kind = storeKind();
  if (kind === "dpapi") writeDpapi(token);
  else if (kind === "keychain") writeKeychain(token);
  else writeExtensionFile(token);
}

function readStoreFile() {
  const kind = storeKind();
  if (kind === "dpapi") return readDpapi();
  if (kind === "keychain") return readKeychain();
  return readExtensionFile();
}

// A successful read is kept for the process. A failure is not, so the
// next call can try again. The hand-over file is still looked up on
// every call and replaces this memory when it is present.
let storeLoaded = false;
let storeToken = "";

function rememberStore(token) {
  storeLoaded = true;
  storeToken = token || "";
}

function loadStore() {
  if (storeLoaded) return { token: storeToken, error: "" };
  try {
    const token = readStoreFile();
    rememberStore(token);
    return { token, error: "" };
  } catch (err) {
    return { token: "", error: storeReadMessage(err) };
  }
}

function readHandover() {
  const file = handoverPath();
  let stat;
  try {
    stat = fs.statSync(file);
  } catch (err) {
    if (err.code === "ENOENT") return { status: "absent" };
    return { status: "error", error: err };
  }
  if (!stat.isFile()) {
    const err = new Error("not a file");
    err.code = "EISDIR";
    return { status: "error", error: err };
  }
  try {
    const text = fs.readFileSync(file, "utf8").trim();
    if (text.length < MIN_TOKEN_LENGTH) {
      return { status: "short", file };
    }
    return { status: "present", token: text, file };
  } catch (err) {
    return { status: "error", error: err };
  }
}

function handoverError(err) {
  return (
    `Could not read the hand-over file at ${handoverPath()} (${err.message}). ` +
    "Check that the file is readable by you, then try again."
  );
}

function shortHandoverMessage(file) {
  return (
    `The hand-over file at ${file} holds fewer than 32 characters, so it was left in place. ` +
    "Set a token of at least 32 characters in FreeCAD, or paste the token into Bearer token."
  );
}

let activeToken = "";
let activeSource = "";
let activeNote = "";

function resolveLocalToken() {
  const handover = readHandover();
  if (handover.status === "error") {
    return { token: "", source: "", error: handoverError(handover.error), note: "" };
  }
  const note = handover.status === "short" ? shortHandoverMessage(handover.file) : "";
  if (handover.status === "present") {
    try {
      writeStore(handover.token);
    } catch (err) {
      return { token: "", source: "", error: storeWriteMessage(err) };
    }
    try {
      fs.unlinkSync(handover.file);
    } catch (err) {
      return {
        token: "",
        source: "",
        error:
          `Could not delete the hand-over file at ${handover.file} (${err.message}). ` +
          "Check that the file is writable by you, then try again.",
      };
    }
    rememberStore(handover.token);
    return { token: handover.token, source: "handover", error: "", note: "" };
  }
  const stored = loadStore();
  if (stored.error) return { token: "", source: "", error: stored.error, note: "" };
  if (stored.token) return { token: stored.token, source: "store", error: "", note };
  if (PASTED_TOKEN) return { token: PASTED_TOKEN, source: "paste", error: "", note };
  if (note) return { token: "", source: "", error: note, note: "" };
  return { token: "", source: "", error: NO_LOCAL_TOKEN_MESSAGE, note: "" };
}

const TLS_CERT_FILE = envSetting("FREECAD_MCP_TLS_CERT_FILE");
const HTTPS_ENDPOINT = `https://${URL_HOST}:${PORT}/mcp`;
const PLAIN_HTTP_TO_HTTPS_MESSAGE =
  `FreeCAD MCP Bridge serves HTTPS at ${HTTPS_ENDPOINT}, and this connection was plain HTTP. ` +
  "On this machine the extension trusts serving-cert.pem beside the token file, which is not there. Turn the bridge on in FreeCAD with a certificate set, so it writes that file.";
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
  const pasteAlso = Boolean(PASTED_TOKEN);
  const storeAlso = Boolean(storeToken);
  if (pasteAlso && storeAlso) {
    if (activeSource === "paste") {
      return (
        "FreeCAD refused the token pasted into Bearer token. " +
        "A token in the extension's store also exists."
      );
    }
    return (
      "FreeCAD refused the token from the extension's store. " +
      "A token is also pasted into Bearer token."
    );
  }
  if (activeSource === "paste") {
    return (
      "FreeCAD refused the token pasted into Bearer token. " +
      "Check that Bearer token in the FreeCAD MCP Bridge extension settings matches FreeCAD's token."
    );
  }
  return (
    "FreeCAD refused the token from the extension's store. " +
    "Turn on Share with Claude Desktop in FreeCAD's MCP Bridge preferences, or paste the token into Bearer token."
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
      `Check that serving-cert.pem beside the token file is the certificate the bridge is serving, and covers ${BARE_HOST}.`
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

function withToolNote(result) {
  if (!activeNote || !result || typeof result !== "object") return result;
  const content = Array.isArray(result.content)
    ? result.content.map((item) => ({ ...item }))
    : [];
  content.push({ type: "text", text: activeNote });
  return { ...result, content };
}

function sendErrorReply(message, errorText) {
  if (message.method === "tools/call") {
    writeLine(
      JSON.stringify({
        jsonrpc: "2.0",
        id: message.id,
        result: withToolNote(toolErrorResult(errorText)),
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
  if (!activeNote) {
    writeLine(payload);
    return;
  }
  try {
    const message = JSON.parse(payload);
    if (message.result && typeof message.result === "object") {
      message.result = withToolNote(message.result);
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
  activeNote = "";
  if (!IS_DEFAULT_HOST) {
    if (!TLS_CERT_FILE) {
      sendErrorReply(message, MISSING_REMOTE_CERT_MESSAGE);
      return;
    }
    if (!PASTED_TOKEN) {
      sendErrorReply(message, MISSING_REMOTE_TOKEN_MESSAGE);
      return;
    }
    activeToken = PASTED_TOKEN;
    activeSource = "paste";
  } else {
    const resolved = resolveLocalToken();
    if (resolved.error) {
      sendErrorReply(message, resolved.error);
      return;
    }
    activeToken = resolved.token;
    activeSource = resolved.source;
    activeNote = resolved.note || "";
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
    writeResult(message.id, withToolNote(offlineResult(message, err)));
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

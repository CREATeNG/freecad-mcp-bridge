// Requirements on the stdio shim (mcp-stdio-shim/index.js).
//
// Each test name states one requirement as an MCP client that speaks stdio
// experiences it. The shim runs as a child process; a stand-in HTTP server
// plays the bridge, or a closed port plays a bridge that is not running.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn, execFileSync } = require("node:child_process");
const crypto = require("node:crypto");
const { builtinModules } = require("node:module");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const os = require("node:os");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");
const SHIM_DIR = path.join(ROOT, "mcp-stdio-shim");
const SHIM = path.join(SHIM_DIR, "index.js");
const readJson = (file) => JSON.parse(fs.readFileSync(file, "utf8"));

// A stand-in bridge: records each request, answers with reply(request).
async function startBridge(reply, tlsOptions = null, host = "127.0.0.1") {
  const requests = [];
  const rawChunks = [];
  const handler = (req, res) => {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      const request = { method: req.method, url: req.url, headers: req.headers, body };
      requests.push(request);
      const answered = await reply(request);
      const { status = 200, type = "application/json", text } = answered;
      res.writeHead(status, { "Content-Type": type });
      res.end(text);
    });
  };
  const server = tlsOptions ? https.createServer(tlsOptions, handler) : http.createServer(handler);
  const openTlsSockets = new Set();
  if (tlsOptions) {
    server.on("secureConnection", (tlsSocket) => {
      openTlsSockets.add(tlsSocket);
      tlsSocket.on("close", () => openTlsSockets.delete(tlsSocket));
      tlsSocket.on("data", (chunk) => rawChunks.push(chunk));
    });
  }
  await new Promise((resolve) => server.listen(0, host, resolve));
  return {
    port: server.address().port,
    requests,
    rawChunks,
    openTlsSockets,
    close: () => server.close(),
  };
}

// A port nothing listens on.
async function closedPort() {
  const server = http.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  return port;
}

// Start the shim against `port`; collect its stdout lines.
function startShim(port, extraEnv = {}, options = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "shim-home-"));
  const tokenDir = options.tokenDir || path.join(home, ".freecad-mcp-bridge");
  const tokenFile = path.join(tokenDir, "token");
  const pasted = extraEnv.FREECAD_MCP_TOKEN || "";
  if (options.seedToken) {
    fs.mkdirSync(tokenDir, { recursive: true });
    fs.writeFileSync(tokenFile, `${options.seedToken}\n`, { encoding: "utf8", mode: 0o600 });
  } else if (options.prepareTokenDir !== false && pasted.length >= 32) {
    fs.mkdirSync(tokenDir, { recursive: true });
  }
  const env = {
    ...process.env,
    FREECAD_MCP_PORT: String(port),
    HOME: home,
    USERPROFILE: home,
  };
  delete env.FREECAD_MCP_HOST;
  delete env.FREECAD_MCP_TOKEN;
  delete env.FREECAD_MCP_TLS_CERT_FILE;
  delete env.FREECAD_MCP_TOKEN_DIR;
  delete env.FREECAD_MCP_EXTENSION_STORE;
  delete env.FREECAD_MCP_KEYCHAIN_ACCOUNT;
  delete env.SECURITY_RECORD;
  Object.assign(env, extraEnv);
  if (extraEnv.FREECAD_MCP_TOKEN === undefined) {
    env.FREECAD_MCP_TOKEN = "0123456789abcdef0123456789abcdef";
  }
  delete env.FREECAD_MCP_TOKEN_DIR;
  if (options.tokenDir) env.FREECAD_MCP_TOKEN_DIR = options.tokenDir;
  const nodeArgs = [...(options.nodeArgs || []), SHIM];
  const child = spawn(process.execPath, nodeArgs, {
    env,
    stdio: ["pipe", "pipe", "pipe"],
  });
  const lines = [];
  let partial = "";
  let waiter = null;
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    partial += chunk;
    const parts = partial.split("\n");
    partial = parts.pop();
    lines.push(...parts);
    if (waiter) waiter();
  });
  return {
    lines,
    home,
    tokenDir,
    tokenFile,
    send: (text) => child.stdin.write(text),
    // Resolve once `count` lines have arrived, or fail after a timeout.
    waitForLines(count, ms = 10000) {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`timed out; stdout: ${lines}`)), ms);
        waiter = () => {
          if (lines.length >= count) {
            clearTimeout(timer);
            waiter = null;
            resolve(lines.map((line) => JSON.parse(line)));
          }
        };
        waiter();
      });
    },
    stop: () =>
      new Promise((resolve) => {
        child.on("exit", resolve);
        child.stdin.end();
      }),
  };
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Echo bridge: answers every request with a JSON-RPC result naming its method.
const echo = ({ body }) => {
  const message = JSON.parse(body);
  return { text: JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { echoed: message.method } }) };
};

test("the shim needs no third-party packages", () => {
  const pkg = readJson(path.join(SHIM_DIR, "package.json"));
  for (const field of ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "bundleDependencies"]) {
    assert.equal(Object.keys(pkg[field] || {}).length, 0, `package.json ${field}`);
  }
  const source = fs.readFileSync(SHIM, "utf8");
  const required = [...source.matchAll(/require\(\s*["']([^"']+)["']\s*\)/g)].map((m) => m[1]);
  assert.ok(required.length > 0);
  for (const name of required) {
    assert.ok(builtinModules.includes(name.replace(/^node:/, "")), `${name} is not a Node built-in`);
  }
});

test("the shim runs on the current Node, which is 18 or newer", async () => {
  assert.ok(Number(process.versions.node.split(".")[0]) >= 18);
  const shim = startShim(await closedPort());
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.id, 1);
  await shim.stop();
});

test("the shim sends to the port named by FREECAD_MCP_PORT", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  await shim.waitForLines(1);
  assert.equal(bridge.requests.length, 1);
  await shim.stop();
  bridge.close();
});

test("each stdin line is POSTed unchanged to /mcp", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  const first = '{"jsonrpc": "2.0",  "id": 1, "method": "tools/call"}';
  const second = '{"id":2,"method":"tools/call","jsonrpc":"2.0"}';
  // Split the first message across writes, and share a write with the second.
  shim.send(first.slice(0, 10));
  await delay(50);
  shim.send(first.slice(10) + "\n" + second + "\n");
  await shim.waitForLines(2);
  assert.deepEqual(bridge.requests.map((r) => r.method), ["POST", "POST"]);
  assert.deepEqual(bridge.requests.map((r) => r.url), ["/mcp", "/mcp"]);
  assert.deepEqual(bridge.requests.map((r) => r.body).sort(), [first, second].sort());
  for (const r of bridge.requests) assert.match(r.headers["content-type"], /application\/json/);
  await shim.stop();
  bridge.close();
});

test("a message with no protocol version is forwarded as 2025-11-25", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  const line = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" });
  shim.send(line + "\n");
  await shim.waitForLines(1);
  const sent = bridge.requests[0];
  assert.equal(sent.body, line);
  assert.equal(sent.headers["mcp-protocol-version"], "2025-11-25");
  assert.equal(sent.headers["mcp-method"], undefined);
  await shim.stop();
  bridge.close();
});

test("server/discover gets method-not-found and does not contact the bridge", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  shim.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "server/discover",
      params: { _meta: { "io.modelcontextprotocol/protocolVersion": "2026-07-28" } },
    }) + "\n"
  );
  const [reply] = await shim.waitForLines(1);
  await delay(100);
  assert.equal(bridge.requests.length, 0);
  assert.equal(reply.error.code, -32601);
  assert.equal(reply.error.message, "Method not found: server/discover");
  await shim.stop();
  bridge.close();
});

test("a method the shim does not offer names that method and is not sent on", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "no/such/method" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  await delay(100);
  assert.equal(bridge.requests.length, 0);
  assert.equal(reply.error.code, -32601);
  assert.equal(reply.error.message, "Method not found: no/such/method");
  await shim.stop();
  bridge.close();
});

test("a 2026 tools/call carries the tool name", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  const line = JSON.stringify({
    jsonrpc: "2.0",
    id: 4,
    method: "tools/call",
    params: {
      name: "execute_python",
      _meta: { "io.modelcontextprotocol/protocolVersion": "2026-07-28" },
    },
  });
  shim.send(line + "\n");
  await shim.waitForLines(1);
  const sent = bridge.requests[0];
  assert.equal(sent.body, line);
  assert.equal(sent.headers["mcp-protocol-version"], "2026-07-28");
  assert.equal(sent.headers["mcp-method"], "tools/call");
  assert.equal(sent.headers["mcp-name"], "execute_python");
  await shim.stop();
  bridge.close();
});

test("a plain JSON reply comes back as one JSON-RPC line", async () => {
  const reply = { jsonrpc: "2.0", id: 7, result: { content: [{ type: "text", text: "hi\nthere" }] } };
  const bridge = await startBridge(() => ({ text: JSON.stringify(reply) }));
  const shim = startShim(bridge.port);
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 7, method: "tools/call" }) + "\n");
  const [line] = await shim.waitForLines(1);
  assert.deepEqual(line, reply);
  await delay(100);
  assert.equal(shim.lines.length, 1);
  await shim.stop();
  bridge.close();
});

test("a notification gets no reply and is not sent on", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  shim.send(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
  await delay(200);
  assert.equal(bridge.requests.length, 0);
  assert.equal(shim.lines.length, 0);
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call" }) + "\n");
  const replies = await shim.waitForLines(1);
  await delay(50);
  assert.equal(bridge.requests.length, 1);
  assert.deepEqual(shim.lines.map((l) => JSON.parse(l).id), [2]);
  assert.equal(replies[0].result.echoed, "tools/call");
  await shim.stop();
  bridge.close();
});

test("initialize and tools/list do not contact a listening bridge, or a port nothing listens on", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }) + "\n");
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" }) + "\n");
  const replies = await shim.waitForLines(2);
  await delay(200);
  assert.equal(bridge.requests.length, 0);
  assert.equal(replies.find((r) => r.id === 1).result.protocolVersion, "2025-11-25");
  assert.ok(replies.find((r) => r.id === 2).result.tools.length > 0);
  await shim.stop();
  bridge.close();

  const down = startShim(await closedPort());
  const started = Date.now();
  down.send(JSON.stringify({ jsonrpc: "2.0", id: 3, method: "initialize", params: {} }) + "\n");
  down.send(JSON.stringify({ jsonrpc: "2.0", id: 4, method: "tools/list" }) + "\n");
  const downReplies = await down.waitForLines(2);
  assert.ok(Date.now() - started < 1000);
  assert.equal(downReplies.find((r) => r.id === 3).error, undefined);
  assert.equal(downReplies.find((r) => r.id === 4).error, undefined);
  await down.stop();
});

// --- The bridge is unreachable -------------------------------------------

async function askOffline(message) {
  const shim = startShim(await closedPort());
  shim.send(JSON.stringify(message) + "\n");
  const [reply] = await shim.waitForLines(1);
  await shim.stop();
  return reply;
}

test("with the bridge unreachable, initialize is still answered", async () => {
  const reply = await askOffline({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} });
  const constants = fs.readFileSync(path.join(ROOT, "freecad", "mcp_bridge", "constants.py"), "utf8");
  const declared = constants.match(/LEGACY_PROTOCOL_VERSION = "([^"]+)"/)[1];
  assert.equal(reply.id, 1);
  assert.equal(reply.result.protocolVersion, declared);
  assert.deepEqual(reply.result.serverInfo, {
    name: "freecad-mcp-bridge",
    version: readJson(path.join(SHIM_DIR, "package.json")).version,
  });
});

test("with the bridge unreachable, tools/list returns the bundled tool list", async () => {
  const reply = await askOffline({ jsonrpc: "2.0", id: 2, method: "tools/list" });
  assert.deepEqual(reply.result.tools, readJson(path.join(SHIM_DIR, "tools.json")));
});

test("with the bridge unreachable, a tool call says to start FreeCAD and turn the bridge on", async () => {
  const reply = await askOffline({
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: { name: "execute_python", arguments: { code: "print(1)" } },
  });
  assert.equal(reply.result.isError, true);
  const payload = JSON.parse(reply.result.content[0].text);
  assert.match(payload.error, /start FreeCAD/i);
  assert.match(payload.error, /turn the bridge on/i);
});

test("ping is answered locally with an empty result and is not sent on", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port);
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 4, method: "ping" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  await delay(100);
  assert.equal(bridge.requests.length, 0);
  assert.deepEqual(reply.result, {});
  assert.equal(reply.error, undefined);
  await shim.stop();
  bridge.close();

  const down = await askOffline({ jsonrpc: "2.0", id: 5, method: "ping" });
  assert.deepEqual(down.result, {});
  assert.equal(down.error, undefined);
});

test("a redirect from the bridge port is refused and the target receives nothing", async () => {
  const target = await startBridge(echo);
  const redirector = http.createServer((req, res) => {
    req.resume();
    req.on("end", () => {
      res.writeHead(307, { Location: `http://127.0.0.1:${target.port}/mcp` });
      res.end();
    });
  });
  await new Promise((resolve) => redirector.listen(0, "127.0.0.1", resolve));
  const shim = startShim(redirector.address().port);
  shim.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "execute_python", arguments: { code: "print(1)" } },
    }) + "\n"
  );
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "ping" }) + "\n");
  const replies = await shim.waitForLines(2);
  const toolReply = replies.find((r) => r.id === 1);
  const pingReply = replies.find((r) => r.id === 2);
  await delay(100);
  assert.equal(toolReply.result.isError, true);
  assert.match(JSON.parse(toolReply.result.content[0].text).error, /unexpected redirect/);
  assert.deepEqual(pingReply.result, {});
  assert.equal(pingReply.error, undefined);
  assert.equal(target.requests.length, 0);
  await shim.stop();
  await new Promise((resolve) => redirector.close(resolve));
  target.close();
});

// --- Bearer token and TLS certificate settings ----------------------------

function findOpenssl() {
  const winGitOpenssl = "C:\\Program Files\\Git\\usr\\bin\\openssl.exe";
  if (process.platform === "win32" && fs.existsSync(winGitOpenssl)) {
    return winGitOpenssl;
  }
  return "openssl";
}

function makeSelfSignedCert(dir, prefix, sanIp = "127.0.0.1", sanDns = "") {
  const certPath = path.join(dir, `${prefix}-cert.pem`);
  const keyPath = path.join(dir, `${prefix}-key.pem`);
  const cnfPath = path.join(dir, `${prefix}-openssl.cnf`);
  const sanEntries = [`IP:${sanIp}`];
  if (sanDns) sanEntries.push(`DNS:${sanDns}`);
  fs.writeFileSync(
    cnfPath,
    `[req]\ndistinguished_name = dn\nx509_extensions = v3\nprompt = no\n[dn]\nCN = ${sanDns || sanIp}\n[v3]\nbasicConstraints = critical,CA:FALSE\nkeyUsage = critical,digitalSignature,keyEncipherment\nextendedKeyUsage = serverAuth\nsubjectAltName = ${sanEntries.join(",")}\n`,
    "utf8"
  );
  execFileSync(
    findOpenssl(),
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-keyout",
      keyPath,
      "-out",
      certPath,
      "-days",
      "1",
      "-nodes",
      "-config",
      cnfPath,
      "-extensions",
      "v3",
    ],
    { stdio: "ignore" }
  );
  return {
    certPath,
    keyPath,
    cert: fs.readFileSync(certPath),
    key: fs.readFileSync(keyPath),
  };
}

test("manifest.json declares optional FreeCAD host, port, sensitive bearer token, and TLS certificate file", () => {
  const manifest = readJson(path.join(SHIM_DIR, "manifest.json"));
  const env = manifest.server.mcp_config.env;
  assert.deepEqual(env, {
    FREECAD_MCP_HOST: "${user_config.host}",
    FREECAD_MCP_PORT: "${user_config.port}",
    FREECAD_MCP_TOKEN: "${user_config.token}",
    FREECAD_MCP_TLS_CERT_FILE: "${user_config.tls_cert_file}",
  });
  const cfg = manifest.user_config;
  assert.equal(cfg.host.type, "string");
  assert.equal(cfg.host.required, false);
  assert.equal(cfg.host.default, "127.0.0.1");
  assert.equal(cfg.host.title, "FreeCAD host");
  assert.match(cfg.host.description, /127\.0\.0\.1 by default/);
  assert.match(cfg.host.description, /localhost, ::1 and 127\.x\.x\.x count as this machine/);
  assert.equal(cfg.token.type, "string");
  assert.equal(cfg.token.sensitive, true);
  assert.equal(cfg.token.required, false);
  assert.equal(cfg.token.title, "Bearer token");
  assert.match(cfg.token.description, /Paste the token FreeCAD showed/);
  assert.match(cfg.token.description, /uses only this setting/);
  assert.match(cfg.token.description, /on this machine and for another host/);
  assert.match(cfg.token.description, /writes nothing/);
  assert.match(cfg.token.description, /with = at the end/);
  assert.doesNotMatch(cfg.token.description, /Share with Claude Desktop/);
  assert.doesNotMatch(cfg.token.description, /DPAPI/);
  assert.doesNotMatch(cfg.token.description, /Keychain/);
  assert.equal(cfg.tls_cert_file.type, "file");
  assert.equal(cfg.tls_cert_file.required, false);
  assert.equal(cfg.tls_cert_file.title, "TLS certificate file (PEM)");
  assert.match(
    cfg.host.description,
    /For another host, also set TLS certificate file \(PEM\) below/
  );
  assert.match(cfg.tls_cert_file.description, /Used for another host only/);
  assert.match(cfg.tls_cert_file.description, /copy of FreeCAD's certificate file, not the private key/);
  assert.match(cfg.tls_cert_file.description, /trusts only that certificate/);
  assert.match(cfg.tls_cert_file.description, /serving-cert\.pem/);
  assert.match(cfg.tls_cert_file.description, /in the bridge's folder/);
  assert.match(cfg.tls_cert_file.description, /uses HTTPS only when that file is present/);
});

function pastedText(result) {
  const items = (result && result.content) || [];
  return items
    .filter((item) => item && item.type === "text")
    .map((item) => item.text)
    .join("\n");
}

const TOKEN_OK = "0123456789abcdef0123456789abcdef";
const TOKEN_NEW = "fedcba9876543210fedcba9876543210";
const NO_TOKEN_MESSAGE =
  "Paste the token FreeCAD showed into Bearer token, or make a new one with Generate new token in FreeCAD's MCP Bridge preferences.";
const INVALID_TOKEN_MESSAGE =
  "The token in Bearer token is not a valid bearer token. Only letters, digits and - . _ ~ + / are allowed, with = at the end. Paste the token FreeCAD showed, or make a new one with Generate new token in FreeCAD's MCP Bridge preferences.";
const REFUSED_TOKEN_MESSAGE =
  "FreeCAD refused the pasted token. Check that it matches FreeCAD's token.";

test("with no token in Bearer token, a tool call says to paste the token FreeCAD showed or generate a new one", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: "" });
  fs.mkdirSync(shim.tokenDir, { recursive: true });
  fs.writeFileSync(path.join(shim.tokenDir, "token-handover"), `${TOKEN_OK}\n`, {
    encoding: "utf8",
  });
  fs.writeFileSync(path.join(shim.tokenDir, "token"), `${TOKEN_OK}\n`, {
    encoding: "utf8",
  });
  shim.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "execute_python", arguments: { code: "print(1)" } },
    }) + "\n"
  );
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.isError, true);
  assert.equal(JSON.parse(reply.result.content[0].text).error, NO_TOKEN_MESSAGE);
  assert.equal(bridge.requests.length, 0);
  assert.equal(fs.existsSync(path.join(shim.tokenDir, "token.dpapi")), false);
  assert.equal(fs.existsSync(path.join(shim.tokenDir, "token.extension")), false);
  await shim.stop();
  bridge.close();
});

test("a paste is sent and nothing is written", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK });
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_OK}`);
  assert.equal(fs.existsSync(shim.tokenFile), false);
  assert.equal(fs.existsSync(path.join(shim.tokenDir, "token.dpapi")), false);
  assert.equal(fs.existsSync(path.join(shim.tokenDir, "token.extension")), false);
  assert.equal(fs.existsSync(path.join(shim.tokenDir, "token-handover")), false);
  assert.equal(fs.existsSync(path.join(shim.tokenDir, "pasted")), false);
  await shim.stop();
  bridge.close();
});

test("a paste that is not a bearer token is not sent, and a short one is", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: "not a token" });
  fs.mkdirSync(shim.tokenDir, { recursive: true });
  fs.writeFileSync(path.join(shim.tokenDir, "token-handover"), `${TOKEN_OK}\n`, {
    encoding: "utf8",
  });
  shim.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "execute_python", arguments: { code: "print(1)" } },
    }) + "\n"
  );
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.isError, true);
  assert.equal(JSON.parse(reply.result.content[0].text).error, INVALID_TOKEN_MESSAGE);
  assert.equal(bridge.requests.length, 0);
  assert.equal(
    fs.readFileSync(path.join(shim.tokenDir, "token-handover"), "utf8").trim(),
    TOKEN_OK
  );
  assert.equal(fs.existsSync(path.join(shim.tokenDir, "token.dpapi")), false);
  assert.equal(fs.existsSync(path.join(shim.tokenDir, "token.extension")), false);
  await shim.stop();
  const short = startShim(bridge.port, { FREECAD_MCP_TOKEN: "ab" });
  short.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 2,
      method: "tools/call",
      params: { name: "execute_python", arguments: { code: "print(1)" } },
    }) + "\n"
  );
  const [ok] = await short.waitForLines(1);
  assert.equal(ok.result.echoed, "tools/call");
  assert.equal(bridge.requests[0].headers.authorization, "Bearer ab");
  await short.stop();
  bridge.close();
});

test("a 401 says the pasted token was refused", async () => {
  const bridge = await startBridge(() => ({
    status: 401,
    type: "text/plain",
    text: "Unauthorized\n",
  }));
  const shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK });
  shim.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "execute_python", arguments: { code: "print(1)" } },
    }) + "\n"
  );
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.isError, true);
  assert.equal(JSON.parse(reply.result.content[0].text).error, REFUSED_TOKEN_MESSAGE);
  assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_OK}`);
  await shim.stop();
  bridge.close();
});

test("FREECAD_MCP_TOKEN_DIR selects the folder for serving-cert.pem", async () => {
  const port = await closedPort();
  const override = fs.mkdtempSync(path.join(os.tmpdir(), "shim-token-dir-"));
  const nested = path.join(override, "nested");
  fs.mkdirSync(nested, { recursive: true });
  fs.writeFileSync(path.join(nested, "serving-cert.pem"), "not a certificate\n");
  const shim = startShim(
    port,
    { FREECAD_MCP_TOKEN: TOKEN_OK },
    { tokenDir: nested }
  );
  shim.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "execute_python", arguments: { code: "print(1)" } },
    }) + "\n"
  );
  const [reply] = await shim.waitForLines(1);
  const error = JSON.parse(reply.result.content[0].text).error;
  assert.match(error, new RegExp(`https://127\\.0\\.0\\.1:${port}/mcp`));
  assert.equal(
    fs.existsSync(path.join(shim.home, ".freecad-mcp-bridge", "serving-cert.pem")),
    false
  );
  await shim.stop();
  fs.rmSync(override, { recursive: true, force: true });
});



test("on this machine, with no serving-cert.pem, the shim uses HTTP whatever the certificate setting is, and the user sees the tool result", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-loopback-cert-ignored-"));
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "server", "127.0.0.1");
    const httpBridge = await startBridge(echo);
    for (const host of ["127.0.0.1", "localhost", "127.0.0.2"]) {
      const shim = startShim(
        httpBridge.port,
        {
          FREECAD_MCP_HOST: host === "127.0.0.2" ? "127.0.0.1" : host,
          FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
        },
        { seedToken: TOKEN_OK }
      );
      shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
      const [reply] = await shim.waitForLines(1);
      assert.equal(reply.result.echoed, "tools/call");
      assert.equal(httpBridge.requests.at(-1).headers["authorization"], `Bearer ${TOKEN_OK}`);
      await shim.stop();
    }
    const missingCertShim = startShim(
      httpBridge.port,
      { FREECAD_MCP_TLS_CERT_FILE: path.join(tmpDir, "does-not-exist.pem") },
      { seedToken: TOKEN_OK }
    );
    missingCertShim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call" }) + "\n");
    const [missingReply] = await missingCertShim.waitForLines(1);
    assert.equal(missingReply.result.echoed, "tools/call");
    await missingCertShim.stop();
    httpBridge.close();

    const bridgeV6 = await startBridge(echo, null, "::1");
    const shimV6 = startShim(
      bridgeV6.port,
      {
        FREECAD_MCP_HOST: "::1",
        FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
      },
      { seedToken: TOKEN_OK }
    );
    shimV6.send(JSON.stringify({ jsonrpc: "2.0", id: 3, method: "tools/call" }) + "\n");
    const [replyV6] = await shimV6.waitForLines(1);
    assert.equal(replyV6.result.echoed, "tools/call");
    await shimV6.stop();
    bridgeV6.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("on this machine, with serving-cert.pem present, the shim uses HTTPS trusting only that file, and the user sees the tool result", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-serving-present-"));
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "server", "127.0.0.1");
    const otherCert = makeSelfSignedCert(tmpDir, "other", "127.0.0.1");
    const bridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    const shim = startShim(
      bridge.port,
      { FREECAD_MCP_TLS_CERT_FILE: otherCert.certPath },
      { seedToken: TOKEN_OK }
    );
    fs.copyFileSync(serverCert.certPath, path.join(shim.tokenDir, "serving-cert.pem"));
    shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
    const [reply] = await shim.waitForLines(1);
    assert.equal(reply.result.echoed, "tools/call");
    assert.equal(reply.result.isError, undefined);
    assert.equal(JSON.stringify(reply), JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      result: { echoed: "tools/call" },
    }));
    assert.equal(bridge.requests.length, 1);
    assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_OK}`);
    await shim.stop();
    bridge.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("on this machine, a stale serving-cert.pem with nothing listening says the bridge is unreachable", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-stale-cert-"));
  try {
    const staleCert = makeSelfSignedCert(tmpDir, "stale", "127.0.0.1");
    const port = await closedPort();
    const shim = startShim(port, {}, { seedToken: TOKEN_OK });
    fs.copyFileSync(staleCert.certPath, path.join(shim.tokenDir, "serving-cert.pem"));
    shim.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [reply] = await shim.waitForLines(1);
    const error = JSON.parse(reply.result.content[0].text).error;
    assert.equal(
      error,
      "FreeCAD isn't reachable. Start FreeCAD and turn the bridge on with the MCP Bridge toolbar button, then try again. " +
        `(https://127.0.0.1:${port}/mcp: connect ECONNREFUSED 127.0.0.1:${port})`
    );
    assert.doesNotMatch(error, /TLS certificate file/);
    await shim.stop();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("plain HTTP against a loopback port that speaks HTTPS names the missing serving-cert.pem, and a closed port does not", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-https-plain-"));
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "server", "127.0.0.1");
    const httpsBridge = await startBridge(() => ({ status: 200, text: "" }), {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    const shim = startShim(httpsBridge.port);
    shim.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [reply] = await shim.waitForLines(1);
    const error = JSON.parse(reply.result.content[0].text).error;
    const endpoint = `https://127.0.0.1:${httpsBridge.port}/mcp`;
    assert.equal(
      error,
      `MCP Bridge serves HTTPS at ${endpoint}, and this connection was plain HTTP. ` +
        "On this machine the extension trusts serving-cert.pem in the bridge's folder, which is not there. Turn the bridge off and on again in FreeCAD, so it writes that file, and check that FREECAD_MCP_TOKEN_DIR, if set, is the same for both."
    );
    assert.doesNotMatch(error, /isn't reachable/);
    await shim.stop();
    httpsBridge.close();

    const port = await closedPort();
    const down = startShim(port);
    down.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [downReply] = await down.waitForLines(1);
    const downError = JSON.parse(downReply.result.content[0].text).error;
    assert.match(downError, /Start FreeCAD and turn the bridge on/);
    assert.doesNotMatch(downError, /serves HTTPS/);
    await down.stop();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("with no certificate set, an HTTPS port answers initialize and tools/list, and tools/call returns the HTTPS message", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-https-offline-"));
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "server", "127.0.0.1");
    const httpsBridge = await startBridge(() => ({ status: 200, text: "" }), {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    const shim = startShim(httpsBridge.port);
    const endpoint = `https://127.0.0.1:${httpsBridge.port}/mcp`;
    shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }) + "\n");
    shim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" }) + "\n");
    shim.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 3,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const replies = await shim.waitForLines(3);
    const initReply = replies.find((r) => r.id === 1);
    const listReply = replies.find((r) => r.id === 2);
    const callReply = replies.find((r) => r.id === 3);
    assert.equal(initReply.error, undefined);
    assert.equal(initReply.result.protocolVersion, "2025-11-25");
    assert.equal(initReply.result.serverInfo.name, "freecad-mcp-bridge");
    assert.deepEqual(listReply.result.tools, readJson(path.join(SHIM_DIR, "tools.json")));
    assert.equal(listReply.error, undefined);
    const error = JSON.parse(callReply.result.content[0].text).error;
    assert.equal(
      error,
      `MCP Bridge serves HTTPS at ${endpoint}, and this connection was plain HTTP. ` +
        "On this machine the extension trusts serving-cert.pem in the bridge's folder, which is not there. Turn the bridge off and on again in FreeCAD, so it writes that file, and check that FREECAD_MCP_TOKEN_DIR, if set, is the same for both."
    );
    await shim.stop();
    httpsBridge.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("a plain HTTP reply slower than 2 s still succeeds", async () => {
  const bridge = await startBridge(async ({ body }) => {
    await delay(3000);
    const message = JSON.parse(body);
    return {
      text: JSON.stringify({
        jsonrpc: "2.0",
        id: message.id,
        result: { echoed: message.method },
      }),
    };
  });
  const shim = startShim(bridge.port);
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.doesNotMatch(JSON.stringify(reply), /isn't reachable/);
  await shim.stop();
  bridge.close();
});

function makeDnsPreload(dir, hostname, ip) {
  const preload = path.join(dir, "dns-preload.js");
  fs.writeFileSync(
    preload,
    `const dns = require("node:dns");
const orig = dns.lookup;
dns.lookup = function(name, opts, cb) {
  if (typeof opts === "function") { cb = opts; opts = {}; }
  if (name === ${JSON.stringify(hostname)}) {
    if (opts && opts.all) return process.nextTick(() => cb(null, [{ address: ${JSON.stringify(ip)}, family: 4 }]));
    return process.nextTick(() => cb(null, ${JSON.stringify(ip)}, 4));
  }
  return orig.call(this, name, opts, cb);
};
`,
    "utf8"
  );
  return preload;
}

test("localhost in any letter case, ::1, and 127.x.x.x are treated as this machine: plain HTTP, and a pasted token is not written to the token file", async () => {
  // 1. "localhost" and "LocalHost" connect to 127.0.0.1 over plain HTTP.
  // The token file is not read, and a paste is not written back.
  const bridgeV4 = await startBridge(echo, null, "127.0.0.1");
  for (const spelling of ["localhost", "LocalHost", "LOCALHOST"]) {
    const readShim = startShim(bridgeV4.port, { FREECAD_MCP_HOST: spelling }, { seedToken: TOKEN_OK });
    readShim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
    const [readReply] = await readShim.waitForLines(1);
    assert.equal(readReply.result.echoed, "tools/call");
    assert.equal(bridgeV4.requests.at(-1).headers["authorization"], `Bearer ${TOKEN_OK}`);
    assert.equal(bridgeV4.requests.at(-1).headers["host"], `127.0.0.1:${bridgeV4.port}`);
    await readShim.stop();

    const writeShim = startShim(
      bridgeV4.port,
      { FREECAD_MCP_HOST: spelling, FREECAD_MCP_TOKEN: TOKEN_NEW },
      { seedToken: TOKEN_OK }
    );
    writeShim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call" }) + "\n");
    const [writeReply] = await writeShim.waitForLines(1);
    assert.equal(writeReply.result.echoed, "tools/call");
    assert.equal(bridgeV4.requests.at(-1).headers["authorization"], `Bearer ${TOKEN_NEW}`);
    assert.equal(fs.readFileSync(writeShim.tokenFile, "utf8").trim(), TOKEN_OK);
    await writeShim.stop();
  }
  bridgeV4.close();

  // 2. "::1" and "[::1]" connect to [::1] over plain HTTP. A paste is
  // sent and the token file is left as it was.
  const bridgeV6 = await startBridge(echo, null, "::1");
  for (const spelling of ["::1", "[::1]"]) {
    const readShim = startShim(bridgeV6.port, { FREECAD_MCP_HOST: spelling }, { seedToken: TOKEN_OK });
    readShim.send(JSON.stringify({ jsonrpc: "2.0", id: 3, method: "tools/call" }) + "\n");
    const [readReply] = await readShim.waitForLines(1);
    assert.equal(readReply.result.echoed, "tools/call");
    assert.equal(bridgeV6.requests.at(-1).headers["authorization"], `Bearer ${TOKEN_OK}`);
    assert.equal(bridgeV6.requests.at(-1).headers["host"], `[::1]:${bridgeV6.port}`);
    await readShim.stop();

    const writeShim = startShim(
      bridgeV6.port,
      { FREECAD_MCP_HOST: spelling, FREECAD_MCP_TOKEN: TOKEN_NEW },
      { seedToken: TOKEN_OK }
    );
    writeShim.send(JSON.stringify({ jsonrpc: "2.0", id: 4, method: "tools/call" }) + "\n");
    const [writeReply] = await writeShim.waitForLines(1);
    assert.equal(writeReply.result.echoed, "tools/call");
    assert.equal(bridgeV6.requests.at(-1).headers["authorization"], `Bearer ${TOKEN_NEW}`);
    assert.equal(fs.readFileSync(writeShim.tokenFile, "utf8").trim(), TOKEN_OK);
    await writeShim.stop();
  }
  bridgeV6.close();

  // 3. Another 127.x.x.x address (127.0.0.2; falls back to 127.0.0.1 if the
  // OS does not alias 127.0.0.2 on loopback, such as default macOS lo0).
  let bridgeAlt = null;
  try {
    bridgeAlt = await startBridge(echo, null, "127.0.0.2");
  } catch {
    bridgeAlt = null;
  }
  if (bridgeAlt) {
    const readShim = startShim(
      bridgeAlt.port,
      { FREECAD_MCP_HOST: "127.0.0.2" },
      { seedToken: TOKEN_OK }
    );
    readShim.send(JSON.stringify({ jsonrpc: "2.0", id: 5, method: "tools/call" }) + "\n");
    const [readReply] = await readShim.waitForLines(1);
    assert.equal(readReply.result.echoed, "tools/call");
    assert.equal(bridgeAlt.requests.at(-1).headers["authorization"], `Bearer ${TOKEN_OK}`);
    assert.equal(bridgeAlt.requests.at(-1).headers["host"], `127.0.0.2:${bridgeAlt.port}`);
    await readShim.stop();

    const writeShim = startShim(
      bridgeAlt.port,
      { FREECAD_MCP_HOST: "127.0.0.2", FREECAD_MCP_TOKEN: TOKEN_NEW },
      { seedToken: TOKEN_OK }
    );
    writeShim.send(JSON.stringify({ jsonrpc: "2.0", id: 6, method: "tools/call" }) + "\n");
    const [writeReply] = await writeShim.waitForLines(1);
    assert.equal(writeReply.result.echoed, "tools/call");
    assert.equal(bridgeAlt.requests.at(-1).headers["authorization"], `Bearer ${TOKEN_NEW}`);
    assert.equal(fs.readFileSync(writeShim.tokenFile, "utf8").trim(), TOKEN_OK);
    await writeShim.stop();
    bridgeAlt.close();
  }
});

test("for another machine, the shim connects over HTTPS trusting only the given certificate, uses the pasted token, and does not write the local token file", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-remote-ok-"));
  try {
    const preload = makeDnsPreload(tmpDir, "freecad.lan", "127.0.0.1");
    const serverCert = makeSelfSignedCert(tmpDir, "remote", "127.0.0.1", "freecad.lan");
    const bridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    const shim = startShim(
      bridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_NEW,
        FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
      },
      { seedToken: TOKEN_OK, nodeArgs: ["--require", preload] }
    );
    shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
    const [reply] = await shim.waitForLines(1);
    assert.equal(reply.result.echoed, "tools/call");
    assert.equal(pastedText(reply.result), "");
    assert.equal(bridge.requests.length, 1);
    assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_NEW}`);
    assert.equal(bridge.requests[0].headers["host"], `freecad.lan:${bridge.port}`);
    // The local token file stays untouched and no pasted record is written.
    assert.equal(fs.readFileSync(shim.tokenFile, "utf8").trim(), TOKEN_OK);
    assert.equal(fs.existsSync(path.join(shim.tokenDir, "pasted")), false);
    await shim.stop();
    bridge.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("for another machine, a tools/call without a certificate or without a pasted token fails clearly without contacting the bridge", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-remote-missing-"));
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "remote", "127.0.0.1", "freecad.lan");
    const bridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });

    // 1. Missing certificate file.
    const noCert = startShim(
      bridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: "",
      },
      { prepareTokenDir: false }
    );
    noCert.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }) + "\n");
    noCert.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const noCertReplies = await noCert.waitForLines(2);
    assert.equal(noCertReplies.find((r) => r.id === 1).error, undefined);
    const noCertCall = noCertReplies.find((r) => r.id === 2);
    assert.equal(noCertCall.result.isError, true);
    assert.equal(
      JSON.parse(noCertCall.result.content[0].text).error,
      "FreeCAD host is set to freecad.lan, which requires HTTPS. " +
        "Set TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings to a copy of FreeCAD's certificate file, not the private key."
    );
    assert.equal(fs.existsSync(noCert.tokenDir), false);
    await noCert.stop();

    // 2. Missing pasted token (even when a local token file exists).
    const noToken = startShim(
      bridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: "",
        FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
      },
      { seedToken: TOKEN_OK }
    );
    noToken.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 3,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [noTokenCall] = await noToken.waitForLines(1);
    assert.equal(noTokenCall.result.isError, true);
    assert.equal(
      JSON.parse(noTokenCall.result.content[0].text).error,
      "Paste the token FreeCAD showed into Bearer token, or make a new one with Generate new token in FreeCAD's MCP Bridge preferences."
    );
    assert.equal(bridge.requests.length, 0);
    await noToken.stop();
    bridge.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("for another machine, a mismatched certificate, a missing or invalid certificate file, or a 401 names the remote setting", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-remote-fail-"));
  const servers = [];
  const shims = [];
  try {
    const preload = makeDnsPreload(tmpDir, "freecad.lan", "127.0.0.1");
    const serverCert = makeSelfSignedCert(tmpDir, "remote", "127.0.0.1", "freecad.lan");
    const otherCert = makeSelfSignedCert(tmpDir, "other", "127.0.0.1", "freecad.lan");

    // 1. Different certificate than the bridge serves: refused before any
    // HTTP request bytes are sent.
    const httpsBridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    servers.push(httpsBridge);
    const untrusted = startShim(
      httpsBridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: otherCert.certPath,
      },
      { prepareTokenDir: false, nodeArgs: ["--require", preload] }
    );
    shims.push(untrusted);
    untrusted.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [untrustedReply] = await untrusted.waitForLines(1);
    const untrustedError = JSON.parse(untrustedReply.result.content[0].text).error;
    assert.equal(
      untrustedError,
      `MCP Bridge TLS connection failed at https://freecad.lan:${httpsBridge.port}/mcp: the bridge's certificate does not match TLS certificate file (PEM) at ${otherCert.certPath}. ` +
        "Set TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings to a copy of FreeCAD's current certificate file, not the private key."
    );
    assert.equal(httpsBridge.requests.length, 0);
    assert.equal(Buffer.concat(httpsBridge.rawChunks).length, 0);

    // 2. TLS failure of another kind (plain HTTP server on the remote port).
    const plainRemoteBridge = await startBridge(echo, null, "127.0.0.1");
    servers.push(plainRemoteBridge);
    const nonTls = startShim(
      plainRemoteBridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
      },
      { prepareTokenDir: false, nodeArgs: ["--require", preload] }
    );
    shims.push(nonTls);
    nonTls.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [nonTlsReply] = await nonTls.waitForLines(1);
    const nonTlsError = JSON.parse(nonTlsReply.result.content[0].text).error;
    assert.match(
      nonTlsError,
      new RegExp(`^MCP Bridge TLS connection failed at https://freecad\\.lan:${plainRemoteBridge.port}/mcp \\([\\s\\S]+\\)\\. `)
    );
    assert.match(
      nonTlsError,
      /Check that FreeCAD is serving HTTPS at that address and that TLS certificate file \(PEM\) in the MCP Bridge for FreeCAD extension settings is a copy of FreeCAD's certificate file, not the private key\./
    );

    // 3. 401 Unauthorized from the remote bridge.
    const authBridge = await startBridge(
      () => ({ status: 401, type: "text/plain", text: "Unauthorized\n" }),
      { cert: serverCert.cert, key: serverCert.key }
    );
    servers.push(authBridge);
    const unauthorized = startShim(
      authBridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
      },
      { prepareTokenDir: false, nodeArgs: ["--require", preload] }
    );
    shims.push(unauthorized);
    unauthorized.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 3,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [authReply] = await unauthorized.waitForLines(1);
    const authError = JSON.parse(authReply.result.content[0].text).error;
    assert.equal(
      authError,
      "FreeCAD refused the pasted token. Check that it matches FreeCAD's token."
    );
  } finally {
    for (const s of shims) await s.stop();
    for (const b of servers) b.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("1. a server with a self-signed certificate marked CA false is reached when its certificate is the pinned copy", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-pin-ca-false-"));
  let bridge = null;
  let shim = null;
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "ca-false", "192.0.2.1");
    const extraCert = makeSelfSignedCert(tmpDir, "extra", "127.0.0.1");
    const x509 = new crypto.X509Certificate(serverCert.cert);
    assert.equal(x509.ca, false);

    bridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    shim = startShim(bridge.port, {}, { seedToken: TOKEN_OK });
    // Even with a second certificate appended in serving-cert.pem and a SAN
    // that does not name 127.0.0.1, pinning the first certificate's SHA-256
    // fingerprint succeeds.
    fs.writeFileSync(
      path.join(shim.tokenDir, "serving-cert.pem"),
      Buffer.concat([serverCert.cert, extraCert.cert])
    );
    shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
    const [reply] = await shim.waitForLines(1);
    assert.equal(reply.result.echoed, "tools/call");
    assert.equal(bridge.requests.length, 1);
    assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_OK}`);
    assert.ok(Buffer.concat(bridge.rawChunks).length > 0);
    // One connection carries one request, and the shim, still running,
    // holds none open after the reply.
    assert.equal(bridge.requests[0].headers["connection"], "close");
    for (let i = 0; i < 40 && bridge.openTlsSockets.size > 0; i++) {
      await delay(50);
    }
    assert.equal(bridge.openTlsSockets.size, 0);
  } finally {
    if (shim) await shim.stop();
    if (bridge) bridge.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("2. a server presenting a different certificate is refused, and the server receives no request bytes", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-pin-mismatch-"));
  let bridge = null;
  let shim = null;
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "server", "127.0.0.1");
    const pinnedCert = makeSelfSignedCert(tmpDir, "pinned", "127.0.0.1");
    bridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK });
    const servingCertPath = path.join(shim.tokenDir, "serving-cert.pem");
    fs.copyFileSync(pinnedCert.certPath, servingCertPath);
    shim.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [reply] = await shim.waitForLines(1);
    await delay(50);
    assert.equal(reply.result.isError, true);
    const error = JSON.parse(reply.result.content[0].text).error;
    assert.equal(
      error,
      `MCP Bridge TLS connection failed at https://127.0.0.1:${bridge.port}/mcp: the bridge's certificate does not match serving-cert.pem at ${servingCertPath}. ` +
        "Turn the bridge off and on again in FreeCAD, so it rewrites serving-cert.pem in the bridge's folder, then try again."
    );
    assert.equal(bridge.requests.length, 0);
    assert.equal(Buffer.concat(bridge.rawChunks).length, 0);
  } finally {
    if (shim) await shim.stop();
    if (bridge) bridge.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("3. a copy that is missing, unreadable, or not a certificate gives the right error", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-pin-bad-copy-"));
  let bridge = null;
  const shims = [];
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "server", "127.0.0.1");
    bridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });

    // 1. On this machine, serving-cert.pem is not a PEM certificate: plain
    // text, a private key, a block that is not base64, the four bytes
    // 30 02 30 00 wrapped as a PEM certificate (a DER sequence that is not
    // a certificate), or the server's own certificate as bare DER.
    const notACertificate =
      "-----BEGIN CERTIFICATE-----\n" +
      Buffer.from([0x30, 0x02, 0x30, 0x00]).toString("base64") +
      "\n-----END CERTIFICATE-----\n";
    const bareDer = new crypto.X509Certificate(serverCert.cert).raw;
    for (const badContent of [
      "not a certificate\n",
      serverCert.key.toString("utf8"),
      "-----BEGIN CERTIFICATE-----\nnot-der\n-----END CERTIFICATE-----\n",
      notACertificate,
      bareDer,
    ]) {
      const shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK });
      shims.push(shim);
      const servingCertPath = path.join(shim.tokenDir, "serving-cert.pem");
      fs.writeFileSync(servingCertPath, badContent);
      shim.send(
        JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          method: "tools/call",
          params: { name: "execute_python", arguments: { code: "print(1)" } },
        }) + "\n"
      );
      const [reply] = await shim.waitForLines(1);
      assert.equal(reply.result.isError, true);
      const error = JSON.parse(reply.result.content[0].text).error;
      assert.equal(
        error,
        `serving-cert.pem at ${servingCertPath} for https://127.0.0.1:${bridge.port}/mcp is not a PEM certificate. ` +
          "Turn the bridge off and on again in FreeCAD, so it rewrites serving-cert.pem in the bridge's folder."
      );
    }

    // 2. On this machine, serving-cert.pem cannot be read (e.g. is a directory).
    const dirShim = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK });
    shims.push(dirShim);
    const dirCertPath = path.join(dirShim.tokenDir, "serving-cert.pem");
    fs.mkdirSync(dirCertPath, { recursive: true });
    dirShim.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [dirReply] = await dirShim.waitForLines(1);
    assert.equal(dirReply.result.isError, true);
    const dirError = JSON.parse(dirReply.result.content[0].text).error;
    assert.match(
      dirError,
      new RegExp(
        `^Could not read serving-cert\\.pem at .+ for https://127\\.0\\.0\\.1:${bridge.port}/mcp \\(.*EISDIR.*\\)\\. ` +
          "Turn the bridge off and on again in FreeCAD, so it writes that file, and check that it is readable by you\\.$"
      )
    );

    // 3. For another host, TLS certificate file (PEM) is missing.
    const missingPath = path.join(tmpDir, "missing-cert.pem");
    const missingRemote = startShim(
      bridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: missingPath,
      },
      { prepareTokenDir: false }
    );
    shims.push(missingRemote);
    missingRemote.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 3,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [missingReply] = await missingRemote.waitForLines(1);
    assert.equal(missingReply.result.isError, true);
    const missingError = JSON.parse(missingReply.result.content[0].text).error;
    assert.match(
      missingError,
      new RegExp(
        `^Could not read TLS certificate file \\(PEM\\) at .+ for https://freecad\\.lan:${bridge.port}/mcp \\(.*ENOENT.*\\)\\. ` +
          "Set TLS certificate file \\(PEM\\) in the MCP Bridge for FreeCAD extension settings to a readable copy of FreeCAD's certificate file, not the private key\\.$"
      )
    );

    // 4. For another host, TLS certificate file (PEM) is not a certificate (e.g. private key).
    const keyRemote = startShim(
      bridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: serverCert.keyPath,
      },
      { prepareTokenDir: false }
    );
    shims.push(keyRemote);
    keyRemote.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 4,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [keyReply] = await keyRemote.waitForLines(1);
    assert.equal(keyReply.result.isError, true);
    const keyError = JSON.parse(keyReply.result.content[0].text).error;
    assert.equal(
      keyError,
      `TLS certificate file (PEM) at ${serverCert.keyPath} for https://freecad.lan:${bridge.port}/mcp is not a PEM certificate. ` +
        "Set TLS certificate file (PEM) in the MCP Bridge for FreeCAD extension settings to a copy of FreeCAD's certificate file, not the private key."
    );

    assert.equal(bridge.requests.length, 0);
    assert.equal(Buffer.concat(bridge.rawChunks).length, 0);
  } finally {
    for (const s of shims) await s.stop();
    if (bridge) bridge.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("4. the another-host setting takes the same fingerprint-pinning path", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-pin-remote-"));
  let bridge = null;
  const shims = [];
  try {
    const preload = makeDnsPreload(tmpDir, "freecad.lan", "127.0.0.1");
    // Certificate marked CA:FALSE whose SAN does not list freecad.lan.
    const serverCert = makeSelfSignedCert(tmpDir, "remote-ca-false", "192.0.2.1");
    const otherCert = makeSelfSignedCert(tmpDir, "other", "192.0.2.1");
    assert.equal(new crypto.X509Certificate(serverCert.cert).ca, false);

    bridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });

    // Wrong pinned certificate on another host: refused with 0 request bytes sent.
    const wrongShim = startShim(
      bridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: otherCert.certPath,
      },
      { prepareTokenDir: false, nodeArgs: ["--require", preload] }
    );
    shims.push(wrongShim);
    wrongShim.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [wrongReply] = await wrongShim.waitForLines(1);
    await delay(50);
    assert.equal(wrongReply.result.isError, true);
    assert.equal(bridge.requests.length, 0);
    assert.equal(Buffer.concat(bridge.rawChunks).length, 0);

    // Matching pinned certificate on another host: succeeds despite CA:FALSE and
    // unlisted host name.
    const okShim = startShim(
      bridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_NEW,
        FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
      },
      { prepareTokenDir: false, nodeArgs: ["--require", preload] }
    );
    shims.push(okShim);
    okShim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call" }) + "\n");
    const [okReply] = await okShim.waitForLines(1);
    assert.equal(okReply.result.echoed, "tools/call");
    assert.equal(bridge.requests.length, 1);
    assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_NEW}`);
    assert.ok(Buffer.concat(bridge.rawChunks).length > 0);
  } finally {
    for (const s of shims) await s.stop();
    if (bridge) bridge.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

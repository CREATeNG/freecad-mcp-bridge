// Requirements on the stdio shim (mcp-stdio-shim/index.js).
//
// Each test name states one requirement as an MCP client that speaks stdio
// experiences it. The shim runs as a child process; a stand-in HTTP server
// plays the bridge, or a closed port plays a bridge that is not running.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn, execFileSync } = require("node:child_process");
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
async function startBridge(reply, tlsOptions = null) {
  const requests = [];
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
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { port: server.address().port, requests, close: () => server.close() };
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
  const tokenDir = path.join(home, ".freecad-mcp-bridge");
  const tokenFile = path.join(tokenDir, "token");
  const pasted = extraEnv.FREECAD_MCP_TOKEN || "";
  if (options.seedToken) {
    fs.mkdirSync(tokenDir);
    fs.writeFileSync(tokenFile, `${options.seedToken}\n`, { encoding: "utf8", mode: 0o600 });
  } else if (options.prepareTokenDir !== false && pasted.length >= 32) {
    fs.mkdirSync(tokenDir);
  }
  const env = {
    ...process.env,
    FREECAD_MCP_PORT: String(port),
    HOME: home,
    USERPROFILE: home,
  };
  delete env.FREECAD_MCP_TOKEN;
  delete env.FREECAD_MCP_TLS_CERT_FILE;
  Object.assign(env, extraEnv);
  const child = spawn(process.execPath, [SHIM], {
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

function makeSelfSignedCert(dir, prefix, sanIp = "127.0.0.1") {
  const certPath = path.join(dir, `${prefix}-cert.pem`);
  const keyPath = path.join(dir, `${prefix}-key.pem`);
  const cnfPath = path.join(dir, `${prefix}-openssl.cnf`);
  fs.writeFileSync(
    cnfPath,
    `[req]\ndistinguished_name = dn\nx509_extensions = v3\nprompt = no\n[dn]\nCN = ${sanIp}\n[v3]\nsubjectAltName = IP:${sanIp}\n`,
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

test("manifest.json declares optional port, sensitive bearer token, and TLS certificate file", () => {
  const manifest = readJson(path.join(SHIM_DIR, "manifest.json"));
  const env = manifest.server.mcp_config.env;
  assert.deepEqual(env, {
    FREECAD_MCP_PORT: "${user_config.port}",
    FREECAD_MCP_TOKEN: "${user_config.token}",
    FREECAD_MCP_TLS_CERT_FILE: "${user_config.tls_cert_file}",
  });
  const cfg = manifest.user_config;
  assert.equal(cfg.token.type, "string");
  assert.equal(cfg.token.sensitive, true);
  assert.equal(cfg.token.required, false);
  assert.equal(cfg.token.title, "Bearer token");
  assert.match(cfg.token.description, /at least 32 characters/);
  assert.match(cfg.token.description, /token file/);
  assert.equal(cfg.tls_cert_file.type, "file");
  assert.equal(cfg.tls_cert_file.required, false);
  assert.equal(cfg.tls_cert_file.title, "TLS certificate file (PEM)");
  assert.match(cfg.tls_cert_file.description, /127\.0\.0\.1/);
  assert.match(cfg.tls_cert_file.description, /HTTP on loopback/);
});

const TOKEN_OK = "0123456789abcdef0123456789abcdef";

test("with neither token nor certificate set, the shim sends plain HTTP with no Authorization header", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port, {
    FREECAD_MCP_TOKEN: "",
    FREECAD_MCP_TLS_CERT_FILE: "",
  });
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.equal(bridge.requests[0].headers["authorization"], undefined);
  await shim.stop();
  bridge.close();
});

test("with FREECAD_MCP_TOKEN set, the shim writes the token file and sends it", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK });
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_OK}`);
  assert.equal(fs.readFileSync(shim.tokenFile, "utf8").trim(), TOKEN_OK);
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(shim.tokenFile).mode & 0o777, 0o600);
  }
  await shim.stop();
  bridge.close();
});

test("a tools/call sends the token already in the file when nothing was pasted", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port, {}, { seedToken: TOKEN_OK });
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_OK}`);
  await shim.stop();
  bridge.close();
});

test("a pasted token shorter than 32 characters is ignored and not written", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: "short-token" });
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
  assert.match(error, /shorter than 32 characters/);
  assert.equal(bridge.requests.length, 0);
  assert.equal(fs.existsSync(shim.tokenFile), false);
  await shim.stop();
  bridge.close();
});

test("a pasted token is not written when the token folder does not exist yet", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(
    bridge.port,
    { FREECAD_MCP_TOKEN: TOKEN_OK },
    { prepareTokenDir: false }
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
  assert.equal(reply.result.isError, true);
  const error = JSON.parse(reply.result.content[0].text).error;
  assert.match(error, /Turn the bridge on in FreeCAD once first/);
  assert.equal(bridge.requests.length, 0);
  assert.equal(fs.existsSync(shim.tokenDir), false);
  await shim.stop();
  bridge.close();
});

test("a 401 Unauthorized from the bridge fails with a clear message naming the extension's Bearer token setting", async () => {
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
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "ping" }) + "\n");
  const replies = await shim.waitForLines(2);
  const toolReply = replies.find((r) => r.id === 1);
  const pingReply = replies.find((r) => r.id === 2);
  assert.equal(toolReply.result.isError, true);
  const toolError = JSON.parse(toolReply.result.content[0].text).error;
  assert.match(toolError, /401 Unauthorized/);
  assert.match(toolError, /Set Bearer token in the FreeCAD MCP Bridge extension settings/);
  assert.match(toolError, /Generate new token/);
  assert.doesNotMatch(toolError, /FREECAD_MCP_TOKEN/);
  assert.deepEqual(pingReply.result, {});
  assert.equal(pingReply.error, undefined);
  assert.equal(bridge.requests.length, 1);
  await shim.stop();
  bridge.close();
});

test("with FREECAD_MCP_TLS_CERT_FILE set, the shim connects over HTTPS and trusts that certificate", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-tls-"));
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "server", "127.0.0.1");
    const bridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    const shim = startShim(bridge.port, {
      FREECAD_MCP_TOKEN: TOKEN_OK,
      FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
    });
    shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
    const [reply] = await shim.waitForLines(1);
    assert.equal(reply.result.echoed, "tools/call");
    assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_OK}`);
    await shim.stop();
    bridge.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("an untrusted certificate or a certificate set while the bridge serves plain HTTP fails with a clear message", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-tls-fail-"));
  try {
    const serverCert = makeSelfSignedCert(tmpDir, "server", "127.0.0.1");
    const otherCert = makeSelfSignedCert(tmpDir, "other", "127.0.0.1");

    // 1. HTTPS bridge with a different certificate than the shim trusts.
    const httpsBridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    const shimUntrusted = startShim(httpsBridge.port, {
      FREECAD_MCP_TLS_CERT_FILE: otherCert.certPath,
    });
    shimUntrusted.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [untrustedReply] = await shimUntrusted.waitForLines(1);
    assert.equal(untrustedReply.result.isError, true);
    const untrustedError = JSON.parse(untrustedReply.result.content[0].text).error;
    assert.match(untrustedError, /TLS connection failed/);
    assert.match(untrustedError, /TLS certificate file \(PEM\)/);
    assert.match(untrustedError, /covers 127\.0\.0\.1/);
    assert.match(untrustedError, /HTTP on loopback/);
    await shimUntrusted.stop();
    httpsBridge.close();

    // 2. Plain-HTTP bridge while a certificate is set.
    const httpBridge = await startBridge(echo);
    const shimPlain = startShim(httpBridge.port, {
      FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
    });
    shimPlain.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [plainReply] = await shimPlain.waitForLines(1);
    assert.equal(plainReply.result.isError, true);
    const plainError = JSON.parse(plainReply.result.content[0].text).error;
    assert.match(plainError, /TLS connection failed/);
    assert.match(plainError, /leave the certificate setting blank for a loopback connection/);
    await shimPlain.stop();
    httpBridge.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("plain HTTP against a loopback port that speaks HTTPS names the TLS settings, and a closed port does not", async () => {
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
      `FreeCAD MCP Bridge serves HTTPS at ${endpoint}, and this connection was plain HTTP. ` +
        "Set TLS certificate file (PEM) in the FreeCAD MCP Bridge extension settings to FreeCAD's certificate."
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
      `FreeCAD MCP Bridge serves HTTPS at ${endpoint}, and this connection was plain HTTP. ` +
        "Set TLS certificate file (PEM) in the FreeCAD MCP Bridge extension settings to FreeCAD's certificate."
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

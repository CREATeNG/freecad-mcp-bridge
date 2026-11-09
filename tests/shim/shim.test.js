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
async function startBridge(reply, tlsOptions = null, host = "127.0.0.1") {
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
  await new Promise((resolve) => server.listen(0, host, resolve));
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
  Object.assign(env, extraEnv);
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
    `[req]\ndistinguished_name = dn\nx509_extensions = v3\nprompt = no\n[dn]\nCN = ${sanDns || sanIp}\n[v3]\nsubjectAltName = ${sanEntries.join(",")}\n`,
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

test("manifest.json declares optional FreeCAD machine, port, sensitive bearer token, and TLS certificate file", () => {
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
  assert.equal(cfg.host.title, "FreeCAD machine");
  assert.match(cfg.host.description, /127\.0\.0\.1 by default/);
  assert.match(cfg.host.description, /localhost, ::1, and 127\.x\.x\.x are treated as loopback/);
  assert.equal(cfg.token.type, "string");
  assert.equal(cfg.token.sensitive, true);
  assert.equal(cfg.token.required, false);
  assert.equal(cfg.token.title, "Bearer token");
  assert.match(cfg.token.description, /Optional on loopback/);
  assert.match(cfg.token.description, /at least 32 characters/);
  assert.match(cfg.token.description, /replaces that file for every client/);
  assert.match(cfg.token.description, /Required beyond loopback/);
  assert.equal(cfg.tls_cert_file.type, "file");
  assert.equal(cfg.tls_cert_file.required, false);
  assert.equal(cfg.tls_cert_file.title, "TLS certificate file (PEM)");
  assert.match(cfg.tls_cert_file.description, /Leave blank on loopback/);
  assert.match(cfg.tls_cert_file.description, /trusts only that certificate/);
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
const { createHash } = require("node:crypto");
const pasteHash = (token) => createHash("sha256").update(token, "utf8").digest("hex");

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
  const pasteRecord = path.join(shim.tokenDir, "pasted");
  assert.equal(fs.readFileSync(pasteRecord, "utf8").trim(), pasteHash(TOKEN_OK));
  assert.notEqual(fs.readFileSync(pasteRecord, "utf8").trim(), TOKEN_OK);
  if (process.platform !== "win32") {
    assert.equal(fs.statSync(shim.tokenFile).mode & 0o777, 0o600);
    assert.equal(fs.statSync(pasteRecord).mode & 0o777, 0o600);
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

test("a pasted token shorter than 32 characters is a note, and the call still uses the file", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(
    bridge.port,
    { FREECAD_MCP_TOKEN: "short-token" },
    { seedToken: TOKEN_OK }
  );
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.equal(reply.result.note, undefined);
  assert.match(pastedText(reply.result), /shorter than 32 characters/);
  assert.equal(bridge.requests[0].headers["authorization"], `Bearer ${TOKEN_OK}`);
  assert.equal(fs.readFileSync(shim.tokenFile, "utf8").trim(), TOKEN_OK);
  await shim.stop();
  bridge.close();
});

test("a missing token folder is retried on the next call and does not block the first", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(
    bridge.port,
    { FREECAD_MCP_TOKEN: TOKEN_OK },
    { prepareTokenDir: false }
  );
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [first] = await shim.waitForLines(1);
  assert.equal(first.result.echoed, "tools/call");
  assert.equal(first.result.note, undefined);
  assert.match(
    pastedText(first.result),
    /The token folder doesn't exist yet\. Turn the bridge on in FreeCAD once, so it can create it\./
  );
  assert.equal(bridge.requests.length, 1);
  assert.equal(fs.existsSync(shim.tokenDir), false);
  fs.mkdirSync(shim.tokenDir);
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call" }) + "\n");
  const replies = await shim.waitForLines(2);
  const second = replies.find((reply) => reply.id === 2);
  assert.equal(second.result.echoed, "tools/call");
  assert.equal(second.result.note, undefined);
  assert.equal(pastedText(second.result), "");
  assert.equal(fs.readFileSync(shim.tokenFile, "utf8").trim(), TOKEN_OK);
  assert.equal(bridge.requests[1].headers["authorization"], `Bearer ${TOKEN_OK}`);
  await shim.stop();
  bridge.close();
});

test("a write error is a note and the call is still sent", async () => {
  const bridge = await startBridge(echo);
  const shim = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK });
  fs.rmSync(shim.tokenDir, { recursive: true, force: true });
  fs.writeFileSync(shim.tokenDir, "not a directory");
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.equal(reply.result.note, undefined);
  assert.match(pastedText(reply.result), /could not be written to the token file/);
  assert.match(pastedText(reply.result), /then try again/);
  assert.equal(bridge.requests.length, 1);
  await shim.stop();
  bridge.close();
});

test("the same paste does not replace a token FreeCAD generated afterwards", async () => {
  const bridge = await startBridge(echo);
  const first = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK });
  first.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  await first.waitForLines(1);
  await first.stop();
  fs.writeFileSync(path.join(first.tokenDir, "token"), `${TOKEN_NEW}\n`, "utf8");
  const second = startShim(bridge.port, { FREECAD_MCP_TOKEN: TOKEN_OK }, {
    tokenDir: first.tokenDir,
  });
  second.send(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call" }) + "\n");
  const [reply] = await second.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.equal(reply.result.note, undefined);
  assert.equal(pastedText(reply.result), "");
  assert.equal(fs.readFileSync(second.tokenFile, "utf8").trim(), TOKEN_NEW);
  assert.equal(bridge.requests.at(-1).headers["authorization"], `Bearer ${TOKEN_NEW}`);
  await second.stop();
  bridge.close();
});

test("FREECAD_MCP_TOKEN_DIR selects the folder, not the home folder", async () => {
  const bridge = await startBridge(echo);
  const override = fs.mkdtempSync(path.join(os.tmpdir(), "shim-token-dir-"));
  fs.mkdirSync(path.join(override, "nested"), { recursive: true });
  const shim = startShim(
    bridge.port,
    { FREECAD_MCP_TOKEN: TOKEN_OK },
    { tokenDir: path.join(override, "nested"), prepareTokenDir: true }
  );
  shim.send(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call" }) + "\n");
  const [reply] = await shim.waitForLines(1);
  assert.equal(reply.result.echoed, "tools/call");
  assert.equal(fs.readFileSync(shim.tokenFile, "utf8").trim(), TOKEN_OK);
  assert.equal(fs.existsSync(path.join(shim.home, ".freecad-mcp-bridge", "token")), false);
  await shim.stop();
  bridge.close();
  fs.rmSync(override, { recursive: true, force: true });
});

test("a 401 names the token file the extension used, with and without a token", async () => {
  const bridge = await startBridge(() => ({
    status: 401,
    type: "text/plain",
    text: "Unauthorized\n",
  }));
  const shim = startShim(
    bridge.port,
    { FREECAD_MCP_TOKEN: "short-token" },
    { seedToken: TOKEN_OK }
  );
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
  assert.equal(toolReply.result.note, undefined);
  const toolBody = JSON.parse(toolReply.result.content[0].text);
  assert.equal(
    toolBody.error,
    `FreeCAD refused the token from ${shim.tokenFile}. ` +
      "FreeCAD and the extension must use the same token file. " +
      "Check that FreeCAD runs as the same user, and that " +
      "FREECAD_MCP_TOKEN_DIR, if set, is the same for both."
  );
  assert.match(toolBody.note, /shorter than 32 characters/);
  assert.deepEqual(pingReply.result, {});
  assert.equal(pingReply.error, undefined);
  assert.equal(bridge.requests.length, 1);
  const leftovers = fs.readdirSync(shim.tokenDir).filter((name) => name.endsWith(".tmp"));
  assert.deepEqual(leftovers, []);
  await shim.stop();

  const missing = startShim(bridge.port, {}, { prepareTokenDir: false });
  missing.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 3,
      method: "tools/call",
      params: { name: "execute_python", arguments: { code: "print(1)" } },
    }) + "\n"
  );
  const [noneReply] = await missing.waitForLines(1);
  const noneBody = JSON.parse(noneReply.result.content[0].text);
  assert.equal(
    noneBody.error,
    `No token file at ${missing.tokenFile}. ` +
      "Turn the bridge on in FreeCAD once, or check FREECAD_MCP_TOKEN_DIR."
  );
  await missing.stop();

  const blocked = startShim(bridge.port, {}, { prepareTokenDir: false });
  fs.mkdirSync(blocked.tokenDir, { recursive: true });
  fs.mkdirSync(blocked.tokenFile);
  let readError = "";
  try {
    fs.readFileSync(blocked.tokenFile, "utf8");
  } catch (err) {
    readError = err.message;
  }
  blocked.send(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 4,
      method: "tools/call",
      params: { name: "execute_python", arguments: { code: "print(1)" } },
    }) + "\n"
  );
  const [blockedReply] = await blocked.waitForLines(1);
  const blockedBody = JSON.parse(blockedReply.result.content[0].text);
  assert.equal(
    blockedBody.error,
    `Could not read the token file at ${blocked.tokenFile} (${readError}). ` +
      "Check that the file is readable by you, then try again."
  );
  await blocked.stop();
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
    assert.doesNotMatch(untrustedError, /covers 127\.0\.0\.1/);
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

test("localhost in any letter case, ::1, and 127.x.x.x are treated as loopback: plain HTTP, token read from the file, and a pasted token written back", async () => {
  // 1. "localhost" and "LocalHost" connect to 127.0.0.1 over plain HTTP,
  // read the token file, and write back a pasted token.
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
    assert.equal(fs.readFileSync(writeShim.tokenFile, "utf8").trim(), TOKEN_NEW);
    await writeShim.stop();
  }
  bridgeV4.close();

  // 2. "::1" and "[::1]" connect to [::1] over plain HTTP, read the token
  // file, and write back a pasted token.
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
    assert.equal(fs.readFileSync(writeShim.tokenFile, "utf8").trim(), TOKEN_NEW);
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
    assert.equal(fs.readFileSync(writeShim.tokenFile, "utf8").trim(), TOKEN_NEW);
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
      "FreeCAD machine is set to freecad.lan, which requires HTTPS. " +
        "Set TLS certificate file (PEM) in the FreeCAD MCP Bridge extension settings to FreeCAD's certificate."
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
      "FreeCAD machine is set to freecad.lan. " +
        "Paste FreeCAD's bearer token into Bearer token in the FreeCAD MCP Bridge extension settings."
    );
    assert.equal(bridge.requests.length, 0);
    await noToken.stop();
    bridge.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("for another machine, an untrusted certificate, an uncovered host, or a 401 names the remote setting", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "shim-remote-fail-"));
  try {
    const preload = makeDnsPreload(tmpDir, "freecad.lan", "127.0.0.1");
    const serverCert = makeSelfSignedCert(tmpDir, "remote", "127.0.0.1", "freecad.lan");
    const otherCert = makeSelfSignedCert(tmpDir, "other", "127.0.0.1", "freecad.lan");
    const uncoveredCert = makeSelfSignedCert(tmpDir, "uncovered", "192.0.2.1");

    // 1. Different certificate than the bridge serves.
    const httpsBridge = await startBridge(echo, {
      cert: serverCert.cert,
      key: serverCert.key,
    });
    const untrusted = startShim(
      httpsBridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: otherCert.certPath,
      },
      { prepareTokenDir: false, nodeArgs: ["--require", preload] }
    );
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
    assert.match(untrustedError, /TLS connection failed/);
    assert.match(untrustedError, /covers freecad\.lan/);
    await untrusted.stop();
    httpsBridge.close();

    // 2. Certificate does not cover the configured host.
    const uncoveredBridge = await startBridge(echo, {
      cert: uncoveredCert.cert,
      key: uncoveredCert.key,
    });
    const uncovered = startShim(
      uncoveredBridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: uncoveredCert.certPath,
      },
      { prepareTokenDir: false, nodeArgs: ["--require", preload] }
    );
    uncovered.send(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: { name: "execute_python", arguments: { code: "print(1)" } },
      }) + "\n"
    );
    const [uncoveredReply] = await uncovered.waitForLines(1);
    const uncoveredError = JSON.parse(uncoveredReply.result.content[0].text).error;
    assert.match(uncoveredError, /TLS connection failed/);
    assert.match(uncoveredError, /covers freecad\.lan/);
    await uncovered.stop();
    uncoveredBridge.close();

    // 3. 401 Unauthorized from the remote bridge.
    const authBridge = await startBridge(
      () => ({ status: 401, type: "text/plain", text: "Unauthorized\n" }),
      { cert: serverCert.cert, key: serverCert.key }
    );
    const unauthorized = startShim(
      authBridge.port,
      {
        FREECAD_MCP_HOST: "freecad.lan",
        FREECAD_MCP_TOKEN: TOKEN_OK,
        FREECAD_MCP_TLS_CERT_FILE: serverCert.certPath,
      },
      { prepareTokenDir: false, nodeArgs: ["--require", preload] }
    );
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
      `FreeCAD at https://freecad.lan:${authBridge.port}/mcp refused the bearer token. ` +
        "Check that Bearer token in the FreeCAD MCP Bridge extension settings matches FreeCAD's token."
    );
    await unauthorized.stop();
    authBridge.close();
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

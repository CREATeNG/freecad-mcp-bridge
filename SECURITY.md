# Security Policy

## Reporting a Vulnerability

If you discover any security issue in MCP Bridge, use this repository's [Report a vulnerability](https://github.com/CREATeNG/freecad-mcp-bridge/security/advisories/new) form on GitHub, rather than creating a public issue.

**Contact:** chris@createng.com

Please include:

* A description of the issue and its potential impact
* Steps to reproduce
* FreeCAD version and addon version (`package.xml` `<version>`)
* Operating system

## Response

* Reports are acknowledged as soon as possible.
* Confirmed issues are prioritized for fix and release.
* Users are informed when a security fix is available, with upgrade guidance in the release notes or advisory.

## Privacy, connections, and trust

By default, the bridge listens on `127.0.0.1:39280`, and only while you
toggle it on in your FreeCAD session. Adding a non-loopback entry in
**Listen addresses** in **Edit → Preferences → MCP Bridge** exposes the
endpoint beyond this machine. Every listener requires a bearer token.
FreeCAD keeps a SHA-256 hash of it, not the token. Nothing is written
for an MCP client to read. When your own certificate and private key are
set in **Certificate (PEM)** and **Private key (PEM)**, every listener
serves HTTPS, loopback included, and each start writes a copy of the
serving certificate to `<home>/.freecad-mcp-bridge/serving-cert.pem` for
the local Claude Desktop extension. Over HTTPS, the extension pins the
bridge's certificate by its SHA-256 fingerprint against **TLS
certificate file (PEM)** when that setting is set, or against
`serving-cert.pem` on this machine when it is empty, and sends no
request bytes (the bearer token included) until they match. With that
setting set, the extension never uses HTTP. When they are not set,
loopback entries use HTTP, and a start with a non-loopback entry fails.
If you have no certificate, the optional **Generate self-signed
certificate** creates `tls-cert.pem` and `tls-key.pem` in
`<home>/.freecad-mcp-bridge/`, with `tls-key.pem` readable by you only,
covering `127.0.0.1`, `localhost`, each fixed address and host name in
**Listen addresses**, loopback entries such as `::1` included, and one
IPv4 address for each listed adapter that is up when you click it: its
first that isn't 169.254.x.x. Any client that can reach an entry, with
the bearer token, can run Python code in FreeCAD as your user account.
The first start asks you to accept that, loopback included, and each
later start warns in the status bar. In the **Ask** permission mode, the
default, FreeCAD shows code an agent sends in a dialog before it runs. A
file also shows its path. **Don't run** is the default. The
**Unrestricted** mode, in which that code runs unseen, stays off until
you confirm a second dialog. The On/Off button on the status bar shows
the mode: a shield with a tick for Ask, or a warning triangle for
Unrestricted. Its menu switches modes the same way as **Permission
mode** under **Execution permission** in **Edit → Preferences → MCP
Bridge**. A request that carries an `Origin` header is refused, as is a
`Host` header that, port aside, names neither the address that listener
is bound to, nor a name its certificate covers, nor a name in **Host
names**. A loopback listener accepts the address it is bound to,
`127.0.0.1`, `localhost` and `[::1]`, but not a name its certificate
covers.

See [README § Privacy Policy](README.md#privacy-policy) for how the
bridge (the MCP server inside FreeCAD) and your MCP client interact,
and what runs locally vs through your MCP client.

# Security Policy

## Reporting a Vulnerability

If you discover any security issue in MCP Bridge (FreeCAD MCP Bridge), use this repository's [Report a vulnerability](https://github.com/CREATeNG/freecad-mcp-bridge/security/advisories/new) form on GitHub, rather than creating a public issue.

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

By default, the bridge listens on `127.0.0.1:39280`, and
only while you toggle it on in your FreeCAD session. Adding a
non-loopback entry in **Listen addresses** in **Edit → Preferences →
MCP Bridge** exposes the endpoint beyond this machine. Every
listener requires a bearer token, stored in
`<home>/.freecad-mcp-bridge/token`, a file only you can read.
Preferences do not keep a copy of it. Non-loopback entries serve
HTTPS when **Require TLS (HTTPS)** is on, using your own
certificate and private key files. Loopback entries use HTTP. Any
client that can reach an entry, with the bearer token, can run
Python code in FreeCAD as your user account. The first start asks
you to accept that, loopback included, and each later start warns
in the status bar and **Report view**. Before a file an agent
sends is run, FreeCAD shows its path and whole text. **Don't run**
is the default. **Trust any file** stays off until you confirm a
second dialog, and a label beside the On/Off button then says
**MCP: trusts any file**, even when that button is hidden.
Turn it off in **Edit → Preferences → MCP Bridge**. A request that
carries an
`Origin` header is refused, as is a `Host` header that does not
name the address that listener is bound to.

See [README § Privacy Policy](README.md#privacy-policy) for how the
bridge (the MCP server inside FreeCAD) and your MCP client interact,
and what runs locally vs through your MCP client.

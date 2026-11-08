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
MCP Bridge** exposes the endpoint beyond this machine. Non-loopback
entries require a bearer token and TLS (HTTPS) by default, and
either can be turned off in Preferences. **Apply to loopback**
(off by default) applies both to loopback entries too. FreeCAD keeps
only a one-way SHA-256 hash of the token in FreeCAD's preferences,
and TLS uses your own certificate and private key files. Any client
that can reach a non-loopback entry (with the bearer token, when
required) can run Python code in FreeCAD as your user account.
FreeCAD asks you to accept the remaining risks for each entry once
and warns in the status bar and **Report view** at each start.

See [README § Privacy Policy](README.md#privacy-policy) for how the
bridge (the MCP server inside FreeCAD) and your MCP client interact,
and what runs locally vs through your MCP client.

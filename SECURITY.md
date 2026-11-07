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

By default, the bridge listens on loopback (`127.0.0.1`) only, and
only while you toggle it on in your FreeCAD session. Adding another
address in **Listen address** in **Edit → Preferences → MCP Bridge**
exposes the HTTP endpoint on that address with no authentication or
encryption: any machine that can reach the address and port can run
Python code in FreeCAD as your user account. FreeCAD asks you to
accept the risks for each address once and warns in the status bar
and **Report view** at each start.

See [README § Privacy Policy](README.md#privacy-policy) for how the
bridge (the MCP server inside FreeCAD) and your MCP client interact,
and what runs locally vs through your MCP client.

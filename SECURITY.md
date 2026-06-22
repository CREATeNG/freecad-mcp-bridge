# Security Policy

## Reporting a Vulnerability

If you discover a security issue in FreeCAD MCP Bridge, please report it privately rather than opening a public issue.

**Contact:** chris@createng.com

You can also use [GitHub Security Advisories](https://github.com/CREATeNG/freecad-mcp-bridge/security/advisories) for this repository if you prefer.

Please include:

* A description of the issue and its potential impact
* Steps to reproduce
* FreeCAD version and addon version (`package.xml` `<version>`)
* Operating system

## Response

* Reports are acknowledged as soon as possible.
* Confirmed issues are prioritized for fix and release.
* Users are informed when a security fix is available, with upgrade guidance in the release notes or advisory.

## Scope Notes

* The in-FreeCAD bridge listens on a **local socket only** when explicitly enabled by the user.
* The optional MCP client binary is a separate process configured by the user in their AI editor.
* Arbitrary Python execution inside FreeCAD is an intended capability while the bridge is enabled; treat bridge access as equivalent to running macros in your FreeCAD session.
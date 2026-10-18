## 0.1.23

**What's new since 0.1.22**

- **Switching the bridge on is easier to find:** a status-bar button shows **MCP: Off** or **MCP: On**, and clicking it switches the bridge. The Preferences page says how to switch it on, and a successful start shows FreeCAD's own notification.
- **Page history now expires from when a job finishes:** a job's page history is kept for the Page history retention time from when the job finishes, and each page read restarts that time. A gap longer than that between reads loses the output not yet read. Expired output is released even while the bridge is idle.
- **Docs:** the README says more about installing the Claude Desktop extension, and says in full what code run by an agent can reach. Security issues can be reported privately through the repository's **Report a vulnerability** form.
- **Behind the scenes:** each release now tests the packed Claude Desktop extension against the real bridge on Linux, macOS and Windows before tagging, and attaches that same file. A unit test keeps the three version files in step, and TESTING.md describes how testing works. Before this release, the extension was also checked by hand in Claude Desktop on Linux.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI), with Cursor among their tools.

## 0.1.22

**What's new since 0.1.20**

- **MCP versions:** the bridge speaks 2026-07-28 and 2025-11-25, chosen per request, and 2025-03-26 is dropped. A client's handshake is answered with 2025-11-25. A request carrying any other version gets an error naming the two the bridge supports.
- **Claude Desktop extension:** update it from the attached `freecad-mcp-bridge.mcpb`, because earlier versions can't connect to this bridge. It now passes on the protocol version the client asks for.
- **Long output:** pages are smaller and end on a line break wherever one fits, capped by Max page size (32 by default). Each result lists `has_more`, `page_no` and `job_token` before the output.
- **If Max page size shows 64 K characters:** some clients refuse pages that large. Set it to 32 in Edit → Preferences → MCP Bridge.
- **Fixes:**
  - Stopping the bridge drops queued jobs, so they never run.
  - A message with no id gets 202 and runs nothing.
  - A negative page number is reported as out of range.
  - The "Help & feedback" heading in Preferences shows its ampersand.
- **Behind the scenes:** acceptance tests run on every pull request and every push to `main`. Releases now go through a pull request, and a check installs each release from the Index once it's listed.

Built with a team of AI agents: Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI).

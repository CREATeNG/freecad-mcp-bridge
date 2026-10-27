## 0.1.26

**What's new since 0.1.25**

- **Claude Desktop extension:** the port field on the extension's page
  now says what's needed on the FreeCAD side and shows the extension's
  version (`0.1.26`). The manifest adds a longer description, plus
  repository, homepage, documentation, support, privacy-policy, and
  compatibility fields.
- **Redirects refused:** if something other than the bridge answers on
  the port and redirects, the Claude Desktop extension now stops and
  says so, instead of following the redirect.
- **Tool titles and annotations:** `execute_python`,
  `execute_python_file`, and `get_output_page` now carry titles
  (`Execute Python`, `Execute Python file`, `Get output page`) and MCP
  annotations. The two execute tools are marked as changing the FreeCAD
  session, and `get_output_page` is marked read-only.
- **Preferences and docs:** the six tooltips on the **MCP Bridge**
  Preferences page are shorter. The README replaces **Privacy &
  security** with a **Privacy Policy**, adds three example prompts, and
  explains that **Max response timeout** is a per-page wait counted from
  when that page starts and that a newline counts as two characters in
  **Max page size**.
- **Behind the scenes:** unit and shim tests now cover the redirect
  refusal, the tool titles and annotations, and the version in the port
  field's help line.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI), with Cursor among their tools.

## 0.1.25

**What's new since 0.1.24**

- **Port-taken warning on Windows:** on Windows, starting the bridge on
  a port another program already held with address reuse could still
  show **MCP On** instead of warning. The bridge now claims its port
  exclusively on Windows, so the bridge fails to start on a taken port
  and shows the port-taken warning there too.
- **Clearer Preferences page:** the client address uses `<PORT>` instead
  of a fixed port number. The **Max response timeout** tooltip says the
  agent fetches the rest of the output with `get_output_page`. The
  **Page history retention** tooltip says each page the agent reads
  restarts the retention time. The default **Position** choice reads
  **Right of FreeCAD's items (default)**.
- **Docs:** the README's **Quick start** gives Claude Desktop users
  their own short path. The README's **Preferences** section lists all
  six settings the page has. Example ports across the docs read as
  variables or as the default (`39280`).
- **Behind the scenes:** the occupied-port acceptance test now holds its
  port with address reuse, the case that let a taken port on Windows go
  unnoticed.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI), with Cursor among their tools.

## 0.1.24

**What's new since 0.1.23**

- **A status-bar button that fits in:** it reads **MCP Off** or **MCP On**, drawn flat like FreeCAD's other status-bar items, and its text turns green when on, in a shade that suits the theme. Click the words to switch the bridge on or off. Click the arrow for a menu whose one item opens the bridge's Preferences page. In Preferences you can hide the button, or place it left of FreeCAD's items.
- **Notifications instead of a popup:** switching off and a failed start now show FreeCAD's own notification too, the failed start as a warning. When the port is already taken, the warning says where to change it.
- **Stdio clients, and the Claude Desktop extension:** when the bridge streams a reply as several events, each now reaches the client as its own message. Before, they were joined into one line the client couldn't read. Update the extension from the attached `freecad-mcp-bridge.mcpb`, or update the shim (`mcp-stdio-shim/`) to this release, to get this.
- **Behind the scenes:** a release refuses to publish without its notes in `CHANGELOG.md`, and the release checklist now includes a check of the extension in Claude Desktop. TESTING.md matches the current test scripts and workflows.

Built by a team: @zewpo and the AI agents Claude (Anthropic), Grok (xAI), Gemini (Google) and Codex (OpenAI), with Cursor among their tools.

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

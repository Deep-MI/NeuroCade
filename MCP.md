# Connect an external assistant to NeuroCade

Open the top-right **Settings → Connect external agents** menu to connect a client.
**Settings → Agent approval** has independent controls for the built-in assistant
(on by default) and connected external agents (off by default for new connections).
Appearance and built-in approval are saved per user in NeuroCade; external approval
is saved per connection. The backend reads the user’s saved approval preference. Turning built-in approval off
applies to new chat requests, without changing permissions or already pending
approval requests. Appearance also persists between workspace and settings pages.

External agents can upload scans into a paired workspace, prepare and submit analyses,
monitor runs and logs, and download individual artifacts or complete case ZIPs.
The adapter runs inside NeuroCade. Processing uses the same catalog, execution ledger,
job worker and host runtime bridge as the built-in assistant.

`neurocade_open_case` (local assistant: `open_case`) opens an authorized case in
the user's NeuroCade browser. Supply `arguments.case_id`; optionally select
`arguments.browser_session_id` from `neurocade_get_context.browser_sessions` when
multiple tabs are available. This does not start an analysis. The local agent
targets its own tab. MCP auto-selects only a single eligible tab. Commands expire,
are acknowledged by the browser, and return `queued` rather than claiming the case
is already open. If no tab is available, the result includes the case path and
instructions to open NeuroCade. Read-only MCP connections cannot navigate the UI.

Transfer errors include a structured error code and an actionable message.
Case titles must be lowercase slugs of 2–64 characters (letters, digits and
hyphens; start/end with a letter or digit), for example `claude-case`. Invalid
titles are rejected by the connector before it reads or uploads the scan. Known
server validation messages are preserved without reflecting arbitrary response
bodies, credentials or validation inputs. A generic HTTP 400 is not evidence of
scan corruption; only an explicit checksum/header error identifies those failures.
After a submission timeout, the outcome is unknown: inspect runs and retry with
the same arguments and idempotency key, never a fresh key based only on a timeout.

## Pairing local agents

Choose a workspace in **Settings → Connect external agents**. NeuroCade creates a
single-use setup grant valid for 10 minutes. The grant fixes the user, workspace,
access level and approval policy. Only a hash is stored in SQLite; redemption and
credential creation are one atomic transaction. Expired, reused and wrong-installation
grants cannot create another credential. Workspace membership and launch permissions
are checked again at redemption.

### Claude Desktop

Select Claude Desktop, download the extension and open it in Claude on this Mac.
Install and launch within 10 minutes, then start a new conversation. No JSON file,
file picker or token entry is needed. NeuroCade must be running with MCP enabled.
The extension launches the installed host connector outside Cowork’s isolated shell.

The bundle contains the logo, launcher, installation identity, host connector path
and short-lived pairing grant. It contains no permanent access token. Treat the
unused bundle as private until its grant expires. On first launch, the shared
connector redeems the grant and saves mode-600 credentials in
`~/.local/share/neurocade/connections`. Later launches reuse those credentials,
even after the setup grant expires. Concurrent launches share a file lock.

If initial pairing expires, download a new extension. If the app moved, download a
new extension from the running installation. Revocation stays in NeuroCade and does
not silently re-pair an existing connection. A lost redemption response may require
a fresh grant; inspect and revoke any unused connection in NeuroCade.

### ChatGPT Work locally, Codex, and Claude Code

Select the exact local client, choose the workspace, and copy the generated setup
prompt into a local task. Run it within 10 minutes. The prompt calls the installed
`neurocade-mcp setup` with a one-time grant; permanent tokens are never printed or
included in the prompt. The connector saves credentials, checks the MCP identity and
tools, and registers with the selected client. Start a new local task afterwards.
Rerunning the same prompt on the same Mac reuses its saved credential.

`chatgpt` targets local ChatGPT Work through its bundled Codex executable and
`codex mcp add`. `codex` uses direct MCP registration. `codex-plugin` generates and
installs a local marketplace plugin; its configuration references only the host
connector and private credential path. `claude-code` uses `claude mcp add`.
`manual` prints the MCP command for another local adapter after pairing.

Codex plugin templates remain in
`packages/neurocade-mcp/src/neurocade_mcp/plugin_template/neurocade`. Generated
plugins use content-based versions to invalidate stale caches. Registration identity
is stable per installation, user, workspace and adapter; a new setup grant updates
the registered credential path instead of adding another local server or plugin.
Manual connections retain separate pairing files so different adapters cannot overwrite each other's credentials.
Previous credentials remain revocable in NeuroCade; replacing a local registration
does not revoke credentials that might still be used by another installation. Do not distribute machine-specific generated plugins.
Uninstalling a plugin does not revoke the credential; revoke it in NeuroCade.

For host-command setup without a generated prompt, `scripts/connect-agent.sh
--client CLIENT --workspace WORKSPACE_ID` starts the app and uses the same pairing
service. `--list-workspaces` and `--create-workspace NAME` remain available. For an
authenticated deployment, prefer a prompt generated while signed in; alternatively
provide `NEUROCADE_UI_TOKEN` through the local environment. Redeeming a grant requires
no UI login because the grant already carries that authorization. MCP credentials
cannot create grants, connections or change approval settings.

Only local clients are supported. Online connectors, gateways and tunnels are not
part of this flow. A client running in a separate sandbox cannot execute the host
installer; Claude Desktop’s extension is the local path for that client.

## Confirmation

**External analyses start immediately by default.** Server instructions require the
assistant to explain the case, scan and workflow and obtain user confirmation in the
conversation before submission. This is an instruction to a trusted external agent,
not proof that a human confirmed. Permissions, input validation, preset validation,
revocation and duplicate-submission protection remain enforced by NeuroCade.

Under **Settings → Agent approval → Connected external agents**, turn on the
approval switch for an agent, or use `--require-approval` during setup, for explicit
NeuroCade review. New connections default to extra approval off. Each connection records this
policy. The optional review applies to workflow submissions/cancellation; direct file
transfers use workspace permissions. Pairing remains scoped to one workspace.

## Upload, analyze, download

1. Call `neurocade_get_context` to inspect workspace, access and confirmation policy.
2. Call `neurocade_upload_case` with an absolute local `path`, optional lowercase case
   `title`, and a stable `idempotency_key`. This creates a case and imports the scan.
   Supply `case_id` instead to attach a scan to an existing case. Supported inputs
   include NIfTI, MGZ and a DICOM ZIP. The connector transfers bytes directly; they
   are not encoded in MCP messages.
3. Inspect `neurocade_get_case` or `neurocade_list_artifacts`, search presets using
   `neurocade_tool_search`, and inspect the matching preset/docs. Most server tools
   use an envelope: `case_id` plus `arguments`.
4. Call `neurocade_prepare_analysis` with `case_id` and
   `arguments: {tool_id, artifact_ids: [...]}`. It validates ordered input artifacts
   and returns the exact submission arguments. Runtime image readiness is checked
   during submission.
5. Confirm the case, inputs and workflow with the user. Submit
   `neurocade_tool_call` using the prepared arguments and a stable `idempotency_key`.
   If the connection requires NeuroCade review, open the approval link and wait.
6. Poll `neurocade_get_invocation` and `neurocade_tool_run_status`. Use
   `neurocade_tool_run_logs` for bounded stdout/stderr with byte offsets. Never
   recover a lost response by submitting the same work with a new key.
7. Call `neurocade_download_file` with exactly one of `artifact_id` or `case_id`, and
   an absolute local `destination`. Artifact downloads include original imported
   scans and generated results. Case downloads contain the case directory as a ZIP
   and require the case to be idle. Artifact downloads also snapshot idle case or
   workspace files before streaming, so renames or later edits cannot change the
   bytes associated with the advertised checksum. Existing destination files are never overwritten.

Downloads use HTTP ranges and SHA-256 checksums. An interrupted transfer leaves a
private partial file and transfer metadata alongside the destination; repeat the
same call to resume. Case ZIP snapshots are cached for up to a day and pruned on
subsequent archive requests. Archive preparation reserves only its case; other cases
can continue processing. Uploads are streamed and checksum-verified; an identical
retry with the same key returns the original result. An interrupted import is not
blindly replayed: inspect its case/activity before choosing a new key.

HTTP clients can inspect transfer endpoint information in `neurocade_get_context`.
Transfers require both the paired bearer token and `X-NeuroCade-Installation` header.
The local connector handles these automatically. The raw upload endpoint enforces
the configured file-size limit before importing. All transfer endpoints recheck
workspace scope and revocation. A local path is never sent to the backend for it to
open; the local connector reads only the explicitly supplied source file.

## Build, diagnostics and lifecycle

```sh
./scripts/run.sh start --build --mcp -d
./scripts/run.sh status
/absolute/path/to/NeuroCade/.runtime/bridge-venv/bin/neurocade-mcp check --connection-file /private/connection.json
```

The launcher requires a local profile and loopback publication. Docker and Apptainer
use the same adapter; upgrade the application and host connector together. A running
application must be restarted to use a rebuilt image. No separate LLM configuration
is needed for MCP or the optional approval page.

Connections survive application restarts and are bound to the stable installation
identity in `$HOST_DATA_DIR/.mcp-installation-id`. Keep that identity with installation
data. Revoke unused connections on Connect external agents. A port change requires updating or
re-pairing the connection. Revoked credentials cannot read, upload or download.

The UI distinguishes never-used connections from those that reached NeuroCade.
File transfer errors identify authentication, scope, conflicting requests, size and
checksum failures. Read results are transient; mutation identities remain durable.
Case deletion invalidates prior invocations; workspace deletion removes its clients.
Cancelling an MCP session does not cancel a workflow. Explicit cancellation must be
followed until the runtime confirms stopped writers and released output ownership.

The external model provider handles tool results requested by its agent. Binary
transfers bypass model context, but local MCP does not make the external model local.
Documentation is bundled and version-matched; see `config/documentation/README.md`.

Regression coverage lives in `tests/test_mcp_*.py` and database migration tests. Product
registration and actual model tool loading are different checks: test the final
reload and task execution in the assistant client you intend to use.

## Shared tools and prompting

MCP uses `AssistantToolBuilder`, the same registry as the embedded models. Discovery
advertises both workspace and case tools; each invocation rebuilds the registry for
the authorized workspace and optional `case_id`. This includes scoped file reads,
search, writes and edits; case/workspace inspection; the full workflow catalog and
private configuration tools; image search; isolated image probes; documentation;
and all MRI viewer tools. Inspection helpers, analysis preparation and run logs also
live in the shared registry. Local stdio upload/download tools are additional
transport capabilities, not a second implementation of the analysis tools.

Private workflow edits affect the connected user's catalog across their workspaces.
The same validation and isolated runtime restrictions as the embedded assistant
apply. Read-only connections do not expose file/catalog mutations or viewer changes.

Both entry points use `build_system_prompt` and the same `config/SOUL.md`,
`config/INFORMATION.md`, and `config/RULES.md`. MCP initialization carries this shared
guidance plus a short transport section explaining prefixes, argument envelopes,
confirmation and transfers. `neurocade_get_context` accepts optional `case_id` and
`gui_session_id` and returns updated scope-specific instructions.

For viewer control, open the case in NeuroCade and inspect `viewer_sessions` from
`neurocade_get_context`. Pass the chosen `case_id` and `gui_session_id` alongside
`arguments`. Only synced sessions belonging to the paired user/workspace are listed
or accepted. Viewer changes require a standard connection and an explicit session;
commands remain queued until the browser acknowledges them. A headless connection
can inspect files and run analyses without a viewer.

Case inspection uses the same canonical tools in the built-in assistant and MCP:
`list_cases` returns a page of IDs, titles, latest run statuses and workspace paths,
with `next_cursor` for the next page; `get_case` reads the selected case’s metadata.
Use the `neurocade_` prefix for MCP calls. New credentials are issued only through
the one-time pairing service; client-management endpoints list, revoke or update
existing connections and do not directly issue permanent tokens.

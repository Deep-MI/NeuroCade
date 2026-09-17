---
name: neurocade
description: Use the paired local NeuroCade workspace to upload neuroimaging scans, inspect available workflows, prepare and run user-confirmed analyses, monitor progress, and download artifacts or complete cases.
---

# NeuroCade

Start with `neurocade_get_context`. Use the returned workspace, capabilities,
instructions and confirmation policy as the current source of truth. Tool names
may have a client namespace prefix. Discover the available tools rather than
assuming a fixed catalog or copying NeuroCade's system prompt into this skill.

Use the MCP tools for NeuroCade tasks. If they are missing, explain that this
plugin needs a paired local installation and a new conversation after installation.
Direct the user to NeuroCade's Local agents page and the Codex plugin setup prompt.
If the app is stopped or a connection revoked, report that clearly; do not silently
fall back to browser automation, change credentials, or enable another workspace.

1. Inspect existing cases and workflows before choosing inputs or parameters.
2. Use `neurocade_upload_case` for a user-selected file on this computer. An upload
   in a cloud conversation is not necessarily a host file; obtain a usable local
   path instead of guessing one. Preserve the upload retry key on retries.
3. Inspect the workflow preset and relevant NeuroCade documentation. Prepare the
   analysis and show its case, scan, workflow, parameters and expected outputs.
4. Obtain the user's confirmation before starting the analysis. An existing
   confirmation for that exact analysis remains valid. By default NeuroCade trusts
   you to obtain confirmation; do not claim that the app will ask again. If the
   context requires additional app approval, follow that returned policy.
5. Submit through the available NeuroCade workflow tool. Reuse its idempotency key
   for retries, and monitor status/logs. Distinguish queued, running, failed and
   finished; never infer completion from successful submission.
6. Use `neurocade_download_file` to save an artifact or complete case archive to
   the user's chosen local destination. Preserve the destination on retries and
   report the returned path and transfer result.

Keep credentials private. Never print or read connection-file contents into the
conversation. Do not use shell commands to run neuroimaging workflows or bypass
workspace permissions. Processing goes through NeuroCade's approved runtime.

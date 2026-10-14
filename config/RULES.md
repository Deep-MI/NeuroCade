# Operating Rules

- Use only tools listed in the current prompt and match their JSON schemas exactly.
- For configured neuroimaging work, call `tool_search`, inspect the selected tool,
  then call it with explicit ordered paths. Do not guess command flags.
- Inspect unfamiliar CLIs with `tool_inspect` (locate, help, version, bounded source).
  Use literal arguments and the intended tagged image. It has no case/host mounts.
- Review a minimal draft early with `tool_workflow_test` mode `validate`, before
  spending rounds on elaborate tests. Require the main output; address warnings
  before asking to save. Schema validation does not establish execution success.
- For supported directory staging, use `tool_workflow_test` mode `staging` with the
  processing executable basename. Its prepared-subject fixture uses real mount
  aliases, spaces, existing output, and two runs. The harness supplies the stub
  and grades the filesystem. Do not construct your own stub/probe scripts.
  The current stub contract is COMMAND SUBJECT_NAME SUBJECTS_DIR; other workflows
  can be schema-validated, but must be reported as untested for execution.
  Do not save or launch a separate probe workflow to work around a missing test.
  Honor draft-only requests: return the draft without requesting save approval.
- After two failures with the same cause, stop repeating equivalent inspections.
  Read the diagnostics and test a different hypothesis, or return the validated
  draft with explicit limits. Preserve useful progress before the round budget
  is exhausted; never claim a partial test proves real processing.
- When a retry fails with the same error, compare its saved workflow snapshot
  with the intended fix before another run. State the failed assumption and test
  a different hypothesis; do not call a fix robust until the relevant test passes.
- If a requested action has no supported tool, report the missing capability.
  Writing a cleanup script does not execute it; emptying a file does not delete it.
- Follow the runtime path contract in INFORMATION.md: output paths are relative
  to `case_output_folder` when set, and the output mount aliases that case folder.
  Preserve inputs and never recursively copy an input ancestor into its output.
- After an assistant error, check durable workflow status before resubmitting;
  the workflow may already have been queued or started.
- Use case or workspace file-tree tools when an exact path is unknown.
- `gui_load_layer` automatically registers existing managed files before loading.
  For untracked volumes, supply `layer_type` and a segmentation `lut` explicitly in
  that same call. Omit those fields to preserve a tracked file's metadata; provide
  them to correct it. Do not rerun processing just to register/load an existing result.
  Verify the loaded layer type/LUT with `gui_list_layers`.
- Use GUI tools for viewer actions. To load a compatible case artifact explicitly,
  use `gui_load_layer`; confirm queued GUI actions with `gui_command_status` before
  claiming they happened.
- Do not repeat an identical tool call when the arguments cannot reveal new
  evidence. Narrow the request or choose a different tool instead.
- Treat vague requests such as “analyze” or “review” as ambiguous unless the
  requested output is clear from the conversation.
- Report bilateral measurements for both sides when the user requests a plural or
  bilateral structure.
- Never estimate normative ranges, percentiles, or z-scores from memory.
- After tool use, summarize what happened, what evidence was returned, and what
  remains queued, running, incomplete, or unavailable.

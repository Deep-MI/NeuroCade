# Operating Rules

- Use only tools listed in the current prompt and match their JSON schemas exactly.
- For configured neuroimaging work, call `tool_search`, inspect the selected tool,
  then call it with explicit ordered paths. Do not guess command flags.
- Before adding a workflow for an unfamiliar command-line utility, use
  `tool_probe` on the intended tagged image to verify the executable and inspect
  its help or version output. If it is not on `PATH`, search `/opt` and
  `/usr/local` for it. Base the workflow flags on that observed output.
- For new workflow staging logic, test the complete file-handling steps with tiny
  synthetic directories and a processing stub in `tool_probe` before saving.
  Executable discovery and `--help` alone do not validate staging or output paths.
- Before requesting workflow-save approval, call `tool_config_validate` on the
  exact draft and address its warnings. Require the main requested output; keep
  only genuinely optional reports optional. Static validation does not establish
  execution success.
- Synthetic probes must assert expected files and absence of recursive copies;
  printing a directory listing and exiting zero is not a passing assertion.
  Symlinks do not reproduce bind-mount aliases. Prefer reusing a working workflow's
  explicit directory-copy staging over inventing path-exclusion heuristics.
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

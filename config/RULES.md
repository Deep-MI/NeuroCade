# Operating Rules

- Use only tools listed in the current prompt and match their JSON schemas exactly.
- For configured neuroimaging work, call `tool_search`, inspect the selected tool,
  then call it with explicit ordered paths. Do not guess command flags.
- Before adding a workflow for an unfamiliar command-line utility, use
  `tool_probe` on the intended tagged image to verify the executable and inspect
  its help or version output. If it is not on `PATH`, search `/opt` and
  `/usr/local` for it. Base the workflow flags on that observed output.
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

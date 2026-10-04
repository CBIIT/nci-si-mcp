A prompt is listed only in a profile that has every tool it names (M5.1). All four need both
modules, so the evs and cadsr profiles list none and unified lists all four. Each starts from
`resolve_release`, so that one release is pinned for the whole sequence, and the tools it names,
in order, are the order the caller makes the calls.

There is at most one prompt for each use the Statements of Work name, and only where it adds
something the tools do not. Decision: there is no prompt for grounding a value, because
`ground_value` does it in one call and a prompt would only restate the tool.

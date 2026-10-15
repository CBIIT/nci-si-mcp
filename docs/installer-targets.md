# Installer target interface
`nci-si-install plan` describes a target and optional index, documentation and companion. The MCP server is always selected.

**check prerequisites → install → configure → verify**
Uninstall is separate. Phase 32 provides planning, prerequisite execution, resumable state, status and guidance. Later phases add target install/configure/verify actions and uninstall. `check` runs every selected prerequisite before changes and reports every failure with its corrective action.
Local checks cover the OS, git, PDM, selected Node/container tools and ports 8080/8081, and the configured EVS/caDSR endpoints. Checks change nothing. Expected failures carry static corrections; bugs propagate. AWS executable checks arrive with #324.
`local` is executable. Standard-account `aws` is plannable; executable operations await #324. `cloud-one` only reports its block on [#292](https://github.com/CBIIT/nci-si-mcp/issues/292). AWS companion administration awaits [#197](https://github.com/CBIIT/nci-si-mcp/issues/197). Azure and GCP have no implementation; future targets use the same flow without plugins.
`run` and `status` require `--data-dir`; `NCI_SI_DATA_DIR` belongs to the server. Reconciliation and the status snapshot hold `installer-state.lock`. State uses an fsynced temporary sibling, atomic replacement and a directory fsync.
Non-restart runs reverify from the beginning; the first stale step and later records are discarded. Phase 32 executes `check_prerequisites`; failures are BLOCKED with static corrections.
`--restart` resets only the same selection and deletes nothing. Another selection requires a new `--data-dir`. Invalid, truncated or unsupported state remains unchanged.
On a terminal, BLOCKED `run` prints fixes to stderr; Enter rechecks and EOF returns BLOCKED JSON.
Non-TTY stdin and `--non-interactive` never read input. `help check_prerequisites --target local` explains the only executable step; later help arrives with Phase 33 implementations.

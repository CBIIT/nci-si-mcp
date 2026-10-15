# Installer target interface

`nci-si-install plan` describes an installation before the installer changes anything. The MCP
server is always present. The NCIt index, static documentation and validation companion are
optional components.

## Install flow

Every target follows the same install order:

**check prerequisites → install → configure → verify**

Uninstall is a separate operation, not a stage of installation. This phase provides planning,
prerequisite execution, resumable state, status and prerequisite guidance. Later phases add target
install, configure and verify actions and uninstall. `plan` prints the selected flow as JSON.
`check` runs every applicable prerequisite before any change and reports every failure with its
corrective action; one failed check never hides another.

The local checks cover the supported operating system, git, PDM, component-gated Node.js and
container tools, fixed loopback ports 8080 and 8081 where selected, and reachability of the
configured EVS and caDSR endpoints.
Checks do not create installer state or install anything. Expected observation failures become a
failed prerequisite with its static correction; an unexpected bug propagates rather than being
reported as an environmental failure. Standard-AWS prerequisite checks arrive with #324.

The implemented target names are `local` and standard-account `aws`. `cloud-one` is accepted only
to report that its implementation is blocked on [the Cloud One consultation](https://github.com/CBIIT/nci-si-mcp/issues/292).
Standard AWS refuses the validation companion until [platform protection](https://github.com/CBIIT/nci-si-mcp/issues/197)
is approved.

Azure and GCP are future targets. They have no command flags or implementation. A future target
implements the same install flow without adding a plugin loader.

## State and resume

`run` and `status` require an installation `--data-dir`. They do not read `NCI_SI_DATA_DIR`, which
belongs to a serving process. The installer stores `installer-state.json` there and holds
`installer-state.lock` while reconciling and while taking the state snapshot used for status. State is written through an fsynced
temporary sibling, atomic replacement and a directory fsync.

Every non-restart run reverifies completed steps from the beginning. The first stale step and all
later records are discarded before safe-to-repeat work resumes. In Phase 32 the real production step is
`check_prerequisites`: verification reruns every selected check, and a failure is BLOCKED with its
static corrective actions. Later targets add install, configure and verify actions.

`--restart` resets progress only for the same target and component selection. It does not uninstall
or delete resources. A different selection fails closed and asks for a new `--data-dir`, because
adopting it could orphan a component removed from the recorded installation. Invalid, truncated or
unsupported state is left unchanged for inspection.

On a terminal, a BLOCKED `run` prints every corrective action on stderr and waits for Enter before
rerunning the machine checks. Enter never confirms completion. EOF returns the current BLOCKED JSON.
With non-TTY stdin, or with `--non-interactive`, `run` never reads stdin and returns every unresolved
action at once. `help check_prerequisites --target local` explains the only executable Phase 32
step; help for install, configure and verify arrives with their Phase 33 implementations.

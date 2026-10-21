# Installer target interface
`nci-si-install plan --target local` describes the local MCP server flow. **check prerequisites → install → configure → verify**
The local target runs those stages through the same resumable state path. `run --transport stdio --profile unified` names the defaults; transport also accepts `http`, and profile accepts `evs` or `cadsr`. Install creates owned `venv`, `server-data` and `runtime` children and installs only the exact release tag represented by the running installer. A development-version installer is BLOCKED. Verification reads the installed version and PEP 610 requested revision rather than trusting the install command.
Local server checks cover macOS/Linux, the running Python 3.14 or newer, the Git executable used by pip for the released VCS URL, and the configured EVS/caDSR endpoints. They require neither PDM nor a Git checkout. Checks change nothing. Expected failures carry static corrections; bugs propagate. The index, documentation site and validation companion are installed independently with their documented local commands; they are not installer targets or flags.
`run` and `status` require `--data-dir`; `NCI_SI_DATA_DIR` belongs to the server. Reconciliation and the status snapshot hold `installer-state.lock`. State version 2 binds the target, components, local transport and domain profile. Version 1 is refused without migration. State uses an fsynced temporary sibling, atomic replacement and a directory fsync.
Non-restart runs reverify from the beginning; the first stale step and later records are discarded. Missing prerequisites are BLOCKED with static corrections. HTTP readiness failures with no missing input are FAILED. Stdio launch or timeout failures, and any unsuccessful server initialization or MCP verification, exit with code 2; child-process failures name the owned `runtime/server.log`. `--restart` clears recorded progress for the same selection but deletes no installed assets. Another selection requires a new `--data-dir`. Invalid, truncated or unsupported state remains unchanged. On a terminal, BLOCKED `run` prints fixes to stderr; Enter rechecks and EOF returns BLOCKED JSON. Non-TTY stdin and `--non-interactive` never read input. `help <step> --target local` explains every executable stage.

`start --transport stdio` prints a JSON client configuration containing the owned executable, arguments and required environment; it opens no listener. HTTP binds `127.0.0.1`, uses trusted-local access and explicitly sets `NCI_SI_HTTP_REQUIRE_INDEX=0`: the installer stays server-only, while the index uses the [independent local index path](local-guide.md#index). HTTP verification reuses a process whose PID and recorded start-time identity match. With no recorded running process, it starts a transient process, checks `/ready` and stops it; an unverifiable or mismatched ownership record fails closed. An unowned listener on port 8000 is BLOCKED. `start` and `stop` operate the recorded HTTP process. `uninstall` refuses a running owned process, removes only the three owned children, then removes installer state so later status reports no installation.

## Verify a released local installation

Set `PYTHON` to an absolute Python 3.14-or-newer executable, `RELEASE_TAG` to a released `vX.Y.Z`,
and choose fresh absolute paths for the other variables below. Run from a directory outside a
source checkout. The HTTP commands inherit any configured upstream URLs; trusted-local HTTP remains
on loopback.

```bash
"$PYTHON" -m venv "$INSTALLER_VENV"
"$INSTALLER_VENV/bin/python" -m pip install "nci-si-mcp[server] @ git+https://github.com/CBIIT/nci-si-mcp@${RELEASE_TAG}"
"$INSTALLER_VENV/bin/nci-si-install" check --target local
"$INSTALLER_VENV/bin/nci-si-install" run --target local --transport stdio --data-dir "$STDIO_ROOT" --non-interactive
"$INSTALLER_VENV/bin/nci-si-install" status --target local --transport stdio --data-dir "$STDIO_ROOT"
"$INSTALLER_VENV/bin/nci-si-install" start --target local --transport stdio --data-dir "$STDIO_ROOT"
"$INSTALLER_VENV/bin/nci-si-install" uninstall --target local --transport stdio --data-dir "$STDIO_ROOT"
"$INSTALLER_VENV/bin/nci-si-install" check --target local
"$INSTALLER_VENV/bin/nci-si-install" run --target local --transport http --data-dir "$HTTP_ROOT" --non-interactive
"$INSTALLER_VENV/bin/nci-si-install" status --target local --transport http --data-dir "$HTTP_ROOT"
"$INSTALLER_VENV/bin/nci-si-install" start --target local --transport http --data-dir "$HTTP_ROOT"
"$INSTALLER_VENV/bin/nci-si-install" stop --target local --transport http --data-dir "$HTTP_ROOT"
"$INSTALLER_VENV/bin/nci-si-install" uninstall --target local --transport http --data-dir "$HTTP_ROOT"
```

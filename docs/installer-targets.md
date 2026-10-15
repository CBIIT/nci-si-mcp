# Installer target interface

`nci-si-install plan` describes an installation before the installer changes anything. The MCP
server is always present. The NCIt index, static documentation and validation companion are
optional components.

## Install flow

Every target follows the same install order:

**check prerequisites → install → configure → verify**

Uninstall is a separate operation, not a stage of installation. Later installer phases add target
execution, resumable state, status, verified manual steps and uninstall. The current command only
prints the plan as JSON.

The implemented target names are `local` and standard-account `aws`. `cloud-one` is accepted only
to report that its implementation is blocked on [the Cloud One consultation](https://github.com/CBIIT/nci-si-mcp/issues/292).
Standard AWS refuses the validation companion until [platform protection](https://github.com/CBIIT/nci-si-mcp/issues/197)
is approved.

Azure and GCP are future targets. They have no command flags or implementation. A future target
implements the same install flow without adding a plugin loader.

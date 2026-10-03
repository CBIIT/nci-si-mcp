"""Run the server under test and talk to it over MCP, in fixture or live mode.

The harness knows nothing of the server's implementation. It launches the server's
command over stdio with an environment that names the upstream of the run mode:
in `fixture` mode every upstream base URL points at the fixture server, in `live`
mode the server's own defaults (the production services) apply, and the developer's
upstream credentials are passed on.

    NCI_SI_ACCEPTANCE_MODE     fixture (default) or live
    NCI_SI_ACCEPTANCE_SERVER   the command that starts the server, default "nci-si-mcp serve"
    NCI_SI_ACCEPTANCE_PROFILE  the profile that command serves (M1.5): evs, cadsr or unified
                               (default)
    NCI_SI_ACCEPTANCE_PREPARE  the operator's prepare command, a shell command line run once
                               before any test (the acceptance README); without it the tests
                               that need its result are NOT RUN
"""

from __future__ import annotations

import os
import shlex
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TextIO

from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from nci_si_acceptance.fixture_server import UPSTREAM_VARIABLES
from nci_si_acceptance.spec import PROFILES

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from mcp import types

type Mode = Literal["fixture", "live"]

MODE_VARIABLE = "NCI_SI_ACCEPTANCE_MODE"
SERVER_VARIABLE = "NCI_SI_ACCEPTANCE_SERVER"
PROFILE_VARIABLE = "NCI_SI_ACCEPTANCE_PROFILE"
PREPARE_VARIABLE = "NCI_SI_ACCEPTANCE_PREPARE"
DEFAULT_SERVER = "nci-si-mcp serve"
# How long the harness waits for any one answer of the server, startup included.
READ_TIMEOUT_SECONDS = 60

# Credentials for licensed upstream content (docs/SPEC.md §8), used in live mode only.
CREDENTIAL_VARIABLES = ("NCI_SI_EVS_LICENSE_KEY", "NCI_SI_CADSR_CREDENTIAL")


@dataclass(frozen=True, slots=True)
class Target:
    """The server under test, the profile it serves and the run mode."""

    mode: Mode
    command: list[str]
    profile: str = "unified"
    prepare: str | None = None

    @classmethod
    def from_env(cls) -> Target:
        mode = os.environ.get(MODE_VARIABLE, "fixture")
        if mode not in ("fixture", "live"):
            raise ValueError(f"{MODE_VARIABLE} must be fixture or live, not {mode!r}")
        command = shlex.split(os.environ.get(SERVER_VARIABLE, DEFAULT_SERVER))
        if not command:
            raise ValueError(f"{SERVER_VARIABLE} must name a command")
        profile = os.environ.get(PROFILE_VARIABLE, "unified")
        if profile not in PROFILES:
            raise ValueError(f"{PROFILE_VARIABLE} must be one of {', '.join(PROFILES)}")
        return cls(mode, command, profile, os.environ.get(PREPARE_VARIABLE) or None)


def _upstream_settings(mode: Mode, fixture_url: str | None) -> dict[str, str]:
    """Fixture mode: every upstream names the fixture server. Live: only the credentials."""

    if mode == "live":
        return {name: os.environ[name] for name in CREDENTIAL_VARIABLES if name in os.environ}
    if fixture_url is None:
        raise ValueError("fixture mode needs the fixture server's URL")
    return {name: f"{fixture_url}/{surface}" for name, surface in UPSTREAM_VARIABLES.items()}


def server_environment(mode: Mode, data_dir: Path, fixture_url: str | None) -> dict[str, str]:
    """The environment of the server under test.

    The developer's own `NCI_SI_*` settings never reach it, so a run depends only on
    the mode; in live mode the upstream credentials are the exception. In fixture mode
    every upstream base URL names the fixture server.
    """

    environment = {key: value for key, value in os.environ.items() if not key.startswith("NCI_SI_")}
    environment |= {"NCI_SI_UPSTREAM_MODE": mode, "NCI_SI_DATA_DIR": str(data_dir)}
    return environment | _upstream_settings(mode, fixture_url)


class Session:
    """A synchronous MCP client session, for tests."""

    def __init__(self, portal: BlockingPortal, client: Client) -> None:
        self._portal = portal
        self._client = client

    def list_tools(self) -> types.ListToolsResult:
        return self._portal.call(self._client.list_tools)

    def call_tool(
        self, name: str, arguments: dict[str, Any], meta: types.RequestParamsMeta | None = None
    ) -> types.CallToolResult:
        return self._portal.call(partial(self._client.call_tool, name, arguments, meta=meta))


@contextmanager
def open_session(
    command: list[str], environment: dict[str, str], errlog: TextIO = sys.stderr
) -> Iterator[Session]:
    """Start the server's command, its standard error going to `errlog`, and hold one MCP
    session with it."""

    server = StdioServerParameters(command=command[0], args=command[1:], env=environment)
    transport = stdio_client(server, errlog=errlog)
    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(
            # No response cache: each tools/list must reach the server (P-6).
            Client(transport, read_timeout_seconds=READ_TIMEOUT_SECONDS, cache=None)
        ) as client,
    ):
        yield Session(portal, client)

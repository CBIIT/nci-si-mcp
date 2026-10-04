"""A remote server under test: what the harness checks before the first test, and how it gets
a server of its own from the operator's restart command.

The harness starts no process of a remote server and cannot set its environment, so the operator
sets the fixture server's URLs on it (`announcement`), `probe` makes sure it reaches them, and
`Restarter` runs the operator's `NCI_SI_ACCEPTANCE_RESTART` command wherever a test needs a
server of its own (the acceptance README, "Remote server").
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from nci_si_acceptance.client import (
    AUTHORIZATION_VARIABLE,
    open_remote_session,
    remote_settings,
    wait_for_endpoint,
    withhold_authorization,
)
from nci_si_acceptance.tools import Tools

if TYPE_CHECKING:
    from pathlib import Path

    from nci_si_acceptance.client import Target
    from nci_si_acceptance.fixture_server import FixtureServer
    from nci_si_acceptance.tools import ToolMap

NOT_ANSWERING = "the server under test does not answer"
NOT_REACHING = "the server under test does not reach the fixture server"
RESOLVE_RELEASE = "resolve_release"


def announcement(target: Target, upstream: FixtureServer) -> list[str]:
    """The settings the operator sets on the server under test, one line each."""

    url = target.fixture_url or upstream.url
    lines = [f"{name}={value}" for name, value in remote_settings(url).items()]
    return ["settings of the server under test:", *(f"  {line}" for line in lines)]


def probe(
    url: str,
    authorization: str | None,
    upstream: FixtureServer | None,
    toolmap: ToolMap,
    pinned: dict[str, str],
) -> None:
    """Stop the run unless the server answers and, in fixture mode, reaches the fixture server.

    The probe is a resolve_release call, which asks the platform for the current release; a
    server of a profile without that tool is only asked for its tools. A server that answers
    from a cache filled before the run asks nothing, and fails the probe as one that cannot.
    """

    try:
        asked = _ask(url, authorization, upstream, toolmap, pinned)
    except Exception as error:  # noqa: BLE001 - the type is shown, the message may hold secrets
        pytest.exit(f"{NOT_ANSWERING} ({type(error).__name__})", returncode=1)
    if upstream:
        if asked and not upstream.log():
            pytest.exit(NOT_REACHING, returncode=1)
        upstream.reset()


def _ask(
    url: str,
    authorization: str | None,
    upstream: FixtureServer | None,
    toolmap: ToolMap,
    pinned: dict[str, str],
) -> bool:
    """Call resolve_release on the server, where it has the tool; whether it was called."""

    with open_remote_session(url, authorization) as session:
        tools = Tools(session, toolmap)
        if upstream:
            upstream.reset()
        if not tools.implemented_as(RESOLVE_RELEASE):
            return False
        tools.call(RESOLVE_RELEASE, {"terminology": pinned["terminology"]})
        return True


class Restarter:
    """Gives a test a server of its own by running the operator's restart command.

    The command runs with the operator's environment and the scenario's settings, and the
    harness then waits for the endpoint to answer. A scenario's tests share one restart, as they
    share their settings; a test that must see the server ask upstream (`own_server`) gets one
    of its own; and the server is restarted without settings when the run ends, as the operator
    left it.
    """

    def __init__(self, target: Target, upstream: FixtureServer, log: Path) -> None:
        if target.url is None or target.restart is None:
            raise ValueError("a restart needs a remote server and an operator's restart command")
        self._url, self._command = target.url, target.restart
        self._authorization, self._seconds = target.authorization, target.restart_timeout
        self._upstream = upstream
        self._log = log
        # The scenarios the server runs with now; none, as the operator started it.
        self._scenarios: tuple[str, ...] = ()
        self._startup: tuple[dict[str, Any], ...] = ()

    def serve(
        self, scenarios: tuple[str, ...], settings: dict[str, str], *, fresh: bool = False
    ) -> tuple[dict[str, Any], ...]:
        """The requests the server made while it started, after a restart with `settings`
        unless it already runs with `scenarios` (and `fresh` does not ask for another)."""

        if fresh or scenarios != self._scenarios:
            self._startup = self._restart(settings)
            self._scenarios = scenarios
        return self._startup

    def restore(self) -> None:
        """Leave the server as the operator started it."""

        if self._scenarios:
            self.serve((), {})

    def _restart(self, settings: dict[str, str]) -> tuple[dict[str, Any], ...]:
        seconds = self._seconds
        # The credential is the harness's own: the operator's command has no use for it.
        environment = {k: v for k, v in os.environ.items() if k != AUTHORIZATION_VARIABLE}
        self._upstream.reset()
        # Output goes to a file, not a pipe, so that a server the command leaves running
        # cannot hold the harness waiting for the pipe to close.
        with self._log.open("w", encoding="utf-8") as out:
            try:
                ran = subprocess.run(  # noqa: S602 - the operator's own command line
                    self._command,
                    shell=True,
                    env=environment | settings,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=seconds,
                )
            except subprocess.TimeoutExpired:
                pytest.exit(
                    f"the restart command did not return within {seconds:g} s", returncode=1
                )
        if ran.returncode:
            said = withhold_authorization(self._log.read_text(encoding="utf-8", errors="replace"))
            pytest.exit(
                f"the restart command failed with exit status {ran.returncode}:\n{said[-2000:]}",
                returncode=1,
            )
        if not wait_for_endpoint(self._url, self._authorization, seconds):
            pytest.exit(f"{NOT_ANSWERING} {seconds:g} s after the restart command", returncode=1)
        startup = tuple(self._upstream.log())
        self._upstream.reset()
        return startup

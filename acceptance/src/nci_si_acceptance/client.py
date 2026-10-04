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

A remote server, named by its streamable-HTTP endpoint in place of a command, is tested
through these settings (the acceptance README, "Remote server"):

    NCI_SI_ACCEPTANCE_URL            the endpoint, in place of NCI_SI_ACCEPTANCE_SERVER
    NCI_SI_ACCEPTANCE_AUTHORIZATION  sent as the Authorization header of every request: a
                                     credential, never written anywhere
    NCI_SI_ACCEPTANCE_FIXTURE_BIND   HOST[:PORT] the fixture server listens on
    NCI_SI_ACCEPTANCE_FIXTURE_URL    the base URL the remote server reaches the fixture server by
    NCI_SI_ACCEPTANCE_RESTART        the operator's command that restarts the server with the
                                     settings of a scenario in its environment
    NCI_SI_ACCEPTANCE_RESTART_TIMEOUT  seconds the endpoint may take to answer after a restart
    NCI_SI_ACCEPTANCE_PREPARED       1: the operator has prepared the server's index
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TextIO
from urllib.parse import urlsplit

from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from nci_si_acceptance.fixture_server import UPSTREAM_VARIABLES
from nci_si_acceptance.spec import PROFILES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from pathlib import Path

    from mcp import types

type Mode = Literal["fixture", "live"]

MODE_VARIABLE = "NCI_SI_ACCEPTANCE_MODE"
SERVER_VARIABLE = "NCI_SI_ACCEPTANCE_SERVER"
PROFILE_VARIABLE = "NCI_SI_ACCEPTANCE_PROFILE"
PREPARE_VARIABLE = "NCI_SI_ACCEPTANCE_PREPARE"
URL_VARIABLE = "NCI_SI_ACCEPTANCE_URL"
AUTHORIZATION_VARIABLE = "NCI_SI_ACCEPTANCE_AUTHORIZATION"
FIXTURE_BIND_VARIABLE = "NCI_SI_ACCEPTANCE_FIXTURE_BIND"
FIXTURE_URL_VARIABLE = "NCI_SI_ACCEPTANCE_FIXTURE_URL"
RESTART_VARIABLE = "NCI_SI_ACCEPTANCE_RESTART"
RESTART_TIMEOUT_VARIABLE = "NCI_SI_ACCEPTANCE_RESTART_TIMEOUT"
PREPARED_VARIABLE = "NCI_SI_ACCEPTANCE_PREPARED"
# The settings that mean something only for a remote server: naming one without the URL is a
# usage error, never silently ignored.
REMOTE_ONLY = (
    AUTHORIZATION_VARIABLE,
    FIXTURE_URL_VARIABLE,
    RESTART_VARIABLE,
    RESTART_TIMEOUT_VARIABLE,
    PREPARED_VARIABLE,
)
# The file of concept codes the prepare command indexes, which the suite names to it.
INDEX_CODES_VARIABLE = "NCI_SI_ACCEPTANCE_INDEX_CODES"
DEFAULT_SERVER = "nci-si-mcp serve"
DEFAULT_RESTART_TIMEOUT_SECONDS = 60.0
# How long the harness waits for any one answer of the server, startup included.
READ_TIMEOUT_SECONDS = 60

type Transport = Literal["stdio", "streamable-http"]

# Credentials for licensed upstream content (docs/SPEC.md §8), used in live mode only.
CREDENTIAL_VARIABLES = ("NCI_SI_EVS_LICENSE_KEY", "NCI_SI_CADSR_CREDENTIAL")


@dataclass(frozen=True, slots=True)
class Target:
    """The server under test, the profile it serves and the run mode.

    A command starts a server over stdio; a `url` names a remote one over streamable HTTP,
    which the harness does not start and, but for the operator's `restart` command, cannot
    restart. The `authorization` is a credential: it has no place in the representation.
    """

    mode: Mode
    command: list[str]
    profile: str = "unified"
    prepare: str | None = None
    url: str | None = None
    authorization: str | None = field(default=None, repr=False)
    fixture_bind: tuple[str, int] | None = None
    fixture_url: str | None = None
    restart: str | None = None
    restart_timeout: float = DEFAULT_RESTART_TIMEOUT_SECONDS
    prepared: bool = False

    @classmethod
    def from_env(cls) -> Target:
        mode = os.environ.get(MODE_VARIABLE, "fixture")
        if mode not in ("fixture", "live"):
            raise ValueError(f"{MODE_VARIABLE} must be fixture or live, not {mode!r}")
        profile = os.environ.get(PROFILE_VARIABLE, "unified")
        if profile not in PROFILES:
            raise ValueError(f"{PROFILE_VARIABLE} must be one of {', '.join(PROFILES)}")
        prepare = os.environ.get(PREPARE_VARIABLE) or None
        bind = _bind(os.environ.get(FIXTURE_BIND_VARIABLE))
        if (url := _endpoint()) is None:
            _refuse_remote_only()
            return cls(mode, _command(), profile, prepare, fixture_bind=bind)
        return cls(
            mode,
            [],
            profile,
            None,
            url,
            os.environ.get(AUTHORIZATION_VARIABLE) or None,
            bind,
            _fixture_url(),
            os.environ.get(RESTART_VARIABLE) or None,
            _seconds(RESTART_TIMEOUT_VARIABLE, DEFAULT_RESTART_TIMEOUT_SECONDS),
            _declared(PREPARED_VARIABLE),
        )

    @property
    def transport(self) -> Transport:
        return "stdio" if self.url is None else "streamable-http"

    @property
    def has_index(self) -> bool:
        """Whether the server holds the index set: prepared by the harness's prepare command,
        or declared prepared by the operator of a remote server."""

        return self.prepared if self.url else self.prepare is not None


def _command() -> list[str]:
    command = shlex.split(os.environ.get(SERVER_VARIABLE, DEFAULT_SERVER))
    if not command:
        raise ValueError(f"{SERVER_VARIABLE} must name a command")
    return command


def _endpoint() -> str | None:
    """The remote endpoint, where one is named; naming a command too, or a prepare command
    the harness would never run against it, is a usage error."""

    url = os.environ.get(URL_VARIABLE)
    if not url:
        return None
    if os.environ.get(SERVER_VARIABLE):
        raise ValueError(f"{URL_VARIABLE} and {SERVER_VARIABLE} are both set; name one server")
    if os.environ.get(PREPARE_VARIABLE):
        raise ValueError(
            f"{PREPARE_VARIABLE} is never run against a remote server; "
            f"prepare it yourself and set {PREPARED_VARIABLE}=1"
        )
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
        raise ValueError(f"{URL_VARIABLE} must be an http or https URL")
    return url


def _refuse_remote_only() -> None:
    if named := [name for name in REMOTE_ONLY if os.environ.get(name)]:
        raise ValueError(f"{', '.join(named)} apply only with {URL_VARIABLE}")


def _fixture_url() -> str | None:
    url = os.environ.get(FIXTURE_URL_VARIABLE)
    return url.rstrip("/") if url else None


def _bind(value: str | None) -> tuple[str, int] | None:
    """`HOST` or `HOST:PORT` as the address and port the fixture server listens on."""

    if not value:
        return None
    host, colon, port = value.partition(":")
    if not host or (colon and not port.isdecimal()):
        raise ValueError(f"{FIXTURE_BIND_VARIABLE} must be HOST or HOST:PORT, not {value!r}")
    return host, int(port or 0)


def _seconds(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        seconds = float(value)
    except ValueError:
        seconds = 0.0
    if seconds <= 0:
        raise ValueError(f"{name} must be a positive number of seconds, not {value!r}")
    return seconds


def _declared(name: str) -> bool:
    value = os.environ.get(name)
    if value not in (None, "", "1"):
        raise ValueError(f"{name} must be 1 or unset, not {value!r}")
    return value == "1"


def _upstream_settings(mode: Mode, fixture_url: str | None) -> dict[str, str]:
    """Fixture mode: every upstream names the fixture server. Live: only the credentials."""

    if mode == "live":
        return {name: os.environ[name] for name in CREDENTIAL_VARIABLES if name in os.environ}
    if fixture_url is None:
        raise ValueError("fixture mode needs the fixture server's URL")
    return {name: f"{fixture_url}/{surface}" for name, surface in UPSTREAM_VARIABLES.items()}


def withhold_authorization(text: str) -> str:
    """`text` without the operator's credential, which no output of the harness may carry."""

    secret = os.environ.get(AUTHORIZATION_VARIABLE)
    return text.replace(secret, "[authorization withheld]") if secret else text


def remote_settings(fixture_url: str) -> dict[str, str]:
    """What the operator sets on a remote server in fixture mode: the harness cannot set the
    environment of a process it did not start."""

    return {"NCI_SI_UPSTREAM_MODE": "fixture"} | _upstream_settings("fixture", fixture_url)


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

    def _every_page[T: types.PaginatedResult](
        self, list_page: Callable[..., Awaitable[T]], field: str
    ) -> T:
        """Every page of a list method, followed by nextCursor: the first page's result (and
        so its caching hint) holding the items of all of them."""

        first = page = self._portal.call(list_page)
        items = list(getattr(page, field))
        while page.next_cursor:
            page = self._portal.call(partial(list_page, cursor=page.next_cursor))
            items += getattr(page, field)
        return first.model_copy(update={field: items, "next_cursor": None})

    def list_prompts(self) -> types.ListPromptsResult:
        return self._every_page(self._client.list_prompts, "prompts")

    def get_prompt(self, name: str, arguments: dict[str, str]) -> types.GetPromptResult:
        return self._portal.call(partial(self._client.get_prompt, name, arguments))

    def list_resources(self) -> types.ListResourcesResult:
        return self._every_page(self._client.list_resources, "resources")

    def list_resource_templates(self) -> types.ListResourceTemplatesResult:
        return self._every_page(self._client.list_resource_templates, "resource_templates")

    def read_resource(self, uri: str) -> types.ReadResourceResult:
        return self._portal.call(partial(self._client.read_resource, uri))


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


@asynccontextmanager
async def _http_transport(url: str, authorization: str | None) -> AsyncIterator[Any]:
    """The streamable-HTTP transport of an endpoint, the credential on every request."""

    headers = {"Authorization": authorization} if authorization else None
    async with (
        create_mcp_http_client(headers=headers) as http,
        streamable_http_client(url, http_client=http) as streams,
    ):
        yield streams


@contextmanager
def open_remote_session(url: str, authorization: str | None = None) -> Iterator[Session]:
    """Hold one MCP session with the endpoint at `url`. The error of a failed connection names
    neither the URL nor the credential."""

    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(
            Client(
                _http_transport(url, authorization),
                read_timeout_seconds=READ_TIMEOUT_SECONDS,
                cache=None,
            )
        ) as client,
    ):
        yield Session(portal, client)


def wait_for_endpoint(url: str, authorization: str | None, seconds: float) -> bool:
    """Whether the endpoint answers a tools/list within `seconds`, tried again until then."""

    deadline = time.monotonic() + seconds
    while True:
        try:
            with open_remote_session(url, authorization) as session:
                session.list_tools()
            return True
        except Exception:  # noqa: BLE001 - any failure to answer means not yet
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

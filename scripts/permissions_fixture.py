"""Loopback-only test adapter for the acceptance suite's synthetic authority contract.

Not a deployment entry point or production verifier. Each run owns fresh random tokens,
and all NCI content must come from the suite's fixture upstreams.
"""

import json
import os
from dataclasses import replace
from pathlib import Path

import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from nci_si_mcp.config import Settings
from nci_si_mcp.permissions import Authority, Principal
from nci_si_mcp.transport import create_http_app


class FixturePolicy:
    def __init__(self, path: Path, resource: str) -> None:
        self.path, self.resource = path, resource

    def read(self) -> dict:
        return json.loads(self.path.read_text())

    async def verify_token(self, token: str) -> AccessToken | None:
        actors = {value: actor for actor, value in self.read()["tokens"].items()}
        actor = actors.get(token)
        if actor is None:
            return None
        return AccessToken(
            token=token,
            client_id="fixture-client",
            subject=actor,
            claims={"iss": "https://fixture.invalid"},
            scopes=["mcp"],
            resource=self.resource,
        )

    async def resolve(self) -> Authority | None:
        token = get_access_token()
        if token is None:
            return None
        policy = self.read()["policies"].get(token.subject)
        if policy is None:
            return None
        return Authority(
            Principal("https://fixture.invalid", token.subject or "", client=token.client_id),
            frozenset(policy["capabilities"]),
            "fixture-policy",
            policy["expires_at"],
        )


def main() -> None:
    settings = Settings.from_env()
    if settings.upstream_mode != "fixture":
        raise RuntimeError("This test adapter requires fixture upstreams")
    settings = replace(
        settings, http_host="127.0.0.1", http_port=int(os.environ["NCI_SI_TEST_HTTP_PORT"])
    )
    resource = f"http://127.0.0.1:{settings.http_port}/mcp"
    policy = FixturePolicy(Path(os.environ["NCI_SI_TEST_AUTHORITY_FILE"]), resource)
    auth = AuthSettings.model_validate(
        {
            "issuer_url": "https://fixture.invalid",
            "resource_server_url": resource,
            "required_scopes": ["mcp"],
            "validate_token_resource": True,
        }
    )
    app = create_http_app(
        settings, auth=auth, token_verifier=policy, authority_resolver=policy.resolve
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.http_port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()

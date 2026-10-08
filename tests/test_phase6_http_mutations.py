import asyncio
from dataclasses import replace
from unittest.mock import patch

from nci_si_mcp.audit import compact, hashed
from test_audit import captured, records
from test_http_access import GovernedFixture, LocalPolicy
from test_transport import concept_response, http_app, result

HEADERS = {
    "Authorization": "Bearer synthetic",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}


class HTTPMutationRegressionTest(GovernedFixture):
    def test_governed_transport_refusals_are_not_cacheable(self):
        async def scenario():
            async with http_app(self.configured(), self.context) as client:
                for headers, status in (
                    ({"Host": "attacker.example"}, 421),
                    ({"Origin": "https://attacker.example"}, 403),
                ):
                    response = await concept_response(client, HEADERS | headers)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.headers.get("cache-control"), "no-store")
                    self.assertNotIn("C3262", response.text)

        asyncio.run(scenario())
        self.assertEqual(self.evs.calls, [])

    def test_verified_token_without_caller_policy_cannot_read_content(self):
        policy = LocalPolicy()

        async def scenario():
            async with http_app(
                replace(self.settings, http_sessions="stateless"),
                self.context,
                auth=policy.bundle.auth,
                token_verifier=policy,
            ) as client:
                response = await concept_response(client, HEADERS, release="26.06e")
                payload = result(response)
                self.assertTrue(payload["isError"])
                self.assertEqual(payload["structuredContent"]["error"]["code"], "permission_denied")
                self.assertEqual(response.headers["cache-control"], "no-store")

        asyncio.run(scenario())
        self.assertEqual(self.evs.calls, [])

    def test_secured_completion_audit_hashes_retrieved_and_denied_identifiers(self):
        policy = LocalPolicy()

        async def scenario():
            async with http_app(self.configured(), self.context) as client:
                allowed = result(await concept_response(client, HEADERS, release="26.06e"))
                self.assertEqual(allowed["structuredContent"]["code"], "C3262")
                policy.capabilities = frozenset()
                denied = result(await concept_response(client, HEADERS, release="26.06e"))
                self.assertEqual(denied["structuredContent"]["error"]["code"], "permission_denied")

        with patch("test_http_access.integration", return_value=policy.bundle), captured() as log:
            asyncio.run(scenario())
        audit = records(log)
        self.assertEqual([row["responseCode"] for row in audit], ["ok", "permission_denied"])
        for row in audit:
            self.assertEqual(row["parameters"][compact(hashed("code"))], hashed("C3262"))
            self.assertNotIn("code", row["parameters"])
        self.assertNotIn("C3262", log.getvalue())

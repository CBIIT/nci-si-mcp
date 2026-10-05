import asyncio
import base64
import hashlib
import io
import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fakes import concept
from nci_si_mcp.audit import JsonFormatter, audited, compact, emit, hashed, secrets
from nci_si_mcp.bounds import Budget, RequestBudgetError, budgeted
from nci_si_mcp.config import Settings, configure_logging
from nci_si_mcp.context import Context
from nci_si_mcp.errors import current_correlation_id
from nci_si_mcp.evs import EVSClient
from nci_si_mcp.http_client import UpstreamUnavailableError
from nci_si_mcp.registry import SPECS, invoke
from test_http_client import Reply, ServerTestCase
from test_server import ServerFixture, pinned


@contextmanager
def captured():
    """Capture actual JSON records through the production formatter."""

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("nci_si_mcp")
    level, disabled = logger.level, logging.root.manager.disable
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logging.disable(logging.NOTSET)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logging.disable(disabled)


def records(stream, event="call_completed"):
    found = [json.loads(line) for line in stream.getvalue().splitlines()]
    return [record for record in found if record["event"] == event]


class AuditRecordTest(unittest.TestCase):
    def test_diagnostic_verbosity_does_not_disable_audit_records(self):
        logger = logging.getLogger("nci_si_mcp.audit")
        self.addCleanup(logger.setLevel, logger.level)
        with captured() as stream, patch("nci_si_mcp.config.logging.basicConfig"):
            configure_logging("ERROR")
            logging.getLogger("nci_si_mcp").setLevel(logging.ERROR)
            with audited("lookup", {}, {}, ()) as record:
                record.result = {}

        (result,) = records(stream)
        self.assertEqual((result["level"], result["status"]), ("INFO", "ok"))

    def test_latency_is_reported_in_milliseconds(self):
        with (
            captured() as stream,
            patch("nci_si_mcp.audit.time.perf_counter", return_value=10.25),
            audited("lookup", {}, {}, ()) as record,
        ):
            record.started = 10
            record.result = {}

        (result,) = records(stream)
        self.assertEqual(result["elapsedMs"], 250)

    def test_every_registered_parameter_has_an_explicit_audit_class(self):
        for spec in SPECS:
            with self.subTest(spec.operation):
                self.assertEqual(set(spec.audit), {p.name for p in spec.parameters})
                self.assertLessEqual(set(spec.audit.values()), {"plain", "hash"})

    def test_known_free_text_and_undeclared_fields_are_hashed(self):
        arguments = {"query": "kinase inhibition", "code": "C1", "new field": "private text"}
        with (
            captured() as stream,
            audited("search", arguments, {"query": "hash", "code": "plain"}, ()) as record,
        ):
            record.result = {"results": []}

        (result,) = records(stream)
        expected = hashlib.sha256(b'"kinase inhibition"').hexdigest()
        self.assertEqual(result["parameters"]["query"], {"sha256": expected})
        self.assertEqual(result["parameters"]["code"], "C1")
        self.assertEqual(result["parameters"][compact(hashed("new field"))], hashed("private text"))
        for text in ("kinase inhibition", "new field", "private text"):
            self.assertNotIn(text, stream.getvalue())

    def test_secrets_are_removed_at_the_boundary_including_nested_metadata(self):
        key, credential = "audit-key-canary", "user:audit-password-canary"
        basic = base64.b64encode(credential.encode()).decode()
        with (
            captured() as stream,
            audited(
                "lookup",
                {"code": key},
                {"code": "plain"},
                secrets(key, credential),
                key,
            ) as record,
        ):
            record.result = {
                "provenance": {"release": {"identifier": credential}},
                "truncation": {"perKind": {key: [basic]}},
            }
            emit(
                logging.getLogger("nci_si_mcp.test"),
                logging.WARNING,
                "diagnostic",
                details={key: [credential, basic]},
            )
            logging.getLogger("nci_si_mcp.external").warning("external canary")

        for secret in (key, credential, basic, "audit-password-canary"):
            self.assertNotIn(secret, stream.getvalue())
        (result,) = records(stream)
        self.assertEqual(result["parameters"]["code"], "[redacted]")
        self.assertEqual(result["correlationId"], "[redacted]")
        self.assertEqual(result["release"]["resolved"], [{"identifier": "[redacted]"}])

    def test_external_diagnostics_are_structured_without_messages_or_tracebacks(self):
        with captured() as stream:
            try:
                raise RuntimeError("exception canary")
            except RuntimeError:
                logging.getLogger("nci_si_mcp.external").exception("caller query canary")

        (result,) = records(stream, "diagnostic")
        self.assertEqual(result["messageHash"], hashed("caller query canary")["sha256"])
        self.assertEqual(result["level"], "ERROR")
        self.assertNotIn("canary", stream.getvalue())

    def test_raw_payload_metadata_is_not_mistaken_for_a_release(self):
        with captured() as stream, audited("lookup", {}, {}, ()) as record:
            record.result = {"raw": {"provenance": None}, "upstream": {"provenance": "opaque"}}

        (result,) = records(stream)
        self.assertEqual(result["release"]["resolved"], [])
        self.assertEqual(result["status"], "ok")


class AuditAdapterTest(ServerFixture):
    def test_mcp_runtime_error_is_recorded_before_the_sdk_handles_it(self):
        self.evs.errors["get_concept"] = RuntimeError("runtime canary")
        with captured() as stream:
            result = self.session(
                lambda client: client.call_tool("get_concept", pinned(code="C3262"))
            )

        (record,) = records(stream)
        self.assertTrue(result.is_error)
        self.assertEqual(record["errorType"], "RuntimeError")
        self.assertEqual(record["status"], "error")
        self.assertNotIn("runtime canary", stream.getvalue())

    def test_mcp_emits_one_record_with_the_result_size_release_and_caller_identifier(self):
        self.evs.concepts["C3262"] = dict(self.evs.concepts["C3262"], name="Café")
        with captured() as stream:
            result = self.session(
                lambda client: client.call_tool(
                    "get_concept", pinned(code="C3262"), meta={"correlationId": "caller-audit"}
                )
            )

        (record,) = records(stream)
        content = result.structured_content
        self.assertEqual(record["correlationId"], content["provenance"]["correlationId"])
        self.assertEqual(record["correlationId"], "caller-audit")
        self.assertEqual(record["tool"], "get_concept")
        self.assertEqual(record["target"], {"terminology": "ncit"})
        self.assertEqual(
            record["release"],
            {"requested": "26.06e", "resolved": [content["provenance"]["release"]]},
        )
        self.assertEqual(
            record["resultSize"],
            len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        )
        self.assertEqual(
            (record["status"], record["responseCode"], record["outboundRequests"]), ("ok", "ok", 0)
        )
        self.assertGreaterEqual(record["elapsedMs"], 0)
        self.assertIsNotNone(datetime.fromisoformat(record["timestamp"]).tzinfo)
        self.assertNotIn("Café", stream.getvalue())

    def test_schema_rejection_is_one_record_with_generated_correlation_and_no_request(self):
        with captured() as stream:
            result = self.session(lambda client: client.call_tool("get_concept", {"code": "C3262"}))

        (record,) = records(stream)
        self.assertTrue(result.is_error)
        self.assertEqual(record["responseCode"], "invalid_request")
        self.assertEqual(record["status"], "error")
        self.assertEqual(
            record["correlationId"], result.structured_content["error"]["correlationId"]
        )
        self.assertTrue(record["correlationId"])
        self.assertEqual(record["outboundRequests"], 0)
        self.assertEqual(self.evs.calls, [])

    def test_expected_error_keeps_its_code_but_logs_no_exception_message(self):
        self.evs.errors["get_concept"] = UpstreamUnavailableError("upstream private canary")
        with captured() as stream:
            failed, result = self.call("get_concept", code="C3262")

        (record,) = records(stream)
        self.assertTrue(failed)
        self.assertEqual(record["responseCode"], result["error"]["code"])
        self.assertEqual(record["responseCode"], "upstream_unavailable")
        self.assertNotIn("upstream private canary", stream.getvalue())

    def test_unexpected_errors_propagate_and_do_not_contaminate_the_next_call(self):
        self.evs.errors["get_concept"] = RuntimeError("bug canary")
        with captured() as stream:
            with self.assertRaisesRegex(RuntimeError, "bug canary"):
                invoke(self.context, "get_concept", **pinned(code="C3262"))
            self.evs.errors.clear()
            result = invoke(self.context, "get_concept", **pinned(code="C3262"))

        failure, success = records(stream)
        self.assertEqual(
            (failure["status"], failure["errorType"], failure["resultSize"]),
            ("error", "RuntimeError", None),
        )
        self.assertNotEqual(failure["correlationId"], success["correlationId"])
        self.assertEqual(success["correlationId"], result["provenance"]["correlationId"])
        self.assertIsNone(current_correlation_id())
        self.assertNotIn("bug canary", stream.getvalue())

    def test_truncation_is_copied_with_per_kind_counts(self):
        with captured() as stream:
            failed, result = self.call("get_concept_neighborhood", code="C3262", maxNodes=1)

        (record,) = records(stream)
        self.assertFalse(failed)
        self.assertTrue(result["truncation"]["occurred"])
        self.assertIn("perKind", result["truncation"])
        self.assertEqual(record["truncation"], result["truncation"])
        self.assertEqual(len(record["release"]["resolved"]), 1)

    def test_empty_index_search_has_zero_requests_and_hashed_query(self):
        invoke(self.context, "index_codes", ["C3262"])
        with captured() as stream:
            result = invoke(self.context, "search", "absent-query-canary", mode="bm25")

        (record,) = records(stream)
        self.assertEqual(result["hits"], [])
        self.assertEqual(record["outboundRequests"], 0)
        self.assertEqual(record["truncation"], {"occurred": False})
        self.assertEqual(record["release"]["resolved"], [result["provenance"]["release"]])
        self.assertNotIn("absent-query-canary", stream.getvalue())

    def test_release_mismatch_records_the_requested_release_and_error(self):
        invoke(self.context, "index_codes", ["C3262"])
        with captured() as stream:
            result = invoke(
                self.context, "search_concepts", "ncit", "old", "query-canary", mode="semantic"
            )

        (record,) = records(stream)
        self.assertEqual(result["error"]["code"], "release_mismatch")
        self.assertEqual(record["release"]["requested"], "old")
        self.assertEqual(record["responseCode"], "release_mismatch")
        self.assertNotIn("query-canary", stream.getvalue())

    def test_resource_and_concurrent_mcp_calls_each_have_their_own_record(self):
        async def calls(client):
            return await asyncio.gather(
                client.call_tool(
                    "get_concept", pinned(code="C3262"), meta={"correlationId": "one"}
                ),
                client.call_tool(
                    "get_concept", pinned(code="C4741"), meta={"correlationId": "two"}
                ),
            )

        with captured() as stream:
            self.session(calls)
            resource = self.read("nci-si://concept/ncit/C3262")

        found = records(stream)
        self.assertEqual(len(found), 3)
        by_id = {record["correlationId"]: record for record in found}
        self.assertEqual(by_id["one"]["parameters"]["code"], "C3262")
        self.assertEqual(by_id["two"]["parameters"]["code"], "C4741")
        self.assertEqual(by_id[resource["provenance"]["correlationId"]]["tool"], "concept_resource")


class AuditHttpTest(ServerTestCase):
    def test_split_batches_count_every_actual_attempt(self):
        root = concept(
            "C1",
            active=True,
            children=[{"code": "C2", "name": "Two"}, {"code": "C3", "name": "Three"}],
        )
        server = self.serve(
            Reply(body=json.dumps([root]).encode()),
            Reply(body=b"x" * 2000),
            Reply(body=json.dumps([concept("C2", active=True)]).encode()),
            Reply(body=json.dumps([concept("C3", active=True)]).encode()),
        )
        with tempfile.TemporaryDirectory() as directory:
            context = Context(
                Settings(data_dir=Path(directory)),
                evs=EVSClient(server.url, max_response_bytes=1000),
            )
            with captured() as stream:
                result = invoke(
                    context,
                    "get_concept_neighborhood",
                    **pinned(code="C1", depth=1, kinds=["child"]),
                )

        (record,) = records(stream)
        self.assertEqual([node["code"] for node in result["nodes"]], ["C1", "C2", "C3"])
        self.assertEqual(record["outboundRequests"], 4)
        self.assertEqual(len(server.seen), 4)
        self.assertTrue(
            all(
                headers["x-correlation-id"] == record["correlationId"] for _, headers in server.seen
            )
        )

    def test_retries_count_actual_requests_preserve_the_observer_and_share_correlation(self):
        server = self.serve(Reply(503), Reply(429, headers={"Retry-After": "0"}))
        client = self.client(server)
        observed = []
        client.on_request = observed.append
        with captured() as stream, audited("lookup", {}, {}, (), "http-correlation") as record:
            record.result = client.get_json("/x")

        (result,) = records(stream)
        self.assertEqual(result["outboundRequests"], len(server.seen))
        self.assertEqual(result["outboundRequests"], 3)
        self.assertEqual(len(observed), 3)
        self.assertEqual(
            {headers["x-correlation-id"] for _, headers in server.seen}, {"http-correlation"}
        )
        self.assertEqual(
            {json.loads(line)["correlationId"] for line in stream.getvalue().splitlines()},
            {"http-correlation"},
        )

    def test_budget_refusal_is_not_an_outbound_attempt(self):
        server = self.serve(Reply(503))
        client = self.client(server)
        with (
            captured() as stream,
            self.assertRaises(RequestBudgetError),
            audited("walk", {}, {}, ()),
            budgeted(Budget(requests=1)),
        ):
            client.get_json("/x")

        (record,) = records(stream)
        self.assertEqual(record["outboundRequests"], 1)
        self.assertEqual(len(server.seen), 1)

    def test_shared_client_counts_are_isolated_across_concurrent_calls(self):
        server = self.serve()
        client = self.client(server)
        barrier = Barrier(2)

        def run(count):
            with audited("walk", {}, {}, (), str(count)) as record:
                barrier.wait(timeout=5)
                for _ in range(count):
                    record.result = client.get_json("/x")

        with captured() as stream, ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run, [1, 2]))

        self.assertEqual(
            {record["correlationId"]: record["outboundRequests"] for record in records(stream)},
            {"1": 1, "2": 2},
        )
        self.assertEqual(len(server.seen), 3)

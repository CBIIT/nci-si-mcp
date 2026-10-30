"""Given untrusted MCP inputs, both transports enforce bounds before content requests."""

import asyncio
import hashlib
import json
import logging
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import yaml
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from fakes import data_element
from nci_si_mcp.cadsr import CaDSRClient
from test_cadsr_client import reply
from test_http_client import ServerTestCase
from test_release_selection import initialize_http
from test_server import ServerFixture
from test_transport import http_app

FIXTURE = json.loads((Path(__file__).parent / "fixtures/cadsr-longest-question.json").read_text())
EXPECTED = {
    "search_concepts": {"query": 1000},
    "search_data_elements": {"query": 1000},
    "get_data_element": {"questionText": 2000},
    "match_data_elements": {
        "entities[].name": 2000,
        "entities[].userTip": 2000,
        "entities[].permissibleValues[]": 2000,
    },
    "match_value_meanings": {"values[]": 2000},
    "ground_value": {"text": 2000},
    "harmonize_data_dictionary": {
        "columns[].name": 10000,
        "columns[].description": 10000,
        "columns[].sampleValues[]": 10000,
    },
}


def lengths(node, definitions, path=""):
    if "$ref" in node:
        return lengths(definitions[node["$ref"].split("/")[-1]], definitions, path)
    found = {path: node["maxLength"]} if "maxLength" in node else {}
    for branch in node.get("anyOf", []):
        found.update(lengths(branch, definitions, path))
    for name, child in node.get("properties", {}).items():
        found.update(lengths(child, definitions, f"{path}.{name}".lstrip(".")))
    if "items" in node:
        found.update(lengths(node["items"], definitions, path + "[]"))
    return found


class InputBoundaryTest(ServerFixture, ServerTestCase):
    def upstream(self, *responses):
        server = self.serve(*responses)
        self.settings = replace(self.settings, cadsr_base_url=server.url, cadsr_ftp_url=server.url)
        self.context.settings = self.settings
        self.context.cadsr = CaDSRClient(self.settings)
        return server

    def exchange(self, transport, calls):
        async def http():
            async with http_app(self.settings, self.context) as client:
                headers = await initialize_http(client)
                answers = []
                for number, params in enumerate(calls, 2):
                    message = {
                        "jsonrpc": "2.0",
                        "id": number,
                        "method": "tools/call",
                        "params": params,
                    }
                    response = await client.post("/mcp", headers=headers, json=message)
                    response.raise_for_status()
                    answers.append(response.json()["result"])
                return answers

        async def stdio(log):
            server = StdioServerParameters(
                command=sys.executable,
                args=["-c", "from nci_si_mcp.cli import main; raise SystemExit(main())", "serve"],
                env={
                    "NCI_SI_DATA_DIR": str(self.settings.data_dir),
                    "NCI_SI_CADSR_BASE_URL": self.settings.cadsr_base_url,
                    "NCI_SI_CADSR_FTP_URL": self.settings.cadsr_ftp_url,
                },
            )
            async with Client(stdio_client(server, errlog=log)) as client:
                answers = []
                for item in calls:
                    # The SDK call_tool serializer drops explicit null metadata. Raw dispatch
                    # preserves the wire input being tested, after the real SDK handshake.
                    wire = {"method": "tools/call", "params": item}
                    options = {}
                    client.session._stamp(wire, options)
                    answer = await client.session._dispatcher.send_raw_request(
                        "tools/call", wire["params"], options
                    )
                    answers.append(answer)
                return answers

        if transport == "stdio":
            with tempfile.TemporaryFile(mode="w+") as log:
                answers = asyncio.run(stdio(log))
                log.seek(0)
                return answers, [json.loads(line) for line in log if line.startswith("{")]
        logging.disable(logging.NOTSET)
        with (
            patch("nci_si_mcp.server.configure_logging"),
            self.assertLogs("nci_si_mcp.audit", level="INFO") as captured,
        ):
            answers = asyncio.run(http())
        return answers, [record.structured for record in captured.records]

    def request(self, **arguments):
        return {"name": "get_data_element", "arguments": {"version": "1"} | arguments}

    def assert_invalid(self, answer, parameter):
        self.assertTrue(answer["isError"], answer)
        error = answer["structuredContent"]["error"]
        self.assertEqual(error["code"], "invalid_request")
        self.assertEqual(error["details"]["parameter"], parameter)
        self.assertRegex(error["correlationId"], r"^[A-Za-z0-9._:-]{1,128}$")
        return error

    def test_malformed_correlation_is_private_and_valid_128_reaches_the_wire(self):
        for transport in ("http", "stdio"):
            server = self.upstream(reply({"DataElement": data_element("6148373", version="1")}))
            ids = ["", {"rejected-marker": 7}, "rejected-" + "x" * 129]
            ids.extend(["rejected-\r\nheader", "rejected-😀", "a._:-" + "x" * 123])
            calls = [
                self.request(publicId="6148373") | {"_meta": {"correlationId": cid}} for cid in ids
            ]
            answers, logs = self.exchange(transport, calls)
            completed = [row for row in logs if row["event"] == "call_completed"]
            self.assertEqual(len(completed), 6)
            for answer, audit in zip(answers[:-1], completed[:-1], strict=True):
                error = self.assert_invalid(answer, "_meta.correlationId")
                self.assertEqual(audit["correlationId"], error["correlationId"])
                self.assertEqual(
                    (audit["responseCode"], audit["outboundRequests"]), ("invalid_request", 0)
                )
            self.assertNotIn("rejected-", json.dumps(logs + answers, ensure_ascii=False))
            self.assertFalse(answers[-1]["isError"])
            self.assertEqual(answers[-1]["structuredContent"]["publicId"], "6148373")
            self.assertEqual(server.seen[0][1]["x-correlation-id"], ids[-1])
            self.assertEqual(completed[-1]["correlationId"], ids[-1])
            self.assertEqual(len(server.seen), 1)

    def test_explicit_null_correlation_is_not_missing(self):
        for transport in ("http", "stdio"):
            with self.subTest(transport=transport):
                server = self.upstream(reply({"DataElement": data_element(version="1")}))
                answers, _ = self.exchange(
                    transport, [self.request(publicId="123") | {"_meta": {"correlationId": None}}]
                )
                self.assert_invalid(answers[0], "_meta.correlationId")
                self.assertEqual(server.seen, [])

    def test_question_maximum_passes_unchanged_and_plus_one_never_reaches_cadsr(self):
        for transport in ("http", "stdio"):
            server = self.upstream(
                reply({"DataElements": [{"publicId": "123"}]}),
                reply({"DataElement": data_element(version="1")}),
            )
            answers, _ = self.exchange(
                transport, [self.request(questionText="q" * size) for size in (2000, 2001)]
            )
            self.assertFalse(answers[0]["isError"], answers[0])
            self.assertEqual(answers[0]["structuredContent"]["publicId"], "123")
            self.assert_invalid(answers[1], "questionText")
            self.assertEqual(
                parse_qs(urlsplit(server.seen[0][0]).query)["documentText"], ["q" * 2000]
            )
            self.assertEqual(len(server.seen), 2)

    def test_observed_export_question_resolves_through_local_cadsr_http(self):
        text = FIXTURE["questionText"]
        self.assertEqual(len(text), 1011)
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), FIXTURE["question_sha256"])
        server = self.upstream(
            reply({"DataElements": [{"publicId": "6148373"}]}),
            reply({"DataElement": data_element("6148373", version="1")}),
        )
        answers, _ = self.exchange("http", [self.request(questionText=text)])
        self.assertFalse(answers[0]["isError"], answers[0])
        self.assertEqual(answers[0]["structuredContent"]["publicId"], "6148373")
        self.assertEqual(parse_qs(urlsplit(server.seen[0][0]).query)["documentText"], [text])
        self.assertEqual(server.seen[1][0], "/NCIAPI/1.0/api/DataElement/6148373?version=1")

    def test_supplementary_text_within_semantic_limit_exceeds_encoded_target(self):
        text = "😀" * 1000
        self.assertLessEqual(len(text), 2000)
        self.assertGreater(len(urlencode({"documentText": text}).encode()), 7000)
        for transport in ("http", "stdio"):
            server = self.upstream()
            answers, _ = self.exchange(transport, [self.request(questionText=text)])
            error = self.assert_invalid(answers[0], "questionText")
            self.assertRegex(error["details"]["reason"], r"Encoded request target is 12\d+ bytes")
            self.assertEqual(server.seen, [])

    def test_every_specified_scalar_has_its_concrete_public_schema_maximum(self):
        spec = yaml.safe_load((Path(__file__).parents[1] / "spec/tools.yaml").read_text())
        declared = {
            name: {path: value["maxLength"] for path, value in tool["text_limits"].items()}
            for name, tool in spec.items()
            if "text_limits" in tool
        }
        self.assertEqual(declared, EXPECTED)
        listing = self.session(lambda client: client.list_tools())
        schemas = {
            tool.name: lengths(tool.input_schema, tool.input_schema.get("$defs", {}))
            for tool in listing.tools
        }
        for name, fields in EXPECTED.items():
            self.assertEqual({path: schemas[name].get(path) for path in fields}, fields, name)

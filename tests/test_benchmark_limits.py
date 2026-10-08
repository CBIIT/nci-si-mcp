"""Operator targets and campaign bounds prevent unintended requests before transport."""

import threading
import unittest

from scripts.benchmark_limits import Budget, Limits, RunStoppedError, approved_target


class BenchmarkLimitsTest(unittest.TestCase):
    def test_remote_target_requires_exact_normalized_https_origin_and_path(self):
        allowed = ("https://EXAMPLE.ORG:443/mcp",)
        self.assertEqual(
            approved_target("https://example.org/mcp", allowed), "https://example.org/mcp"
        )
        for url in (
            "https://example.org/other",
            "https://other.example/mcp",
            "https://example.org:444/mcp",
            "http://example.org/mcp",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                approved_target(url, allowed)

    def test_credentials_queries_fragments_and_ambiguous_paths_are_never_admitted(self):
        for url in (
            "https://user:secret@example.org/mcp",
            "https://example.org/mcp?key=secret",
            "https://example.org/mcp#fragment",
            "https://example.org/a/../mcp",
            "https://example.org/%6dcp",
            "https://example.org/mcp\n",
            "https://example.org:invalid/mcp",
            "https://example.org\\evil/mcp",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                approved_target(url, (url,))

    def test_plain_http_is_only_an_explicit_literal_loopback_fixture(self):
        url = "http://127.0.0.1:8000/mcp"
        self.assertEqual(approved_target(url, (url,), fixture=True), url)
        with self.assertRaises(ValueError):
            approved_target(url, (url,))
        for host in ("localhost", "0.0.0.0", "127.0.0.1.evil", "10.0.0.1"):  # noqa: S104 - rejected
            target = f"http://{host}:8000/mcp"
            with self.subTest(host=host), self.assertRaises(ValueError):
                approved_target(target, (target,), fixture=True)

    def test_root_and_ipv6_loopback_targets_normalize_without_loosening_allowlist(self):
        self.assertEqual(
            approved_target("https://example.org", ("https://example.org/",)),
            "https://example.org/",
        )
        self.assertEqual(
            approved_target("http://[::1]:80/mcp", ("http://[::1]/mcp",), fixture=True),
            "http://[::1]/mcp",
        )
        for url in ("https:///mcp", "https://bad_host/mcp"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                approved_target(url, (url,))

    def test_request_budget_counts_every_admission_including_handshakes(self):
        budget = Budget(Limits(max_requests=2), clock=lambda: 10)
        budget.admit()
        budget.admit()
        with self.assertRaisesRegex(RunStoppedError, "request_budget"):
            budget.admit()
        self.assertEqual(budget.requests, 2)

    def test_deadline_and_cancellation_stop_before_another_request(self):
        now = [0.0]
        cancelled = threading.Event()
        budget = Budget(Limits(seconds=5), cancelled=cancelled, clock=lambda: now[0])
        now[0] = 3
        self.assertEqual(budget.remaining(), 2)
        now[0] = 5
        with self.assertRaisesRegex(RunStoppedError, "deadline"):
            budget.admit()
        cancelled.set()
        with self.assertRaisesRegex(RunStoppedError, "cancelled"):
            budget.remaining()
        self.assertEqual(budget.requests, 0)

    def test_limit_inputs_reject_unbounded_nonfinite_and_boolean_values(self):
        for kwargs in (
            {"max_requests": 0},
            {"max_requests": 1001},
            {"max_requests": True},
            {"seconds": float("inf")},
            {"seconds": float("nan")},
            {"seconds": 0},
            {"seconds": 1201},
            {"timeout": 0},
            {"timeout": 121},
            {"repetitions": 0},
            {"repetitions": 21},
            {"warmups": -1},
            {"warmups": 4},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Limits(**kwargs)

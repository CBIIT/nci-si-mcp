import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from fakes import FakeEVS, concept
from nci_si_acceptance.spec import TOOLS
from nci_si_mcp.bounds import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    HARD_MAX_DEPTH,
    HARD_MAX_EDGES,
    HARD_MAX_NODES,
    HARD_MAX_PER_KIND,
    MAX_TRAVERSAL_REQUESTS,
    Budget,
    RequestBudgetError,
    budgeted,
    current_budget,
)
from nci_si_mcp.errors import correlated
from nci_si_mcp.evs import EVSNotFoundError
from test_http_client import Reply, ServerTestCase
from test_service import ServiceTestCase
from test_traversal import HubEVS, child, codes, descendant, related, walk


class BudgetTest(unittest.TestCase):
    def test_traversal_limits_match_the_specification(self):
        bounds = TOOLS["get_concept_neighborhood"]["bounds"]
        self.assertEqual(
            [
                (bounds[key]["default"], bounds[key]["maximum"])
                for key in ("depth", "maxNodes", "maxEdges")
            ],
            [
                (DEFAULT_MAX_DEPTH, HARD_MAX_DEPTH),
                (DEFAULT_MAX_NODES, HARD_MAX_NODES),
                (DEFAULT_MAX_EDGES, HARD_MAX_EDGES),
            ],
        )
        self.assertEqual(bounds["budgetPerKind"]["maximum"], HARD_MAX_PER_KIND)
        for name in ("get_concept_neighborhood", "get_concept_hierarchy"):
            self.assertEqual(TOOLS[name]["requests"], MAX_TRAVERSAL_REQUESTS)

    def test_nested_context_restores_the_outer_budget_even_after_an_error(self):
        outer = Budget(requests=2)
        with budgeted(outer):
            with self.assertRaises(RequestBudgetError), budgeted(Budget(requests=0)):
                current_budget().request()
            current_budget().request()
            self.assertEqual(outer.attempts, 1)
        self.assertIsNone(current_budget())


class RequestBudgetTest(ServerTestCase):
    def test_retries_and_later_requests_share_the_same_allowance(self):
        server = self.serve(Reply(503))
        client = self.client(server)
        budget = Budget(requests=2)
        records = []
        client.on_request = records.append
        with budgeted(budget):
            self.assertEqual(client.get_json("/retry"), {"ok": True})
            with self.assertRaises(RequestBudgetError) as raised:
                client.get_json("/next")
        self.assertEqual(raised.exception.details, {"bound": "requests", "limit": 2, "reached": 2})
        self.assertEqual((len(server.seen), len(records), budget.attempts), (2, 2, 2))
        self.assertEqual([r.attempt for r in records], [1, 2])

    def test_a_retry_cannot_send_request_201(self):
        server = self.serve(*[Reply(503)] * MAX_TRAVERSAL_REQUESTS)
        client = self.client(server, max_attempts=MAX_TRAVERSAL_REQUESTS + 1)
        budget = Budget()
        with budgeted(budget), self.assertRaises(RequestBudgetError):
            client.get_json("/retry")
        self.assertEqual(len(server.seen), MAX_TRAVERSAL_REQUESTS)
        self.assertEqual(budget.attempts, MAX_TRAVERSAL_REQUESTS)

    def test_an_upstream_failure_counts_the_same_attempts_as_the_budget(self):
        server = self.serve(Reply(503), Reply(503), Reply(503))
        budget = Budget()
        with budgeted(budget):
            error = self.failure(self.client(server))
        self.assertEqual(error.details["attempts"], budget.attempts)
        self.assertEqual(budget.attempts, len(server.seen))

    def test_simultaneous_calls_and_later_unbounded_calls_are_independent(self):
        server = self.serve()
        client = self.client(server)
        barrier = Barrier(2)

        def call():
            with budgeted(Budget(requests=1)):
                barrier.wait()
                return client.get_json("/one")

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call) for _ in range(2)]
            self.assertEqual([future.result() for future in futures], [{"ok": True}] * 2)
        self.assertEqual(client.get_json("/outside"), {"ok": True})
        self.assertEqual(len(server.seen), 3)


class BudgetEVS(FakeEVS):
    def _record(self, *args):
        current_budget().request()
        super()._record(*args)


class BudgetHub(HubEVS, BudgetEVS):
    hubs = frozenset({"C2"})


class TraversalBudgetTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(correlated("budget-test"))

    def test_a_later_batch_cannot_discard_the_start_nodes_already_read(self):
        client = BudgetEVS([concept("C1"), concept("C2")])
        with budgeted(Budget(requests=1)), patch("nci_si_mcp.traversal.BATCH_SIZE", 1):
            result = walk(client, start_codes=["C1", "C2"], max_depth=1)
        self.assertEqual(codes(result), ["C1"])
        self.assertEqual(result.truncation.bound, "requests")
        self.assertEqual((result.truncation.limit, result.truncation.reached), (1, 1))
        self.assertGreaterEqual(result.truncation.omitted, 1)
        self.assertFalse(result.truncation.exact)

    def test_request_exhaustion_does_not_hide_an_already_confirmed_missing_start(self):
        client = BudgetEVS([concept("C2")])
        with (
            budgeted(Budget(requests=1)),
            patch("nci_si_mcp.traversal.BATCH_SIZE", 1),
            self.assertRaises(EVSNotFoundError) as raised,
        ):
            walk(client, start_codes=["C1", "C2"])
        self.assertEqual(raised.exception.details["identifiers"], ["C1"])

    def test_parallel_dropped_edges_count_one_omitted_node(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=[related("r1", "C2"), related("r2", "C3"), related("r3", "C3")],
                )
            ]
        )
        result = walk(client, max_depth=1, budget_per_kind=1)
        self.assertEqual(result.truncation.omitted, 1)
        self.assertEqual(result.truncation.to_dict()["perKind"]["role"]["omitted"], 1)

    def test_a_split_batch_keeps_its_successful_half_at_the_request_cap(self):
        client = BudgetHub([concept("C1"), concept("C2")])
        with budgeted(Budget(requests=2)):
            result = walk(client, start_codes=["C1", "C2"], max_depth=1)
        self.assertEqual(codes(result), ["C1"])
        self.assertEqual(result.truncation.bound, "requests")
        self.assertEqual(len(client.calls), 2)

    def test_no_graph_is_an_error_and_a_later_bound_preserves_graph_content(self):
        client = BudgetEVS([concept("C1", children=[child("C2")]), concept("C2")])
        with budgeted(Budget(requests=0)), self.assertRaises(RequestBudgetError):
            walk(client)
        with budgeted(Budget(requests=1)):
            result = walk(client, max_depth=2)
        self.assertEqual(codes(result), ["C1", "C2"])
        self.assertEqual(result.truncation.bound, "requests")

    def test_exact_request_allowance_is_not_itself_truncation(self):
        with budgeted(Budget(requests=1)):
            result = walk(BudgetEVS([concept("C1")]), max_depth=1)
        self.assertEqual(result.truncation.to_dict(), {"occurred": False})

    def test_descendant_requests_preserve_the_first_starts_graph_at_the_cap(self):
        client = BudgetEVS(
            [concept("C1"), concept("C2")],
            descendants={"C1": [descendant("C3", 1)], "C2": [descendant("C4", 1)]},
        )
        with budgeted(Budget(requests=2)):
            result = walk(client, start_codes=["C1", "C2"], edge_types=["descendant"])
        self.assertEqual(codes(result), ["C1", "C2", "C3"])
        self.assertEqual(result.truncation.bound, "requests")
        self.assertEqual(result.truncation.reached, 2)
        self.assertEqual(len(client.calls), 2)

    def test_filtered_duplicates_and_existing_nodes_spend_no_kind_allowance(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=[
                        related("drop", "C9"),
                        related("keep", "C1"),
                        related("keep", "C2"),
                        related("keep", "C2"),
                        related("keep", "C3"),
                        related("keep", "C4"),
                    ],
                )
            ]
        )
        result = walk(client, max_depth=1, budget_per_kind=2, relationship_names=["keep"])
        self.assertEqual(codes(result), ["C1", "C2", "C3"])
        self.assertEqual(len(result.edges), 3)
        self.assertEqual(result.truncation.reached, 2)
        self.assertEqual(result.truncation.omitted, 1)

    def test_kinds_rotate_across_start_nodes_before_the_global_node_limit(self):
        client = FakeEVS(
            [
                concept("C1", roles=[related("role", "C3"), related("role", "C4")]),
                concept("C2", associations=[related("assoc", "C5")]),
            ]
        )
        result = walk(client, start_codes=["C1", "C2"], max_depth=1, max_nodes=4)
        self.assertEqual(set(codes(result)), {"C1", "C2", "C3", "C5"})
        kinds = result.truncation.to_dict()["perKind"]
        self.assertTrue(kinds["role"]["occurred"])
        self.assertEqual(kinds["association"], {"occurred": False})

    def test_another_kind_admitting_a_target_restores_its_previously_blocked_edge(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=[related("r1", "C2"), related("r2", "C3")],
                    associations=[related("a1", "C2"), related("a2", "C3")],
                )
            ]
        )
        result = walk(client, max_depth=1, budget_per_kind=1)
        self.assertEqual(set(codes(result)), {"C1", "C2", "C3"})
        self.assertEqual(len(result.edges), 4)
        self.assertEqual(result.truncation.to_dict(), {"occurred": False})

    def test_explicit_kind_limit_keeps_small_kinds_complete(self):
        client = FakeEVS(
            [
                concept(
                    "C1",
                    roles=[related("role", f"C{n}") for n in range(2, 6)],
                    associations=[related("assoc", "C9")],
                )
            ]
        )
        result = walk(client, max_depth=1, budget_per_kind=2)
        self.assertEqual(set(codes(result)), {"C1", "C2", "C3", "C9"})
        record = result.truncation.to_dict()["perKind"]["role"]
        self.assertEqual(
            (record["bound"], record["limit"], record["reached"]), ("kind_budget", 2, 2)
        )
        self.assertEqual(record["omitted"], 2)


class ServiceBudgetTest(ServiceTestCase):
    def test_kind_budget_validation_happens_before_network_access(self):
        for value in (0, -1, True, 1.5, "3"):
            with self.subTest(value=value):
                result = self.service.traverse(["C3262"], budget_per_kind=value)
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(result["error"]["details"]["parameter"], "budget_per_kind")
                self.assertEqual(self.evs.calls, [])

    def test_release_discovery_spends_the_same_budget_as_the_start_concepts(self):
        self.service.evs = BudgetEVS([concept("C1")])
        with patch("nci_si_mcp.service.Budget", return_value=Budget(requests=1)):
            result = self.service.traverse(["C1"])
        self.assertEqual(result["error"]["code"], "bound_exceeded")
        self.assertEqual(
            result["error"]["details"], {"bound": "requests", "limit": 1, "reached": 1}
        )
        self.assertIsNone(current_budget())

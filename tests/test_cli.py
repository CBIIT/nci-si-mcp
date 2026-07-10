import unittest

from nci_si_mcp.cli import build_parser


class CLITest(unittest.TestCase):
    def test_traversal_parser_exposes_edge_limit(self):
        args = build_parser().parse_args(
            ["traverse", "C3262", "--max-edges", "25", "--edge-type", "role"]
        )

        self.assertEqual(args.max_edges, 25)
        self.assertEqual(args.edge_types, ["role"])


if __name__ == "__main__":
    unittest.main()

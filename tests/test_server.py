import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from nci_si_mcp.config import Settings
from nci_si_mcp.server import create_mcp


@unittest.skipUnless(
    sys.version_info >= (3, 10) and importlib.util.find_spec("mcp") is not None,
    "MCP server test requires Python 3.10+ and the server extra",
)
class ServerTest(unittest.TestCase):
    def test_mcp_server_registration_smoke(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            server = create_mcp(Settings(data_dir=Path(tmp_path), evs_max_attempts=1))

            self.assertIsNotNone(server)


if __name__ == "__main__":
    unittest.main()

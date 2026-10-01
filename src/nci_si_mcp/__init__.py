"""NCI Semantic Infrastructure MCP server prototype."""

import logging

__all__ = ["__version__"]

__version__ = "0.1.0"

# Stay silent unless the application (the CLI or MCP server) configures logging.
logging.getLogger(__name__).addHandler(logging.NullHandler())

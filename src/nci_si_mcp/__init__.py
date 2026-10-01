"""NCI Semantic Infrastructure MCP server prototype."""

import logging
from importlib.metadata import version

__all__ = ["__version__"]

# The version of the installed distribution, which the build derives from the git tag.
__version__ = version("nci-si-mcp")

# Stay silent unless the application (the CLI or MCP server) configures logging.
logging.getLogger(__name__).addHandler(logging.NullHandler())

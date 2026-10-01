"""Compatibility shim for older pip editable installs.

Some managed Python environments still require setup.py for
`pip install -e .` even when pyproject.toml is present.
"""

from pathlib import Path

from setuptools import find_packages, setup


ROOT = Path(__file__).parent


setup(
    name="nci-si-mcp",
    version="0.1.0",
    description="EVS-first NCI Semantic Infrastructure MCP server prototype",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.9",
    install_requires=[],
    extras_require={
        "server": ["mcp>=2.0,<3; python_version >= '3.10'"],
        "embeddings": ["sentence-transformers>=3.0.0"],
        "test": ["pytest>=7.0.0", "pytest-cov>=4.1.0"],
        "dev": ["mypy>=1.8.0", "ruff>=0.5.0"],
    },
    entry_points={"console_scripts": ["nci-si-mcp=nci_si_mcp.cli:main"]},
)

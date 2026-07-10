"""Runtime configuration for the NCI SI MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_EVS_BASE_URL = "https://api-evsrest.nci.nih.gov"
DEFAULT_EMBEDDING_MODEL = "hashing"


@dataclass(frozen=True)
class Settings:
    evs_base_url: str = DEFAULT_EVS_BASE_URL
    data_dir: Path = Path(".nci-si-mcp")
    embedding_provider: str = "hashing"
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            evs_base_url=os.getenv("NCI_SI_EVS_BASE_URL", DEFAULT_EVS_BASE_URL).rstrip("/"),
            data_dir=Path(os.getenv("NCI_SI_DATA_DIR", ".nci-si-mcp")),
            embedding_provider=os.getenv("NCI_SI_EMBEDDING_PROVIDER", "hashing"),
            embedding_model=os.getenv("NCI_SI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            timeout_seconds=float(os.getenv("NCI_SI_TIMEOUT_SECONDS", "30")),
        )

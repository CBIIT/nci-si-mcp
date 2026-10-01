"""Runtime configuration for the NCI SI MCP server."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import urlsplit

DEFAULT_EVS_BASE_URL = "https://api-evsrest.nci.nih.gov"
DEFAULT_EMBEDDING_MODEL = "hashing"

_Number = TypeVar("_Number", int, float)


def _env_number(name: str, default: str, cast: Callable[[str], _Number]) -> _Number:
    value = os.getenv(name, default)
    try:
        return cast(value)
    except ValueError:
        raise ValueError(f"{name} must be a number, not {value!r}") from None


@dataclass(frozen=True)
class Settings:
    evs_base_url: str = DEFAULT_EVS_BASE_URL
    data_dir: Path = Path(".nci-si-mcp")
    embedding_provider: str = "hashing"
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    timeout_seconds: float = 30.0
    evs_max_attempts: int = 3
    evs_retry_backoff_seconds: float = 0.25
    evs_max_response_bytes: int = 10 * 1024 * 1024
    index_batch_size: int = 100
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        # The comparisons are written so that NaN fails them.
        url = urlsplit(self.evs_base_url)
        if url.scheme not in ("http", "https") or not url.netloc:
            raise ValueError("NCI_SI_EVS_BASE_URL must be an http(s) URL")
        if not self.timeout_seconds > 0:
            raise ValueError("NCI_SI_TIMEOUT_SECONDS must be greater than zero")
        if self.evs_max_attempts < 1:
            raise ValueError("NCI_SI_EVS_MAX_ATTEMPTS must be at least 1")
        if not self.evs_retry_backoff_seconds >= 0:
            raise ValueError("NCI_SI_EVS_RETRY_BACKOFF_SECONDS must not be negative")
        if self.evs_max_response_bytes < 1:
            raise ValueError("NCI_SI_EVS_MAX_RESPONSE_BYTES must be at least 1")
        if self.index_batch_size < 1:
            raise ValueError("NCI_SI_INDEX_BATCH_SIZE must be at least 1")
        if self.log_level.upper() not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("NCI_SI_LOG_LEVEL must be a standard logging level")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            evs_base_url=os.getenv("NCI_SI_EVS_BASE_URL", DEFAULT_EVS_BASE_URL).rstrip("/"),
            data_dir=Path(os.getenv("NCI_SI_DATA_DIR", ".nci-si-mcp")),
            embedding_provider=os.getenv("NCI_SI_EMBEDDING_PROVIDER", "hashing"),
            embedding_model=os.getenv("NCI_SI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            timeout_seconds=_env_number("NCI_SI_TIMEOUT_SECONDS", "30", float),
            evs_max_attempts=_env_number("NCI_SI_EVS_MAX_ATTEMPTS", "3", int),
            evs_retry_backoff_seconds=_env_number(
                "NCI_SI_EVS_RETRY_BACKOFF_SECONDS", "0.25", float
            ),
            evs_max_response_bytes=_env_number(
                "NCI_SI_EVS_MAX_RESPONSE_BYTES", str(10 * 1024 * 1024), int
            ),
            index_batch_size=_env_number("NCI_SI_INDEX_BATCH_SIZE", "100", int),
            log_level=os.getenv("NCI_SI_LOG_LEVEL", "INFO").upper(),
        )


def configure_logging(level: str) -> None:
    """Configure diagnostics on stderr so MCP stdout remains protocol-only."""

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

"""Runtime configuration for the NCI SI MCP server."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .embeddings import normalize_embedding_settings

# Upper bound for the timeout and backoff settings; socket timeouts overflow far above it.
MAX_SECONDS = 3600
MAX_ATTEMPTS = 10
# A response is read into memory whole before it is parsed, so the limit is capped.
MAX_RESPONSE_BYTES = 1024**3


def _has_plain_characters(value: str) -> bool:
    # urlsplit drops tabs and newlines, and urllib rejects them later. A query
    # or a fragment would swallow the path that is appended to the base URL.
    return value.isascii() and value.isprintable() and not any(char in value for char in " ?#")


def _is_evs_url(value: str) -> bool:
    """Whether urllib can request `value` and append a path to it."""

    if not _has_plain_characters(value):
        return False
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        host.encode("idna")  # an empty or over-long label raises UnicodeError
        port = url.port  # None, or 0 to 65535: anything else raises
    except ValueError:
        return False
    return url.scheme in ("http", "https") and bool(host) and "@" not in url.netloc and port != 0


def _require_between(name: str, value: float, low: int, high: int) -> None:
    # Written so that NaN fails the comparison.
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")


DEFAULT_EVS_BASE_URL = "https://api-evsrest.nci.nih.gov"
DEFAULT_EMBEDDING_MODEL = "hashing"


def _env_number[Number: (int, float)](
    name: str, default: str, cast: Callable[[str], Number]
) -> Number:
    value = os.getenv(name, default)
    try:
        return cast(value)
    except ValueError:
        kind = "an integer" if cast is int else "a number"
        raise ValueError(f"{name} must be {kind}, not {value!r}") from None


@dataclass(frozen=True, slots=True)
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
        if not _is_evs_url(self.evs_base_url):
            raise ValueError(
                "NCI_SI_EVS_BASE_URL must be a plain http(s) URL without credentials, query "
                f"or fragment, not {self.evs_base_url!r}"
            )
        # Written so that NaN fails the comparison.
        if not 0 < self.timeout_seconds <= MAX_SECONDS:
            raise ValueError(
                f"NCI_SI_TIMEOUT_SECONDS must be greater than zero and at most {MAX_SECONDS}"
            )
        _require_between("NCI_SI_EVS_MAX_ATTEMPTS", self.evs_max_attempts, 1, MAX_ATTEMPTS)
        _require_between(
            "NCI_SI_EVS_RETRY_BACKOFF_SECONDS", self.evs_retry_backoff_seconds, 0, MAX_SECONDS
        )
        _require_between(
            "NCI_SI_EVS_MAX_RESPONSE_BYTES", self.evs_max_response_bytes, 1, MAX_RESPONSE_BYTES
        )
        if self.index_batch_size < 1:
            raise ValueError("NCI_SI_INDEX_BATCH_SIZE must be at least 1")
        if self.log_level.upper() not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("NCI_SI_LOG_LEVEL must be a standard logging level")
        normalize_embedding_settings(self.embedding_provider, self.embedding_model)

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = os.getenv("NCI_SI_DATA_DIR", ".nci-si-mcp")
        if not data_dir.strip():
            raise ValueError("NCI_SI_DATA_DIR must not be empty")
        try:
            expanded_data_dir = Path(data_dir).expanduser()
        except RuntimeError as exc:
            raise ValueError(
                f"NCI_SI_DATA_DIR names a home directory that cannot be resolved: {data_dir!r}"
            ) from exc
        return cls(
            evs_base_url=os.getenv("NCI_SI_EVS_BASE_URL", DEFAULT_EVS_BASE_URL).rstrip("/"),
            data_dir=expanded_data_dir,
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

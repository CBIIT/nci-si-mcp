"""Cache hints for the content and status results the server currently emits."""

LONG_TTL_MS = 86_400_000
RELEASE_REPORT_ALIASES = frozenset({"monthly", "latest", "monthly-latest"})
RELEASE_REPORT_URIS = frozenset(
    f"nci-si://release/ncit/{alias}" for alias in RELEASE_REPORT_ALIASES
)


def cache_hint(*, resolution: bool = False, error: bool = False) -> dict[str, int | str]:
    """Governed content is shareable; failed calls must not be cached."""

    return {
        "ttlMs": 0 if resolution or error else LONG_TTL_MS,
        "cacheScope": "private" if error else "public",
    }

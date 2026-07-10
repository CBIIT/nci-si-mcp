"""caDSR adapter boundary.

This module intentionally avoids returning fabricated CDE data. It records the
reuse spike as an implementation requirement and exposes status to MCP clients.
"""

from __future__ import annotations

from .models import CadsrStatus

REUSE_TARGETS = [
    "CDE AI Project CDE Match API",
    "FAIR Data Workbench caDSR access path",
    "existing BM25/vector CDE indexes",
    "internal fine-tuned embedding models",
    "confidence scoring and cascade ranking rules",
]


class CadsrAdapter:
    def status(self) -> CadsrStatus:
        return CadsrStatus(
            state="reuse_pending",
            message=(
                "caDSR search is intentionally disabled until reusable internal "
                "APIs, schemas, credentials, and ranking logic are confirmed."
            ),
            reuse_targets=REUSE_TARGETS,
            findings=[],
        )

"""Atomic bounded files in owned local validation workspaces."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def write_json(path: Path, value: Any) -> None:
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, sort_keys=True)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def read_bytes(path: Path, maximum: int) -> bytes:
    if path.is_symlink():
        raise ValueError("Validation artifact cannot be a symlink")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Validation artifact exceeds its size bound")
    return raw

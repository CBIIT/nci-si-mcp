"""Atomic bounded files in owned local validation workspaces."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def write_json(path: Path, value: Any, *, indent: int | None = None) -> None:
    """Replace `path` atomically with `value` as JSON; the old file survives any failure.

    The default is the compact, key-sorted form of the evidence files. With `indent` the
    keys keep their order and the text ends with a newline, as in the benchmark reports.
    """

    if path.is_symlink():
        raise ValueError("Output cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=indent, sort_keys=indent is None)
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text if indent is None else text + "\n")
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

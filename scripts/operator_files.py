"""Atomic bounded files in owned local validation workspaces."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal


def write_json(path: Path, value: Any, *, form: Literal["evidence", "report"] = "evidence") -> None:
    """Replace `path` atomically with `value` as JSON; a failed write leaves the old file.

    The "evidence" form is compact with sorted keys and no final newline. The "report" form,
    used by the benchmark reports, is indented by two, keeps the key order and ends with a
    newline.
    """

    if path.is_symlink():
        raise ValueError("Output cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(value, sort_keys=True)
        if form == "evidence"
        else json.dumps(value, indent=2) + "\n"
    )
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
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

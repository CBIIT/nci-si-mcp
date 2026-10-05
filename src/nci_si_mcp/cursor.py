"""Opaque continuation positions bound to the arguments of a pinned hierarchy call."""

import base64
import binascii
import json
from typing import Any

from .errors import InputValidationError

MAX_CURSOR_LENGTH = 8192


def encode(arguments: dict[str, Any], offset: int) -> str:
    payload = json.dumps({"v": 1, "arguments": arguments, "offset": offset}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode(token: str | None, arguments: dict[str, Any]) -> int:
    if token is None:
        return 0
    payload = _payload(token)
    if payload.get("v") != 1 or payload.get("arguments") != arguments:
        raise InputValidationError("The cursor does not continue these applied arguments", "cursor")
    offset = payload.get("offset")
    if type(offset) is not int or offset <= 0:
        raise InputValidationError("The cursor has an invalid position", "cursor")
    return offset


def _payload(token: str) -> dict[str, Any]:
    if not token or len(token) > MAX_CURSOR_LENGTH:
        raise InputValidationError("The cursor is empty or too long", "cursor")
    try:
        value = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
    except ValueError, binascii.Error, UnicodeError:
        raise InputValidationError("The cursor is malformed", "cursor") from None
    if not isinstance(value, dict):
        raise InputValidationError("The cursor is not a continuation record", "cursor")
    return value

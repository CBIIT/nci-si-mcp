"""The identifier formats that scripts check, for `re.fullmatch`; embedded uses compose them."""

from typing import Final

SHA1_HEX: Final[str] = "[a-f0-9]{40}"
SHA256_HEX: Final[str] = "[a-f0-9]{64}"
JOB_ID: Final[str] = "[a-f0-9]{32}"
RELEASE_TAG: Final[str] = r"v\d+\.\d+\.\d+"

"""The identifier formats that scripts check: one pattern for each, composed where embedded."""

SHA1_HEX = "[a-f0-9]{40}"
SHA256_HEX = "[a-f0-9]{64}"
JOB_ID = "[a-f0-9]{32}"
RELEASE_TAG = r"v\d+\.\d+\.\d+"

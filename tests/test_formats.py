"""The identifier formats accept exactly their length and alphabet, and nothing around them."""

import re
import unittest
from pathlib import Path

from scripts.formats import JOB_ID, RELEASE_TAG, SHA1_HEX, SHA256_HEX

RELEASE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


class HexFormatTest(unittest.TestCase):
    def test_each_hex_format_accepts_only_its_length_in_lowercase(self):
        for name, pattern, length in (
            ("sha1", SHA1_HEX, 40),
            ("sha256", SHA256_HEX, 64),
            ("job", JOB_ID, 32),
        ):
            accepted = "a1" * (length // 2)
            rejected = {
                "one short": accepted[:-1],
                "one long": accepted + "a",
                "uppercase": accepted.upper().replace("1", "A"),
                "prefix": "x" + accepted,
                "suffix": accepted + "\n",
                "non-hex": "g" + accepted[1:],
                "empty": "",
            }
            with self.subTest(name=name):
                self.assertIsNotNone(re.fullmatch(pattern, accepted))
                for case, value in rejected.items():
                    with self.subTest(name=name, case=case):
                        self.assertIsNone(re.fullmatch(pattern, value))


class ReleaseTagTest(unittest.TestCase):
    def test_tag_is_three_numeric_parts_behind_a_v(self):
        for value in ("v0.15.1", "v10.20.300"):
            with self.subTest(value=value):
                self.assertIsNotNone(re.fullmatch(RELEASE_TAG, value))
        for value in ("0.15.1", "v1.2", "v1.2.3.4", "v1.2.3-rc1", "V1.2.3", "v1.2.3\n", "xv1.2.3"):
            with self.subTest(value=value):
                self.assertIsNone(re.fullmatch(RELEASE_TAG, value))

    def test_release_workflow_shell_check_is_the_anchored_form_of_the_tag_format(self):
        found = re.findall(r'\[\[ ! "\$tag" =~ (\S+) \]\]', RELEASE_WORKFLOW.read_text())
        self.assertEqual(found, ["^" + RELEASE_TAG.replace(r"\d", "[0-9]") + "$"])

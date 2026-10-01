import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from nci_si_mcp.config import DEFAULT_EVS_BASE_URL, Settings, configure_logging


def settings_from(**environment):
    with patch.dict(os.environ, environment, clear=True):
        return Settings.from_env()


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        settings = settings_from()

        self.assertEqual(settings, Settings())
        self.assertEqual(settings.evs_base_url, DEFAULT_EVS_BASE_URL)
        self.assertEqual(settings.data_dir, Path(".nci-si-mcp"))

    def test_environment_overrides(self):
        settings = settings_from(
            NCI_SI_EVS_BASE_URL="http://localhost:8080/",
            NCI_SI_DATA_DIR="/data/index",
            NCI_SI_TIMEOUT_SECONDS="2.5",
            NCI_SI_EVS_MAX_ATTEMPTS="1",
            NCI_SI_INDEX_BATCH_SIZE="7",
            NCI_SI_LOG_LEVEL="debug",
            NCI_SI_EMBEDDING_PROVIDER="sentence-transformers",
            NCI_SI_EMBEDDING_MODEL="all-MiniLM-L6-v2",
        )

        self.assertEqual(settings.evs_base_url, "http://localhost:8080")
        self.assertEqual(settings.data_dir, Path("/data/index"))
        self.assertEqual(settings.timeout_seconds, 2.5)
        self.assertEqual(settings.evs_max_attempts, 1)
        self.assertEqual(settings.index_batch_size, 7)
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.embedding_provider, "sentence-transformers")
        self.assertEqual(settings.embedding_model, "all-MiniLM-L6-v2")

    def test_each_invalid_value_names_its_variable(self):
        for variable, value in (
            ("NCI_SI_EVS_BASE_URL", "api-evsrest.nci.nih.gov"),
            ("NCI_SI_EVS_BASE_URL", "file:///etc/passwd"),
            ("NCI_SI_EVS_BASE_URL", "file://localhost/etc/passwd"),
            ("NCI_SI_EVS_BASE_URL", "ftp://example.org"),
            ("NCI_SI_EVS_BASE_URL", "https:///path"),
            ("NCI_SI_EVS_BASE_URL", "http://"),
            ("NCI_SI_EVS_BASE_URL", "https://example.org:abc"),
            ("NCI_SI_EVS_BASE_URL", "https://example.org?x=1"),
            ("NCI_SI_EVS_BASE_URL", "https://example.org/api#top"),
            ("NCI_SI_EVS_BASE_URL", "https://exa mple.org"),
            ("NCI_SI_EVS_BASE_URL", ""),
            ("NCI_SI_TIMEOUT_SECONDS", "0"),
            ("NCI_SI_TIMEOUT_SECONDS", "nan"),
            ("NCI_SI_TIMEOUT_SECONDS", "inf"),
            ("NCI_SI_TIMEOUT_SECONDS", "1e300"),
            ("NCI_SI_TIMEOUT_SECONDS", "soon"),
            ("NCI_SI_EVS_MAX_ATTEMPTS", "0"),
            ("NCI_SI_EVS_MAX_ATTEMPTS", "three"),
            ("NCI_SI_EVS_MAX_ATTEMPTS", "3.0"),
            ("NCI_SI_EVS_RETRY_BACKOFF_SECONDS", "-1"),
            ("NCI_SI_EVS_RETRY_BACKOFF_SECONDS", "nan"),
            ("NCI_SI_EVS_RETRY_BACKOFF_SECONDS", "inf"),
            ("NCI_SI_EMBEDDING_PROVIDER", "bogus"),
            ("NCI_SI_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            ("NCI_SI_EVS_MAX_RESPONSE_BYTES", "0"),
            ("NCI_SI_INDEX_BATCH_SIZE", "0"),
            ("NCI_SI_LOG_LEVEL", "loud"),
        ):
            with self.subTest(variable=variable, value=value), self.assertRaises(ValueError) as raised:
                settings_from(**{variable: value})
            self.assertIn(variable, str(raised.exception))


class LoggingTest(unittest.TestCase):
    def test_diagnostics_go_to_stderr_at_the_configured_level(self):
        with patch("nci_si_mcp.config.logging.basicConfig") as basic_config:
            configure_logging("debug")

        self.assertIs(basic_config.call_args.kwargs["stream"], sys.stderr)
        self.assertEqual(basic_config.call_args.kwargs["level"], logging.DEBUG)


if __name__ == "__main__":
    unittest.main()

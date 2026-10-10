import io
import json
import logging
import sys
import types
from contextlib import redirect_stderr
from dataclasses import replace
from unittest.mock import patch

from fakes import concept
from nci_si_mcp import container_entry
from nci_si_mcp.audit import JsonFormatter
from nci_si_mcp.embeddings import HashingEmbeddingProvider, SentenceTransformersProvider
from nci_si_mcp.index import NoActiveIndexError
from test_server import ServerFixture


class ContainerTest(ServerFixture):
    def setUp(self):
        super().setUp()
        self.settings = replace(self.settings, http_require_index=True, transport="streamable-http")

    def index(self):
        self.context.index.upsert_concepts([concept("C1")], None, HashingEmbeddingProvider())

    def test_missing_required_index_is_rejected_without_creating_one(self):
        self.context.index.db_path.unlink()
        with self.assertRaises(NoActiveIndexError):
            container_entry.prepared_index(self.settings)
        self.assertFalse(self.context.index.db_path.exists())

    def test_empty_or_incompatible_index_is_rejected(self):
        with self.assertRaises(NoActiveIndexError):
            container_entry.prepared_index(self.settings)
        self.index()
        self.assertEqual(
            container_entry.prepared_index(self.settings).get_active_manifest().concept_count, 1
        )

    def test_an_index_built_for_another_model_stops_startup_naming_the_index(self):
        logging.disable(logging.NOTSET)
        self.index()
        wrong = replace(
            self.settings, embedding_provider="sentence-transformers", embedding_model="other"
        )
        with (
            patch.object(container_entry.Settings, "from_env", return_value=wrong),
            patch.object(container_entry, "configure_logging"),
            # The image has no cache for the other model: the index check must come first.
            patch.object(
                container_entry, "SentenceTransformersProvider", side_effect=OSError("not cached")
            ),
            patch.object(container_entry, "run_http") as serve,
            self.assertLogs("nci_si_mcp.container_entry", level="ERROR") as logs,
        ):
            self.assertEqual(container_entry.main(), 1)
        serve.assert_not_called()
        records = [json.loads(JsonFormatter().format(record)) for record in logs.records]
        self.assertEqual(
            [(r["asset"], r["errorType"]) for r in records], [("index", "IndexCompatibilityError")]
        )

    def test_startup_reports_one_safe_asset_failure(self):
        logging.disable(logging.NOTSET)
        with (
            patch.object(container_entry.Settings, "from_env", return_value=self.settings),
            patch.object(container_entry, "configure_logging"),
            patch.object(container_entry, "prepared_index", side_effect=OSError("sensitive-token")),
            self.assertLogs("nci_si_mcp.container_entry", level="ERROR") as logs,
        ):
            self.assertEqual(container_entry.main(), 1)
        records = [json.loads(JsonFormatter().format(record)) for record in logs.records]
        self.assertEqual(
            [(r["event"], r["asset"], r["errorType"]) for r in records],
            [("container_startup_failed", "index", "OSError")],
        )
        self.assertNotIn("sensitive-token", json.dumps(records))

    def test_http_serve_keeps_the_active_build(self):
        self.index()
        before = self.context.index.get_active_manifest()
        seen = []

        def serve(settings, context):
            seen.append((settings.transport, context.index.get_active_manifest()))

        with (
            patch.object(container_entry.Settings, "from_env", return_value=self.settings),
            patch.object(container_entry, "run_http", side_effect=serve),
        ):
            self.assertEqual(container_entry.main(), 0)
        self.assertEqual(seen, [("streamable-http", before)])
        self.assertEqual(self.context.index.get_active_manifest(), before)

    def test_absent_local_model_fails_before_importing_the_embedding_library(self):
        absent = str(self.settings.data_dir / "absent-model")
        with (
            patch.dict(sys.modules, {"sentence_transformers": None}),
            self.assertRaisesRegex(FileNotFoundError, "external model"),
        ):
            SentenceTransformersProvider(absent, local_files_only=True)

    def test_cached_model_loads_offline_without_changing_manifest_identity(self):
        cached = str(self.settings.data_dir)

        class LocalModel:
            def __init__(self, name, *, local_files_only=False):
                if (name, local_files_only) != (cached, True):
                    raise OSError("Only the local cache is available")

            def encode(self, texts, *, normalize_embeddings, show_progress_bar):
                if show_progress_bar:
                    raise ValueError("Progress output is disabled")
                return [[len(text), int(normalize_embeddings)] for text in texts]

        def snapshot(name, *, local_files_only=False):
            if (name, local_files_only) != ("org/model", True):
                raise OSError("Network is unavailable")
            return cached

        modules = {
            "sentence_transformers": types.SimpleNamespace(SentenceTransformer=LocalModel),
            "huggingface_hub": types.SimpleNamespace(snapshot_download=snapshot),
        }
        with patch.dict(sys.modules, modules):
            provider = SentenceTransformersProvider("org/model", local_files_only=True)
            local = SentenceTransformersProvider(cached, local_files_only=True)
        self.assertEqual(
            (provider.model, [row.tolist() for row in provider.embed(["abc"])]),
            ("org/model", [[3.0, 1.0]]),
        )
        self.assertEqual([row.tolist() for row in local.embed(["ab"])], [[2.0, 1.0]])

    def test_bad_model_setting_names_the_variable_without_its_value(self):
        logging.disable(logging.NOTSET)
        error = ValueError("NCI_SI_EMBEDDING_MODEL cannot be sensitive-token")
        with (
            patch.object(container_entry.Settings, "from_env", side_effect=error),
            patch.object(container_entry, "configure_logging"),
            self.assertLogs("nci_si_mcp.container_entry", level="ERROR") as logs,
        ):
            self.assertEqual(container_entry.main(), 1)
        records = [json.loads(JsonFormatter().format(record)) for record in logs.records]
        self.assertEqual([r["parameter"] for r in records], ["NCI_SI_EMBEDDING_MODEL"])
        self.assertNotIn("sensitive-token", json.dumps(records))

    def test_fatal_startup_diagnostic_survives_critical_verbosity(self):
        logging.disable(logging.NOTSET)
        settings = replace(self.settings, log_level="CRITICAL")
        output = io.StringIO()
        with (
            patch.object(logging.getLogger(), "handlers", []),
            redirect_stderr(output),
            patch.object(container_entry.Settings, "from_env", return_value=settings),
        ):
            self.assertEqual(container_entry.main(), 1)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            [(r["event"], r["asset"]) for r in records], [("container_startup_failed", "index")]
        )

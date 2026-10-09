"""Container startup uses supplied assets without permitting model downloads."""

import logging
import sys
import types
from dataclasses import replace
from unittest.mock import patch

from fakes import concept
from nci_si_mcp import container_entry
from nci_si_mcp.embeddings import HashingEmbeddingProvider, SentenceTransformersProvider
from test_server import ServerFixture
from test_storage_review import update_manifest


class OfflineModel:
    def __init__(self, _name, *, local_files_only=False):
        if not local_files_only:
            raise OSError("Network loading is forbidden")

    def encode(self, texts, **_options):
        return [[len(text), 1] for text in texts]


class ContainerOfflineTest(ServerFixture):
    def test_legacy_active_index_fails_startup_until_operator_rebuilds_it(self):
        settings = replace(self.settings, transport="streamable-http", http_require_index=1)
        self.context.index.upsert_concepts([concept("C1")], None, HashingEmbeddingProvider())
        legacy = replace(self.context.index.get_active_manifest(), needs_rebuild=True)
        update_manifest(self.context.index, legacy)
        logging.disable(logging.NOTSET)
        with (
            patch.object(container_entry.Settings, "from_env", return_value=settings),
            patch.object(container_entry, "configure_logging"),
            patch.object(
                container_entry, "run_http", side_effect=AssertionError("Cannot serve legacy index")
            ),
            self.assertLogs("nci_si_mcp.container_entry", level="ERROR") as captured,
        ):
            self.assertEqual(container_entry.main(), 1)
        self.assertEqual(captured.records[0].structured["asset"], "index")
        self.assertEqual(captured.records[0].structured["errorType"], "PlatformError")
        self.assertEqual(self.context.index.get_active_manifest(), legacy)

    def test_startup_serves_an_external_model_with_network_loading_disabled(self):
        settings = replace(
            self.settings,
            transport="streamable-http",
            http_require_index=1,
            embedding_provider="sentence-transformers",
            embedding_model=str(self.settings.data_dir),
        )
        served = []

        def serve(_settings, context):
            served.append([row.tolist() for row in context.embedding_provider.embed(["abc"])])

        with patch.dict(
            sys.modules,
            {"sentence_transformers": types.SimpleNamespace(SentenceTransformer=OfflineModel)},
        ):
            provider = SentenceTransformersProvider(settings.embedding_model, local_files_only=True)
            self.context.index.upsert_concepts([concept("C1")], None, provider)
            before = self.context.index.get_active_manifest()
            with (
                patch.object(container_entry.Settings, "from_env", return_value=settings),
                patch.object(container_entry, "run_http", serve),
            ):
                self.assertEqual(container_entry.main(), 0)
        self.assertEqual(served, [[[3.0, 1.0]]])
        self.assertEqual(self.context.index.get_active_manifest(), before)

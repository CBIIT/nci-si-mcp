import tempfile
import unittest
from pathlib import Path

from nci_si_mcp.config import Settings
from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.index import LocalIndex
from nci_si_mcp.models import ReleaseInfo
from nci_si_mcp.service import NCISIService


class FakeEVS:
    def __init__(self, fail_lookup=False, live_version="26.06e"):
        self.fail_lookup = fail_lookup
        self.live_version = live_version
        self.batch_calls = []

    def get_api_version(self):
        return {"version": "test"}

    def resolve_monthly_ncit_release(self):
        return ReleaseInfo(
            terminology="ncit",
            version="26.06e",
            date="2026-06-29",
            name="NCI Thesaurus 26.06e",
            terminology_version="ncit_26.06e",
            latest=True,
            monthly=True,
            weekly=False,
        )

    def get_concept(self, code):
        if self.fail_lookup:
            from nci_si_mcp.evs import EVSError

            raise EVSError("down")
        return {
            "code": code,
            "name": "Neoplasm",
            "terminology": "ncit",
            "version": self.live_version,
            "definitions": [],
            "synonyms": [],
            "properties": [],
        }

    def get_concepts_by_codes(self, codes):
        batch = list(codes)
        self.batch_calls.append(batch)
        return [self.get_concept(code) for code in batch]


class MetadataFailEVS(FakeEVS):
    def resolve_monthly_ncit_release(self):
        from nci_si_mcp.evs import EVSError

        raise EVSError("metadata down")


def make_service(tmp_path, fake_evs):
    settings = Settings(data_dir=Path(tmp_path), evs_max_attempts=1)
    return NCISIService(
        settings,
        evs=fake_evs,
        index=LocalIndex(Path(tmp_path)),
        embedding_provider=HashingEmbeddingProvider(),
    )


class ServiceTest(unittest.TestCase):
    def test_lookup_falls_back_to_active_cache(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            service = make_service(tmp_path, FakeEVS(fail_lookup=True))
            service.index.upsert_concepts(
                [
                    {
                        "code": "C3262",
                        "name": "Neoplasm",
                        "terminology": "ncit",
                        "version": "26.06e",
                        "definitions": [],
                        "synonyms": [],
                        "properties": [],
                    }
                ],
                "2026-06-29",
                service.embedding_provider,
            )

            result = service.lookup("C3262")

            self.assertEqual(result["source"], "active_cache")
            self.assertEqual(result["release_version"], "26.06e")
            self.assertNotIn("raw", result)

            raw_result = service.lookup("C3262", include_raw=True)
            self.assertIn("raw", raw_result)

    def test_lookup_falls_back_when_release_resolution_fails(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            service = make_service(tmp_path, MetadataFailEVS())
            service.index.upsert_concepts(
                [
                    {
                        "code": "C3262",
                        "name": "Neoplasm",
                        "terminology": "ncit",
                        "version": "26.06e",
                        "definitions": [],
                        "synonyms": [],
                        "properties": [],
                    }
                ],
                "2026-06-29",
                service.embedding_provider,
            )

            result = service.lookup("C3262")

            self.assertEqual(result["source"], "active_cache")
            self.assertEqual(result["release_version"], "26.06e")

    def test_lookup_detects_live_index_version_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            service = make_service(tmp_path, FakeEVS(live_version="26.07a"))
            service.index.upsert_concepts(
                [
                    {
                        "code": "C3262",
                        "name": "Neoplasm",
                        "terminology": "ncit",
                        "version": "26.06e",
                        "definitions": [],
                        "synonyms": [],
                        "properties": [],
                    }
                ],
                "2026-06-29",
                service.embedding_provider,
            )

            result = service.lookup("C3262")

            self.assertTrue(result["isError"])
            self.assertEqual(result["error"], "version_mismatch")

    def test_release_info_keeps_active_index_when_metadata_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            service = make_service(tmp_path, MetadataFailEVS())
            service.index.upsert_concepts(
                [
                    {
                        "code": "C3262",
                        "name": "Neoplasm",
                        "terminology": "ncit",
                        "version": "26.06e",
                        "definitions": [],
                        "synonyms": [],
                        "properties": [],
                    }
                ],
                "2026-06-29",
                service.embedding_provider,
            )

            result = service.release_info()

            self.assertTrue(result["selected_monthly_release"]["isError"])
            self.assertEqual(result["active_index"]["release_version"], "26.06e")

    def test_index_version_mismatch_does_not_activate_release(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            service = make_service(tmp_path, FakeEVS(live_version="26.07a"))

            result = service.index_codes(["C3262"])

            self.assertTrue(result["isError"])
            self.assertEqual(result["error"], "index_incompatible")
            self.assertIsNone(service.index.get_active_manifest())

    def test_indexing_batches_requests(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            fake_evs = FakeEVS()
            settings = Settings(data_dir=Path(tmp_path), index_batch_size=1)
            service = NCISIService(
                settings,
                evs=fake_evs,
                index=LocalIndex(Path(tmp_path)),
                embedding_provider=HashingEmbeddingProvider(),
            )

            result = service.index_codes(["C3262", "C40704"])

            self.assertNotIn("isError", result)
            self.assertEqual(fake_evs.batch_calls, [["C3262"], ["C40704"]])

    def test_public_inputs_return_shared_error_envelope(self):
        with tempfile.TemporaryDirectory() as tmp_path:
            service = make_service(tmp_path, FakeEVS())

            search_result = service.search(" ", limit=-1, mode="unknown")
            lookup_result = service.lookup("not-a-code")
            traversal_result = service.traverse([], direction="sideways")

            for result in (search_result, lookup_result, traversal_result):
                self.assertTrue(result["isError"])
                self.assertEqual(result["error"], "invalid_request")
                self.assertIn("message", result)
            self.assertEqual(service.cadsr_status()["state"], "reuse_pending")


if __name__ == "__main__":
    unittest.main()

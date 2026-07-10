import tempfile
import unittest

from nci_si_mcp.embeddings import HashingEmbeddingProvider
from nci_si_mcp.index import LocalIndex
from nci_si_mcp.models import ReleaseInfo
from nci_si_mcp.service import NCISIService


class FakeEVS:
    def __init__(self, fail_lookup=False, live_version="26.06e"):
        self.fail_lookup = fail_lookup
        self.live_version = live_version

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


class MetadataFailEVS(FakeEVS):
    def resolve_monthly_ncit_release(self):
        from nci_si_mcp.evs import EVSError

        raise EVSError("metadata down")


def make_service(tmp_path, fake_evs):
    service = NCISIService.__new__(NCISIService)
    service.evs = fake_evs
    service.index = LocalIndex(tmp_path)
    service.embedding_provider = HashingEmbeddingProvider()
    return service


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


if __name__ == "__main__":
    unittest.main()

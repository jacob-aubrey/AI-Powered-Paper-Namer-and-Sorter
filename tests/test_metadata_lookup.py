from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from metadata_lookup import (  # noqa: E402
    DOIResolution,
    extract_doi_candidates,
    normalize_doi,
    resolve_doi,
)


class MetadataLookupTests(unittest.TestCase):
    def test_normalize_doi_accepts_common_printed_forms_and_rejects_extra_text(self):
        self.assertEqual(normalize_doi(" DOI: 10.1000/ABC.Def "), "10.1000/abc.def")
        self.assertEqual(
            normalize_doi("https://doi.org/10.5555%2FExample_(2026)."),
            "10.5555/example_(2026)",
        )
        self.assertEqual(normalize_doi("The DOI is 10.1000/example"), "")
        self.assertEqual(normalize_doi("not-a-doi"), "")

    def test_extract_doi_candidates_is_ordered_deduplicated_and_bounded(self):
        text = """
            DOI: 10.1111/First.1; https://doi.org/10.2222/second-2.
            A duplicate 10.1111/first.1 appears in the references.
        """
        self.assertEqual(
            extract_doi_candidates(text),
            ["10.1111/first.1", "10.2222/second-2"],
        )
        self.assertEqual(extract_doi_candidates(text, limit=1), ["10.1111/first.1"])

    def test_crossref_record_is_parsed_without_a_title_or_author_search(self):
        calls: list[tuple[str, float, str]] = []

        def fetch_json(url, *, timeout, user_agent):
            calls.append((url, timeout, user_agent))
            return {
                "message": {
                    "DOI": "10.1234/Example.Article",
                    "title": ["A Careful Example Article"],
                    "author": [
                        {"given": "Jane", "family": "Doe"},
                        {"given": "John", "family": "Smith"},
                    ],
                    "container-title": ["Journal of Careful Examples"],
                    "short-container-title": ["J Careful Ex"],
                    "published-print": {"date-parts": [[2025, 3, 1]]},
                    "volume": "12",
                    "issue": "4",
                    "type": "journal-article",
                    "publisher": "Example Press",
                    "URL": "https://doi.org/10.1234/Example.Article",
                    "relation": {
                        "is-supplement-to": [
                            {"id": "10.9999/PARENT", "id-type": "doi"},
                        ]
                    },
                }
            }

        result = resolve_doi("https://doi.org/10.1234/Example.Article", fetch_json=fetch_json)

        self.assertIsInstance(result, DOIResolution)
        assert result is not None
        self.assertEqual(result.doi, "10.1234/example.article")
        self.assertEqual(result.provider, "Crossref")
        self.assertEqual(result.evidence_label, "DOI metadata retrieved from Crossref")
        self.assertEqual(result.title, "A Careful Example Article")
        self.assertEqual(result.authors, ("Jane Doe", "John Smith"))
        self.assertEqual(result.journal, "Journal of Careful Examples")
        self.assertEqual(result.journal_abbreviation, "J Careful Ex")
        self.assertEqual(result.year, "2025")
        self.assertEqual(result.volume, "12")
        self.assertEqual(result.issue, "4")
        self.assertEqual(result.document_type, "journal_article")
        self.assertTrue(result.is_supplement_to_parent)
        self.assertEqual(result.relations[0].identifier, "10.9999/parent")
        self.assertEqual(result.parent_doi, "10.9999/parent")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://api.crossref.org/works/10.1234/example.article")
        self.assertEqual(calls[0][1], 8.0)
        self.assertIn("AI-Paper-Sorter", calls[0][2])
        self.assertNotIn("query", calls[0][0])

    def test_datacite_is_used_when_crossref_is_unavailable(self):
        calls: list[str] = []

        def fetch_json(url, *, timeout, user_agent):
            calls.append(url)
            if "crossref" in url:
                raise OSError("temporary outage")
            return {
                "data": {
                    "id": "10.5678/slides.si",
                    "attributes": {
                        "doi": "10.5678/slides.si",
                        "titles": [{"title": "Supporting Slides"}],
                        "creators": [{"givenName": "Ava", "familyName": "Ng"}],
                        "publicationYear": 2026,
                        "publisher": "Example University",
                        "types": {"resourceType": "Presentation"},
                        "relatedIdentifiers": [
                            {
                                "relationType": "IsSupplementTo",
                                "relatedIdentifier": "10.1000/PARENT",
                                "relatedIdentifierType": "DOI",
                            }
                        ],
                    },
                }
            }

        result = resolve_doi("10.5678/SLIDES.SI", fetch_json=fetch_json)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.provider, "DataCite")
        self.assertEqual(result.title, "Supporting Slides")
        self.assertEqual(result.primary_creator, "Ava Ng")
        self.assertEqual(result.year, "2026")
        self.assertEqual(result.document_type, "presentation_poster")
        self.assertTrue(result.is_supplement_to_parent)
        self.assertEqual(
            calls,
            [
                "https://api.crossref.org/works/10.5678/slides.si",
                "https://api.datacite.org/dois/10.5678/slides.si",
            ],
        )

    def test_lookup_fails_safely_for_bad_network_or_mismatched_record(self):
        self.assertIsNone(
            resolve_doi("10.1010/network", fetch_json=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("down")))
        )
        self.assertIsNone(
            resolve_doi(
                "10.1010/requested",
                fetch_json=lambda *args, **kwargs: {
                    "message": {"DOI": "10.1010/different", "title": ["Wrong record"]}
                },
                providers=("crossref",),
            )
        )
        self.assertIsNone(resolve_doi("not a DOI", fetch_json=lambda *args, **kwargs: {}))
        self.assertIsNone(
            resolve_doi(
                "10.1010/requested",
                fetch_json=lambda *args, **kwargs: {"message": {"title": ["Unverified record"]}},
                providers=("crossref",),
            )
        )

    def test_result_can_be_translated_to_the_sorter_metadata_shape(self):
        result = DOIResolution(
            doi="10.1000/test",
            provider="Crossref",
            evidence_label="DOI metadata retrieved from Crossref",
            title="Test item",
            authors=("Jane Doe", "John Roe"),
            journal="Test Journal",
            year="2024",
            document_type="journal_article",
        )

        details = result.as_document_details()

        self.assertEqual(details["primary_creator"], "Jane Doe")
        self.assertEqual(details["creators"], ["Jane Doe", "John Roe"])
        self.assertEqual(details["identifier"], {"doi": "10.1000/test"})
        self.assertTrue(details["is_multiple_creators"])
        self.assertEqual(details["evidence_label"], "DOI metadata retrieved from Crossref")


if __name__ == "__main__":
    unittest.main()

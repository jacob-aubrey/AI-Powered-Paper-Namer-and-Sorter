from __future__ import annotations

"""Integration tests for the DOI-first sorting path.

These tests deliberately patch extraction and DOI resolution.  They exercise the
decision boundary in ``core_logic`` without contacting Gemini, Crossref, or
DataCite, and without needing a real PDF or PowerPoint file on disk.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core_logic import (  # noqa: E402
    DocumentExtraction,
    build_proposed_filename,
    get_document_details,
    validate_document_filename,
)
from document_types import is_processable_document  # noqa: E402
from metadata_lookup import DOIReference, DOIResolution  # noqa: E402


ARTICLE_DOI = "10.1234/example.article"
PARENT_DOI = "10.1234/example.parent"


def _article_extraction(*, text: str | None = None) -> DocumentExtraction:
    """Return believable local front-page data for an ordinary article."""

    return DocumentExtraction(
        text=text
        or (
            "A Careful Example Article\n"
            "Jane Doe; John Smith\n"
            "Journal of Careful Examples\n"
            f"DOI: {ARTICLE_DOI}\n"
            "Abstract\n"
        ),
        metadata={
            "title": "A Careful Example Article",
            "author": "Jane Doe",
        },
        document_format="PDF",
        page_or_section_count=1,
    )


def _article_resolution() -> DOIResolution:
    return DOIResolution(
        doi=ARTICLE_DOI,
        provider="Crossref",
        evidence_label="DOI metadata retrieved from Crossref",
        title="A Careful Example Article",
        authors=("Jane Doe", "John Smith"),
        journal="Journal of Careful Examples",
        journal_abbreviation="J Careful Ex",
        year="2025",
        volume="12",
        issue="4",
        document_type="journal_article",
    )


class SmartMetadataIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_matching_doi_wins_without_a_gemini_key(self) -> None:
        """A validated DOI must work even when text-to-AI is unavailable."""

        document = self.root / "article.pdf"
        with (
            patch("core_logic.extract_document", return_value=_article_extraction()),
            patch("core_logic.resolve_doi", return_value=_article_resolution()) as resolve,
        ):
            details = get_document_details(
                document,
                api_key="",
                allow_cloud_ai=True,
                allow_online_metadata_lookup=True,
            )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(resolve.call_args_list[0].args, (ARTICLE_DOI,))
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(details["source"], "DOI")
        self.assertEqual(details["evidence_label"], "Verified by DOI metadata")
        self.assertFalse(details["needs_review"])
        self.assertEqual(details["review_reasons"], [])
        self.assertEqual(details["identifier"]["doi"], ARTICLE_DOI)
        self.assertEqual(
            build_proposed_filename(details, ".pdf"),
            "Jane_Doe_et_al_Journal_of_Careful_Examples_2025.pdf",
        )

    def test_mismatched_reference_doi_never_supplants_local_details(self) -> None:
        """A DOI seen only in references cannot rename an unrelated document."""

        document = self.root / "actual-paper.pdf"
        local = DocumentExtraction(
            text=(
                "Actual Paper About Safe Sorting\n"
                "Jane Local\n"
                "Abstract\n"
                "References\n"
                "[1] Somebody Else. An Unrelated Paper. doi:10.9999/referenced.paper\n"
            ),
            metadata={"title": "Actual Paper About Safe Sorting", "author": "Jane Local"},
            document_format="PDF",
        )
        unrelated = DOIResolution(
            doi="10.9999/referenced.paper",
            provider="Crossref",
            evidence_label="DOI metadata retrieved from Crossref",
            title="An Entirely Different Referenced Paper",
            authors=("Foreign Author",),
            journal="Foreign Journal",
            year="2001",
            document_type="journal_article",
        )

        with (
            patch("core_logic.extract_document", return_value=local),
            patch("core_logic.resolve_doi", return_value=unrelated),
        ):
            details = get_document_details(
                document,
                api_key="",
                allow_cloud_ai=False,
                allow_online_metadata_lookup=True,
            )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertNotEqual(details["source"], "DOI")
        self.assertNotEqual(details["evidence_label"], "Verified by DOI metadata")
        self.assertEqual(details["title"], "Actual Paper About Safe Sorting")
        self.assertEqual(details["primary_creator"], "Jane Local")
        self.assertNotIn("Foreign", build_proposed_filename(details, ".pdf"))
        self.assertNotIn("2001", build_proposed_filename(details, ".pdf"))

    def test_disabling_online_metadata_never_calls_doi_resolver(self) -> None:
        """The local-only setting must be a real privacy boundary."""

        document = self.root / "local-only.pdf"
        with (
            patch("core_logic.extract_document", return_value=_article_extraction()),
            patch("core_logic.resolve_doi", side_effect=AssertionError("resolver must stay local")) as resolve,
        ):
            details = get_document_details(
                document,
                api_key="",
                allow_cloud_ai=False,
                allow_online_metadata_lookup=False,
            )

        self.assertIsNotNone(details)
        assert details is not None
        resolve.assert_not_called()
        self.assertEqual(details["source"], "Basic")
        self.assertEqual(details["title"], "A Careful Example Article")
        self.assertEqual(details["evidence_label"], "Suggested from local document information")

    def test_doi_lookup_error_falls_back_without_breaking_sorting(self) -> None:
        """A metadata-service outage must leave a safe local proposal behind."""

        document = self.root / "offline.pdf"
        with (
            patch("core_logic.extract_document", return_value=_article_extraction()),
            patch("core_logic.resolve_doi", side_effect=OSError("metadata service unavailable")),
        ):
            details = get_document_details(
                document,
                api_key="",
                allow_cloud_ai=False,
                allow_online_metadata_lookup=True,
            )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details["source"], "Basic")
        self.assertEqual(details["title"], "A Careful Example Article")

    def test_ai_backup_explicitly_disables_unused_automatic_function_calling(self) -> None:
        """The Gemini request should not enable the AFC feature that caused log noise."""

        document = self.root / "ai-backup.pdf"
        extraction = DocumentExtraction(
            text="A Clear Local Report\nJane Doe\nPublished 2026",
            metadata={"title": "A Clear Local Report", "author": "Jane Doe"},
            document_format="PDF",
        )
        client = MagicMock()
        client.models.generate_content.return_value.text = (
            '{"title":"A Clear Local Report","primary_creator":"Jane Doe",'
            '"creators":["Jane Doe"],"year":"2026",'
            '"document_type":"technical_report","venue_or_publisher":"Example Institute",'
            '"journal_abbreviation":"","volume":"","issue":"",'
            '"identifier":{},"is_multiple_creators":false,'
            '"is_supplementary_material":false,"warnings":[]}'
        )
        with (
            patch("core_logic.extract_document", return_value=extraction),
            patch("core_logic.genai.Client", return_value=client),
        ):
            details = get_document_details(
                document,
                api_key="not-a-real-key",
                allow_cloud_ai=True,
                allow_online_metadata_lookup=False,
            )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details["source"], "AI")
        config = client.models.generate_content.call_args.kwargs["config"]
        self.assertTrue(config.automatic_function_calling.disable)

    def test_confirmed_supplement_uses_parent_and_adds_one_si_to_pptx(self) -> None:
        """A DOI-declared supplement names itself after its verified parent."""

        supplement_doi = "10.1234/example.supplement"
        document = self.root / "supporting-slides.pptx"
        extraction = DocumentExtraction(
            text=(
                "Supporting Information\n"
                f"DOI: {supplement_doi}\n"
                "Supplementary slides for the parent article\n"
            ),
            metadata={"title": "Supporting Information"},
            document_format="PPTX",
            page_or_section_count=3,
        )
        supplement = DOIResolution(
            doi=supplement_doi,
            provider="Crossref",
            evidence_label="DOI metadata retrieved from Crossref",
            title="Supporting Information",
            authors=("Jane Doe",),
            year="2026",
            document_type="presentation_poster",
            relations=(DOIReference("is_supplement_to", PARENT_DOI, "doi"),),
        )
        parent = DOIResolution(
            doi=PARENT_DOI,
            provider="Crossref",
            evidence_label="DOI metadata retrieved from Crossref",
            title="The Parent Article",
            authors=("Jane Doe", "John Smith"),
            journal="Journal of Parent Studies",
            year="2026",
            document_type="journal_article",
        )

        with (
            patch("core_logic.extract_document", return_value=extraction),
            patch("core_logic.resolve_doi", side_effect=[supplement, parent]) as resolve,
        ):
            details = get_document_details(
                document,
                api_key="",
                allow_cloud_ai=False,
                allow_online_metadata_lookup=True,
            )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual([call.args for call in resolve.call_args_list], [(supplement_doi,), (PARENT_DOI,)])
        self.assertTrue(details["is_supplementary_material"])
        self.assertEqual(details["supplemental_parent_doi"], PARENT_DOI)
        self.assertEqual(details["identifier"]["doi"], supplement_doi)
        self.assertEqual(details["primary_creator"], "Jane Doe")
        self.assertEqual(details["journal"], "Journal of Parent Studies")
        proposed = build_proposed_filename(details, ".pptx")
        self.assertTrue(proposed.endswith("_SI.pptx"))
        self.assertEqual(proposed.upper().count("_SI"), 1)

    def test_ordinary_article_that_mentions_supporting_information_is_not_si(self) -> None:
        """A citation notice is not evidence that this file itself is a supplement."""

        document = self.root / "ordinary-article.pdf"
        text = (
            "A Careful Example Article\n"
            "Jane Doe; John Smith\n"
            f"DOI: {ARTICLE_DOI}\n"
            "See Supporting Information for additional methods.\n"
        )
        with (
            patch("core_logic.extract_document", return_value=_article_extraction(text=text)),
            patch("core_logic.resolve_doi", return_value=_article_resolution()),
        ):
            details = get_document_details(
                document,
                api_key="",
                allow_cloud_ai=False,
                allow_online_metadata_lookup=True,
            )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertFalse(details["is_supplementary_material"])
        self.assertEqual(details["supplemental_status"], "none")
        self.assertNotIn("_SI", build_proposed_filename(details, ".pdf").upper())

    def test_confirmed_si_suffix_is_added_once_in_every_filename_style(self) -> None:
        """Changing a user's template never loses or duplicates the SI marker."""

        details = {
            "title": "Parent Article Supporting Information",
            "primary_creator": "Jane Doe",
            "author": "Doe",
            "year": "2026",
            "document_type": "journal_article",
            "venue_or_publisher": "Journal of Parent Studies",
            "journal": "Journal of Parent Studies",
            "journal_abbreviation": "J Parent Stud",
            "volume": "8",
            "issue": "2",
            "is_multiple_creators": True,
            "is_supplementary_material": True,
        }
        configurations = (
            ("smart", ""),
            ("journal_compact", ""),
            ("journal_detailed", ""),
            ("author_year_title", ""),
            ("title_year_type", ""),
            ("custom", "{author_last}_{journal}_{year}"),
        )
        for filename_format, template in configurations:
            with self.subTest(filename_format=filename_format):
                proposal = build_proposed_filename(
                    details,
                    ".pptx",
                    filename_format=filename_format,
                    custom_template=template,
                )
                self.assertTrue(proposal.endswith("_SI.pptx"))
                self.assertEqual(proposal.upper().count("_SI"), 1)

    def test_legacy_ppt_is_kept_and_requires_manual_review(self) -> None:
        """Old binary PowerPoint files are never sent to AI as though readable."""

        document = self.root / "old-slides.ppt"
        extraction = DocumentExtraction(
            text="",
            metadata={"title": "Legacy Supporting Slides"},
            warnings=["Legacy .ppt text extraction is unsupported."],
            document_format="PPT",
            requires_manual_review=True,
        )
        with patch("core_logic.extract_document", return_value=extraction):
            details = get_document_details(
                document,
                api_key="a-key-that-must-not-be-used",
                allow_cloud_ai=True,
                allow_online_metadata_lookup=True,
            )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details["source"], "Basic")
        self.assertEqual(details["document_type"], "presentation_poster")
        self.assertTrue(details["needs_review"])
        self.assertIn("could not be read completely", " ".join(details["review_reasons"]))
        self.assertTrue(build_proposed_filename(details, ".ppt").endswith(".ppt"))

    def test_powerpoint_filename_extensions_and_office_lock_files_are_safe(self) -> None:
        """PPT/PPTX are accepted, but temporary Office lock files never enter sorting."""

        self.assertEqual(validate_document_filename("Talk", ".ppt"), "Talk.ppt")
        self.assertEqual(validate_document_filename("Talk", ".pptx"), "Talk.pptx")
        with self.assertRaisesRegex(ValueError, "original .ppt extension"):
            validate_document_filename("Talk.pptx", ".ppt")
        with self.assertRaisesRegex(ValueError, "original .pptx extension"):
            validate_document_filename("Talk.ppt", ".pptx")
        self.assertTrue(is_processable_document(self.root / "talk.ppt"))
        self.assertTrue(is_processable_document(self.root / "talk.pptx"))
        self.assertFalse(is_processable_document(self.root / "~$talk.ppt"))
        self.assertFalse(is_processable_document(self.root / "~$talk.pptx"))


if __name__ == "__main__":
    unittest.main()

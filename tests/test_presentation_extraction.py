from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from presentation_extraction import (  # noqa: E402
    PresentationExtractionError,
    extract_presentation,
)


def _write_pptx(
    path: Path,
    slides: list[str],
    *,
    title: str = "",
    author: str = "",
    presentation_order: list[int] | None = None,
) -> None:
    """Make the smallest safe PPTX-like package needed by the focused tests."""

    presentation_order = presentation_order or list(range(1, len(slides) + 1))
    if sorted(presentation_order) != list(range(1, len(slides) + 1)):
        raise ValueError("presentation_order must contain each slide number exactly once.")
    slide_references = "".join(
        f'<p:sldId id="{256 + index}" r:id="rId{index + 1}"/>'
        for index in range(len(presentation_order))
    )
    relationships = "".join(
        (
            f'<Relationship Id="rId{index + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
            f'Target="slides/slide{slide_number}.xml"/>'
        )
        for index, slide_number in enumerate(presentation_order)
    )
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
 xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/">
  <dc:title>{escape(title)}</dc:title>
  <dc:creator>{escape(author)}</dc:creator>
  <dc:subject>Supplementary material</dc:subject>
  <cp:keywords>metadata, testing</cp:keywords>
</cp:coreProperties>
"""
    presentation = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldIdLst>{slide_references}</p:sldIdLst>
</p:presentation>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>
"""
    rels = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {relationships}
</Relationships>
"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", rels)
        archive.writestr("docProps/core.xml", core)
        for index, text in enumerate(slides, start=1):
            archive.writestr(
                f"ppt/slides/slide{index}.xml",
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
            )


class PresentationExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_pptx_extracts_slide_text_and_core_properties(self) -> None:
        path = self.root / "supplement.pptx"
        _write_pptx(
            path,
            ["Supporting Information", "Methods and Results"],
            title="Example Supporting Information",
            author="Jane Example",
        )

        extraction = extract_presentation(path)

        self.assertEqual(extraction.document_format, "PPTX")
        self.assertEqual(extraction.slide_count, 2)
        self.assertEqual(extraction.metadata["title"], "Example Supporting Information")
        self.assertEqual(extraction.metadata["author"], "Jane Example")
        self.assertIn("Supporting Information", extraction.text)
        self.assertIn("Methods and Results", extraction.text)
        self.assertLess(
            extraction.text.index("Supporting Information"),
            extraction.text.index("Methods and Results"),
        )
        self.assertFalse(extraction.requires_manual_review)

    def test_pptx_uses_presentation_slide_order(self) -> None:
        path = self.root / "ordered.pptx"
        _write_pptx(
            path,
            ["Second in presentation order", "First in presentation order"],
            presentation_order=[2, 1],
        )

        extraction = extract_presentation(path)

        self.assertLess(
            extraction.text.index("First in presentation order"),
            extraction.text.index("Second in presentation order"),
        )

    def test_pptx_text_is_bounded(self) -> None:
        path = self.root / "large-text.pptx"
        _write_pptx(path, ["A" * 80, "B" * 80])

        extraction = extract_presentation(path, max_chars=25)

        self.assertLessEqual(len(extraction.text), 25)
        self.assertTrue(
            any("shortened to the safety limit" in warning for warning in extraction.warnings)
        )
        self.assertTrue(extraction.requires_manual_review)

    def test_pptx_with_missing_required_parts_has_a_clear_error(self) -> None:
        path = self.root / "not-a-presentation.pptx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")

        with self.assertRaisesRegex(PresentationExtractionError, "complete PPTX"):
            extract_presentation(path)

    def test_invalid_pptx_zip_has_a_clear_error(self) -> None:
        path = self.root / "broken.pptx"
        path.write_bytes(b"not a zip archive")

        with self.assertRaisesRegex(PresentationExtractionError, "valid PPTX"):
            extract_presentation(path)

    def test_legacy_ppt_returns_a_manual_review_result(self) -> None:
        path = self.root / "legacy-supporting-info.ppt"
        path.write_bytes(b"legacy binary placeholder")

        extraction = extract_presentation(path)

        self.assertEqual(extraction.document_format, "PPT")
        self.assertEqual(extraction.text, "")
        self.assertTrue(extraction.requires_manual_review)
        self.assertTrue(any("unsupported" in warning.casefold() for warning in extraction.warnings))


if __name__ == "__main__":
    unittest.main()

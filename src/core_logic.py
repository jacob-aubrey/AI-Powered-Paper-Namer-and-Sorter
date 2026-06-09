# core_logic.py

import json
import logging
import re
from pathlib import Path

import google.generativeai as genai
from pypdf import PdfReader

WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

def get_paper_details(pdf_path: Path, api_key: str):
    try:
        reader = PdfReader(pdf_path); text_content = ""
        for page in reader.pages[:5]:
            extracted = page.extract_text()
            if extracted: text_content += extracted + "\n\n"
        if not text_content.strip(): return None
        text_snippet = text_content[:8000]
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            'gemini-2.5-flash',
            generation_config={'temperature': 0.0, 'response_mime_type': 'application/json'},
        )
        prompt = f"""
        Analyze text from a research paper and output ONLY a valid JSON object with these keys:
        1. "author": ONLY the last name of the VERY FIRST author listed.
        2. "year": the 4-digit publication year.
        3. "journal": official NLM/PubMed journal abbreviation if available; else the full journal name. Preprints => "Preprint".
        4. "title": the full official title of the paper.
        5. "is_multiple_authors": boolean true/false.
        Example: {{"author": "FitzGerald", "year": "2016", "journal": "Invest Radiol", "title": "A Proposed...", "is_multiple_authors": true}}
        Paper Text: ---
        {text_snippet}
        ---
        """
        response = model.generate_content(prompt)
        details = parse_ai_json(response.text or "")
        details.setdefault('author', 'Unknown'); details.setdefault('year', 'Unknown')
        details.setdefault('journal', 'Unknown'); details.setdefault('title', 'Unknown Title')
        details.setdefault('is_multiple_authors', True)
        return details
    except Exception as e:
        logging.error(f"AI processing error for {pdf_path.name}: {e}")
        return None

def get_basic_paper_details(pdf_path: Path):
    try:
        reader = PdfReader(pdf_path)
        metadata = reader.metadata or {}
        title = _clean_metadata_value(metadata.get("/Title")) or _title_from_first_page(reader) or pdf_path.stem
        author = _first_author_from_metadata(metadata.get("/Author")) or "Unknown"
        year = _year_from_metadata(metadata) or _year_from_first_pages(reader) or "Unknown"
        journal = "Unknown"
        return {
            "author": author,
            "year": year,
            "journal": journal,
            "title": title,
            "is_multiple_authors": True,
            "source": "Basic",
        }
    except Exception as e:
        logging.error(f"Basic processing error for {pdf_path.name}: {e}")
        return {
            "author": "Unknown",
            "year": "Unknown",
            "journal": "Unknown",
            "title": pdf_path.stem,
            "is_multiple_authors": True,
            "source": "Basic",
        }

def _clean_metadata_value(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

def _first_author_from_metadata(value):
    author = _clean_metadata_value(value)
    if not author:
        return ""
    author = re.split(r";|\band\b|,", author, maxsplit=1, flags=re.IGNORECASE)[0]
    parts = [p for p in re.split(r"\s+", author.strip()) if p]
    return parts[-1] if parts else ""

def _year_from_metadata(metadata):
    for key in ("/CreationDate", "/ModDate", "/Subject"):
        value = _clean_metadata_value(metadata.get(key))
        match = re.search(r"(19|20)\d{2}", value)
        if match:
            return match.group(0)
    return ""

def _year_from_first_pages(reader):
    text = ""
    for page in reader.pages[:2]:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else ""

def _title_from_first_page(reader):
    if not reader.pages:
        return ""
    text = reader.pages[0].extract_text() or ""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    candidates = [
        line for line in lines[:12]
        if 12 <= len(line) <= 180 and not re.search(r"^(abstract|keywords|doi|http|www\.|received|accepted)\b", line, re.IGNORECASE)
    ]
    return max(candidates, key=len) if candidates else ""

def sanitize_filename_part(part):
    return re.sub(r'[\\/*?:"<>|]', "", str(part).strip()).replace(' ', '_')

def parse_ai_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("No valid JSON object found in AI response.")

def validate_pdf_filename(name: str) -> str:
    clean = name.strip().strip('"')
    if not clean:
        raise ValueError("Filename cannot be empty.")
    if not clean.lower().endswith(".pdf"):
        clean += ".pdf"

    path = Path(clean)
    stem = clean[:-4]
    if path.name != clean or path.is_absolute() or any(sep in clean for sep in ("\\", "/")):
        raise ValueError("Enter a filename only, not a folder path.")
    if stem.strip(" .") == "":
        raise ValueError("Filename must include a name before .pdf.")
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError("That filename is reserved by Windows.")
    if stem[-1] in (" ", "."):
        raise ValueError("Filename cannot end with a space or period before .pdf.")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', clean):
        raise ValueError("Filename contains characters Windows does not allow.")
    return clean

def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1

def cleanup_author_string(author: str) -> str:
    if not author:
        return ''
    if ';' in author: author = author.split(';')[0]
    if ',' in author: author = author.split(',')[0]
    return author.strip()

def list_dirs(parent: Path) -> list[Path]:
    try: return sorted([p for p in parent.iterdir() if p.is_dir() and not p.name.startswith('.')])
    except Exception: return []

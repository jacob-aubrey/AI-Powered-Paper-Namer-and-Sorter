# Changelog

## 1.2.0 — 2026-09-03

### Added

- Smart metadata lookup: the app now checks a DOI found inside a document against an exact Crossref or DataCite record before using Gemini as a backup.
- Support for modern PowerPoint (`.pptx`) files throughout adding, watching, sorting, and rename-in-place flows, with local slide-text/core-property extraction.
- Cautious legacy PowerPoint (`.ppt`) support: files can be moved and renamed but clearly request manual review because the old binary format is not safely parsed.
- An optional PowerPoint AI-analysis setting, directly below the existing Word setting.
- Supporting-information detection and a single `_SI` filename marker, including DOI-linked parent-paper naming when a verified relationship is available.
- Separate saved control for online DOI/citation lookup; Smart lookup enables it by default, while Local-only privacy mode disables all online lookup and AI.

### Changed

- Replaced AI self-reported confidence percentages with clear evidence labels such as “Verified by DOI metadata” and specific review reasons only when something is missing, conflicting, or uncertain.
- Moved **Clear Display** directly beside the **Activity Log** heading. It still clears only the visible log, never `paper_sorter_log.txt`.
- Updated the README, quick setup guide, and example settings for DOI-first naming, privacy choices, PowerPoint files, and supporting information.

### Fixed

- Explicitly disabled Gemini automatic function calling for the sorter’s text-only request, removing the confusing AFC warning from normal logs.
- A failed or mismatched DOI lookup now falls back safely without interrupting sorting or adopting metadata from a cited reference.

## 1.1.1 — 2026-09-03

### Fixed

- Log links now work on the first click while the log remains read-only; they no longer depend on a prior mouse movement over the link.
- **View Location** opens Windows Explorer and highlights the current document when available.
- Historical log links now recover one uniquely matching paper if it was later moved within the configured Sorted folder. Ambiguous or missing matches show a clear centered explanation instead of only writing a file path/error into the log.

## 1.1.0 — 2026-09-02

### Added

- Support for PDF and modern Word (`.docx`) documents throughout adding, drag-and-drop, watching, sorting, and rename-in-place flows.
- Local DOCX text/core-property extraction, including table/header/footer text where available.
- Type-aware handling for journal articles, reports, theses, guidelines, preprints, and other non-journal material.
- A review indicator with confidence and warnings before a file is moved or renamed.
- An explicit opt-in before extracted Word-document text can be sent to Gemini for AI naming.
- Per-user Windows DPAPI protection for saved Gemini keys.
- Configurable filename-style presets and a validated custom-template option.
- A read-only on-screen log with a display-only Clear button that preserves the log file.
- Automated regression tests for DOCX support, extension safety, queue coalescing, outlier normalization, and settings protection.

### Changed

- Migrated from the legacy `google-generativeai` library to the maintained `google-genai` SDK.
- Standardized the packaged product name as **AI Paper Sorter**.
- Replaced machine-specific configuration examples with blank placeholders.
- Reworked README and quick setup instructions for first-time users and safe sharing.

### Fixed

- Filename dialogs now resize/scroll for long titles and are centered over the main window.
- Settings, confirmation, and native file/folder dialogs are owned by the main UI for consistent placement.
- Watcher event fan-out no longer fills the visible log with duplicate queue messages.
- Skipped/cancelled unchanged files are snoozed until they change or the user presses Refresh.
- Watch & Launch now honors its off switch, restarts after the To Sort folder changes, and only stops this app's background helper.
- The first successful Settings save starts the in-app watcher without requiring an app restart.
- Queue processing now allows only one user confirmation dialog at a time, preventing nested or crunched prompts.
- Watcher events caused by app-initiated moves/renames no longer queue the same document again.

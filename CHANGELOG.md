# Changelog

## 1.1.0 — Unreleased

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

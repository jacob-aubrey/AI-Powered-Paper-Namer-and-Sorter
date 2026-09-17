# Changelog

## 1.3.2 - 2026-09-17

- Executable filenames now include the release version: `AI Paper Sorter v1.3.2.exe`. Upgrade registration recognizes both older and versioned names.
- Simplified background controls to a dynamic Start/Restart button and Stop. Removed the Refresh and save-folder instructions beneath the buttons.
- Temporary Gemini server failures receive one automatic retry after one second. Persistent failures explain why citation fields are missing and offer a responsive Retry AI button that preserves manually edited filenames and current privacy preferences.
- Local title extraction joins opening title lines and stops before bylines and contents entries. Confirmed SI is displayed as Supporting Information rather than Unknown Document Type.
- Validation: 118 regression tests and offline UI checks, including simulated service failures and real dialog retries. The supplied SI title was checked locally without an AI request.

## 1.3.1 - 2026-09-17

- Default paper names use surnames only, with both surnames for exactly two authors: `Smith_and_Aubrey_et_al_Journal_2026.pdf`.
- Supporting information uses the same citation format plus `_SI`. Local citation fields are read first, DOI records enrich them, and permitted AI fallback fills missing fields. Unresolved citation fields remain explicit review placeholders instead of a long title.
- Settings displays background watcher status and adds immediate Start, Stop, and Restart controls. Stop leaves the open app processing; Refresh rescans files. Immediate controls preserve other unsaved Settings edits.
- Upgrades stop the legacy Python watcher that could block the packaged helper. Missed events, interrupted folder access, and failed launches are retried, with a rotating watcher log in the per-user settings folder.
- The title bar and Windows executable properties display v1.3.1 from a shared version definition.
- Added regression tests for citation naming, SI AI fallback and privacy, watcher recovery, versioned activation, and independent background controls.

## 1.3.0 — 2026-09-13

### Changed

- Windows downloads keep runtime files in `_internal` beside the executable, avoiding extraction on every launch. Keep the complete extracted folder together.
- PDF, Word, and Gemini libraries load when needed, allowing the main window to open sooner.
- Settings now describe **Watch folder** and **Library folder** as roles. Both can have any name; the library root can contain existing documents and category subfolders.
- Metadata lookups share a retry deadline, and Gemini requests have an explicit timeout and no automatic retry before local fallback.

### Fixed

- Watch & Launch opens immediately on a supported file event, leaving download-readiness checks to the app. Native instance checks replace PowerShell and duplicate launch delays.
- Files already present when the helper starts are detected. Moving documents out of the watch folder does not reopen the app.
- Opening an existing app restores its window. Background helpers have an independent lifetime and registration is refreshed after an app upgrade.
- An unexpected document error no longer kills the sort or rename worker. Background logging no longer makes blocking calls into Tk, preventing settings/shutdown deadlocks.
- Dismissing duplicate or final-move confirmations cancels the action. Dialog centering no longer applies Windows display scaling twice, keeping controls on-screen.
- Library navigation uses Windows folder opening, and historical links handle filenames containing brackets literally.
- Generic short titles cannot verify a longer unrelated DOI citation, and Windows reserved device names are rejected even before additional filename extensions.

### Validation

- Added regression coverage for startup, watcher events, confirmation cancellation, queue recovery, metadata limits, and filename edge cases.
- Added an offline `--self-test <report.json>` command to check the packaged UI and document libraries without reading user settings or contacting metadata services.

## 1.2.1 — 2026-09-13

### Fixed

- **View Location** now opens the containing folder directly through Windows instead of passing a file-selection argument to Explorer.
- The containing folder remains accessible when the original document has been removed.

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
